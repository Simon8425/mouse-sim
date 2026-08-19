"""FreeCADCmd worker for deterministic STL import and smooth GLB export.

This module is executed by FreeCADCmd, not imported by the normal server
interpreter.  FreeCAD's Mesh module reads STD ASCII and binary STL robustly
(nonconforming headers, trailing footers) and welds coincident points, so the
display asset matches what a FreeCAD user sees: a clean, smoothly-shaded
surface instead of a faceted triangle soup.

The worker emits the same output contract as ``freecad_step_worker.py`` so a
single adapter (step_kernel.tessellate_stl) and the existing asset-serving
pipeline can consume it:

- ``<id>.glb``            binary glTF with per-vertex smooth NORMALS
- ``<id>.mesh.json``      analysis mesh (metres, scene-frame flipped)
- ``<id>.manifest.json``  provenance metadata

All FreeCAD and OCCT imports happen only inside ``main`` so compiling the
repository with Python 3.9 remains dependency-free.
"""

import hashlib
import json
import math
import os
import struct
import sys


BACKEND = "freecad-occt"

# Hard caps on tessellation output; exceeding them fails the import cleanly
# instead of exhausting memory in the server process.
_MAX_TRIANGLES = 5_000_000
_MAX_VERTICES = 2_000_000

# Seam-stitching weld tolerance as a fraction of the mesh's own diagonal
# (mirrors mouse_sim/importers.py).  Merges numerically-duplicated seam
# vertices so per-vertex normals produce smooth (Gouraud) shading.
_WELD_TOLERANCE_FRACTION = 1e-5

_IDENTITY_TRANSFORM = {
    "rotation": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "translation": (0.0, 0.0, 0.0),
    "units": "m",
}


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


def _mesh_diagonal(vertices):
    if not vertices:
        return 0.0
    min_x = min(vertex[0] for vertex in vertices)
    max_x = max(vertex[0] for vertex in vertices)
    min_y = min(vertex[1] for vertex in vertices)
    max_y = max(vertex[1] for vertex in vertices)
    min_z = min(vertex[2] for vertex in vertices)
    max_z = max(vertex[2] for vertex in vertices)
    width = max_x - min_x
    height = max_y - min_y
    depth = max_z - min_z
    return math.sqrt(width * width + height * height + depth * depth)


def _weld_vertices(vertices, triangles, tolerance):
    """Merge vertices closer than ``tolerance`` and rebuild the triangles.

    Deterministic (hash grid + first-writer representative).  Returns
    ``(welded_vertices, welded_triangles)``; degenerate triangles are dropped.
    """
    if not vertices or tolerance <= 0.0:
        return list(vertices), list(triangles)
    origin = (
        min(vertex[0] for vertex in vertices),
        min(vertex[1] for vertex in vertices),
        min(vertex[2] for vertex in vertices),
    )

    def cell_key(vertex):
        return (
            int(math.floor((vertex[0] - origin[0]) / tolerance)),
            int(math.floor((vertex[1] - origin[1]) / tolerance)),
            int(math.floor((vertex[2] - origin[2]) / tolerance)),
        )

    grid = {}
    remap = [-1] * len(vertices)
    for index, vertex in enumerate(vertices):
        cx, cy, cz = cell_key(vertex)
        joined = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    representative = grid.get((cx + dx, cy + dy, cz + dz))
                    if representative is None:
                        continue
                    other = vertices[representative]
                    if all(abs(vertex[axis] - other[axis]) <= tolerance for axis in range(3)):
                        joined = representative
                        break
                if joined is not None:
                    break
            if joined is not None:
                break
        if joined is not None:
            remap[index] = joined
        else:
            remap[index] = index
            grid[(cx, cy, cz)] = index
    final_index_by_original = {}
    welded_vertices = []
    for index in range(len(vertices)):
        representative = remap[index]
        if representative not in final_index_by_original:
            final_index_by_original[representative] = len(welded_vertices)
            welded_vertices.append(vertices[index])
    rebuilt = []
    for triangle in triangles:
        mapped = tuple(final_index_by_original[remap[index]] for index in triangle)
        if len(set(mapped)) == 3:
            rebuilt.append(mapped)
    return welded_vertices, rebuilt


