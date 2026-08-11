import hashlib
import json
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mouse_sim.geometry import TriangleMesh
from mouse_sim.importers import load_geometry
from mouse_sim.step_kernel import (
    StepKernelFailure,
    StepKernelUnavailable,
    requires_kernel,
    tessellate_step,
)
from mouse_sim.freecad_step_worker import _glb_parts, _mesh_and_parts


def _synthetic_glb(vertices, indices, translation=(0.0, 0.0, 0.0), color=None, mode=4):
    """Build a minimal binary glTF with one node/mesh/primitive."""
    bin_chunk = struct.pack("<" + "f" * (len(vertices) * 3), *[c for v in vertices for c in v])
    bin_chunk += struct.pack("<" + "H" * len(indices), *indices)
    position_view = {"buffer": 0, "byteOffset": 0, "byteLength": len(vertices) * 3 * 4, "byteStride": 12}
    index_view = {
        "buffer": 0,
        "byteOffset": len(vertices) * 3 * 4,
        "byteLength": len(indices) * 2,
    }
    primitive = {
        "attributes": {"POSITION": 0},
        "indices": 1,
        "material": 0 if color else None,
    }
    if mode != 4:
        primitive["mode"] = mode
    gltf = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "TestPart", "mesh": 0, "translation": list(translation)}],
        "meshes": [{"primitives": [primitive]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(vertices), "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": len(indices), "type": "SCALAR"},
        ],
        "bufferViews": [position_view, index_view],
        "buffers": [{"byteLength": len(bin_chunk)}],
        "materials": (
            [{"pbrMetallicRoughness": {"baseColorFactor": list(color) + [1.0]}}] if color else []
        ),
    }
    json_chunk = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    header = struct.pack("<III", 0x46546C67, 2, total)
    return (
        header
        + struct.pack("<II", len(json_chunk), 0x4E4F534A)
        + json_chunk
        + struct.pack("<II", len(bin_chunk), 0x004E4942)
        + bin_chunk
    )


class GlbPartsParserTests(unittest.TestCase):
    def test_parses_single_mesh_with_transform_color_and_flip(self):
        with tempfile.TemporaryDirectory() as directory:
            glb_path = Path(directory) / "test.glb"
            glb_path.write_bytes(
                _synthetic_glb(
                    [(0.0, 0.0, 0.0), (1000.0, 0.0, 0.0), (0.0, 1000.0, 0.0)],
                    [0, 1, 2],
                    translation=(10.0, 20.0, 30.0),
                    color=[0.25, 0.5, 0.75],
                )
            )
            parts = _glb_parts(str(glb_path))
        self.assertEqual(len(parts), 1)
        part = parts[0]
        self.assertEqual(part["id"], "part-0")
        self.assertEqual(part["name"], "TestPart")
        self.assertEqual(part["color"], [0.25, 0.5, 0.75])
        # GLB vertices are already metres; translation applied and the
        # authoring frame flipped (x, -y, -z).
        vertices = part["geometry"]["vertices"]
        self.assertAlmostEqual(vertices[0][0], 10.0, places=6)
        self.assertAlmostEqual(vertices[0][1], -20.0, places=6)
        self.assertAlmostEqual(vertices[0][2], -30.0, places=6)
        self.assertEqual(part["geometry"]["triangles"], [[0, 1, 2]])
        geometry, metadata = _mesh_and_parts(parts)
        self.assertEqual(len(geometry["triangles"]), 1)
        self.assertEqual(metadata["triangle_count"], 1)
        self.assertEqual(metadata["object_count"], 1)

    def test_rejects_malformed_glb(self):
        with tempfile.TemporaryDirectory() as directory:
            glb_path = Path(directory) / "bad.glb"
            glb_path.write_bytes(b"not a glb")
            with self.assertRaises(ValueError):
                _glb_parts(str(glb_path))


class StepBackendSelectionTests(unittest.TestCase):
    def test_requires_kernel_marker_matrix(self):
        self.assertTrue(requires_kernel(b"CONTEXT_DEPENDENT_SHAPE_REPRESENTATION"))
        self.assertTrue(requires_kernel(b"SHAPE_REPRESENTATION_RELATIONSHIP"))
        self.assertTrue(requires_kernel(b"BREP_WITH_VOIDS"))
        self.assertTrue(
            requires_kernel(b"ADVANCED_BREP_SHAPE_REPRESENTATION MANIFOLD_SOLID_BREP")
        )
        self.assertFalse(requires_kernel(b"ADVANCED_BREP_SHAPE_REPRESENTATION"))
        self.assertFalse(requires_kernel(b"BREP_WITH_VOIDS", backend="stdlib"))
        self.assertTrue(requires_kernel(b"faceted cube", backend="kernel"))

    def test_invalid_backend_is_rejected(self):
        with self.assertRaises(ValueError):
            requires_kernel(b"STEP", backend="not-a-backend")


