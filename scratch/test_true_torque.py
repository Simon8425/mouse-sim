import math
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.pipeline import run_pipeline

register_existing_assets(default_asset_dir())
asset_id = "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404"
asset_objects = _load_registered_asset_objects(asset_id)

# Test with flat, inverted, and corner drops
for ori in ["flat", "corner", "edge"]:
    req = {
        "schema_id": "gms.project/1",
        "objects": asset_objects,
        "drop_simulation": {
            "test": "drop",
            "height_m": 0.75,
            "surface": "concrete",
            "drop_count": 1,
            "orientation": ori
        }
    }
    res = run_pipeline(req, use_cache=False)
    d = res["drop_simulation"]["drops"][0]
    traj = res["drop_simulation"]["trajectory"]
    print(f"\n--- Orientation: {ori} ---")
    print(f"Settled: {d['settled']} in {d['settled_s']}s, impacts: {d['impact_count']}")
    print(f"First sample q: {traj[0][4:8]}")
    print(f"Impact sample q: {traj[24][4:8] if len(traj)>24 else 'N/A'}")
    print(f"Final sample q: {traj[-1][4:8]}")