def _face_normal(first, second, third):
    ux, uy, uz = (second[0] - first[0], second[1] - first[1], second[2] - first[2])
    vx, vy, vz = (third[0] - first[0], third[1] - first[1], third[2] - first[2])
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    norm = math.sqrt(nx * nx + ny * ny + nz * nz)
    if norm <= 0.0:
        return None
    # Area-weighted (norm is twice the triangle area).
    return (nx / norm, ny / norm, nz / norm), norm


def _smooth_vertex_normals(vertices, triangles):
    """Area-weighted smooth (Gouraud) vertex normals over welded triangles."""
    normals = [(0.0, 0.0, 0.0)] * len(vertices)
    for triangle in triangles:
        first = vertices[triangle[0]]
        second = vertices[triangle[1]]
        third = vertices[triangle[2]]
        result = _face_normal(first, second, third)
        if result is None:
            continue
        normal, area = result
        for corner in triangle:
            x, y, z = normals[corner]
            normals[corner] = (
                x + normal[0] * area,
                y + normal[1] * area,
                z + normal[2] * area,
            )
    output = []
    for x, y, z in normals:
        norm = math.sqrt(x * x + y * y + z * z)
        if norm <= 0.0:
            output.append((0.0, 0.0, 1.0))
        else:
            output.append((x / norm, y / norm, z / norm))
    return output


def _crease_aware_normals(vertices, triangles, crease_angle_rad=math.radians(30)):
    """Crease-angle aware per-vertex normals, matching FreeCAD's SoShapeHints.

    For each position (welded vertex), incident faces are partitioned into
    smoothing groups where the dihedral angle between edge-adjacent faces is
    < creaseAngle. Faces within a group share an averaged normal; faces in
    different groups get distinct normals at the same position (sharp edge
    stays sharp). Returns (new_vertices, new_triangles, new_normals) where
    positions are duplicated at sharp features, like FreeCAD/Coin does.
    """
    if not vertices or not triangles:
        return list(vertices), list(triangles), _smooth_vertex_normals(vertices, triangles)
    # Face normals (unit) + area for weighting
    face_normals = []
    face_areas = []
    for tri in triangles:
        res = _face_normal(vertices[tri[0]], vertices[tri[1]], vertices[tri[2]])
        if res is None:
            face_normals.append((0.0, 0.0, 1.0))
            face_areas.append(0.0)
        else:
            n, a = res
            face_normals.append(n)
            face_areas.append(a)
    cos_threshold = math.cos(crease_angle_rad)

    # Vertex -> incident faces
    vertex_faces = [[] for _ in range(len(vertices))]
    for fi, tri in enumerate(triangles):
        for vi in tri:
            vertex_faces[vi].append(fi)

    # Edge -> faces (for adjacency)
    edge_faces = {}
    for fi, tri in enumerate(triangles):
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            key = (a, b) if a < b else (b, a)
            edge_faces.setdefault(key, []).append(fi)

    new_vertices = []
    new_normals = []
    # (orig_vertex, face) -> new vertex index
    corner_to_new = {}
    # For each original vertex, partition its incident faces into smoothing groups
    for vi, incident in enumerate(vertex_faces):
        if not incident:
            continue
        # Build DSU over incident faces indices (0..n-1)
        n = len(incident)
        parent = list(range(n))
        rank = [0] * n

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            if rank[ra] < rank[rb]:
                parent[ra] = rb
            elif rank[ra] > rank[rb]:
                parent[rb] = ra
            else:
                parent[rb] = ra
                rank[ra] += 1

        # Map face global index -> position in incident list
        pos_map = {fi: idx for idx, fi in enumerate(incident)}
        # For each pair of incident faces that share an edge containing vi,
        # union them if dihedral angle < threshold
        for fi in incident:
            tri = triangles[fi]
            # edges of fi that contain vi
            for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                if vi not in (a, b):
                    continue
                key = (a, b) if a < b else (b, a)
                for fj in edge_faces.get(key, ()):
                    if fj == fi or fj not in pos_map:
                        continue
                    # dihedral angle between fi and fj
                    ni = face_normals[fi]
                    nj = face_normals[fj]
                    dot = ni[0] * nj[0] + ni[1] * nj[1] + ni[2] * nj[2]
                    dot = max(-1.0, min(1.0, dot))
                    if dot >= cos_threshold:
                        union(pos_map[fi], pos_map[fj])

        # Group by root
        groups = {}
        for idx, fi in enumerate(incident):
            r = find(idx)
            groups.setdefault(r, []).append(fi)

        # For each group, compute averaged normal and create a duplicated vertex
        for group in groups.values():
            nx, ny, nz = 0.0, 0.0, 0.0
            for fi in group:
                n = face_normals[fi]
                a = face_areas[fi]
                nx += n[0] * a
                ny += n[1] * a
                nz += n[2] * a
            norm = math.sqrt(nx * nx + ny * ny + nz * nz)
            if norm > 1e-12:
                nx /= norm
                ny /= norm
                nz /= norm
            else:
                # fallback to first face's normal
                nx, ny, nz = face_normals[group[0]]
            new_idx = len(new_vertices)
            new_vertices.append(vertices[vi])
            new_normals.append((nx, ny, nz))
            for fi in group:
                corner_to_new[(vi, fi)] = new_idx

    # Rebuild triangles with new indices
    new_triangles = []
    for fi, tri in enumerate(triangles):
        try:
            a = corner_to_new[(tri[0], fi)]
            b = corner_to_new[(tri[1], fi)]
            c = corner_to_new[(tri[2], fi)]
        except KeyError:
            continue
        if len({a, b, c}) == 3:
            new_triangles.append((a, b, c))

    if not new_triangles:
        # Fallback: no crease handling produced valid mesh
        return list(vertices), list(triangles), _smooth_vertex_normals(vertices, triangles)

    return new_vertices, new_triangles, new_normals


