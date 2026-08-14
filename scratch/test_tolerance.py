import math
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.drop_sim import simulate, support_points, SUPPORT_DIRECTIONS

register_existing_assets(default_asset_dir())
asset_id = "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404"
asset_objects = _load_registered_asset_objects(asset_id)

all_verts = []
for obj in asset_objects:
    geom = obj.get("geometry", {})
    verts = geom.get("vertices", [])
    if verts:
        all_verts.extend(verts)

supp = support_points(all_verts, SUPPORT_DIRECTIONS)

from mouse_sim.pipeline import run_pipeline
req = {
    "schema_id": "gms.project/1",
    "objects": asset_objects,
    "drop_simulation": {
        "test": "drop",
        "height_m": 0.75,
        "surface": "concrete",
        "drop_count": 1,
        "orientation": "flat"
    },
    "options": {"strict": False}
}

res = run_pipeline(req, use_cache=False)
drop_sim = res["drop_simulation"]
traj = drop_sim["trajectory"]

print(f"Total samples: {len(traj)}")
z_vals = [s[3] for s in traj]
print(f"Z max: {max(z_vals)*100:.1f} cm, Z min: {min(z_vals)*100:.2f} cm")

# Find all rebound peaks
min_z = min(z_vals)
for i in range(1, len(traj)-1):
    t, x, y, z = traj[i][:4]
    z_prev = traj[i-1][3]
    z_next = traj[i+1][3]
    if z > z_prev and z >= z_next and (z - min_z) > 0.005:
        rebound_h = (z - min_z) * 100
        print(f"Rebound peak at t={t:.4f}s: height = {rebound_h:.2f} cm above floor")

print("\nImpacts recorded:")
for imp in drop_sim.get("impacts", []):
    print(f"  t={imp['t_s']}s, speed={imp['impact_speed_m_s']} m/s, KE={imp['kinetic_energy_j']} J, manifold={imp['manifold_size']}")
