import math
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.pipeline import run_pipeline

register_existing_assets(default_asset_dir())
asset_id = "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404"
asset_objects = _load_registered_asset_objects(asset_id)

for orient in ["flat", "corner", "edge"]:
    req = {
        "schema_id": "gms.project/1",
        "objects": asset_objects,
        "drop_simulation": {
            "test": "drop",
            "height_m": 0.75,
            "surface": "concrete",
            "drop_count": 1,
            "orientation": orient
        },
        "options": {"strict": False}
    }
    res = run_pipeline(req, use_cache=False)
    drop_sim = res["drop_simulation"]
    traj = drop_sim["trajectory"]
    drop = drop_sim["drops"][0]
    min_z = min(s[3] for s in traj)
    rebounds = []
    for i in range(1, len(traj)-1):
        t, x, y, z = traj[i][:4]
        z_prev = traj[i-1][3]
        z_next = traj[i+1][3]
        if z > z_prev and z >= z_next and (z - min_z) > 0.005:
            rebounds.append((t, (z - min_z) * 100))
    print(f"\n--- Orientation: {orient} ---")
    print(f"Settled: {drop['settled']} at {drop['settled_s']:.3f}s, total samples: {len(traj)}")
    print(f"Impacts count: {len(drop_sim.get('impacts', []))}")
    for imp in drop_sim.get("impacts", []):
        print(f"  impact at t={imp['t_s']}s, speed={imp['impact_speed_m_s']:.2f} m/s, manifold={imp['manifold_size']}")
    for r in rebounds:
        print(f"  rebound peak at t={r[0]:.3f}s: {r[1]:.2f} cm")
