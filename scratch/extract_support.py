import json
from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim import drop_sim, importers

asset_dir = default_asset_dir()
register_existing_assets(asset_dir)
asset_id = "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404"
objects = _load_registered_asset_objects(asset_id)

vertices = []
for obj in objects:
    geom_data = obj.get("geometry")
    if geom_data:
        geom, _ = importers.parse_and_repair_geometry(geom_data)
        if hasattr(geom, "vertices") and geom.vertices:
            vertices.extend(geom.vertices)

support = drop_sim.support_points(vertices)
print(f"Extracted {len(support)} support points:")
for p in support:
    print(" ", p)

with open("scratch/g3_constants.json") as f:
    g3 = json.load(f)

fixture = {
    "mass_kg": g3["model"]["mass_kg"],
    "inertia_kg_m2": g3["model"]["inertia_kg_m2"],
    "com_offset_m": g3["model"]["com_offset_m"],
    "support": support
}
with open("scratch/g3_fixture.json", "w") as f:
    json.dump(fixture, f, indent=2)
print("Saved to scratch/g3_fixture.json")
