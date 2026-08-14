import math
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.drop_sim import (
    simulate,
    support_points,
    SUPPORT_DIRECTIONS
)

DIRS_26 = (
    # 6 cardinal
    (1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, -1.0),
    # 12 edges
    (1.0, 1.0, 0.0), (1.0, -1.0, 0.0), (-1.0, 1.0, 0.0), (-1.0, -1.0, 0.0),
    (1.0, 0.0, 1.0), (1.0, 0.0, -1.0), (-1.0, 0.0, 1.0), (-1.0, 0.0, -1.0),
    (0.0, 1.0, 1.0), (0.0, 1.0, -1.0), (0.0, -1.0, 1.0), (0.0, -1.0, -1.0),
    # 8 corners
    (1.0, 1.0, 1.0), (1.0, 1.0, -1.0), (1.0, -1.0, 1.0), (1.0, -1.0, -1.0),
    (-1.0, 1.0, 1.0), (-1.0, 1.0, -1.0), (-1.0, -1.0, 1.0), (-1.0, -1.0, -1.0),
)

register_existing_assets(default_asset_dir())
asset_id = "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404"
asset_objects = _load_registered_asset_objects(asset_id)

all_verts = []
for obj in asset_objects:
    geom = obj.get("geometry", {})
    verts = geom.get("vertices", [])
    if verts:
        all_verts.extend(verts)

supp_26 = support_points(all_verts, DIRS_26)

mass = 0.075
dx, dy, dz = 0.065, 0.125, 0.040
inertia = (
    (mass / 12.0 * (dy**2 + dz**2), 0.0, 0.0),
    (0.0, mass / 12.0 * (dx**2 + dz**2), 0.0),
    (0.0, 0.0, mass / 12.0 * (dx**2 + dy**2))
)

res = simulate(mass, inertia, supp_26, height_m=0.75, surface="concrete", drop_count=1, orientation="flat")
drops = res["drops"]
traj = res["trajectory"]
print(f"Drop 0: settled={drops[0]['settled']}, settled_s={drops[0]['settled_s']}")
print(f"Total trajectory samples: {len(traj)}")

orientations = [s[4:8] for s in traj]
unique_q = len(set(tuple(round(x, 4) for x in q) for q in orientations))
print(f"Unique orientations during drop: {unique_q}")
print(f"First q: {traj[0][4:8]}")
print(f"Mid q (bounce): {traj[25][4:8] if len(traj)>25 else 'N/A'}")
print(f"Final q: {traj[-1][4:8]}")
