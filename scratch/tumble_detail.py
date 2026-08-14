"""Detail the tumble trajectory around the 3rd impact (original) vs fixed."""
import sys, math
sys.path.insert(0, ".")
sys.path.insert(0, "tests")
import mouse_sim.drop_sim as ds

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

from tests.test_drop_sim import CUBE_INERTIA, CUBE_SUPPORT
r0 = ds.simulate(0.1, CUBE_INERTIA, CUBE_SUPPORT, 0.5, test="tumble", spin_rps=6.0)
print("ORIGINAL: settled", r0["drops"][0]["settled_s"], "settled_flag", r0["drops"][0]["settled"])
print("traj samples around 0.5-0.6:")
for s in r0["trajectory"]:
    if 0.45 <= s[0] <= 0.65:
        print("  t={:.3f} z={:.4f} q=({:.3f},{:.3f},{:.3f},{:.3f})".format(s[0], s[3], s[4], s[5], s[6], s[7]))

patched = make_patched()
inv, _ = ds._solve_inertia(CUBE_INERTIA)
r1 = patched(0.1, CUBE_INERTIA, inv, CUBE_SUPPORT, 0.5, "concrete", (1.0,0,0,0), 6.0, 9.81, 1/240, 8.0)
print("FIXED: settled", r1["settled_s"], "settled_flag", r1["settled"])
print("traj samples around 0.45-0.65:")
for s in r1["trajectory"]:
    if 0.45 <= s[0] <= 0.65:
        print("  t={:.3f} z={:.4f} q=({:.3f},{:.3f},{:.3f},{:.3f})".format(s[0], s[3], s[4], s[5], s[6], s[7]))
