"""Deterministic rigid-body drop simulation for durability screening.

The engine's other models are closed-form; this module adds a small,
deterministic rigid-body integrator so a drop test is a real physics
simulation: free fall, rotation, contact with a table plane (restitution and
Coulomb friction), bouncing, and settling.  Output is a fixed-rate trajectory
plus per-impact metrics that feed the existing impact/qualification models.

Honest scope: the contact geometry uses extreme support vertices sampled from
the tessellated mesh (a lightweight convex support model); the table is a
horizontal plane at z = 0; the integrator is semi-implicit Euler at a fixed
240 Hz timestep.  No fracture, deformation, or sub-surface contact is modeled.
"""

import math

DT_S = 1.0 / 240.0
TRAJECTORY_HZ = 60
GRAVITY_M_S2 = 9.81
MAX_DURATION_S = 8.0
# Impacts below this contact speed are micro-bounces of a nearly resting
# body, not test impacts; a drop settles once impacts stay below it.
MICRO_BOUNCE_SPEED_M_S = 0.3
# Per-drop initial-condition variation applied to drops 1+ (drop 0 is the
# pristine reference drop).  Every value is deterministic from the seed.
JITTER_MAX_TILT_DEG = 6.0
JITTER_MAX_LATERAL_FRACTION = 0.03
JITTER_MAX_SPIN_RAD_S = 0.5

SUPPORT_DIRECTIONS = (
    (1.0, 0.0, 0.0),
    (-1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, -1.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.0, 0.0, -1.0),
    (1.0, 1.0, 1.0),
    (-1.0, 1.0, 1.0),
    (1.0, -1.0, 1.0),
    (1.0, 1.0, -1.0),
    (-1.0, -1.0, 1.0),
    (-1.0, 1.0, -1.0),
    (1.0, -1.0, -1.0),
    (-1.0, -1.0, -1.0),
)

SURFACES = {
    "concrete": {"restitution": 0.30, "friction": 0.60},
    "wood": {"restitution": 0.40, "friction": 0.55},
    "foam": {"restitution": 0.12, "friction": 0.80},
    "steel": {"restitution": 0.50, "friction": 0.35},
}

TESTS = ("drop", "impact", "tumble")
ORIENTATIONS = ("flat", "edge", "corner", "random")


class DropSimulationError(ValueError):
    """Invalid drop-simulation configuration or inputs."""


def validate_config(config):
    """Validate a drop-simulation configuration dict; raise on invalid input."""
    if not isinstance(config, dict):
        raise DropSimulationError("drop_simulation must be an object")
    test = str(config.get("test", "drop")).strip().lower()
    if test not in TESTS:
        raise DropSimulationError("drop_simulation.test must be one of {}".format(", ".join(TESTS)))
    try:
        height_m = float(config.get("height_m", 0.75))
    except (TypeError, ValueError):
        raise DropSimulationError("drop_simulation.height_m must be numeric")
    if not math.isfinite(height_m) or height_m < 0.02 or height_m > 2.0:
        raise DropSimulationError("drop_simulation.height_m must be between 0.02 and 2.0 m")
    surface = str(config.get("surface", "concrete")).strip().lower()
    if surface not in SURFACES:
        raise DropSimulationError(
            "drop_simulation.surface must be one of {}".format(", ".join(sorted(SURFACES)))
        )
    try:
        drop_count = int(config.get("drop_count", 1))
    except (TypeError, ValueError):
        raise DropSimulationError("drop_simulation.drop_count must be an integer")
    if drop_count < 1 or drop_count > 20:
        raise DropSimulationError("drop_simulation.drop_count must be between 1 and 20")
    orientation = str(config.get("orientation", "flat")).strip().lower()
    if orientation not in ORIENTATIONS:
        raise DropSimulationError(
            "drop_simulation.orientation must be one of {}".format(", ".join(ORIENTATIONS))
        )
    spin_rps = config.get("spin_rps", 0.0)
    if spin_rps is None:
        spin_rps = 0.0
    try:
        spin_rps = float(spin_rps)
    except (TypeError, ValueError):
        raise DropSimulationError("drop_simulation.spin_rps must be numeric")
    if not math.isfinite(spin_rps) or abs(spin_rps) > 20.0:
        raise DropSimulationError("drop_simulation.spin_rps must be between -20 and 20 rev/s")
    if test != "tumble" and abs(spin_rps) > 1e-9:
        raise DropSimulationError("drop_simulation.spin_rps is only valid for the tumble test")
    mass_kg = config.get("mass_kg")
    if mass_kg is not None:
        try:
            mass_kg = float(mass_kg)
        except (TypeError, ValueError):
            raise DropSimulationError("drop_simulation.mass_kg must be numeric")
        if not math.isfinite(mass_kg) or mass_kg <= 0.0 or mass_kg > 10.0:
            raise DropSimulationError("drop_simulation.mass_kg must be between 0 and 10 kg")
    return {
        "test": test,
        "height_m": height_m,
        "surface": surface,
        "drop_count": drop_count,
        "orientation": orientation,
        "spin_rps": spin_rps,
        "mass_kg": mass_kg,
    }


