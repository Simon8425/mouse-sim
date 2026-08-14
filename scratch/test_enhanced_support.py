import math
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.drop_sim import (
    simulate,
    support_points,
    SUPPORT_DIRECTIONS,
    SURFACES,
    _quaternion_rotate,
    _world_inertia,
    _solve_inertia
)

register_existing_assets(default_asset_dir())
asset_id = "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404"
asset_objects = _load_registered_asset_objects(asset_id)

# Collect all mesh vertices from asset_objects
all_verts = []
for obj in asset_objects:
    geom = obj.get("geometry", {})
    verts = geom.get("vertices", [])
    if verts:
        all_verts.extend(verts)

print(f"Total vertices in G3 assembly: {len(all_verts)}")

# Standard 14 directions vs 26 directions
dirs_26 = [
    # 6 cardinal
    (1,0,0), (-1,0,0), (0,1,0), (0,-1,0), (0,0,1), (0,0,-1),
    # 12 edges
    (1,1,0), (1,-1,0), (-1,1,0), (-1,-1,0),
    (1,0,1), (1,0,-1), (-1,0,1), (-1,0,-1),
    (0,1,1), (0,1,-1), (0,-1,1), (0,-1,-1),
    # 8 corners
    (1,1,1), (1,1,-1), (1,-1,1), (1,-1,-1),
    (-1,1,1), (-1,1,-1), (-1,-1,1), (-1,-1,-1),
]

pts_14 = support_points(all_verts, SUPPORT_DIRECTIONS)
pts_26 = support_points(all_verts, dirs_26)

print(f"Unique points with 14 dirs: {len(set(pts_14))}")
print(f"Unique points with 26 dirs: {len(set(pts_26))}")

# Print the lowest points (z coordinates)
bottom_14 = sorted([p for p in pts_14 if p[2] < min(p[2] for p in pts_14) + 0.005], key=lambda p: p[0])
bottom_26 = sorted([p for p in pts_26 if p[2] < min(p[2] for p in pts_26) + 0.005], key=lambda p: p[0])

print(f"\nBottom points with 14 dirs: {len(bottom_14)}")
for p in bottom_14:
    print(f"  x={p[0]:.4f}, y={p[1]:.4f}, z={p[2]:.4f}")

print(f"\nBottom points with 26 dirs: {len(bottom_26)}")
for p in bottom_26:
    print(f"  x={p[0]:.4f}, y={p[1]:.4f}, z={p[2]:.4f}")
