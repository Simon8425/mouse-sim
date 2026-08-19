import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mouse_sim.geometry import TriangleMesh
from mouse_sim.importers import load_geometry
from mouse_sim.step_kernel import freecadcmd_path


REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_STEP = REPO_ROOT / "G3-20260320.stp"
RUN_INTEGRATION = os.environ.get("MOUSE_SIM_RUN_SLOW_STEP_INTEGRATION") == "1"


def _tiny_binary_stl():
    """One open cube-style triangle pair, sized in millimetres."""
    count = 2
    binary = b" " * 80 + struct.pack("<I", count)
    for base in (0, 3):
        binary += struct.pack(
            "<12f",
            0.0, 0.0, 1.0,
            float(base), 0.0, 0.0,
            float(base + 1), 0.0, 0.0,
            float(base + 1), float(base + 2), 0.0,
        ) + struct.pack("<H", 0)
    return binary


class StdlibFacetedStepImportTests(unittest.TestCase):
    def test_faceted_step_imports_via_stdlib(self):
        """A faceted STEP file must import through the standard-library parser
        on every platform, never requiring the FreeCAD kernel."""
        fixture = Path(__file__).parent / "fixtures" / "faceted_cube.step"
        with mock.patch(
            "mouse_sim.step_kernel.tessellate_step",
            side_effect=AssertionError("kernel must not run"),
        ):
            result = load_geometry(fixture, fmt="step")
        self.assertTrue(result.is_supported)
        self.assertIsInstance(result.geometry, TriangleMesh)
        self.assertEqual(result.source_units, "mm")
        self.assertEqual(len(result.geometry.triangles), 12)
        self.assertIsNone(result.display_asset)


class StdlibStlImportTests(unittest.TestCase):
    def test_stl_stdlib_backend_never_runs_kernel(self):
        """The stdlib STL parser must stay available and produce NO display
        asset; the FreeCAD kernel is an opt-in upgrade, never a hard
        dependency for reading an STL."""
        with mock.patch(
            "mouse_sim.step_kernel.tessellate_stl",
            side_effect=AssertionError("kernel must not run"),
        ):
            result = load_geometry(
                _tiny_binary_stl(),
                fmt="stl",
                units="mm",
                stl_backend="stdlib",
            )
        self.assertTrue(result.is_supported)
        self.assertEqual(result.source_units, "mm")
        self.assertIsInstance(result.geometry, TriangleMesh)
        self.assertIsNone(result.display_asset)

    def test_stl_default_backend_is_stdlib(self):
        """Direct/CLI STL loads keep using the stdlib parser by default so
        existing behavior (and the 926-test suite) is unchanged."""
        with mock.patch(
            "mouse_sim.step_kernel.tessellate_stl",
            side_effect=AssertionError("kernel must not run"),
        ), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MOUSE_SIM_STL_BACKEND", None)
            result = load_geometry(_tiny_binary_stl(), fmt="stl", units="mm")
        self.assertTrue(result.is_supported)
        self.assertIsNone(result.display_asset)


@unittest.skipUnless(
    RUN_INTEGRATION and freecadcmd_path() is not None,
    "set MOUSE_SIM_RUN_SLOW_STEP_INTEGRATION=1 with FreeCADCmd to run kernel tests",
)
class RealStlKernelTests(unittest.TestCase):
    def test_stl_kernel_produces_smooth_glb_and_analysis_mesh(self):
        with tempfile.TemporaryDirectory(prefix="mouse-sim-stl-integration-") as directory:
            result = load_geometry(
                _tiny_binary_stl(),
                fmt="stl",
                units="mm",
                stl_backend="kernel",
                step_asset_dir=directory,
                step_timeout=300,
            )
            self.assertTrue(result.is_supported)
            self.assertEqual(result.source_units, "mm")
            self.assertIsInstance(result.geometry, TriangleMesh)
            self.assertGreater(len(result.geometry.triangles), 0)
            asset = result.display_asset
            self.assertIsNotNone(asset)
            self.assertEqual(asset["format"], "glb")
            self.assertEqual(asset["backend"], "freecad-occt")
            glb_path = Path(asset["path"])
            self.assertTrue(glb_path.is_file())
            # The GLB must carry per-vertex smooth normals and match the
            # analysis mesh's bounds so the displayed asset lines up with
            # physics/bounds.
            from mouse_sim.freecad_step_worker import _glb_json_and_bin

            gltf, _ = _glb_json_and_bin(glb_path.read_bytes())
            primitive = gltf["meshes"][0]["primitives"][0]
            self.assertIn("POSITION", primitive["attributes"])
            self.assertIn("NORMAL", primitive["attributes"])

            analysis = result.geometry
            source = Path(directory) / (asset["asset_id"] + ".mesh.json")
            payload = json.loads(source.read_text(encoding="utf-8"))
            self.assertEqual(payload["geometry"]["type"], "mesh")
            self.assertEqual(payload["geometry"]["units"], "m")
            self.assertEqual(len(payload["geometry"]["vertices"]), len(analysis.vertices))
            self.assertEqual(len(payload["geometry"]["triangles"]), len(analysis.triangles))


