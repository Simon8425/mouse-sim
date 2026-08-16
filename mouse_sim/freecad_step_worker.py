"""FreeCADCmd worker for deterministic STEP tessellation and GLB export.

This module is executed by FreeCADCmd, not imported by the normal server
interpreter.  Keep all FreeCAD and OCCT imports inside ``main`` so compiling
the repository with Python 3.9 remains dependency-free.
"""

import hashlib
import json
import math
import os
import struct
import sys


BACKEND = "freecad-occt"

# FreeCAD stores geometry in internal millimetres regardless of the STEP file's
# declared units, so the scale to metres is always 0.001.
_SCALE_TO_M = 0.001

# Hard caps on tessellation output; exceeding them fails the import cleanly
# instead of exhausting memory in the server process.
_MAX_PART_TRIANGLES = 2_000_000
_MAX_TOTAL_TRIANGLES = 5_000_000

# Deterministic fallback palette used when the GLB carries no materials.
# Mirrors FreeCAD's default per-part import colors so an assembly always
# renders with distinct part colors.
_DEFAULT_PART_PALETTE = (
    (0.36, 1.0, 0.41),
    (1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.47, 0.42, 1.0),
    (0.955, 0.351, 0.0),
    (0.012, 0.332, 0.068),
    (0.0, 1.0, 0.0),
    (1.0, 1.0, 1.0),
    (0.223, 0.223, 0.223),
    (0.591, 0.638, 0.855),
    (0.168, 0.144, 1.0),
    (0.053, 0.053, 0.053),
    (1.0, 1.0, 0.0),
    (0.0, 0.0, 0.0),
)


_IDENTITY_MATRIX = (
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)


def _glb_json_and_bin(data):
    """Split a binary glTF file into its JSON document and binary chunk."""
    if len(data) < 12 or data[:4] != b"glTF":
        raise ValueError("not a GLB file")
    version = struct.unpack_from("<I", data, 4)[0]
    if version != 2:
        raise ValueError("unsupported GLB version {}".format(version))
    gltf = None
    bin_data = b""
    offset = 12
    while offset + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        chunk_end = offset + 8 + chunk_length
        if chunk_end > len(data):
            raise ValueError("GLB chunk exceeds file length")
        chunk = data[offset + 8:chunk_end]
        if chunk_type == 0x4E4F534A:
            gltf = json.loads(chunk.decode("utf-8"))
        elif chunk_type == 0x004E4942:
            bin_data = chunk
        offset = chunk_end
    if gltf is None:
        raise ValueError("GLB has no JSON chunk")
    return gltf, bin_data


def _read_accessor(gltf, bin_data, accessor_index):
    """Decode an accessor's numeric values as tuples."""
    accessor = gltf["accessors"][accessor_index]
    buffer_view = gltf["bufferViews"][accessor["bufferView"]]
    buffer_index = buffer_view.get("buffer", 0)
    if buffer_index != 0:
        raise ValueError("GLB accessor references a non-zero buffer")
    component_type = accessor["componentType"]
    if component_type == 5126:
        component_code, component_size = "f", 4
    elif component_type == 5123:
        component_code, component_size = "H", 2
    elif component_type == 5125:
        component_code, component_size = "I", 4
    else:
        raise ValueError("unsupported GLB component type {}".format(component_type))
    type_components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
    try:
        components = type_components[accessor["type"]]
    except KeyError:
        raise ValueError("unsupported GLB accessor type {!r}".format(accessor["type"]))
    base = buffer_view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = buffer_view.get("byteStride", components * component_size)
    values = []
    for index in range(accessor["count"]):
        offset = base + index * stride
        try:
            values.append(struct.unpack_from("<" + component_code * components, bin_data, offset))
        except struct.error:
            raise ValueError("GLB accessor {} exceeds the binary chunk".format(accessor_index))
    return values


