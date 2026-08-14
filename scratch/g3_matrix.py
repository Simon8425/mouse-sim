"""Run the full orientation matrix on the real G3 with original vs fixed-axis."""
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
    old = """                if escape_axis is None:
                    escape_axis = (axis[0], axis[1], axis[2])
                else:
                    natural_body = _quaternion_rotate(
                        _conjugate_quaternion(quaternion), (axis[0], axis[1], 0.0)
                    )
                    # Adopt the natural axis only on a genuine angular
                    # reversal (a substantial roll-back); the micro-rock and
                    # the crest-crossing momentum must keep the persisted
                    # escape direction.
                    if _dot(angular_body, natural_body) >= 1.5:
                        escape_axis = (axis[0], axis[1], axis[2])
                    axis = escape_axis"""
    new = """                if escape_axis is None:
                    escape_axis = (axis[0], axis[1], axis[2])
                axis = escape_axis"""
    assert old in src
    ns = dict(vars(ds))
    exec(compile(src.replace(old, new), "<patched>", "exec"), ns)
    return ns["_simulate_drop"]

patched = make_patched()
inv, _ = ds._solve_inertia(inertia)

def run_case(label, q, spin=0.0, lateral=(0.0,0.0), angular=(0.0,0.0,0.0)):
    results = {}
    for name, fn in (("orig", ds._simulate_drop), ("fix", patched)):
        r = fn(0.28867, inertia, inv, support, 0.75, "concrete", q, spin, 9.81, 1/240, 8.0,
               lateral_offset=lateral, initial_angular=angular,
               com_offset_m=(0.000143, -0.003114, 0.017019), restitution=0.3, friction=0.6)
        results[name] = r
    o, f = results["orig"], results["fix"]
    oc = [c["code"] for c in o["checks"]]
    fc = [c["code"] for c in f["checks"]]
    print("{}: orig settle={:.2f} {} checks={} | fix settle={:.2f} {} checks={} | motion_stop orig={:.2f} fix={:.2f}".format(
        label, o["settled_s"], o["settled"], oc, f["settled_s"], f["settled"], fc, o.get("motion_stop_s"), f.get("motion_stop_s")))

# Orientations
run_case("flat", (1,0,0,0))
run_case("edge 90-X", ds._axis_angle_quaternion((1,0,0), math.pi/2))
run_case("corner", ds._orientation_quaternion("corner", 0))
run_case("inverted 180-X", (0,1,0,0))
# jittered drop 1 (the problem case)
run_case("drop1-jitter", (0.99943019703165, -0.02128549827703127, 0.026195587879941376, 0.0),
         lateral=(-0.020351154344557747, -0.0006435177510437899),
         angular=(-0.19986146848660186, 0.24596505063752339, 0.0))
# random orientations
for seed in (1, 2, 3):
    q = ds._orientation_quaternion("random", seed)
    run_case("random seed {}".format(seed), q)