def _quaternion_multiply(first, second):
    aw, ax, ay, az = first
    bw, bx, by, bz = second
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def _quaternion_rotate(quaternion, vector):
    """Rotate a vector by a unit quaternion (w, x, y, z).

    Uses ``v' = v + 2 w (q x v) + 2 (q x (q x v))``.
    """
    w, x, y, z = quaternion
    vx, vy, vz = vector
    cross = (y * vz - z * vy, z * vx - x * vz, x * vy - y * vx)
    return (
        vx + 2.0 * (w * cross[0] + y * cross[2] - z * cross[1]),
        vy + 2.0 * (w * cross[1] + z * cross[0] - x * cross[2]),
        vz + 2.0 * (w * cross[2] + x * cross[1] - y * cross[0]),
    )


def _normalize_quaternion(quaternion):
    norm = math.sqrt(sum(component * component for component in quaternion))
    if norm <= 0.0:
        return (1.0, 0.0, 0.0, 0.0)
    return tuple(component / norm for component in quaternion)


def _axis_angle_quaternion(axis, angle_rad):
    half = 0.5 * angle_rad
    sine = math.sin(half)
    norm = math.sqrt(sum(component * component for component in axis))
    if norm <= 0.0:
        return (1.0, 0.0, 0.0, 0.0)
    ux, uy, uz = (component / norm for component in axis)
    return (math.cos(half), ux * sine, uy * sine, uz * sine)


def _integrate_quaternion(quaternion, angular, dt):
    """Exact exponential-map rotation: q' = q ⊗ exp(omega * dt / 2)."""
    magnitude = math.sqrt(sum(component * component for component in angular))
    half_angle = 0.5 * magnitude * dt
    if half_angle < 1e-12:
        return _normalize_quaternion(quaternion)
    sine = math.sin(half_angle)
    scale = sine / max(magnitude, 1e-12)
    delta = (math.cos(half_angle), angular[0] * scale, angular[1] * scale, angular[2] * scale)
    return _normalize_quaternion(_quaternion_multiply(quaternion, delta))


def _orientation_quaternion(mode, seed):
    """Return the initial orientation for a drop mode (world frame, z-up)."""
    if mode == "flat":
        return (1.0, 0.0, 0.0, 0.0)
    if mode == "edge":
        # Rest on a long edge: 90 degrees about X.
        return _axis_angle_quaternion((1.0, 0.0, 0.0), math.pi / 2.0)
    if mode == "corner":
        # Rest on a corner: 54.7 degrees about the (1, 1, 0) axis.
        return _axis_angle_quaternion((1.0, 1.0, 0.0), math.acos(1.0 / math.sqrt(3.0)))
    # Deterministic pseudo-random orientation from the seed.
    state = seed & 0xFFFFFFFF

    def next_unit():
        nonlocal state
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        return state / 4294967296.0

    u1 = next_unit()
    u2 = next_unit()
    z = 2.0 * u1 - 1.0
    angle = 2.0 * math.pi * u2
    half = 0.5 * angle
    x = math.sqrt(max(0.0, 1.0 - z * z)) * math.cos(half)
    y = math.sqrt(max(0.0, 1.0 - z * z)) * math.sin(half)
    return _normalize_quaternion((z, x, y, 0.0))