def _align_four(byte_length):
    return (4 - (byte_length % 4)) % 4


def _write_glb(path, vertices, triangles, normals, color, roughness=0.35, metallic=0.05):
    """Serialize a minimal binary glTF 2.0 document (POSITION + NORMAL)."""
    position_bin = bytearray()
    for x, y, z in vertices:
        position_bin += struct.pack("<3f", x, y, z)
    normal_bin = bytearray()
    for nx, ny, nz in normals:
        normal_bin += struct.pack("<3f", nx, ny, nz)
    index_count = len(triangles) * 3
    use_short = len(vertices) <= 65535
    index_format = "<HHH" if use_short else "<III"
    index_component_type = 5123 if use_short else 5125
    index_bin = bytearray()
    for triangle in triangles:
        index_bin += struct.pack(
            index_format, int(triangle[0]), int(triangle[1]), int(triangle[2])
        )

    position_alignment = _align_four(len(position_bin))
    normal_offset = len(position_bin) + position_alignment
    normal_alignment = _align_four(len(normal_bin))
    index_offset = normal_offset + len(normal_bin) + normal_alignment
    bin_chunk = (
        bytes(position_bin)
        + b"\x00" * position_alignment
        + bytes(normal_bin)
        + b"\x00" * normal_alignment
        + bytes(index_bin)
    )
    if index_count == 0:
        raise RuntimeError("STL mesh produced no triangles")

    min_x = min(vertex[0] for vertex in vertices)
    min_y = min(vertex[1] for vertex in vertices)
    min_z = min(vertex[2] for vertex in vertices)
    max_x = max(vertex[0] for vertex in vertices)
    max_y = max(vertex[1] for vertex in vertices)
    max_z = max(vertex[2] for vertex in vertices)

    gltf = {
        "asset": {"version": "2.0", "generator": "mouse-sim freecad-stl"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "model", "mesh": 0}],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0, "NORMAL": 1},
                        "indices": 2,
                        "material": 0,
                        "mode": 4,
                    }
                ]
            }
        ],
        "materials": [
            {
                "name": "stl",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [float(color[0]), float(color[1]), float(color[2]), 1.0],
                    "metallicFactor": float(metallic),
                    "roughnessFactor": float(roughness),
                },
                "doubleSided": False,
            }
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(vertices),
                "type": "VEC3",
                "min": [min_x, min_y, min_z],
                "max": [max_x, max_y, max_z],
            },
            {"bufferView": 1, "componentType": 5126, "count": len(vertices), "type": "VEC3"},
            {"bufferView": 2, "componentType": index_component_type, "count": index_count, "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(position_bin), "target": 34962},
            {"buffer": 0, "byteOffset": normal_offset, "byteLength": len(normal_bin), "target": 34962},
            {"buffer": 0, "byteOffset": index_offset, "byteLength": len(index_bin), "target": 34963},
        ],
        "buffers": [{"byteLength": len(bin_chunk)}],
    }
    json_document = json.dumps(gltf, sort_keys=True, separators=(",", ":")).encode("utf-8")
    json_padding = _align_four(len(json_document))
    json_chunk = json_document + b" " * json_padding

    total_length = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    with open(path, "wb") as stream:
        stream.write(struct.pack("<4sII", b"glTF", 2, total_length))
        stream.write(struct.pack("<II", len(json_chunk), 0x4E4F534A))
        stream.write(json_chunk)
        stream.write(struct.pack("<II", len(bin_chunk), 0x004E4942))
        stream.write(bin_chunk)


