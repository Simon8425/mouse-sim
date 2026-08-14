"""Full G3 matrix with ONLY the sticky quiet-pin (no axis change)."""
import sys, math
sys.path.insert(0, ".")
sys.path.insert(0, "tests")
import mouse_sim.drop_sim as ds
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.pipeline import run_pipeline

register_existing_assets(default_asset_dir())
asset_id = "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404"
asset_objects = _load_registered_asset_objects(asset_id)
req = {"schema_id": "gms.project/1", "objects": asset_objects,
       "drop_simulation": {"test": "drop", "height_m": 0.75, "surface": "concrete", "drop_count": 1, "orientation": "flat"},
       "options": {"display_tessellation": True}}
res = run_pipeline(req, use_cache=True)
m = res["drop_simulation"]["model"]
inertia = m["inertia_kg_m2"]
support = ds.support_points([v for obj in asset_objects for v in (obj.get("geometry") or {}).get("vertices") or []])

def make_patched():
    import inspect
    src = inspect.getsource(ds._simulate_drop)
    # Sticky pin: don't reset quiet_pinned every contact frame
    old_reset = "        if in_contact:\n            quiet_pinned = False"
    new_reset = "        if in_contact:\n            pass  # quiet_pinned stays sticky once set"
    assert old_reset in src
    src = src.replace(old_reset, new_reset)
    # Clear the pin in the gate's else branch when the quiet band is left
    old_else = "            else:\n                quiet_accum = max(0.0, quiet_accum - dt)\n                quiet_anchor_up = None"
    new_else = "            else:\n                quiet_accum = max(0.0, quiet_accum - dt)\n                quiet_anchor_up = None\n                if quiet_accum <= 0.0 and quiet_pinned:\n                    quiet_pinned = False"
    assert old_else in src
    src = src.replace(old_else, new_else)
    ns = dict(vars(ds))
    exec(compile(src, "<patched>", "exec"), ns)
    return ns["_simulate_drop"]

patched = make_patched()
inv, _ = ds._solve_inertia(inertia)

def run_case(label, q, lateral=(0.0,0.0), angular=(0.0,0.0,0.0)):
    for name, fn in (("orig", ds._simulate_drop), ("sticky", patched)):
        r = fn(0.28867, inertia, inv, support, 0.75, "concrete", q, 0.0, 9.81, 1/240, 8.0,
               lateral_offset=lateral, initial_angular=angular,
               com_offset_m=(0.000143, -0.003114, 0.017019), restitution=0.3, friction=0.6)
        checks = [c["code"] for c in r["checks"]]
        print("{} {}: settle={:.2f} {} checks={} motion_stop={:.2f}".format(
            label, name, r["settled_s"], r["settled"], checks, r.get("motion_stop_s")))

run_case("flat", (1,0,0,0))
run_case("edge", ds._axis_angle_quaternion((1,0,0), math.pi/2))
run_case("corner", ds._orientation_quaternion("corner", 0))
run_case("inverted", (0,1,0,0))
run_case("drop1", (0.99943019703165, -0.02128549827703127, 0.026195587879941376, 0.0),
         lateral=(-0.020351154344557747, -0.0006435177510437899),
         angular=(-0.19986146848660186, 0.24596505063752339, 0.0))
for seed in (1, 2, 3):
    run_case("random{}".format(seed), ds._orientation_quaternion("random", seed))
