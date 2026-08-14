import math
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.pipeline import run_pipeline

register_existing_assets(default_asset_dir())
asset_id = "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404"
asset_objects = _load_registered_asset_objects(asset_id)

request = {
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

res = run_pipeline(request, use_cache=False)
drop_sim = res["drop_simulation"]
traj = drop_sim["trajectory"]
drop0 = drop_sim["drops"][0]

print("Drop 0 Summary:")
print("  start_s:", drop0["start_s"])
print("  end_s:", drop0["end_s"])
print("  settled_s:", drop0["settled_s"])
print("  settled:", drop0["settled"])
print("  initial orientation:", drop0.get("orientation_quaternion_wxyz"))
print("  starting_pose_m:", drop0.get("starting_pose_m"))
print("  checks:", drop0.get("checks"))

print("\nFirst 10 trajectory samples:")
for s in traj[:10]:
    print(f"t={s[0]:.4f}, pos=({s[1]:.4f}, {s[2]:.4f}, {s[3]:.4f}), q=({s[4]:.4f}, {s[5]:.4f}, {s[6]:.4f}, {s[7]:.4f})")

print("\nSamples around impact (t ~ 0.38 - 0.50):")
impact_samples = [s for s in traj if 0.35 <= s[0] <= 0.65]
for s in impact_samples:
    print(f"t={s[0]:.4f}, pos=({s[1]:.4f}, {s[2]:.4f}, {s[3]:.4f}), q=({s[4]:.4f}, {s[5]:.4f}, {s[6]:.4f}, {s[7]:.4f})")

print("\nLast 10 trajectory samples:")
for s in traj[-10:]:
    print(f"t={s[0]:.4f}, pos=({s[1]:.4f}, {s[2]:.4f}, {s[3]:.4f}), q=({s[4]:.4f}, {s[5]:.4f}, {s[6]:.4f}, {s[7]:.4f})")
