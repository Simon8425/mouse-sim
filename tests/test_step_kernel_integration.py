import json
import os
import tempfile
import unittest
from pathlib import Path

from mouse_sim.geometry import TriangleMesh
from mouse_sim.importers import load_geometry
from mouse_sim.step_kernel import freecadcmd_path


REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_STEP = REPO_ROOT / "G3-20260320.stp"
RUN_INTEGRATION = os.environ.get("MOUSE_SIM_RUN_SLOW_STEP_INTEGRATION") == "1"


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