def _trs_matrix(node):
    """Return the 4x4 row-major transform for a glTF node's TRS."""
    translation = node.get("translation") or (0.0, 0.0, 0.0)
    rotation = node.get("rotation") or (0.0, 0.0, 0.0, 1.0)
    scale = node.get("scale") or (1.0, 1.0, 1.0)
    x, y, z, w = (float(v) for v in rotation)
    rotation_matrix = (
        (1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w), 0.0),
        (2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w), 0.0),
        (2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y), 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    scale_matrix = (
        (float(scale[0]), 0.0, 0.0, 0.0),
        (0.0, float(scale[1]), 0.0, 0.0),
        (0.0, 0.0, float(scale[2]), 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    translation_matrix = (
        (1.0, 0.0, 0.0, float(translation[0])),
        (0.0, 1.0, 0.0, float(translation[1])),
        (0.0, 0.0, 1.0, float(translation[2])),
        (0.0, 0.0, 0.0, 1.0),
    )
    return _matrix_mul(_matrix_mul(translation_matrix, rotation_matrix), scale_matrix)


def _matrix_mul(first, second):
    return tuple(
        tuple(
            sum(first[row][k] * second[k][column] for k in range(4))
            for column in range(4)
        )
        for row in range(4)
    )


def _matrix_point(matrix, point):
    x, y, z = point
    return (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3],
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3],
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3],
    )


def _glb_parts(glb_path):
    """Derive per-part meshes from the worker's own GLB output.

    The GLB is written by OCCT's RWGltf_CafWriter from the XCAF document, so
    it carries the authoritative assembly interpretation: correctly located
    instances, node names, and per-mesh CAD colors.  Parsing it back makes
    the display asset, per-part geometry, and analysis mesh exactly
    consistent.  Vertices are world-space metres; the authoring frame is
    flipped (x, -y, -z) to match the scene's presentation convention.
    """
    with open(glb_path, "rb") as handle:
        data = handle.read()
    gltf, bin_data = _glb_json_and_bin(data)
    nodes = gltf.get("nodes") or []
    meshes = gltf.get("meshes") or []
    materials = gltf.get("materials") or []

    world = {}

    def visit(node_index, parent_matrix):
        if node_index < 0 or node_index >= len(nodes):
            raise ValueError("GLB scene references a missing node")
        node = nodes[node_index]
        world[node_index] = _matrix_mul(parent_matrix, _trs_matrix(node))
        for child in node.get("children") or ():
            visit(child, world[node_index])

    scene_roots = []
    scenes = gltf.get("scenes")
    if scenes:
        scene_index = gltf.get("scene", 0)
        if scene_index < 0 or scene_index >= len(scenes):
            raise ValueError("GLB scene index {} is out of range".format(scene_index))
        scene_roots = (scenes[scene_index] or {}).get("nodes") or []
    for root in scene_roots:
        visit(root, _IDENTITY_MATRIX)
    for index in range(len(nodes)):
        if index not in world:
            visit(index, _IDENTITY_MATRIX)

    parts = []
    for node_index, node in enumerate(nodes):
        if "mesh" not in node:
            continue
        mesh_index = node["mesh"]
        if mesh_index < 0 or mesh_index >= len(meshes):
            raise ValueError("GLB node references a missing mesh")
        primitives = meshes[mesh_index].get("primitives") or ()
        node_vertices = []
        node_triangles = []
        for primitive in primitives:
            mode = primitive.get("mode", 4)
            if mode != 4:
                raise ValueError(
                    "GLB primitive mode {} is not TRIANGLES".format(mode)
                )
            attributes = primitive.get("attributes") or {}
            if "POSITION" not in attributes:
                continue
            positions = _read_accessor(gltf, bin_data, attributes["POSITION"])
            if len(positions) and len(positions[0]) != 3:
                raise ValueError("GLB POSITION accessor must be VEC3")
            if "indices" in primitive:
                indices = _read_accessor(gltf, bin_data, primitive["indices"])
                index_values = [int(item[0]) for item in indices]
            else:
                index_values = list(range(len(positions)))
            matrix = world.get(node_index, _IDENTITY_MATRIX)
            vertices = []
            invalid_vertex = False
            for point in positions:
                x, y, z = _matrix_point(matrix, (float(point[0]), float(point[1]), float(point[2])))
                if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                    invalid_vertex = True
                    break
                vertices.append([x, -y, -z])
            if invalid_vertex:
                raise ValueError("GLB mesh {} contains non-finite vertices".format(mesh_index))
            if any(index < 0 or index >= len(vertices) for index in index_values):
                raise ValueError("GLB mesh {} has out-of-range indices".format(mesh_index))
            offset = len(node_vertices)
            node_vertices.extend(vertices)
            node_triangles.extend(
                [
                    [offset + index_values[i], offset + index_values[i + 1], offset + index_values[i + 2]]
                    for i in range(0, len(index_values) - 2, 3)
                ]
            )
        if not node_triangles:
            continue
        if len(node_triangles) > _MAX_PART_TRIANGLES:
            raise RuntimeError(
                "GLB mesh {} exceeds triangle cap ({})".format(mesh_index, _MAX_PART_TRIANGLES)
            )
        color = None
        first_primitive = primitives[0]
        material_index = first_primitive.get("material")
        if material_index is not None and 0 <= material_index < len(materials):
            pbr = materials[material_index].get("pbrMetallicRoughness") or {}
            factor = pbr.get("baseColorFactor")
            if isinstance(factor, (list, tuple)) and len(factor) >= 3:
                color = [
                    round(float(factor[0]), 6),
                    round(float(factor[1]), 6),
                    round(float(factor[2]), 6),
                ]
        if color is None:
            fallback = _DEFAULT_PART_PALETTE[len(parts) % len(_DEFAULT_PART_PALETTE)]
            color = [round(component, 6) for component in fallback]
        parts.append(
            {
                "id": "part-{}".format(len(parts)),
                "name": str(node.get("name") or "part-{}".format(len(parts))),
                "color": color,
                "geometry": {
                    "type": "mesh",
                    "vertices": node_vertices,
                    "triangles": node_triangles,
                    "units": "m",
                    "transform": {
                        "rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                        "translation": [0.0, 0.0, 0.0],
                        "units": "m",
                    },
                },
            }
        )
    if not parts:
        raise RuntimeError("GLB contains no meshes")
    return parts