def _drop_variation(
    seed,
    drop_index,
    height_m,
    max_tilt_deg=JITTER_MAX_TILT_DEG,
    max_lateral_fraction=JITTER_MAX_LATERAL_FRACTION,
    max_spin_rad_s=JITTER_MAX_SPIN_RAD_S,
):
    """Seeded initial-condition variation for drops 1+ (drop 0 is the reference).

    Returns (tilt_quaternion, tilt_deg, lateral_offset_m, initial_angular):
    a small tilt about a seeded horizontal axis, a horizontal drift offset of
    the initial position (up to ``max_lateral_fraction * height_m``), and a
    small release spin about the same axis so the first contact point's
    velocity differs between drops.  Draws come from a fresh LCG seeded by
    ``seed + 1000 + drop_index``, independent of the base orientation.
    """
    state = (seed + 1000 + drop_index) & 0xFFFFFFFF

    def next_unit():
        nonlocal state
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        return state / 4294967296.0

    axis_unit = next_unit()
    tilt_unit = next_unit()
    offset_angle_unit = next_unit()
    offset_radius_unit = next_unit()
    spin_unit = next_unit()

    axis_angle = 2.0 * math.pi * axis_unit
    axis = (math.cos(axis_angle), math.sin(axis_angle), 0.0)
    tilt_deg = max_tilt_deg * tilt_unit
    tilt_quaternion = _axis_angle_quaternion(axis, math.radians(tilt_deg))
    max_offset = max_lateral_fraction * height_m
    offset_radius = max_offset * offset_radius_unit
    offset_angle = 2.0 * math.pi * offset_angle_unit
    lateral_offset = (
        offset_radius * math.cos(offset_angle),
        offset_radius * math.sin(offset_angle),
    )
    spin_scale = max_spin_rad_s * spin_unit
    initial_angular = (axis[0] * spin_scale, axis[1] * spin_scale, 0.0)
    return tilt_quaternion, tilt_deg, lateral_offset, initial_angular


def box_inertia(mass_kg, bounds):
    """Diagonal inertia tensor for a uniform-density box approximation."""
    dx, dy, dz = (
        max(1e-6, bounds[0][1] - bounds[0][0]),
        max(1e-6, bounds[1][1] - bounds[1][0]),
        max(1e-6, bounds[2][1] - bounds[2][0]),
    )
    return (
        (mass_kg / 12.0 * (dy * dy + dz * dz), 0.0, 0.0),
        (0.0, mass_kg / 12.0 * (dx * dx + dz * dz), 0.0),
        (0.0, 0.0, mass_kg / 12.0 * (dx * dx + dy * dy)),
    )


def support_points(vertices, directions=SUPPORT_DIRECTIONS):
    """Extreme mesh vertices along fixed directions (convex support model).

    Returns body-frame contact points relative to the mesh origin.
    """
    points = []
    for direction in directions:
        best = None
        best_dot = None
        for vertex in vertices:
            dot = vertex[0] * direction[0] + vertex[1] * direction[1] + vertex[2] * direction[2]
            if best_dot is None or dot > best_dot:
                best_dot = dot
                best = vertex
        if best is not None:
            points.append(tuple(best))
    return points


def box_corners(bounds):
    """Eight corners of an axis-aligned world bounding box (support model)."""
    return [
        (bounds[0][x], bounds[1][y], bounds[2][z])
        for x in (0, 1)
        for y in (0, 1)
        for z in (0, 1)
    ]


