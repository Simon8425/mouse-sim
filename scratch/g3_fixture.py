import json
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.pipeline import run_pipeline

asset_dir = default_asset_dir()
register_existing_assets(asset_dir)

asset_id = "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404"
objects = _load_registered_asset_objects(asset_id)

request = {
    "schema_id": "gms.project/1",
    "objects": objects,
    "drop_simulation": {
        "test": "drop",
        "height_m": 0.75,
        "surface": "concrete",
        "drop_count": 1,
        "orientation": "flat"
    }
}
result = run_pipeline(request)
drop_sim = result["drop_simulation"]

# Also get full support points from pipeline
from mouse_sim import drop_sim as drop_module
# Let's save the constants
constants = {
    "mass_kg": drop_sim["model"]["mass_kg"],
    "inertia_kg_m2": drop_sim["model"]["inertia_kg_m2"],
    "com_offset_m": drop_sim["model"]["com_offset_m"],
    "support": result["_raw_support"] if "_raw_support" in result else None
}
print("Pipeline drop_sim model:", json.dumps(drop_sim["model"], indent=2))
with open("scratch/g3_constants.json", "w") as f:
    json.dump(drop_sim, f, indent=2)
print("Saved drop_sim to scratch/g3_constants.json")
