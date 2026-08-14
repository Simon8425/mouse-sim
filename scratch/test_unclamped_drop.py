import math
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.drop_sim import (
    support_points,
    _quaternion_rotate,
    _world_inertia,
    _solve_inertia,
    _low_speed_restitution,
    _gyroscopic_update,
    _integrate_quaternion,
    _matvec,
    _cross,
    _dot,
    _norm,
    _scale,
    _add,
    _conjugate_quaternion,
    _quaternion_multiply,
    _normalize_quaternion
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

# Test dropping from 75cm with true physics
# Let's inspect the drop behavior
print("Support points count:", len(supp_26))
