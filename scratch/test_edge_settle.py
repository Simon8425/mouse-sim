from mouse_sim.drop_sim import (
    simulate,
    support_points,
    box_inertia
)

support = support_points(
    [(x, y, z) for x in (0.0, 0.1) for y in (0.0, 0.1) for z in (0.0, 0.1)]
)
inertia = box_inertia(0.1, ((0.0, 0.1), (0.0, 0.1), (0.0, 0.1)))
result = simulate(
    0.1, inertia, support, 0.75, orientation="edge", surface="concrete",
    drop_count=1,
)
drop = result["drops"][0]
print("Edge drop checks:", [c["code"] for c in drop["checks"]])
print("Settled:", drop["settled"], "settled_s:", drop["settled_s"])
print("Final orientation q:", result["trajectory"][-1][4:8])