def _mesh_and_parts(parts):
    """Concatenate per-part meshes into one flattened analysis mesh."""
    vertices = []
    triangles = []
    for part in parts:
        offset = len(vertices)
        vertices.extend(part["geometry"]["vertices"])
        triangles.extend(
            [
                [offset + index_0, offset + index_1, offset + index_2]
                for index_0, index_1, index_2 in part["geometry"]["triangles"]
            ]
        )
    if not triangles:
        raise RuntimeError("tessellated assembly produced no triangles")
    if len(triangles) > _MAX_TOTAL_TRIANGLES:
        raise RuntimeError(
            "tessellated assembly exceeds total triangle cap ({})".format(_MAX_TOTAL_TRIANGLES)
        )
    metadata = {
        "backend": BACKEND,
        "mesh_deflection_source_units": "mm",
        "object_count": len(parts),
        "part_count": len(parts),
        "triangle_count": len(triangles),
        "parts": [{"id": part["id"], "name": part["name"], "color": part["color"]} for part in parts],
        "invalid_object_count": 0,
        "invalid_objects": [],
    }
    geometry = {
        "type": "mesh",
        "vertices": vertices,
        "triangles": triangles,
        "units": "m",
        "transform": {
            "rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "translation": [0.0, 0.0, 0.0],
            "units": "m",
        },
    }
    return geometry, metadata


def _required_environment(name):
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError("missing required environment variable {}".format(name))
    return value.strip()


def _positive_float(name, default):
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise RuntimeError("{} must be a finite positive number".format(name))
    if not math.isfinite(value) or value <= 0.0:
        raise RuntimeError("{} must be a finite positive number".format(name))
    return value


