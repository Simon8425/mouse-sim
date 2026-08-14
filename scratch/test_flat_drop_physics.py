import math
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.pipeline import run_pipeline
from mouse_sim.drop_sim import _quaternion_rotate

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
res = run_pipeline(req, use_cache=False)
drop_sim = res["drop_simulation"]
traj = drop_sim["trajectory"]

# Track key points in world space over time
# P5 (rear): (-0.0159, -0.0403, -0.0007)
# P9 (front right): (0.0200, 0.0491, 0.0004)
# P11 (front left): (-0.0201, 0.0489, 0.0004)
p_rear = (-0.0159, -0.0403, -0.0007)
p_front = (0.0000, 0.0490, 0.0004)

print("Trajectory during impact and settle (every 3rd sample from t=0.35 to t=0.75):")
for s in traj:
    t = s[0]
    if 0.35 <= t <= 0.75 and round(t * 60) % 3 == 0:
        pos = s[1:4]
        q = s[4:8]
        # World z of rear and front points
        w_rear = pos[2] + _quaternion_rotate(q, p_rear)[2]
        w_front = pos[2] + _quaternion_rotate(q, p_front)[2]
        print(f"t={t:.4f}s: CoM_z={pos[2]:.4f}m, Rear_z={w_rear*1000:.1f}mm, Front_z={w_front*1000:.1f}mm, q=({q[0]:.4f}, {q[1]:.4f}, {q[2]:.4f}, {q[3]:.4f})")

final_s = traj[-1]
pos = final_s[1:4]
q = final_s[4:8]
w_rear = pos[2] + _quaternion_rotate(q, p_rear)[2]
w_front = pos[2] + _quaternion_rotate(q, p_front)[2]
print(f"\nFinal Settled Pose (t={final_s[0]:.4f}s):")
print(f"  CoM_z = {pos[2]:.4f}m")
print(f"  Rear_z = {w_rear*1000:.2f}mm, Front_z = {w_front*1000:.2f}mm")
print(f"  Quaternion = {q}")