class StepKernelImportTests(unittest.TestCase):
    ASSEMBLY_MARKERS = (
        b"ISO-10303-21;\n"
        b"DATA;\n"
        b"#1=CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#2,#3);\n"
        b"#2=SHAPE_REPRESENTATION_RELATIONSHIP();\n"
        b"#3=MANIFOLD_SOLID_BREP('',#4);\n"
        b"ENDSEC;\nEND-ISO-10303-21;"
    )

    def test_unavailable_kernel_is_blocking_and_never_falls_back(self):
        with mock.patch("mouse_sim.step_kernel.freecadcmd_path", return_value=None):
            with mock.patch("mouse_sim.importers._parse_step", side_effect=AssertionError("fallback")):
                result = load_geometry(self.ASSEMBLY_MARKERS, fmt="step")
        self.assertTrue(result.unsupported)
        self.assertIsNone(result.geometry)
        self.assertEqual(result.diagnostic.code, "step_kernel_unavailable")
        self.assertEqual(result.diagnostic.severity, "blocker")

    def test_kernel_failure_is_blocking_and_never_falls_back(self):
        with mock.patch(
            "mouse_sim.step_kernel.tessellate_step",
            side_effect=StepKernelFailure("worker failed"),
        ):
            with mock.patch("mouse_sim.importers._parse_step", side_effect=AssertionError("fallback")):
                result = load_geometry(self.ASSEMBLY_MARKERS, fmt="step")
        self.assertTrue(result.unsupported)
        self.assertIsNone(result.geometry)
        self.assertEqual(result.diagnostic.code, "step_kernel_failed")
        self.assertEqual(result.diagnostic.severity, "blocker")

    def test_simple_fixture_stays_on_stdlib_path(self):
        fixture = Path(__file__).parent / "fixtures" / "faceted_cube.step"
        with mock.patch(
            "mouse_sim.step_kernel.tessellate_step",
            side_effect=AssertionError("kernel should not run"),
        ):
            result = load_geometry(fixture)
        self.assertTrue(result.is_supported)
        self.assertIsInstance(result.geometry, TriangleMesh)
        self.assertEqual(len(result.geometry.triangles), 12)
        self.assertIsNone(result.display_asset)


