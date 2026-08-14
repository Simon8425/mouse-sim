from mouse_sim.step_kernel import default_asset_dir
from mouse_sim.web_api import register_existing_assets, _load_registered_asset_objects
from mouse_sim.pipeline import run_pipeline

register_existing_assets(default_asset_dir())
asset_id = "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404"
asset_objects = _load_registered_asset_objects(asset_id)

for obj in asset_objects[:5]:
    print(obj["id"], "bounds:", obj.get("bounds"))

res = run_pipeline({
    "schema_id": "gms.project/1",
    "objects": asset_objects,
}, use_cache=False)

print("CoM:", res["mass"]["com_m"])
print("Total mass:", res["mass"]["mass_kg"])
print("Inertia:", res["mass"]["inertia_kg_m2"])