def _solve_inertia(inertia):
    """Return (inverse, error) for a 3x3 inertia tensor via Cramer's rule."""
    a00, a01, a02 = inertia[0]
    a10, a11, a12 = inertia[1]
    a20, a21, a22 = inertia[2]
    determinant = (
        a00 * (a11 * a22 - a12 * a21)
        - a01 * (a10 * a22 - a12 * a20)
        + a02 * (a10 * a21 - a11 * a20)
    )
    # Small screening masses (0.1 kg) have inertia entries of order 1e-5 and
    # determinants of order 1e-13; only exact degeneracy is an error.
    if abs(determinant) < 1e-24:
        return None, "inertia tensor is singular"
    inverse = (
        (
            (a11 * a22 - a12 * a21) / determinant,
            (a02 * a21 - a01 * a22) / determinant,
            (a01 * a12 - a02 * a11) / determinant,
        ),
        (
            (a12 * a20 - a10 * a22) / determinant,
            (a00 * a22 - a02 * a20) / determinant,
            (a02 * a10 - a00 * a12) / determinant,
        ),
        (
            (a10 * a21 - a11 * a20) / determinant,
            (a01 * a20 - a00 * a21) / determinant,
            (a00 * a11 - a01 * a10) / determinant,
        ),
    )
    return inverse, None


def _world_inertia(inertia_body, quaternion):
    """Rotate the body-frame inertia tensor into the world frame."""
    rotation = (
        (
            _quaternion_rotate(quaternion, (1.0, 0.0, 0.0)),
            _quaternion_rotate(quaternion, (0.0, 1.0, 0.0)),
            _quaternion_rotate(quaternion, (0.0, 0.0, 1.0)),
        ),
    )[0]
    # I_world = R I_body R^T
    rows = []
    for i in range(3):
        row = []
        for j in range(3):
            value = 0.0
            for a in range(3):
                for b in range(3):
                    value += rotation[i][a] * inertia_body[a][b] * rotation[j][b]
            row.append(value)
        rows.append(tuple(row))
    return tuple(rows)


def _matvec(matrix, vector):
    return (
        matrix[0][0] * vector[0] + matrix[0][1] * vector[1] + matrix[0][2] * vector[2],
        matrix[1][0] * vector[0] + matrix[1][1] * vector[1] + matrix[1][2] * vector[2],
        matrix[2][0] * vector[0] + matrix[2][1] * vector[1] + matrix[2][2] * vector[2],
    )


def _cross(first, second):
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _add(first, second):
    return (first[0] + second[0], first[1] + second[1], first[2] + second[2])


def _scale(vector, factor):
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


def _dot(first, second):
    return first[0] * second[0] + first[1] * second[1] + first[2] * second[2]


def _norm(vector):
    return math.sqrt(_dot(vector, vector))


