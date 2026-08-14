import sys
import math
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim import mass, drop_sim, importers

sys.stdout.reconfigure(line_buffering=True)
asset_dir = default_asset_dir()
register_existing_assets(asset_dir)

asset_id = "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404"
objects = _load_registered_asset_objects(asset_id)

# Parse geometry objects
parsed_objs = {}
vertices = []
for obj in objects:
    geom_data = obj.get("geometry")
    if geom_data:
        geom, _ = importers.parse_and_repair_geometry(geom_data)
        parsed_objs[obj["id"]] = geom
        if hasattr(geom, "vertices") and geom.vertices:
            vertices.extend(geom.vertices)

mass_props = mass.mass_properties(parsed_objs)
mass_kg = mass_props["mass_kg"]
inertia = mass_props["inertia_tensor_kg_m2"]
com_offset_m = mass_props["center_of_mass_m"]
support = drop_sim.support_points(vertices)

print(f"Extracted G3 parameters:")
print(f"  mass_kg = {mass_kg}")
print(f"  inertia = {inertia}")
print(f"  com_offset_m = {com_offset_m}")
print(f"  support count = {len(support)}")

def run_test(label, orientation):
    res = drop_sim.simulate(
        mass_kg,
        inertia,
        support,
        height_m=0.75,
        surface="concrete",
        drop_count=3,
        orientation=orientation,
        com_offset_m=com_offset_m
    )
    print(f"\n--- {label} ---")
    for d in res["drops"]:
        print(f"  Drop {d['index']}: start_s={d['start_s']}, end_s={d['end_s']}, settled_s={d['settled_s']}, settled={d['settled']}, impacts={d['impact_count']}")
        for c in d.get("checks", []):
            print(f"    Check: [{c['severity']}] {c['code']}: {c['message']}")
    
    # Check drop 0 trajectory samples
    traj = res["trajectory"]
    d0_end = res["drops"][0]["end_s"]
    d0_samples = [s for s in traj if s[0] <= d0_end + 1e-6]
    last_motion_t = 0.0
    for i in range(1, len(d0_samples)):
        s0 = d0_samples[i-1]
        s1 = d0_samples[i]
        dpos = max(abs(s1[1]-s0[1]), abs(s1[2]-s0[2]), abs(s1[3]-s0[3]))
        dq = max(abs(s1[4]-s0[4]), abs(s1[5]-s0[5]), abs(s1[6]-s0[6]), abs(s1[7]-s0[7]))
        if dpos > 1e-4 or dq > 1e-4:
            last_motion_t = s1[0]
    print(f"  Drop 0 last motion sample t: {last_motion_t:.4f}s (drop settled_s: {res['drops'][0]['settled_s']}s)")
    print(f"  Frozen dead time in drop 0: {d0_end - last_motion_t:.4f}s")
    if len(res["drops"]) > 1:
        d1_start = res["drops"][1]["start_s"]
        print(f"  Drop 1 start_s: {d1_start:.4f}s (Gap after drop 0 end: {d1_start - d0_end:.4f}s, Total wait after motion stop: {d1_start - last_motion_t:.4f}s)")
    return res

run_test("FLAT", "flat")
run_test("INVERTED (ON THE BACK)", {"quaternion_wxyz": [0.0, 1.0, 0.0, 0.0]})
run_test("INVERTED 6-DEG TILT", {"quaternion_wxyz": [0.0523, 0.9986, 0.0, 0.0]})
run_test("INVERTED 12-DEG TILT", {"quaternion_wxyz": [0.1045, 0.9945, 0.0, 0.0]})
run_test("EDGE", "edge")
run_test("CORNER", "corner")
run_test("RANDOM", "random")
