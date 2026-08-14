import sys
import json
from pathlib import Path
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.pipeline import run_pipeline

asset_dir = default_asset_dir()
print(f"Asset dir: {asset_dir}")
reg = register_existing_assets(asset_dir)
print(f"Registered {reg} assets")

asset_id = "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404"
objects = _load_registered_asset_objects(asset_id)
print(f"Loaded {len(objects) if objects else 0} objects for asset {asset_id}")

def test_orientation(label, orientation, drop_count=3, height=0.75):
    request = {
        "schema_id": "gms.project/1",
        "objects": objects,
        "drop_simulation": {
            "test": "drop",
            "height_m": height,
            "surface": "concrete",
            "drop_count": drop_count,
            "orientation": orientation
        },
        "options": {"display_tessellation": True}
    }
    result = run_pipeline(request)
    drop_sim = result["drop_simulation"]
    print(f"\n================ {label} ================")
    print("Mass kg:", drop_sim["model"]["mass_kg"])
    print("Inertia diag:", [drop_sim["model"]["inertia_kg_m2"][i][i] for i in range(3)])
    print("COM offset:", drop_sim["model"]["com_offset_m"])
    print("Support points count:", drop_sim["model"]["support_point_count"])
    for d in drop_sim["drops"]:
        print(f"  Drop {d['index']}: start_s={d['start_s']}, end_s={d['end_s']}, settled_s={d['settled_s']}, settled={d['settled']}, impacts={d['impact_count']}")
        for c in d.get("checks", []):
            print(f"    Check: [{c['severity']}] {c['code']}: {c['message']}")
    
    # Check drop 0 trajectory samples
    traj = drop_sim["trajectory"]
    d0_end = drop_sim["drops"][0]["end_s"]
    d0_samples = [s for s in traj if s[0] <= d0_end + 1e-6]
    print(f"Drop 0 samples: {len(d0_samples)}")
    # Find last motion
    last_motion_t = 0.0
    for i in range(1, len(d0_samples)):
        s0 = d0_samples[i-1]
        s1 = d0_samples[i]
        dpos = max(abs(s1[1]-s0[1]), abs(s1[2]-s0[2]), abs(s1[3]-s0[3]))
        dq = max(abs(s1[4]-s0[4]), abs(s1[5]-s0[5]), abs(s1[6]-s0[6]), abs(s1[7]-s0[7]))
        if dpos > 1e-4 or dq > 1e-4:
            last_motion_t = s1[0]
    print(f"Drop 0 last motion sample t: {last_motion_t:.4f}s (drop settled_s: {drop_sim['drops'][0]['settled_s']}s)")
    print(f"Frozen dead time in drop 0: {d0_end - last_motion_t:.4f}s")
    if len(drop_sim["drops"]) > 1:
        d1_start = drop_sim["drops"][1]["start_s"]
        print(f"Drop 1 start_s: {d1_start:.4f}s (Gap after drop 0 end: {d1_start - d0_end:.4f}s, Total wait after motion stop: {d1_start - last_motion_t:.4f}s)")
    return drop_sim

test_orientation("FLAT DROP (0.75m concrete)", "flat")
test_orientation("INVERTED / TOP SHELL DROP", {"quaternion_wxyz": [0.0, 1.0, 0.0, 0.0]})
test_orientation("INVERTED 12-DEG TILT", {"quaternion_wxyz": [0.1045, 0.9945, 0.0, 0.0]})
test_orientation("EDGE DROP", "edge")
test_orientation("CORNER DROP", "corner")
