import math
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.drop_sim import support_points, SUPPORT_DIRECTIONS, _quaternion_rotate

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
print(f"Total support points: {len(supp)}")
for i, p in enumerate(supp):
    print(f"  P{i}: x={p[0]:.4f}, y={p[1]:.4f}, z={p[2]:.4f}")

lowest_z = min(p[2] for p in supp)
bottom_pts = [p for p in supp if p[2] <= lowest_z + 0.003]
print(f"\nBottom points on G3 (within 3mm of lowest z={lowest_z:.4f}):")
for p in bottom_pts:
    print(f"  x={p[0]:.4f}, y={p[1]:.4f}, z={p[2]:.4f}")