def _version_text(App):
    try:
        value = App.Version()
    except Exception:
        return None
    if isinstance(value, (list, tuple)):
        return ".".join(str(item) for item in value[:3])
    return str(value) if value else None


def _occt_version():
    try:
        import OCC

        value = getattr(OCC, "VERSION", None)
        return str(value) if value else None
    except Exception:
        return None


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path, payload):
    """Write JSON atomically (temp file + rename), rejecting non-finite values."""
    temp_path = path + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
        os.replace(temp_path, path)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

def _export_glb(input_path, output_path, source_units, scale_to_m, glb_deflection_mm, App):
    """Read STEP through XCAF and export a serial, metre-scaled binary GLB."""
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    from OCC.Core.IFSelect import IFSelect_RetDone
    from OCC.Core.Message import Message_ProgressRange
    from OCC.Core.RWGltf import RWGltf_CafWriter, RWGltf_WriterTrsfFormat_TRS
    from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
    from OCC.Core.TColStd import TColStd_IndexedDataMapOfStringString
    from OCC.Core.TCollection import TCollection_AsciiString
    from OCC.Core.TDocStd import TDocStd_Document
    from OCC.Core.XCAFApp import XCAFApp_Application
    from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool

    app = XCAFApp_Application.GetApplication()
    document = TDocStd_Document("MDTV-CAF")
    app.NewDocument("MDTV-CAF", document)
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    reader.SetLayerMode(True)
    reader.SetPropsMode(True)
    status = reader.ReadFile(input_path)
    if status != IFSelect_RetDone:
        raise RuntimeError("STEPCAFControl_Reader.ReadFile failed with status {}".format(status))
    if not reader.Transfer(document):
        raise RuntimeError("STEPCAFControl_Reader.Transfer failed")
    root_shape = XCAFDoc_DocumentTool.ShapeTool(document.Main()).GetOneShape()
    if root_shape is None or root_shape.IsNull():
        raise RuntimeError("XCAF STEP transfer produced a null root shape")

    glb_deflection_source = glb_deflection_mm / (scale_to_m * 1000.0)
    BRepMesh_IncrementalMesh(root_shape, glb_deflection_source, False, 0.35, False)
    writer = RWGltf_CafWriter(output_path, True)
    converter = writer.ChangeCoordinateSystemConverter()
    converter.SetInputLengthUnit(scale_to_m)
    converter.SetOutputLengthUnit(1.0)
    writer.SetParallel(False)
    writer.SetMergeFaces(False)
    writer.SetTransformationFormat(RWGltf_WriterTrsfFormat_TRS)
    metadata = TColStd_IndexedDataMapOfStringString()
    metadata.Add(TCollection_AsciiString("Backend"), TCollection_AsciiString(BACKEND))
    metadata.Add(TCollection_AsciiString("SourceUnits"), TCollection_AsciiString(str(source_units)))
    metadata.Add(TCollection_AsciiString("OutputUnits"), TCollection_AsciiString("m"))
    metadata.Add(
        TCollection_AsciiString("MeshDeflectionMm"),
        TCollection_AsciiString("{:.12g}".format(glb_deflection_mm)),
    )
    metadata.Add(TCollection_AsciiString("Serial"), TCollection_AsciiString("true"))
    if not writer.Perform(document, metadata, Message_ProgressRange()):
        raise RuntimeError("RWGltf_CafWriter.Perform failed")
    if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
        raise RuntimeError("RWGltf_CafWriter produced no GLB output")
    return {
        "glb_deflection_mm": glb_deflection_mm,
        "glb_deflection_source_units": glb_deflection_source,
        "triangle_count": None,
    }