def _simulate_drop(mass_kg, inertia, inverse_inertia, support, height_m, surface, orientation_q, spin_rps, gravity, dt, max_duration_s, lateral_offset=(0.0, 0.0), initial_angular=(0.0, 0.0, 0.0)):
    """Simulate one drop; returns (trajectory, impacts, settled_s).

    ``lateral_offset`` shifts the starting position in the table plane and
    ``initial_angular`` seeds the release spin; both are per-drop variation
    (drop 0 passes the pristine defaults).
    """
    restitution = SURFACES[surface]["restitution"]
    friction = SURFACES[surface]["friction"]

    # Initial state: the configured height is the clearance of the LOWEST
    # world-frame support point above the table (rotated orientations have a
    # different lowest point than the body frame).  The perturbed orientation
    # is passed in already, so the clearance and the initial position reflect
    # the tilt; the lateral drift offset only shifts x/y in the table plane.
    lowest_world = min(
        _quaternion_rotate(orientation_q, point)[2] for point in support
    )
    position = (lateral_offset[0], lateral_offset[1], height_m - lowest_world)
    quaternion = orientation_q
    velocity = (0.0, 0.0, 0.0)
    spin_angular = (0.0, spin_rps * 2.0 * math.pi, 0.0) if spin_rps else (0.0, 0.0, 0.0)
    angular = _add(spin_angular, initial_angular)

    trajectory = []
    impacts = []
    elapsed = 0.0
    sample_interval = 1.0 / TRAJECTORY_HZ
    next_sample = 0.0
    settled = None
    rest_time = 0.0
    in_contact = False
    micro_streak = 0

    def record_sample(force=False):
        nonlocal next_sample
        if force or elapsed >= next_sample - 1e-12:
            trajectory.append(
                (
                    round(elapsed, 6),
                    position[0],
                    position[1],
                    position[2],
                    quaternion[0],
                    quaternion[1],
                    quaternion[2],
                    quaternion[3],
                )
            )
            next_sample = elapsed + sample_interval

    record_sample(force=True)
    while elapsed < max_duration_s and settled is None:
        # Semi-implicit Euler.
        velocity = (velocity[0], velocity[1], velocity[2] - gravity * dt)
        position = _add(position, _scale(velocity, dt))

        # Contact detection with the table plane (z = 0) via support points.
        world_support = [_add(position, _quaternion_rotate(quaternion, point)) for point in support]
        lowest = min(world_support, key=lambda point: point[2])
        contact_offset = lowest[2]
        if contact_offset <= 0.0:
            r = (lowest[0] - position[0], lowest[1] - position[1], lowest[2] - position[2])
            contact_velocity = _add(velocity, _cross(angular, r))
            normal_speed = contact_velocity[2]
            if normal_speed < 0.0:
                # Normal impulse with restitution.  Effective mass along the
                # contact normal: 1/m + n . ((I^-1 (r x n)) x r).
                inv_mass = 1.0 / mass_kg
                inv_inertia = _world_inertia(inverse_inertia, quaternion)
                inertia_effect = _cross(_matvec(inv_inertia, _cross(r, (0.0, 0.0, 1.0))), r)
                denominator = inv_mass + inertia_effect[2]
                applied_impulse = 0.0
                if denominator > 1e-12:
                    applied_impulse = -(1.0 + restitution) * normal_speed / denominator
                    velocity = _add(velocity, _scale((0.0, 0.0, 1.0), applied_impulse * inv_mass))
                    angular = _add(angular, _matvec(inv_inertia, _scale(_cross(r, (0.0, 0.0, 1.0)), applied_impulse)))
                    impact_speed = -normal_speed
                    # Report the pre-impact system kinetic energy (translation
                    # plus rotation); the contact-point speed alone can exceed
                    # the total energy via lever amplification.
                    pre_velocity = _add(velocity, _scale((0.0, 0.0, 1.0), -applied_impulse * inv_mass))
                    pre_angular = _add(
                        angular,
                        _scale(_matvec(inv_inertia, _cross(r, (0.0, 0.0, 1.0))), -applied_impulse),
                    )
                    pre_energy = 0.5 * mass_kg * _dot(pre_velocity, pre_velocity) + 0.5 * _dot(
                        pre_angular, _matvec(_world_inertia(inertia, quaternion), pre_angular)
                    )
                    # Screening bound: never report more impact energy than the
                    # drop provides (penetration-stage detection can otherwise
                    # overstate the pre-impact kinetic energy slightly).
                    pre_energy = min(pre_energy, mass_kg * gravity * height_m)
                    if impact_speed > MICRO_BOUNCE_SPEED_M_S:
                        micro_streak = 0
                        impacts.append(
                            {
                                "t_s": round(elapsed, 4),
                                "impact_speed_m_s": round(impact_speed, 4),
                                "kinetic_energy_j": round(pre_energy, 6),
                            }
                        )
                    else:
                        micro_streak += 1
                    in_contact = True
            # Positional correction: eliminate table penetration so a resting
            # body sits on the plane instead of hovering in a penetration
            # limit cycle.
            position = (position[0], position[1], position[2] - contact_offset)
            # Tangential friction impulse (Coulomb, impulse-limited by the
            # actually applied normal impulse).
            tangent_velocity = (
                contact_velocity[0],
                contact_velocity[1],
                0.0,
            )
            tangent_speed = _norm(tangent_velocity)
            if tangent_speed > 1e-6 and in_contact:
                max_friction = friction * abs(applied_impulse)
                friction_impulse = min(max_friction, tangent_speed * mass_kg)
                direction = _scale(tangent_velocity, -1.0 / tangent_speed)
                velocity = _add(velocity, _scale(direction, friction_impulse * inv_mass))
                angular = _add(
                    angular,
                    _matvec(inv_inertia, _scale(_cross(r, direction), friction_impulse)),
                )
        else:
            in_contact = False

        # Contact dissipation (rolling resistance) and mild aerodynamic drag:
        # without them a tumbling body enters a perpetual micro-bounce limit
        # cycle.  Values are small but sufficient to let a drop settle.
        if in_contact:
            spin_damping = min(1.0, 12.0 * dt)
            slide_damping = min(1.0, 5.0 * dt)
            angular = (
                angular[0] * (1.0 - spin_damping),
                angular[1] * (1.0 - spin_damping),
                angular[2] * (1.0 - spin_damping),
            )
            velocity = (
                velocity[0] * (1.0 - slide_damping),
                velocity[1] * (1.0 - slide_damping),
                velocity[2],
            )
        else:
            drag_factor = 1.0 - 0.15 * dt
            angular = (
                angular[0] * drag_factor,
                angular[1] * drag_factor,
                angular[2] * drag_factor,
            )

        # Integrate orientation with the exact exponential map.
        quaternion = _integrate_quaternion(quaternion, angular, dt)

        elapsed += dt
        record_sample()

        # Rest detection: at rest on the table, or reduced to consecutive
        # micro-bounces (energy-based, works for every restitution).
        if in_contact and _norm(velocity) < 0.03 and _norm(angular) < 0.3:
            rest_time += dt
            if rest_time >= 0.4:
                settled = elapsed
        else:
            rest_time = 0.0
        if settled is None and micro_streak >= 3:
            settled = elapsed

    if settled is None:
        settled = elapsed
    record_sample(force=True)
    return trajectory, impacts, settled