class StepKernelProcessTests(unittest.TestCase):
    def test_worker_command_is_shell_free_and_cached(self):
        source = b"CONTEXT_DEPENDENT_SHAPE_REPRESENTATION LENGTH_UNIT SI_UNIT(.MILLI.,.METRE.)"
        geometry = {
            "type": "mesh",
            "vertices": [[0.0, 0.0, 0.0], [0.001, 0.0, 0.0], [0.0, 0.001, 0.0]],
            "triangles": [[0, 1, 2]],
            "units": "m",
            "transform": {
                "rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                "translation": [0.0, 0.0, 0.0],
                "units": "m",
            },
        }

        def fake_run(command, **kwargs):
            self.assertEqual(command[1], "-c")
            self.assertNotIn("shell", kwargs)
            if os.name == "nt":
                # preexec_fn is POSIX-only; Windows uses the default process
                # group (start_new_session=False) instead.
                self.assertNotIn("preexec_fn", kwargs)
                self.assertIs(kwargs.get("start_new_session"), False)
            else:
                self.assertIn("preexec_fn", kwargs)
            self.assertIn("MOUSE_SIM_STEP_PARTS_OUTPUT", kwargs["env"])
            mesh_path = Path(kwargs["env"]["MOUSE_SIM_STEP_MESH_OUTPUT"])
            glb_path = Path(kwargs["env"]["MOUSE_SIM_STEP_GLB_OUTPUT"])
            parts_path = Path(kwargs["env"]["MOUSE_SIM_STEP_PARTS_OUTPUT"])
            mesh_path.write_text(
                json.dumps(
                    {
                        "geometry": geometry,
                        "metadata": {
                            "backend": "freecad-occt",
                            "mesh_deflection_mm": 0.5,
                            "glb_deflection_mm": 0.1,
                            "object_count": 1,
                            "triangle_count": 1,
                            "parts": [{"id": "part-0", "name": "AssemblyPart"}],
                        },
                    }
                ),
                encoding="utf-8",
            )
            parts_path.write_text(
                json.dumps({"parts": [dict(geometry, id="part-0", name="AssemblyPart")]}),
                encoding="utf-8",
            )
            glb_path.write_bytes(b"glTF\x02\x00\x00\x00")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch(
                "mouse_sim.step_kernel.freecadcmd_path",
                side_effect=[Path("/fake/freecadcmd"), None],
            ):
                with mock.patch("mouse_sim.step_kernel.subprocess.run", side_effect=fake_run) as run:
                    first = tessellate_step(source, "assembly.step", "mm", directory, timeout=7)
                    second = tessellate_step(source, "assembly.step", "mm", directory, timeout=7)
            self.assertTrue(Path(first[2]["parts_path"]).is_file())
        self.assertEqual(run.call_count, 1)
        self.assertIsInstance(first[0], TriangleMesh)
        self.assertEqual(first[0].vertices[1], (0.001, 0.0, 0.0))
        self.assertEqual(first[2]["format"], "glb")
        self.assertEqual(first[2]["triangle_count"], 1)
        self.assertEqual(first[2]["asset_id"], second[2]["asset_id"])
        self.assertEqual(first[1][-1].code, "step_kernel_tessellated")
        self.assertEqual(first[2]["parts"], [{"id": "part-0", "name": "AssemblyPart"}])
        self.assertEqual(second[2]["parts"], [{"id": "part-0", "name": "AssemblyPart"}])

    def test_timeout_is_a_kernel_failure(self):
        source = b"CONTEXT_DEPENDENT_SHAPE_REPRESENTATION"
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch("mouse_sim.step_kernel.freecadcmd_path", return_value=Path("/fake/freecadcmd")):
                with mock.patch(
                    "mouse_sim.step_kernel.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(["freecadcmd"], 1),
                ):
                    with self.assertRaises(StepKernelFailure):
                        tessellate_step(source, "assembly.step", "mm", directory, timeout=1)

    def test_unavailable_exception_type_is_public(self):
        self.assertTrue(issubclass(StepKernelUnavailable, RuntimeError))