@unittest.skipUnless(
    RUN_INTEGRATION and freecadcmd_path() is not None and REAL_STEP.is_file(),
    "set MOUSE_SIM_RUN_SLOW_STEP_INTEGRATION=1 with FreeCADCmd and G3-20260320.stp",
)
class RealAssemblyStepKernelTests(unittest.TestCase):
    def test_real_assembly_uses_native_kernel_mesh_and_glb(self):
        with tempfile.TemporaryDirectory(prefix="mouse-sim-step-integration-") as directory:
            result = load_geometry(
                REAL_STEP,
                fmt="step",
                step_backend="kernel",
                step_asset_dir=directory,
                step_timeout=300,
            )
            self.assertTrue(result.is_supported)
            self.assertEqual(result.source_units, "mm")
            self.assertIsInstance(result.geometry, TriangleMesh)
            self.assertGreater(len(result.geometry.triangles), 1000)
            self.assertLess(len(result.geometry.triangles), 5000000)
            self.assertGreater(len({triangle[0] for triangle in result.geometry.triangles}), 100)
            self.assertIsNotNone(result.display_asset)
            self.assertEqual(result.display_asset["format"], "glb")
            self.assertEqual(result.display_asset["backend"], "freecad-occt")
            self.assertTrue(Path(result.display_asset["path"]).is_file())

    def test_real_assembly_parts_export_matches_flattened_mesh(self):
        # Requires FreeCADCmd and the real G3-20260320.stp assembly; skip
        # conditions are identical to the class-level marker above.
        with tempfile.TemporaryDirectory(prefix="mouse-sim-step-integration-") as directory:
            result = load_geometry(
                REAL_STEP,
                fmt="step",
                step_backend="kernel",
                step_asset_dir=directory,
                step_timeout=300,
            )
            self.assertTrue(result.is_supported)
            asset = result.display_asset
            self.assertIsNotNone(asset)
            parts = asset.get("parts")
            self.assertIsNotNone(parts)
            self.assertGreaterEqual(len(parts), 2)
            for index, part in enumerate(parts):
                self.assertEqual(part["id"], "part-{}".format(index))
                self.assertIsInstance(part["name"], str)
                self.assertTrue(part["name"])
            parts_path = Path(asset["parts_path"])
            self.assertTrue(parts_path.is_file())
            payload = json.loads(parts_path.read_text(encoding="utf-8"))
            entries = payload["parts"]
            self.assertEqual(len(entries), len(parts))
            self.assertEqual(
                [entry["id"] for entry in entries],
                ["part-{}".format(index) for index in range(len(entries))],
            )
            total_triangles = sum(len(entry["geometry"]["triangles"]) for entry in entries)
            total_vertices = sum(len(entry["geometry"]["vertices"]) for entry in entries)
            self.assertEqual(total_triangles, len(result.geometry.triangles))
            # The assembly must be a coherent mouse shell (~63 x 120 x 38 mm):
            # before instance placements were applied, interior parts floated
            # outside a 66.7 mm-tall union.  This guards placement regressions.
            shell_extent_limits = (0.07, 0.13, 0.042)
            bounds = [[None, None], [None, None], [None, None]]
            for entry in entries:
                vertices = entry["geometry"]["vertices"]
                for axis in range(3):
                    low = min(vertex[axis] for vertex in vertices)
                    high = max(vertex[axis] for vertex in vertices)
                    bounds[axis][0] = low if bounds[axis][0] is None else min(bounds[axis][0], low)
                    bounds[axis][1] = high if bounds[axis][1] is None else max(bounds[axis][1], high)
            for axis in range(3):
                self.assertLess(
                    bounds[axis][1] - bounds[axis][0],
                    shell_extent_limits[axis],
                )
            for entry in entries:
                self.assertEqual(entry["geometry"]["type"], "mesh")
                self.assertEqual(entry["geometry"]["units"], "m")
                self.assertEqual(
                    entry["geometry"]["transform"],
                    {
                        "rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                        "translation": [0.0, 0.0, 0.0],
                        "units": "m",
                    },
                )
            mesh_files = list(Path(directory).glob("*.mesh.json"))
            self.assertEqual(len(mesh_files), 1)
            mesh_payload = json.loads(mesh_files[0].read_text(encoding="utf-8"))
            self.assertEqual(mesh_payload["metadata"]["triangle_count"], total_triangles)
            self.assertEqual(len(mesh_payload["geometry"]["triangles"]), total_triangles)
            self.assertEqual(len(mesh_payload["geometry"]["vertices"]), total_vertices)
            self.assertEqual(mesh_payload["metadata"]["parts"], parts)


if __name__ == "__main__":
    unittest.main()