def _run():
    # These imports intentionally happen only inside the FreeCADCmd process.
    import FreeCAD as App

    input_path = _required_environment("MOUSE_SIM_STEP_INPUT")
    mesh_output = _required_environment("MOUSE_SIM_STEP_MESH_OUTPUT")
    glb_output = _required_environment("MOUSE_SIM_STEP_GLB_OUTPUT")
    parts_output = _required_environment("MOUSE_SIM_STEP_PARTS_OUTPUT")
    source_units = os.environ.get("MOUSE_SIM_STEP_SOURCE_UNITS", "mm").strip() or "mm"
    # The declared STEP unit drives the metre scale: FreeCAD/OCCT hold the
    # model in millimetres, so an inch/feet-declared STEP must be scaled by
    # the true inch/feet->m factor, not the mm factor (a 25.4x mass error).
    scale_to_m = _positive_float("MOUSE_SIM_STEP_SCALE", _SCALE_TO_M)
    mesh_deflection_mm = _positive_float("MOUSE_SIM_STEP_MESH_DEFLECTION_MM", 0.5)
    glb_deflection_mm = _positive_float("MOUSE_SIM_STEP_GLB_DEFLECTION_MM", 0.10)
    os.makedirs(os.path.dirname(mesh_output) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(glb_output) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(parts_output) or ".", exist_ok=True)

    glb_metadata = _export_glb(
        input_path,
        glb_output,
        source_units,
        scale_to_m,
        glb_deflection_mm,
        App,
    )
    # The GLB written by OCCT is the authoritative assembly interpretation
    # (located instances, names, colors); parts and the flattened analysis
    # mesh are derived from it so every consumer sees identical geometry.
    parts = _glb_parts(glb_output)
    geometry, mesh_metadata = _mesh_and_parts(parts)

    mesh_metadata["parts"] = [
        {
            "id": part["id"],
            "name": part["name"],
            "color": part["color"],
        }
        for part in parts
    ]

    mesh_metadata.update(
        {
            "source_units": source_units,
            "scale_to_m": scale_to_m,
            "freecad_version": _version_text(App),
            "occt_version": _occt_version(),
            "source_sha256": os.environ.get("MOUSE_SIM_STEP_SOURCE_SHA256") or None,
        }
    )
    mesh_payload = {"geometry": geometry, "metadata": mesh_metadata}
    _write_json(mesh_output, mesh_payload)
    _write_json(parts_output, {"parts": parts})

    glb_sha256 = _file_sha256(glb_output)
    manifest = {
        "schema_id": "mouse-sim.step-asset/1",
        "backend": BACKEND,
        "source_sha256": os.environ.get("MOUSE_SIM_STEP_SOURCE_SHA256") or None,
        "source_units": source_units,
        "scale_to_m": scale_to_m,
        "freecad_version": _version_text(App),
        "occt_version": mesh_metadata["occt_version"],
        "mesh": mesh_metadata,
        "parts": [
            {
                "id": part["id"],
                "name": part["name"],
                "color": part["color"],
                "vertex_count": len(part["geometry"]["vertices"]),
                "triangle_count": len(part["geometry"]["triangles"]),
            }
            for part in parts
        ],
        "glb": {
            "sha256": glb_sha256,
            "bytes": os.path.getsize(glb_output),
            "deflection_mm": glb_deflection_mm,
            "deflection_source_units": glb_metadata["glb_deflection_source_units"],
        },
        "diagnostics": mesh_metadata["invalid_objects"],
    }
    manifest_output = os.path.splitext(glb_output)[0] + ".manifest.json"
    _write_json(manifest_output, manifest)
    sys.stdout.write(
        "mouse_sim step worker: {objects} objects, {parts} parts, {triangles} triangles, "
        "{glb} glb bytes\n".format(
            objects=mesh_metadata["object_count"],
            parts=len(parts),
            triangles=len(geometry["triangles"]),
            glb=os.path.getsize(glb_output),
        )
    )
    sys.stdout.flush()


def main():
    try:
        _run()
    except Exception as exc:
        sys.stderr.write("mouse_sim FreeCAD STEP worker failed: {}\n".format(exc))
        sys.stderr.flush()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