class StepKernelPartsTests(unittest.TestCase):
    SOURCE = b"CONTEXT_DEPENDENT_SHAPE_REPRESENTATION LENGTH_UNIT SI_UNIT(.MILLI.,.METRE.)"
    GEOMETRY = {
        "type": "mesh",
        "vertices": [[0.0, 0.0, 0.0], [0.001, 0.0, 0.0], [0.0, 0.001, 0.0]],
        "triangles": [[0, 1, 2]],
        "units": "m",
        "transform": {
            "rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "translation": [0.0, 0.0, 0.0],
            "units": "m",
        },
    }

    def test_asset_id_marks_parts_format_version(self):
        from mouse_sim.step_kernel import ASSET_FORMAT_VERSION, _asset_id, _settings

        self.assertEqual(ASSET_FORMAT_VERSION, "parts-v7")
        settings = _settings("mm")
        legacy = hashlib.sha256(
            json.dumps(
                {"source_sha256": "feedface", "settings": settings},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        current = _asset_id("feedface", settings)
        self.assertNotEqual(legacy, current)
        self.assertEqual(len(current), 64)
        self.assertTrue(set(current) <= set("0123456789abcdef"))

    def test_cached_asset_without_parts_file_degrades_gracefully(self):
        def fake_run(command, **kwargs):
            mesh_path = Path(kwargs["env"]["MOUSE_SIM_STEP_MESH_OUTPUT"])
            glb_path = Path(kwargs["env"]["MOUSE_SIM_STEP_GLB_OUTPUT"])
            parts_path = Path(kwargs["env"]["MOUSE_SIM_STEP_PARTS_OUTPUT"])
            mesh_path.write_text(
                json.dumps(
                    {
                        "geometry": self.GEOMETRY,
                        "metadata": {
                            "backend": "freecad-occt",
                            "mesh_deflection_mm": 0.5,
                            "glb_deflection_mm": 0.1,
                            "object_count": 1,
                            "triangle_count": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            parts_path.write_text(
                json.dumps({"parts": [{"id": "part-0", "name": "LegacyPart"}]}),
                encoding="utf-8",
            )
            glb_path.write_bytes(b"glTF\x02\x00\x00\x00")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch(
                "mouse_sim.step_kernel.freecadcmd_path", return_value=Path("/fake/freecadcmd")
            ):
                with mock.patch("mouse_sim.step_kernel.subprocess.run", side_effect=fake_run):
                    tessellate_step(self.SOURCE, "assembly.step", "mm", directory, timeout=7)
            parts_files = list(Path(directory).glob("*.parts.json"))
            self.assertEqual(len(parts_files), 1)
            parts_files[0].unlink()
            with mock.patch(
                "mouse_sim.step_kernel.freecadcmd_path", return_value=Path("/fake/freecadcmd")
            ):
                with mock.patch(
                    "mouse_sim.step_kernel.subprocess.run", side_effect=fake_run
                ) as run:
                    mesh, diagnostics, asset = tessellate_step(
                        self.SOURCE, "assembly.step", "mm", directory, timeout=7
                    )
        # A cached asset missing parts.json is incomplete; the kernel rebuilds
        # it instead of serving a parts-less asset forever.
        self.assertEqual(run.call_count, 1)
        self.assertIsInstance(mesh, TriangleMesh)
        self.assertIsNotNone(asset["parts_path"])
        # The rebuilt asset's part summaries come from parts.json (canonical).
        self.assertEqual(asset["parts"], [{"id": "part-0", "name": "LegacyPart"}])

    def test_corrupt_parts_json_is_a_cache_miss(self):
        def fake_run(command, **kwargs):
            mesh_path = Path(kwargs["env"]["MOUSE_SIM_STEP_MESH_OUTPUT"])
            glb_path = Path(kwargs["env"]["MOUSE_SIM_STEP_GLB_OUTPUT"])
            parts_path = Path(kwargs["env"]["MOUSE_SIM_STEP_PARTS_OUTPUT"])
            mesh_path.write_text(
                json.dumps(
                    {
                        "geometry": self.GEOMETRY,
                        "metadata": {
                            "backend": "freecad-occt",
                            "object_count": 1,
                            "triangle_count": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            parts_path.write_text(json.dumps({"parts": [{"id": "part-0", "name": "A"}]}), encoding="utf-8")
            glb_path.write_bytes(b"glTF\x02\x00\x00\x00")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch(
                "mouse_sim.step_kernel.freecadcmd_path", return_value=Path("/fake/freecadcmd")
            ):
                with mock.patch("mouse_sim.step_kernel.subprocess.run", side_effect=fake_run):
                    tessellate_step(self.SOURCE, "assembly.step", "mm", directory, timeout=7)
            parts_path = next(Path(directory).glob("*.parts.json"))
            parts_path.write_text("{not json", encoding="utf-8")
            with mock.patch(
                "mouse_sim.step_kernel.freecadcmd_path", return_value=Path("/fake/freecadcmd")
            ):
                with mock.patch(
                    "mouse_sim.step_kernel.subprocess.run", side_effect=fake_run
                ) as run:
                    mesh, diagnostics, asset = tessellate_step(
                        self.SOURCE, "assembly.step", "mm", directory, timeout=7
                    )
        self.assertEqual(run.call_count, 1)
        self.assertEqual(asset["parts"], [{"id": "part-0", "name": "A"}])

    def test_read_json_size_cap_and_missing_file(self):
        from mouse_sim.step_kernel import StepKernelFailure, _read_json

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            with self.assertRaises(StepKernelFailure):
                _read_json(path)
            path.write_text(json.dumps({"x": list(range(100))}), encoding="utf-8")
            with self.assertRaises(StepKernelFailure):
                _read_json(path, max_bytes=16)
            self.assertEqual(_read_json(path), {"x": list(range(100))})

    def test_asset_dir_ownership_and_mode(self):
        from mouse_sim.step_kernel import StepKernelFailure, _asset_dir

        with tempfile.TemporaryDirectory() as directory:
            root = _asset_dir(directory)
            self.assertTrue(root.is_dir())
            # chmod 0o700 is a real mode change on POSIX; Windows only
            # honors the read-only attribute, so the mode bits are not
            # asserted there.
            if os.name == "posix":
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            # Ownership is verified against the effective uid only on POSIX;
            # Windows has no geteuid and relies on the per-user temp dir.
            if hasattr(os, "geteuid"):
                with mock.patch(
                    "mouse_sim.step_kernel.os.geteuid", return_value=os.geteuid() + 1
                ):
                    with self.assertRaises(StepKernelFailure):
                        _asset_dir(directory)

    def test_asset_dir_uses_platform_neutral_user_tag(self):
        from mouse_sim.step_kernel import _PROCESS_ASSET_DIR, _user_tag

        tag = _user_tag()
        self.assertIsInstance(tag, str)
        self.assertTrue(tag)
        self.assertEqual(
            _PROCESS_ASSET_DIR,
            Path(tempfile.gettempdir()) / ("mouse-sim-step-assets-" + tag),
        )

    def test_kernel_probe_never_raises(self):
        from mouse_sim.step_kernel import FREECADCMD_ENV, freecadcmd_path, kernel_available

        # A configured path that cannot even be resolved (unknown user home,
        # broken mount) must be reported as absent, never raised.  The rest of
        # the detection chain is mocked so the outcome does not depend on
        # whether FreeCAD happens to be installed on the test machine.
        with mock.patch.dict(os.environ, {FREECADCMD_ENV: "~no_such_user/freecadcmd"}):
            with mock.patch(
                "mouse_sim.step_kernel._windows_freecadcmd_candidates", return_value=[]
            ):
                with mock.patch("mouse_sim.step_kernel.shutil.which", return_value=None):
                    with mock.patch("mouse_sim.step_kernel.sys.platform", "win32"):
                        self.assertIsNone(freecadcmd_path())
                        self.assertFalse(kernel_available())


class GlbPartsEdgeTests(unittest.TestCase):
    def test_out_of_range_indices_rejected_for_small_meshes(self):
        from mouse_sim.freecad_step_worker import _glb_parts

        with tempfile.TemporaryDirectory() as directory:
            glb_path = Path(directory) / "bad.glb"
            glb_path.write_bytes(
                _synthetic_glb([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 5])
            )
            with self.assertRaises(ValueError):
                _glb_parts(str(glb_path))

    def test_non_triangle_mode_rejected(self):
        from mouse_sim.freecad_step_worker import _glb_parts

        with tempfile.TemporaryDirectory() as directory:
            glb_path = Path(directory) / "lines.glb"
            glb_path.write_bytes(
                _synthetic_glb([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2], mode=1)
            )
            with self.assertRaises(ValueError):
                _glb_parts(str(glb_path))

    def test_hierarchy_composition(self):
        import struct

        from mouse_sim.freecad_step_worker import _glb_parts

        with tempfile.TemporaryDirectory() as directory:
            # Build a two-level hierarchy: root translates (10,0,0); child
            # (holding the mesh) translates (2,0,0).
            vertices = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
            bin_chunk = struct.pack("<" + "f" * 9, *[c for v in vertices for c in v])
            bin_chunk += struct.pack("<HHH", 0, 1, 2)
            gltf = {
                "asset": {"version": "2.0"},
                "scene": 0,
                "scenes": [{"nodes": [0]}],
                "nodes": [
                    {"name": "Root", "translation": [10.0, 0.0, 0.0], "children": [1]},
                    {"name": "Child", "mesh": 0, "translation": [2.0, 0.0, 0.0]},
                ],
                "meshes": [
                    {
                        "primitives": [
                            {
                                "attributes": {"POSITION": 0},
                                "indices": 1,
                            }
                        ]
                    }
                ],
                "accessors": [
                    {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
                    {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
                ],
                "bufferViews": [
                    {"buffer": 0, "byteOffset": 0, "byteLength": 36, "byteStride": 12},
                    {"buffer": 0, "byteOffset": 36, "byteLength": 6},
                ],
                "buffers": [{"byteLength": len(bin_chunk)}],
            }
            json_chunk = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
            json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
            total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
            glb = (
                struct.pack("<III", 0x46546C67, 2, total)
                + struct.pack("<II", len(json_chunk), 0x4E4F534A)
                + json_chunk
                + struct.pack("<II", len(bin_chunk), 0x004E4942)
                + bin_chunk
            )
            glb_path = Path(directory) / "hierarchy.glb"
            glb_path.write_bytes(glb)
            parts = _glb_parts(str(glb_path))
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["name"], "Child")
        first = parts[0]["geometry"]["vertices"][0]
        self.assertAlmostEqual(first[0], 12.0, places=6)
        self.assertAlmostEqual(first[1], 0.0, places=6)
        self.assertAlmostEqual(first[2], 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
