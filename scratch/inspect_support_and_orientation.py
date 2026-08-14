import math
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.pipeline import run_pipeline
from mouse_sim.drop_sim import _quaternion_rotate, _orientation_quaternion

register_existing_assets(default_asset_dir())
asset_id = "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404"
asset_objects = _load_registered_asset_objects(asset_id)

res = run_pipeline({
    "schema_id": "gms.project/1",
    "objects": asset_objects,
    "drop_simulation": {
        "test": "drop",
        "height_m": 0.75,
        "surface": "concrete",
        "drop_count": 1,
        "orientation": "flat"
    }
}, use_cache=False)

drop_sim = res["drop_simulation"]
model = drop_sim["model"]
print("Model Mass:", model["mass_kg"])
print("Model CoM offset:", model["com_offset_m"])
print("Model Starting Pose:", model["starting_pose_m"])

support = drop_sim.get("support", [])
print("Support points count:", len(support))

# Let's inspect the drop 0 trajectory samples
traj = drop_sim["trajectory"]
print("Traj length:", len(traj))
print("First sample:", traj[0])
print("Sample at 0.39s (just after impact):", traj[23] if len(traj) > 23 else "N/A")
print("Last sample:", traj[-1])