def _mesh_from_freecad(App, input_path):
    """Parse an (ASCII or binary) STL with FreeCAD's Mesh module."""
    import Mesh

    mesh = Mesh.Mesh()
    mesh.read(input_path)
    for cleanup in ("removeDuplicatedPoints", "removeDuplicatedFacets"):
        try:
            getattr(mesh, cleanup)()
        except Exception:
            # Optional hardening; a mesh that cannot be cleaned is still usable.
            pass
    indexed = {}
    vertices = []
    for point in mesh.Points:
        key = (point.x, point.y, point.z)
        if key not in indexed:
            indexed[key] = len(vertices)
            vertices.append(key)
    triangles = []
    for facet in mesh.Facets:
        corners = []
        valid = True
        for point in facet.Points:
            key = (point[0], point[1], point[2])
            index = indexed.get(key)
            if index is None:
                valid = False
                break
            corners.append(index)
        if valid and len(corners) == 3:
            triangles.append(tuple(corners))
    return vertices, triangles


def _run():
    import FreeCAD as App

    input_path = _required_environment("MOUSE_SIM_STL_INPUT")
    mesh_output = _required_environment("MOUSE_SIM_STL_MESH_OUTPUT")
    glb_output = _required_environment("MOUSE_SIM_STL_GLB_OUTPUT")
    manifest_output = _required_environment("MOUSE_SIM_STL_MANIFEST_OUTPUT")
    source_units = os.environ.get("MOUSE_SIM_STL_SOURCE_UNITS", "mm").strip() or "mm"
    scale_to_m = _positive_float("MOUSE_SIM_STL_SCALE", 0.001)

    vertices, triangles = _mesh_from_freecad(App, input_path)
    if not triangles:
        raise RuntimeError("FreeCAD STL import produced no triangles")
    if len(triangles) > _MAX_TRIANGLES:
        raise RuntimeError("STL exceeds triangle cap ({})".format(_MAX_TRIANGLES))
    if len(vertices) > _MAX_VERTICES:
        raise RuntimeError("STL exceeds vertex cap ({})".format(_MAX_VERTICES))

    diagonal = _mesh_diagonal(vertices)
    if diagonal <= 0.0:
        raise RuntimeError("STL mesh has a degenerate bounding box")
    tolerance = max(2e-6, diagonal * _WELD_TOLERANCE_FRACTION)
    welded_vertices, welded_triangles = _weld_vertices(vertices, triangles, tolerance)
    if not welded_triangles:
        raise RuntimeError("STL welded mesh produced no triangles")

    # Display asset uses the original authoring frame at metre scale.
    # Use crease-angle aware normals like FreeCAD's SoShapeHints: sharp
    # edges (>30°) stay crisp, shallow curves are smoothed. This matches
    # FreeCAD's "sharp, pretty" look and Coin3D's default 0.5 rad.
    scaled_vertices = [(x * scale_to_m, y * scale_to_m, z * scale_to_m) for x, y, z in welded_vertices]
    scaled_triangles = welded_triangles
    crease_deg = float(os.environ.get("MOUSE_SIM_STL_CREASE_DEGREES", "30"))
    crease_rad = math.radians(max(0.0, min(180.0, crease_deg)))
    if crease_rad < 1e-6:
        # Flat shading: per-face normals via vertex duplication (each tri unique)
        flat_vertices = []
        flat_triangles = []
        flat_normals = []
        for tri in scaled_triangles:
            base = len(flat_vertices)
            face = [scaled_vertices[tri[0]], scaled_vertices[tri[1]], scaled_vertices[tri[2]]]
            res = _face_normal(face[0], face[1], face[2])
            n = res[0] if res else (0.0, 0.0, 1.0)
            for pt in face:
                flat_vertices.append(pt)
                flat_normals.append(n)
            flat_triangles.append((base, base + 1, base + 2))
        glb_vertices, glb_triangles, normals = flat_vertices, flat_triangles, flat_normals
    else:
        glb_vertices, glb_triangles, normals = _crease_aware_normals(
            scaled_vertices, scaled_triangles, crease_angle_rad=crease_rad
        )
    # FreeCAD-like light grey with sharper specular (lower roughness) for
    # a crisp, "pretty" highlight — matches Coin's headlight + shape hints.
    color = (0.82, 0.82, 0.85)
    # roughness tuned for sharp specular (FreeCAD's SoShapeHints + headlight
    # gives a tight highlight, not the previous matte 0.9)
    _write_glb(glb_output, glb_vertices, glb_triangles, normals, color, roughness=0.35, metallic=0.05)

    analysis_vertices = [
        (x * scale_to_m, -y * scale_to_m, -z * scale_to_m) for x, y, z in welded_vertices
    ]
    geometry = {
        "type": "mesh",
        "vertices": [list(point) for point in analysis_vertices],
        "triangles": [list(triangle) for triangle in welded_triangles],
        "units": "m",
        "transform": _IDENTITY_TRANSFORM,
    }
    metadata = {
        "backend": BACKEND,
        "source_units": source_units,
        "scale_to_m": scale_to_m,
        "object_count": 1,
        "part_count": 1,
        "triangle_count": len(welded_triangles),
        "vertex_count": len(welded_vertices),
        "freecad_version": _version_text(App),
        "occt_version": _occt_version(),
        "source_sha256": os.environ.get("MOUSE_SIM_STL_SOURCE_SHA256") or None,
    }
    _write_json(mesh_output, {"geometry": geometry, "metadata": metadata})

    glb_sha256 = _file_sha256(glb_output)
    manifest = {
        "schema_id": "mouse-sim.stl-asset/1",
        "backend": BACKEND,
        "source_sha256": os.environ.get("MOUSE_SIM_STL_SOURCE_SHA256") or None,
        "source_units": source_units,
        "scale_to_m": scale_to_m,
        "freecad_version": _version_text(App),
        "occt_version": metadata["occt_version"],
        "mesh": metadata,
        "glb": {"sha256": glb_sha256, "bytes": os.path.getsize(glb_output)},
    }
    _write_json(manifest_output, manifest)
    sys.stdout.write(
        "mouse_sim stl worker: {vertices} vertices, {triangles} triangles, {glb} glb bytes\n".format(
            vertices=len(welded_vertices),
            triangles=len(welded_triangles),
            glb=os.path.getsize(glb_output),
        )
    )
    sys.stdout.flush()


def main():
    try:
        _run()
    except Exception as exc:
        sys.stderr.write("mouse_sim FreeCAD STL worker failed: {}\n".format(exc))
        sys.stderr.flush()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())