def simulate(mass_kg, inertia, support, height_m, surface="concrete", drop_count=1, test="drop", orientation="flat", spin_rps=0.0, gravity=GRAVITY_M_S2, dt=DT_S, max_duration_s=MAX_DURATION_S, seed=0):
    """Run a deterministic multi-drop simulation.

    Drop 0 is the pristine reference (exactly the configured orientation,
    zero lateral offset).  Every later drop gets a deterministic, seeded
    initial-condition variation (tilt jitter, lateral drift, small release
    spin) so repeated drops are unique while staying bit-reproducible for a
    fixed seed and configuration.  Returns a dict with the full trajectory,
    per-drop summaries, impact events, and the simulation model used.  Pure
    stdlib, deterministic for a fixed seed and configuration.
    """
    config = validate_config(
        {
            "test": test,
            "height_m": height_m,
            "surface": surface,
            "drop_count": drop_count,
            "orientation": orientation,
            "spin_rps": spin_rps,
            "mass_kg": mass_kg,
        }
    )
    if not math.isfinite(mass_kg) or mass_kg <= 0.0:
        raise DropSimulationError("drop_simulation.mass_kg must be positive")
    inverse_inertia, inertia_error = _solve_inertia(inertia)
    if inertia_error is not None:
        raise DropSimulationError("drop_simulation inertia: {}".format(inertia_error))
    if not support:
        raise DropSimulationError("drop_simulation support model is empty")

    drops = []
    trajectory = []
    all_impacts = []
    drop_interval_s = 0.35
    t_offset = 0.0
    for drop_index in range(config["drop_count"]):
        orientation_q = _orientation_quaternion(config["orientation"], seed + drop_index)
        if config["test"] == "impact" and drop_index == 0:
            # Impact test drops onto the corner orientation for a harsher hit.
            orientation_q = _axis_angle_quaternion((1.0, 1.0, 0.0), math.acos(1.0 / math.sqrt(3.0)))
        tilt_deg = 0.0
        lateral_offset = (0.0, 0.0)
        initial_angular = (0.0, 0.0, 0.0)
        if drop_index > 0:
            # Every drop after the reference gets a deterministic, seeded
            # initial-condition variation (tilt + lateral drift + release
            # spin), so repeated drops are unique but still bit-reproducible.
            tilt_q, tilt_deg, lateral_offset, initial_angular = _drop_variation(
                seed, drop_index, config["height_m"]
            )
            orientation_q = _normalize_quaternion(
                _quaternion_multiply(tilt_q, orientation_q)
            )
        drop_trajectory, impacts, settled = _simulate_drop(
            mass_kg,
            inertia,
            inverse_inertia,
            support,
            config["height_m"],
            config["surface"],
            orientation_q,
            config["spin_rps"] if config["test"] == "tumble" else 0.0,
            gravity,
            dt,
            max_duration_s,
            lateral_offset,
            initial_angular,
        )
        peak_speed = max((item["impact_speed_m_s"] for item in impacts), default=0.0)
        peak_energy = max((item["kinetic_energy_j"] for item in impacts), default=0.0)
        actual_orientation = config["orientation"]
        if config["test"] == "impact" and drop_index == 0:
            actual_orientation = "corner"
        drops.append(
            {
                "index": drop_index,
                "start_s": round(t_offset, 3),
                "end_s": round(t_offset + settled, 3),
                "settled_s": round(settled, 3),
                "impact_count": len(impacts),
                "peak_impact_speed_m_s": round(peak_speed, 4),
                "peak_kinetic_energy_j": round(peak_energy, 6),
                "orientation": actual_orientation,
                "tilt_deg": round(tilt_deg, 4),
                "lateral_offset_m": [
                    round(lateral_offset[0], 6),
                    round(lateral_offset[1], 6),
                ],
            }
        )
        for impact in impacts:
            all_impacts.append(
                {
                    "drop": drop_index,
                    "t_s": round(impact["t_s"] + t_offset, 4),
                    "impact_speed_m_s": impact["impact_speed_m_s"],
                    "kinetic_energy_j": impact["kinetic_energy_j"],
                }
            )
        for sample in drop_trajectory:
            trajectory.append(
                (
                    round(sample[0] + t_offset, 6),
                    sample[1],
                    sample[2],
                    sample[3],
                    sample[4],
                    sample[5],
                    sample[6],
                    sample[7],
                )
            )
        t_offset += settled + drop_interval_s

    peak_overall = max(all_impacts, key=lambda item: item["impact_speed_m_s"]) if all_impacts else None
    return {
        "config": config,
        "model": {
            "mass_kg": round(mass_kg, 6),
            "inertia_kg_m2": [list(row) for row in inertia],
            "support_model": "mesh_extreme_points",
            "support_point_count": len(support),
            "integrator": "semi_implicit_euler",
            "timestep_s": dt,
            "gravity_m_s2": gravity,
            "surface": config["surface"],
            "jitter": {
                "max_tilt_deg": JITTER_MAX_TILT_DEG,
                "max_lateral_fraction": JITTER_MAX_LATERAL_FRACTION,
                "max_initial_spin_rad_s": JITTER_MAX_SPIN_RAD_S,
                "seed": seed,
            },
        },
        "drops": drops,
        "impacts": all_impacts,
        "peak": (
            {
                "drop": peak_overall["drop"],
                "t_s": peak_overall["t_s"],
                "impact_speed_m_s": peak_overall["impact_speed_m_s"],
                "kinetic_energy_j": peak_overall["kinetic_energy_j"],
            }
            if peak_overall is not None
            else None
        ),
        "trajectory": trajectory,
    }
