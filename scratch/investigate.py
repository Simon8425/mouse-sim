import json
import urllib.request
import math

def query_api(payload):
    req = urllib.request.Request(
        'http://localhost:8000/api/analyze',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    res = urllib.request.urlopen(req)
    return json.loads(res.read().decode('utf-8'))

def analyze_drop(orientation, height=0.75, drop_count=3, test="drop"):
    payload = {
        "schema_id": "gms.web-analysis-request/1",
        "request": {
            "schema_id": "gms.project/1",
            "geometry_asset_id": "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404",
            "drop_simulation": {
                "test": test,
                "height_m": height,
                "surface": "concrete",
                "drop_count": drop_count,
                "orientation": orientation
            },
            "options": {"display_tessellation": True}
        },
        "options": {"strict": False, "use_cache": False}
    }
    data = query_api(payload)
    drop_sim = data['result']['drop_simulation']
    print(f"=== Drop Simulation ({orientation}) ===")
    print("Model mass_kg:", drop_sim['model']['mass_kg'])
    print("Model com_offset_m:", drop_sim['model']['com_offset_m'])
    print("Support points count:", drop_sim['model']['support_point_count'])
    for d in drop_sim['drops']:
        print(f"  Drop {d['index']}: start_s={d['start_s']}, end_s={d['end_s']}, settled_s={d['settled_s']}, settled={d['settled']}, impacts={d['impact_count']}")
        for check in d.get('checks', []):
            print(f"    Check: [{check['severity']}] {check['code']}: {check['message']}")
    
    trajectory = drop_sim['trajectory']
    print(f"Total trajectory samples: {len(trajectory)}, time span: {trajectory[0][0]}s to {trajectory[-1][0]}s")
    
    # Check drop 0 trajectory motion
    # Let's find when motion actually stops in drop 0
    d0_samples = [s for s in trajectory if s[0] <= drop_sim['drops'][0]['end_s']]
    print(f"Drop 0 samples count: {len(d0_samples)}")
    
    # Check consecutive sample position and quaternion changes
    still_since = None
    for i in range(1, len(d0_samples)):
        prev = d0_samples[i-1]
        curr = d0_samples[i]
        dpos = math.sqrt((curr[1]-prev[1])**2 + (curr[2]-prev[2])**2 + (curr[3]-prev[3])**2)
        dq = math.sqrt((curr[4]-prev[4])**2 + (curr[5]-prev[5])**2 + (curr[6]-prev[6])**2 + (curr[7]-prev[7])**2)
        if dpos < 1e-5 and dq < 1e-5:
            if still_since is None:
                still_since = prev[0]
        else:
            still_since = None
    print(f"Drop 0 motion stopped at: {still_since}s (settled_s={drop_sim['drops'][0]['settled_s']}s)")
    if still_since is not None:
        print(f"Drop 0 frozen dead-time at tail: {drop_sim['drops'][0]['end_s'] - still_since:.4f}s")
    return drop_sim

if __name__ == '__main__':
    print("--- Testing Flat Drop ---")
    analyze_drop("flat")
    print("\n--- Testing Inverted / On-The-Back Drop ---")
    analyze_drop({"quaternion_wxyz": [0.0, 1.0, 0.0, 0.0]})
    print("\n--- Testing 12-deg Tilted Inverted Drop ---")
    # 180 + 12 deg tilt
    analyze_drop({"quaternion_wxyz": [0.1045, 0.9945, 0.0, 0.0]})
