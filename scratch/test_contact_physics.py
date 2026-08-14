from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.pipeline import run_pipeline
from mouse_sim.drop_sim import _quaternion_rotate

register_existing_assets(default_asset_dir())
asset_id = "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404"
asset_objects = _load_registered_asset_objects(asset_id)

res = run_pipeline({
    "schema_id": "gms.project/1",
    "objects": asset_objects,
}, use_cache=False)

# Let's inspect the mesh and support vertices extracted for G3
support = res.get("geometry", {}).get("support_points")
print("Extracted support points in geometry:", support)

# Let's check the pipeline's support extraction in pipeline.py
from mouse_sim.pipeline import _extract_support_points
# Let's inspect how _extract_support_points works
