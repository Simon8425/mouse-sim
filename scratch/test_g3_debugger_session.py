import json
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.pipeline import run_pipeline

register_existing_assets(default_asset_dir())
asset_id = "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404"
asset_objects = _load_registered_asset_objects(asset_id)

req = {
    "schema_id": "gms.project/1",
    "objects": asset_objects,
    "drop_simulation": {
        "test": "drop",
        "height_m": 0.75,
        "surface": "concrete",
        "drop_count": 1,
        "orientation": "flat"
    }
}

res = run_pipeline(req, use_cache=True)
m = res["drop_simulation"]["model"]
mass = res.get("mass", {})
drop0 = res["drop_simulation"]["drops"][0]

print("=== G3 MODEL DEBUGGER SUMMARY ===")
print("Model Name:", m.get("name"))
print("Mass [kg]:", m.get("mass_kg"))
print("CoM [m]:", m.get("com_offset_m"))
print("Inertia diag [kg*m^2]:", [m["inertia_kg_m2"][i][i] for i in range(3)])
print("Floor Surface:", m.get("surface"))
print("Restitution:", m.get("restitution"))
print("Friction:", m.get("friction"))
print("Timestep [s]:", m.get("timestep_s"))
print("Gravity [m/s^2]:", m.get("gravity_m_s2"))
print("Initial pose [m]:", drop0.get("starting_pose_m"))
print("Initial quat:", drop0.get("orientation_quaternion_wxyz"))
print("Settled [s]:", drop0.get("settled_s"))
print("Impacts count:", len(res["drop_simulation"].get("impacts", [])))
print("Trajectory samples:", len(res["drop_simulation"].get("trajectory", [])))
