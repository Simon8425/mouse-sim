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

Coordinate and pose conventions
-------------------------------
* World frame: right-handed, z-up.  Gravity acts along -z with magnitude
  ``GRAVITY_M_S2``; the support plane (table) is z = 0.
* Body frame: the CAD/model frame of the device at rest on the table.
  "flat" means the CAD z-axis is vertical (the identity initial orientation).
* Initial pose: the body's lowest world-frame support point starts exactly
  ``height_m`` above the support plane; the lateral offset shifts the
  starting position in the world xy-plane only.
* Orientation modes (world-frame initial quaternions, deterministic for a
  fixed seed):
    - "flat":   identity — the CAD z-axis is vertical (flat-face rest).
    - "edge":   long-edge rest — 90 deg about the world X axis.
    - "corner": corner rest — 54.7 deg about the world (1, 1, 0) axis.
    - "random": seeded uniform orientation, deterministic from the seed.
* Explicit pose: ``orientation`` may instead be an object
  ``{"quaternion_wxyz": [w, x, y, z]}`` — a unit quaternion in the world
  frame (normalized internally; -q is accepted, it is the same orientation).
  Drop 0 then uses exactly that quaternion (no mode mapping) and drops 1+
  keep the seeded jitter on top of the explicit pose.
* Reproducibility: every per-drop result records the actual initial
  orientation quaternion used (after mode mapping and any jitter), the
  gravity direction in the body frame (what an accelerometer on the shell
  reads at release), the initial angular velocity (world frame), the zero
  initial velocity, and the starting pose — a physical drop recorded as a
  pose can be re-run in simulation from the recorded numbers.
"""

import math

DT_S = 1.0 / 240.0
TRAJECTORY_HZ = 60
# Integrator gravity: 9.81 m/s^2 (documented convention; g-UNIT conversions in the
# pipeline use the standard 9.80665 — the 0.03% difference is inert and disclosed).
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

# Drop-surface screening table: polymer-on-surface restitution and friction
    # class values (instrumented impact data class: concrete 0.25-0.35,
    # wood 0.3-0.45, foam 0.08-0.15; friction from published dry sliding
    # polymer pairs).  Screening values — treat bounce/slide behavior as
    # class-level, not material-specific.
SURFACES = {
    "concrete": {"restitution": 0.30, "friction": 0.60},
    "wood": {"restitution": 0.40, "friction": 0.55},
    "foam": {"restitution": 0.12, "friction": 0.80},
    # Polymer-on-steel restitution is ~0.3-0.45 (instrumented impact data);
    # 0.45 is the upper band, 0.50 overstates the rebound.
    "steel": {"restitution": 0.45, "friction": 0.35},
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
    orientation = config.get("orientation", "flat")
    explicit_quaternion = None
    if isinstance(orientation, dict):
        quaternion_wxyz = orientation.get("quaternion_wxyz")
        if not isinstance(quaternion_wxyz, (list, tuple)) or len(quaternion_wxyz) != 4:
            raise DropSimulationError(
                "drop_simulation.orientation must be one of {} or an object "
                "with a quaternion_wxyz list of 4 numbers".format(", ".join(ORIENTATIONS))
            )
        components = []
        for component in quaternion_wxyz:
            try:
                numeric = float(component)
            except (TypeError, ValueError):
                raise DropSimulationError(
                    "drop_simulation.orientation.quaternion_wxyz must contain numeric components"
                )
            if not math.isfinite(numeric):
                raise DropSimulationError(
                    "drop_simulation.orientation.quaternion_wxyz must contain finite components"
                )
            components.append(numeric)
        if _norm(components) <= 0.0:
            raise DropSimulationError(
                "drop_simulation.orientation.quaternion_wxyz must have a non-zero norm"
            )
        orientation = "explicit"
        explicit_quaternion = list(_normalize_quaternion(tuple(components)))
    else:
        orientation = str(orientation).strip().lower()
        if orientation not in ORIENTATIONS:
            raise DropSimulationError(
                "drop_simulation.orientation must be one of {}".format(", ".join(ORIENTATIONS))
            )
    spin_rps = config.get("spin_rps")
    if spin_rps is None:
        # A tumble test with no release spin degenerates into a plain drop;
        # 6 rev/s is the wrist-fling midpoint for a tossed device (~5-15 rps
        # measured) and keeps the contact sweep well-resolved at 240 Hz.
        spin_rps = 6.0 if test == "tumble" else 0.0
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
    unit_seed = config.get("unit_seed")
    if unit_seed is not None:
        try:
            unit_seed = int(unit_seed)
        except (TypeError, ValueError):
            raise DropSimulationError("drop_simulation.unit_seed must be an integer")
        if unit_seed < 0 or unit_seed > 0xFFFFFFFF:
            raise DropSimulationError("drop_simulation.unit_seed must be between 0 and 2^32-1")
    seed = config.get("seed", 0)
    if seed is not None:
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            raise DropSimulationError("drop_simulation.seed must be an integer")
        if seed < 0 or seed > 0xFFFFFFFF:
            raise DropSimulationError("drop_simulation.seed must be between 0 and 2^32-1")
    validated = {
        "test": test,
        "height_m": height_m,
        "surface": surface,
        "drop_count": drop_count,
        "orientation": orientation,
        "spin_rps": spin_rps,
        "mass_kg": mass_kg,
        "unit_seed": unit_seed,
        "seed": seed,
    }
    if explicit_quaternion is not None:
        validated["orientation_quaternion_wxyz"] = explicit_quaternion
    return validated


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


def _integrate_quaternion(quaternion, angular_body, dt):
    """Exact exponential-map rotation for a BODY-frame angular velocity.

    ``q' = q ⊗ exp(omega_body * dt / 2)`` (right-multiply): the exponential
    map rotates the body by the body-frame angular velocity.  The body-frame
    convention is required so the torque-free gyroscopic term (Euler's
    equations) conserves rotational energy to second order.
    """
    magnitude = math.sqrt(sum(component * component for component in angular_body))
    half_angle = 0.5 * magnitude * dt
    if half_angle < 1e-12:
        return _normalize_quaternion(quaternion)
    sine = math.sin(half_angle)
    scale = sine / max(magnitude, 1e-12)
    delta = (math.cos(half_angle), angular_body[0] * scale, angular_body[1] * scale, angular_body[2] * scale)
    return _normalize_quaternion(_quaternion_multiply(quaternion, delta))


def _conjugate_quaternion(quaternion):
    return (quaternion[0], -quaternion[1], -quaternion[2], -quaternion[3])


def _gyroscopic_update(angular_body, inertia_body, inverse_inertia_body, dt):
    """Torque-free Euler step for the body-frame angular velocity.

    ``omega' = omega - dt * I^-1 (omega x I omega)`` — the gyroscopic term
    ``omega . (omega x I omega)`` vanishes identically, so the semi-implicit
    update conserves rotational energy to second order in ``dt``.  The
    linearized system has eigenvalues proportional to the inertia asymmetry
    times ``|omega|``, so the step is subdivided whenever
    ``asymmetry * |omega| * dt`` is large; without the subdivision the update
    self-amplifies at high spin for non-symmetric bodies.
    """
    a00, a01, a02 = inertia_body[0]
    a10, a11, a12 = inertia_body[1]
    a20, a21, a22 = inertia_body[2]
    asym = max(a00, a11, a22) / max(min(a00, a11, a22), 1e-12)
    magnitude = _norm(angular_body)
    # The semi-implicit gyro step's rotational-energy error is +0.5*(kappa*|omega|*dt)^2
    # per substep (coherent, not random); the 0.0045 rad/substep bound keeps
    # it below ~1e-5 of the rotational energy per substep even at
    # lever-amplified spins, so free-flight stretches stay within the energy
    # drift check.
    substeps = max(1, int(math.ceil(asym * magnitude * dt / 0.0045)))
    step_dt = dt / substeps
    for _ in range(substeps):
        inertia_omega = _matvec(inertia_body, angular_body)
        coupling = _cross(angular_body, inertia_omega)
        angular_body = _add(
            angular_body, _scale(_matvec(inverse_inertia_body, coupling), -step_dt)
        )
    return angular_body


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
    """Return (inverse, error) for a 3x3 inertia tensor via Cramer's rule.

    The tensor must be positive-definite (a physical inertia): a negative
    diagonal or indefinite matrix lets the torque-free gyroscopic term
    inject energy and the integrator never terminates.
    """
    a00, a01, a02 = inertia[0]
    a10, a11, a12 = inertia[1]
    a20, a21, a22 = inertia[2]
    for value in (a00, a01, a02, a10, a11, a12, a20, a21, a22):
        if not math.isfinite(value):
            return None, "inertia tensor contains non-finite values"
    if a00 <= 0.0 or a11 <= 0.0 or a22 <= 0.0:
        return None, "inertia tensor diagonal must be positive (positive-definite tensor required)"
    minor_xy = a00 * a11 - a01 * a10
    minor_xz = a00 * a22 - a02 * a20
    minor_yz = a11 * a22 - a12 * a21
    if minor_xy <= 0.0 or minor_xz <= 0.0 or minor_yz <= 0.0:
        return None, "inertia tensor is not positive-definite (off-diagonal terms too large)"
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
    """Rotate the body-frame inertia tensor into the world frame.

    The rotation rows are the images of the world basis vectors; the physical
    world-frame tensor is ``I_world = R I_body R^T`` (rows built from the
    rotated basis, not columns — a transposed index order sign-flips the
    off-diagonal terms for non-diagonal tensors).
    """
    rotation = (
        (
            _quaternion_rotate(quaternion, (1.0, 0.0, 0.0)),
            _quaternion_rotate(quaternion, (0.0, 1.0, 0.0)),
            _quaternion_rotate(quaternion, (0.0, 0.0, 1.0)),
        ),
    )[0]
    rows = []
    for i in range(3):
        row = []
        for j in range(3):
            value = 0.0
            for a in range(3):
                for b in range(3):
                    value += rotation[a][i] * inertia_body[a][b] * rotation[b][j]
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


def _mix_seed(seed):
    """SplitMix64-style integer mixing of the unit seed.

    The raw LCG stream ``u_k(s) = (a^k * A * s + K) mod 2^32 / 2^32`` is a
    linear function of the seed; for small seeds the stream shows
    short-range serial correlation in some draws, which would
    quantize small-sample failure statistics to residue classes.  Mixing the
    seed through SplitMix64 decorrelates consecutive units before the stream.
    """
    z = (int(seed) + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    return (z ^ (z >> 31)) & 0xFFFFFFFFFFFFFFFF


def _unit_variation(unit_seed, support, scale=1.0):
    """Deterministic per-unit manufacturing-tolerance draws for a seed.

    Returns (mass_scale, inertia_scale, com_offset_m, friction_scale,
    restitution_scale).  The draws come from a fixed-order LCG stream seeded
    by a SplitMix64 mix of the unit seed, so the same seed always produces
    the same unit and different seeds represent different manufactured units.
    Bands are engineering tolerances: mass ±3%, per-axis inertia ±5%, CoM
    placement ±2% of the support extent per axis, friction ±10%, restitution
    ±10%.  ``scale`` multiplies the band widths (the population engine uses
    it to apply its ``tolerance_scale`` so the population's parameter draws
    stay consistent with the drop's internal unit variation).
    """
    extent = [0.0, 0.0, 0.0]
    for point in support:
        for axis in range(3):
            extent[axis] = max(extent[axis], abs(point[axis]))
    state = _mix_seed(unit_seed) & 0xFFFFFFFF

    def next_unit():
        nonlocal state
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        return state / 4294967296.0

    def band(half_width):
        return 1.0 + (half_width * scale) * (2.0 * next_unit() - 1.0)

    mass_scale = band(0.03)
    inertia_scale = (band(0.05), band(0.05), band(0.05))
    com_offset_m = (
        (0.02 * scale) * extent[0] * (2.0 * next_unit() - 1.0),
        (0.02 * scale) * extent[1] * (2.0 * next_unit() - 1.0),
        (0.02 * scale) * extent[2] * (2.0 * next_unit() - 1.0),
    )
    friction_scale = band(0.10)
    restitution_scale = max(0.5, band(0.10))
    return mass_scale, inertia_scale, com_offset_m, friction_scale, restitution_scale


def _low_speed_restitution(restitution, impact_speed_m_s):
    """Impact-speed-dependent restitution roll-off at low speeds.

    Below 0.5 m/s viscoelastic dissipation dominates real polymer contacts
    and the effective coefficient of restitution falls off quadratically;
    without the roll-off, near-threshold micro-bounce and rocking chains
    (e.g. a 2 cm steel drop, or a box rocking on an edge) never decay and
    the body rattles indefinitely.
    """
    roll_off = min(1.0, (impact_speed_m_s / 0.5) ** 2)
    return restitution * roll_off


def _simulate_drop(
    mass_kg,
    inertia,
    inverse_inertia,
    support,
    height_m,
    surface,
    orientation_q,
    spin_rps,
    gravity,
    dt,
    max_duration_s,
    lateral_offset=(0.0, 0.0),
    initial_angular=(0.0, 0.0, 0.0),
    com_offset_m=(0.0, 0.0, 0.0),
    restitution=None,
    friction=None,
):
    """Simulate one drop; returns a dict with trajectory, impacts, settle
    state, per-drop energy accounting, and physics self-checks.

    ``com_offset_m`` is the body-fixed mesh-origin-to-center-of-mass vector.
    The integrated position is the CENTER OF MASS (the CoM velocity is the
    dynamically integrated quantity); support points and lever arms are
    resolved about the CoM, while the reported trajectory is the mesh origin
    (``position - R * com_offset``, the display frame).  ``lateral_offset``
    shifts the starting position in the table plane and ``initial_angular``
    seeds the release spin; both are per-drop variation.

    The angular velocity is integrated in the BODY frame with the exact
    exponential map and the torque-free gyroscopic term (Euler's equations),
    which conserves rotational energy to second order; contact impulses are
    applied in the world frame and converted back.
    """
    restitution = SURFACES[surface]["restitution"] if restitution is None else restitution
    friction = SURFACES[surface]["friction"] if friction is None else friction
    rel_support = [
        (point[0] - com_offset_m[0], point[1] - com_offset_m[1], point[2] - com_offset_m[2])
        for point in support
    ]

    extent = [0.0, 0.0, 0.0]
    for point in rel_support:
        for axis in range(3):
            extent[axis] = max(extent[axis], abs(point[axis]))
    min_extent = max(min(extent), 1e-6)

    # Initial state: the configured height is the clearance of the LOWEST
    # world-frame support point above the table (rotated orientations have a
    # different lowest point than the body frame); the CoM sits above it by
    # the CoM-to-lowest offset.
    lowest_world = min(
        _quaternion_rotate(orientation_q, point)[2] for point in rel_support
    )
    position = (lateral_offset[0], lateral_offset[1], height_m - lowest_world)
    initial_position = position
    quaternion = orientation_q
    velocity = (0.0, 0.0, 0.0)
    spin_angular = (0.0, spin_rps * 2.0 * math.pi, 0.0) if spin_rps else (0.0, 0.0, 0.0)
    angular_body = _add(spin_angular, initial_angular)

    trajectory = []
    impacts = []
    elapsed = 0.0
    sample_interval = 1.0 / TRAJECTORY_HZ
    next_sample = 0.0
    settled = None
    settled_flag = True
    rest_time = 0.0
    in_contact = False
    max_penetration = 0.0
    drift_max = 0.0
    flight_base_energy = None
    flight_base_time = 0.0
    flight_drag_loss = 0.0
    energy_creation = False
    rebound_overspeed = False
    lost_contact = 0.0
    lost_drag = 0.0
    first_impact_energy = None

    def total_energy():
        return (
            0.5 * mass_kg * _dot(velocity, velocity)
            + 0.5 * _dot(angular_body, _matvec(inertia, angular_body))
            + mass_kg * gravity * position[2]
        )

    initial_total = total_energy()
    spin_budget = 0.5 * _dot(spin_angular, _matvec(inertia, spin_angular))
    # The release energy is the TRUE initial mechanical energy: the drop
    # budget m*g*h plus the potential energy of the CoM height above the
    # lowest support point (rotated orientations start with extra PE) plus
    # the configured spin budget.  The per-impact cap at m*g*h (the drop
    # budget) is unchanged.
    release_energy = mass_kg * gravity * (height_m - lowest_world) + spin_budget
    settled_energy = None

    def record_sample(force=False):
        nonlocal next_sample
        if force or elapsed >= next_sample - 1e-12:
            origin = _add(
                position,
                _scale(_quaternion_rotate(quaternion, com_offset_m), -1.0),
            )
            trajectory.append(
                (
                    round(elapsed, 6),
                    origin[0],
                    origin[1],
                    origin[2],
                    quaternion[0],
                    quaternion[1],
                    quaternion[2],
                    quaternion[3],
                )
            )
            next_sample = elapsed + sample_interval

    record_sample(force=True)
    while elapsed < max_duration_s and settled is None:
        # Sequential-contact step: the step is partitioned into windows; each
        # window finds the first plane crossing (analytic for low spin,
        # subdivided sweep for high spin), resolves the contact at the
        # crossing state with the crossing-time velocity, and continues with
        # the remainder — a high-spin whip that carries several corners
        # through the plane within one step resolves every contact instead
        # of burying the body and injecting energy through a deep positional
        # correction.
        step_contacted = False
        window = dt
        while window > 1e-9:
            v_end = (velocity[0], velocity[1], velocity[2] - gravity * window)
            omega_mag = _norm(angular_body)
            # The swept corner moves (|omega|*window*lever) per window; the
            # 0.02 rad substep bound keeps the residual crossing penetration
            # below ~1.5 mm for a mouse-scale body.
            spin_sweep = omega_mag * window * max(extent) > 0.02 * min_extent
            lowest_offset = min(
                _quaternion_rotate(quaternion, point)[2] for point in rel_support
            )
            crossing_time = None
            crossing_state = None
            if not spin_sweep:
                fall = v_end[2] * window
                if fall < 0.0:
                    candidate = -(position[2] + lowest_offset) / fall
                    if candidate < 0.0:
                        # Already below the crossing at the window start (a
                        # previous window's fall overshot): resolve the
                        # contact at the window start instead of sinking a
                        # full window deeper.
                        crossing_time = 0.0
                    elif candidate <= 1.0:
                        crossing_time = candidate * window
            else:
                substeps = max(1, int(math.ceil(omega_mag * window / 0.02)))
                for step_index in range(1, substeps + 1):
                    fraction = float(step_index) / float(substeps)
                    q_step = _integrate_quaternion(quaternion, angular_body, window * fraction)
                    pos_step = (
                        position[0] + v_end[0] * window * fraction,
                        position[1] + v_end[1] * window * fraction,
                        position[2] + v_end[2] * window * fraction,
                    )
                    support_step = [
                        _add(pos_step, _quaternion_rotate(q_step, point)) for point in rel_support
                    ]
                    lowest_step = min(support_step, key=lambda point: point[2])
                    if lowest_step[2] <= 0.0:
                        crossing_state = (q_step, pos_step, support_step, lowest_step)
                        crossing_time = window * fraction
                        break
            if crossing_time is None:
                # No contact in this window: advance the whole window.
                velocity = v_end
                position = _add(position, _scale(v_end, window))
                quaternion = _integrate_quaternion(quaternion, angular_body, window)
                angular_body = _gyroscopic_update(angular_body, inertia, inverse_inertia, window)
                break
            # Resolve the contact at the crossing state.
            step_contacted = True
            if crossing_state is not None:
                quaternion, position, world_support, lowest = crossing_state
            else:
                position = (
                    position[0] + v_end[0] * crossing_time,
                    position[1] + v_end[1] * crossing_time,
                    position[2] + v_end[2] * crossing_time,
                )
                quaternion = _integrate_quaternion(quaternion, angular_body, crossing_time)
                world_support = [
                    _add(position, _quaternion_rotate(quaternion, point)) for point in rel_support
                ]
                lowest = min(world_support, key=lambda point: point[2])
            # Crossing-time velocity: end-of-window velocity minus gravity
            # over the uncrossed portion.
            velocity = (
                v_end[0],
                v_end[1],
                v_end[2] + gravity * (window - crossing_time),
            )
            contact_offset = lowest[2]
            if contact_offset <= 0.0:
                # Contact manifold: every support point within tolerance of
                # the lowest one is active (a flat face exposes several
                # coplanar points); the impulse acts at the centroid of the
                # UNIQUE active points (support directions can select the
                # same extreme vertex, and duplicates would skew the
                # centroid and fabricate torque) so face impacts do not spin
                # the body up about a single corner.
                # Manifold band: contact points within 2% of the minimum extent
                # (floor 1e-4 m) are coplanar with the lowest point (screening).
                tolerance = max(1e-4, 0.02 * min_extent)
                active = []
                active_keys = set()
                for point in world_support:
                    if point[2] <= lowest[2] + tolerance and point not in active_keys:
                        active.append(point)
                        active_keys.add(point)
                centroid = (
                    sum(point[0] for point in active) / len(active),
                    sum(point[1] for point in active) / len(active),
                    sum(point[2] for point in active) / len(active),
                )
                # Lever arms are measured from the CENTER OF MASS (the
                # integrated position), so the torque response is independent
                # of where the mesh origin sits relative to the CoM.
                r = (
                    centroid[0] - position[0],
                    centroid[1] - position[1],
                    centroid[2] - position[2],
                )
                angular_world = _quaternion_rotate(quaternion, angular_body)
                contact_velocity = _add(velocity, _cross(angular_world, r))
                normal_speed = contact_velocity[2]
                tangent_velocity = (contact_velocity[0], contact_velocity[1], 0.0)
                tangent_speed = _norm(tangent_velocity)
                inv_mass = 1.0 / mass_kg
                inv_inertia_world = _world_inertia(inverse_inertia, quaternion)
                applied_impulse = 0.0
                if normal_speed < 0.0:
                    # Normal impulse with restitution.  Effective mass along
                    # the contact normal: 1/m + n . ((I^-1 (r x n)) x r).
                    inertia_effect = _cross(_matvec(inv_inertia_world, _cross(r, (0.0, 0.0, 1.0))), r)
                    denominator = inv_mass + inertia_effect[2]
                    if denominator > 1e-12:
                        impact_restitution = _low_speed_restitution(restitution, -normal_speed)
                        applied_impulse = -(1.0 + impact_restitution) * normal_speed / denominator
                        velocity = _add(velocity, _scale((0.0, 0.0, 1.0), applied_impulse * inv_mass))
                        delta_angular_world = _matvec(
                            inv_inertia_world, _scale(_cross(r, (0.0, 0.0, 1.0)), applied_impulse)
                        )
                        angular_body = _add(
                            angular_body,
                            _quaternion_rotate(_conjugate_quaternion(quaternion), delta_angular_world),
                        )
                        impact_speed = -normal_speed
                        # Pre-impact system kinetic energy (translation plus
                        # rotation); the contact-point speed alone can exceed
                        # the total energy via lever amplification.
                        pre_velocity = _add(velocity, _scale((0.0, 0.0, 1.0), -applied_impulse * inv_mass))
                        pre_angular_body = _add(
                            angular_body,
                            _scale(
                                _quaternion_rotate(_conjugate_quaternion(quaternion), delta_angular_world),
                                -1.0,
                            ),
                        )
                        pre_energy = 0.5 * mass_kg * _dot(pre_velocity, pre_velocity) + 0.5 * _dot(
                            pre_angular_body, _matvec(inertia, pre_angular_body)
                        )
                        post_energy = 0.5 * mass_kg * _dot(velocity, velocity) + 0.5 * _dot(
                            angular_body, _matvec(inertia, angular_body)
                        )
                        if pre_energy > initial_total * (1.0 + 1e-3):
                            energy_creation = True
                        if post_energy > pre_energy * (1.0 + 1e-3):
                            rebound_overspeed = True
                        lost_contact += max(0.0, pre_energy - post_energy)
                        if first_impact_energy is None:
                            first_impact_energy = pre_energy
                        if impact_speed > MICRO_BOUNCE_SPEED_M_S:
                            point_speed = _norm(contact_velocity)
                            incidence_angle = math.degrees(math.atan2(tangent_speed, impact_speed))
                            impacts.append(
                                {
                                    "t_s": round(elapsed, 4),
                                    "impact_speed_m_s": round(impact_speed, 4),
                                    "kinetic_energy_j": round(
                                        min(pre_energy, mass_kg * gravity * height_m), 6
                                    ),
                                    "raw_kinetic_energy_j": round(pre_energy, 6),
                                    "contact_location": [
                                        round(centroid[0], 6),
                                        round(centroid[1], 6),
                                        round(centroid[2], 6),
                                    ],
                                    "contact_normal": [0.0, 0.0, 1.0],
                                    "contact_point_speed": round(point_speed, 4),
                                    "tangent_speed": round(tangent_speed, 4),
                                    "incidence_angle_deg": round(incidence_angle, 4),
                                    "manifold_size": len(active),
                                }
                            )
                # Tangential friction impulse (Coulomb, impulse-limited by the
                # actually applied normal impulse and by the tangential
                # effective mass — a body-mass bound over-corrects the slip
                # at off-CoM contacts and injects energy).
                if tangent_speed > 1e-6:
                    direction = _scale(tangent_velocity, -1.0 / tangent_speed)
                    k_tangent = inv_mass + _dot(
                        _cross(_matvec(inv_inertia_world, _cross(r, direction)), r), direction
                    )
                    max_friction = friction * abs(applied_impulse)
                    friction_impulse = min(max_friction, tangent_speed / max(k_tangent, 1e-12))
                    velocity = _add(velocity, _scale(direction, friction_impulse * inv_mass))
                    delta_angular_world = _matvec(
                        inv_inertia_world, _scale(_cross(r, direction), friction_impulse)
                    )
                    angular_body = _add(
                        angular_body,
                        _quaternion_rotate(_conjugate_quaternion(quaternion), delta_angular_world),
                    )
                # Positional correction: eliminate table penetration so a
                # resting body sits on the plane instead of hovering in a
                # penetration limit cycle.  The depth is tracked for the
                # penetration check.
                penetration = -contact_offset
                if penetration > max_penetration:
                    max_penetration = penetration
                position = (position[0], position[1], position[2] - contact_offset)
            angular_body = _gyroscopic_update(
                angular_body, inertia, inverse_inertia, crossing_time
            )
            window = window - crossing_time
            if crossing_time <= 1e-9:
                # A (near-)zero-time crossing is a resting or still-falling
                # contact: resolve it and advance a bounded slice so the
                # window loop can re-resolve subsequent contacts.  After a
                # lever-limited corner impulse the CoM can keep falling fast;
                # advancing the whole remaining window at that velocity would
                # bury the body below the plane (deep penetrations) instead
                # of letting the next corner/face contact catch it.
                slice_time = min(window, 0.0005)
                velocity = (velocity[0], velocity[1], velocity[2] - gravity * slice_time)
                position = _add(position, _scale(velocity, slice_time))
                window = window - slice_time
                continue
            if window <= 1e-9:
                break

        in_contact = step_contacted
        # Contact dissipation (rolling resistance) and mild aerodynamic drag:
        # without them a tumbling body enters a perpetual micro-bounce limit
        # cycle.  Values are small but sufficient to let a drop settle.
        if in_contact:
            spin_damping = min(1.0, 12.0 * dt)
            slide_damping = min(1.0, 5.0 * dt)
            angular_body = (
                angular_body[0] * (1.0 - spin_damping),
                angular_body[1] * (1.0 - spin_damping),
                angular_body[2] * (1.0 - spin_damping),
            )
            velocity = (
                velocity[0] * (1.0 - slide_damping),
                velocity[1] * (1.0 - slide_damping),
                velocity[2],
            )
            # Resting-contact clamp: a nearly stationary body in contact is
            # pulled to true rest (real contacts dissipate residual micro-
            # motion; without the clamp the single-point support model's
            # friction-reinjected corner-rock limit cycle sits just above the
            # rest thresholds and never settles).  The 1.5 rad/s band covers
            # the slow corner-walk of a thin body (tip speed < 10 cm/s at a
            # mouse-scale lever); within it the horizontal and angular micro-
            # motion is zeroed outright while the vertical component is left
            # to the contact resolution.
            if _norm(velocity) < 0.1 and _norm(angular_body) < 1.5:
                velocity = (0.0, 0.0, velocity[2] * 0.6)
                angular_body = (0.0, 0.0, 0.0)
        else:
            pre_drag_energy = total_energy()
            drag_factor = 1.0 - 0.15 * dt
            angular_body = (
                angular_body[0] * drag_factor,
                angular_body[1] * drag_factor,
                angular_body[2] * drag_factor,
            )
            drag_step_loss = max(0.0, pre_drag_energy - total_energy())
            lost_drag += drag_step_loss
            flight_drag_loss += drag_step_loss

        # The rest criterion is evaluated on the CONTACT-TIME velocity: after
        # the remainder integration a resting body always carries the
        # per-step gravity increment (g*dt), which would otherwise keep |v|
        # above the threshold forever.  Rest means the body leaves the
        # contact with negligible speed and spin, sustained in contact.
        rest_velocity = velocity
        rest_angular = angular_body
        if in_contact:
            rest_velocity = _scale(velocity, 1.0)
            rest_angular = _scale(angular_body, 1.0)

        elapsed += dt
        record_sample()

        # Energy drift is only meaningful inside a free-flight stretch (contact
        # legitimately exchanges energy through impulses): the total energy
        # must stay constant between the moment the body leaves contact and
        # the next contact.  Semi-implicit Euler has a known free-flight
        # energy sag of -0.5*m*g^2*dt*t (the discrete position lags the exact
        # parabola by 0.5*g*dt*t) and the by-design angular drag removes
        # energy; both are compensated so the check detects genuine anomalies
        # rather than the integrator's documented behavior.
        if in_contact:
            flight_base_energy = None
            flight_drag_loss = 0.0
        else:
            if flight_base_energy is None:
                flight_base_energy = total_energy()
                flight_base_time = elapsed
            corrected = (
                total_energy()
                + flight_drag_loss
                + 0.5 * mass_kg * gravity * gravity * dt * (elapsed - flight_base_time)
            )
            drift = abs(corrected - flight_base_energy) / max(flight_base_energy, 1e-12)
            if drift > drift_max:
                drift_max = drift

        # Rest detection: at rest on the table (contact sustained with the
        # body nearly stationary).  The angular band is 0.5 rad/s: a
        # corner-balanced body rocking below that speed (tip speed under
        # ~3.5 cm/s for a mouse-scale lever) is at rest for screening; the
        # resting-contact clamp drives the micro-motion below the band.
        if in_contact and _norm(rest_velocity) < 0.05 and _norm(rest_angular) < 0.5:
            rest_time += dt
            # Rest must persist 0.4 s (screening convention) before settle.
            if rest_time >= 0.4:
                settled = elapsed
                settled_energy = total_energy()
        else:
            rest_time = 0.0

    if settled is None:
        settled = elapsed
        settled_flag = False
        settled_energy = total_energy()
    record_sample(force=True)

    drift_pct = 100.0 * drift_max
    checks = []
    if energy_creation:
        checks.append(
            {
                "code": "DROP_SIM_ENERGY_CREATION",
                "severity": "error",
                "message": "pre-impact system energy exceeded the release energy budget",
            }
        )
    if drift_pct > 1.0:
        checks.append(
            {
                "code": "DROP_SIM_ENERGY_DRIFT",
                "severity": "error",
                "message": "free-flight total energy drifted {:.2f}%".format(drift_pct),
            }
        )
    if rebound_overspeed:
        checks.append(
            {
                "code": "DROP_SIM_REBOUND_OVERSPEED",
                "severity": "error",
                "message": "post-impact system energy exceeded the pre-impact energy",
            }
        )
    if max_penetration > max(5e-3, min_extent):
        checks.append(
            {
                "code": "DROP_SIM_EXCESSIVE_PENETRATION",
                "severity": "error",
                "message": "peak table penetration {:.4f} m exceeded the body extent".format(
                    max_penetration
                ),
            }
        )
    elif max_penetration > max(1e-3, 0.25 * min_extent):
        checks.append(
            {
                "code": "DROP_SIM_EXCESSIVE_PENETRATION",
                "severity": "warning",
                "message": "peak table penetration {:.4f} m exceeded a quarter of the body extent".format(
                    max_penetration
                ),
            }
        )
    if not settled_flag:
        checks.append(
            {
                "code": "DROP_SIM_DID_NOT_SETTLE",
                "severity": "warning",
                "message": "drop did not come to rest within the {:.1f} s limit".format(
                    max_duration_s
                ),
            }
        )
    energy = {
        "release_j": round(release_energy, 6),
        "first_impact_j": round(first_impact_energy, 6) if first_impact_energy is not None else None,
        "settled_j": round(settled_energy, 6) if settled_energy is not None else None,
        "lost_contact_j": round(lost_contact, 6),
        "lost_drag_j": round(lost_drag, 6),
        "drift_pct": round(drift_pct, 4),
    }
    return {
        "trajectory": trajectory,
        "impacts": impacts,
        "settled_s": settled,
        "settled": settled_flag,
        "energy": energy,
        "checks": checks,
        "initial_position": initial_position,
    }


def simulate(
    mass_kg,
    inertia,
    support,
    height_m,
    surface="concrete",
    drop_count=1,
    test="drop",
    orientation="flat",
    spin_rps=None,
    gravity=GRAVITY_M_S2,
    dt=DT_S,
    max_duration_s=MAX_DURATION_S,
    seed=0,
    unit_seed=None,
    unit_scale=1.0,
    com_offset_m=None,
    mass_scale=1.0,
    inertia_scale=1.0,
    friction_scale=1.0,
    restitution_scale=1.0,
):
    """Run a deterministic multi-drop simulation.

    Drop 0 is the pristine reference (exactly the configured orientation,
    zero lateral offset).  Every later drop gets a deterministic, seeded
    initial-condition variation (tilt jitter, lateral drift, small release
    spin) so repeated drops are unique while staying bit-reproducible for a
    fixed seed and configuration.  ``orientation`` is a mode string ("flat",
    "edge", "corner", "random") or an explicit pose dict
    ``{"quaternion_wxyz": [w, x, y, z]}``; with an explicit pose, drop 0
    uses exactly that orientation and drops 1+ add the seeded jitter on top.

    ``unit_seed`` adds a deterministic manufacturing-tolerance layer: the
    same seed always produces the same unit (mass/inertia/CoM/friction/
    restitution perturbations within engineering bands) and different seeds
    represent different manufactured units.  The explicit ``*_scale``
    parameters multiply the unit-seed draws (the pipeline uses them to inject
    lifecycle degradation).  ``com_offset_m`` is the mesh-origin-to-CoM
    vector used to resolve contact dynamics about the center of mass.

    Returns a dict with the full trajectory, per-drop summaries, impact
    events, energy accounting, physics self-checks, and the simulation model
    used.  Pure stdlib, deterministic for a fixed seed and configuration.
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
            "unit_seed": unit_seed,
            "seed": seed,
        }
    )
    if mass_kg is None or not math.isfinite(mass_kg) or mass_kg <= 0.0:
        raise DropSimulationError("drop_simulation.mass_kg must be positive")
    if not math.isfinite(gravity) or gravity <= 0.0:
        raise DropSimulationError("drop_simulation gravity must be positive and finite")
    if not math.isfinite(dt) or dt <= 0.0:
        raise DropSimulationError("drop_simulation dt must be positive and finite")
    inverse_inertia, inertia_error = _solve_inertia(inertia)
    if inertia_error is not None:
        raise DropSimulationError("drop_simulation inertia: {}".format(inertia_error))
    if not support:
        raise DropSimulationError("drop_simulation support model is empty")

    # Manufacturing-tolerance variation from the unit seed (nominal when the
    # seed is absent), multiplied by any explicit degradation scales.
    if unit_seed is None:
        unit_mass_scale, unit_inertia_scale, unit_com = 1.0, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)
        unit_friction_scale, unit_restitution_scale = 1.0, 1.0
    else:
        unit_mass_scale, unit_inertia_scale, unit_com, unit_friction_scale, unit_restitution_scale = (
            _unit_variation(int(unit_seed) & 0xFFFFFFFF, support, scale=unit_scale)
        )
    if isinstance(inertia_scale, (list, tuple)) and len(inertia_scale) == 3:
        user_inertia_scale = tuple(float(value) for value in inertia_scale)
    else:
        user_inertia_scale = (float(inertia_scale),) * 3
    # Explicit scale parameters are user/lifecycle inputs: physically
    # invalid values (non-finite, negative friction, out-of-band restitution)
    # are REJECTED — silently coercing them produced NaN results, divergent
    # restitution (24x energy), and non-terminating friction.
    for name, value in (
        ("mass_scale", mass_scale),
        ("friction_scale", friction_scale),
        ("restitution_scale", restitution_scale),
        ("unit_scale", unit_scale),
    ):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise DropSimulationError("drop_simulation.{} must be numeric".format(name))
        if not math.isfinite(numeric):
            raise DropSimulationError("drop_simulation.{} must be finite".format(name))
    for axis in range(3):
        try:
            numeric = float(user_inertia_scale[axis])
        except (TypeError, ValueError):
            raise DropSimulationError("drop_simulation.inertia_scale must be numeric")
        if not math.isfinite(numeric) or numeric <= 0.0:
            raise DropSimulationError(
                "drop_simulation.inertia_scale must be positive and finite"
            )
    if not math.isfinite(float(mass_scale)) or float(mass_scale) <= 0.0:
        raise DropSimulationError("drop_simulation.mass_scale must be positive")
    if not math.isfinite(float(friction_scale)) or float(friction_scale) < 0.0:
        raise DropSimulationError("drop_simulation.friction_scale must be non-negative")
    if not math.isfinite(float(restitution_scale)) or not (0.1 <= float(restitution_scale) <= 2.0):
        raise DropSimulationError(
            "drop_simulation.restitution_scale must be within [0.1, 2.0]"
        )
    if not math.isfinite(float(unit_scale)) or float(unit_scale) < 0.0:
        raise DropSimulationError("drop_simulation.unit_scale must be non-negative")
    if com_offset_m is not None:
        for axis in range(3):
            try:
                offset_value = float(com_offset_m[axis])
            except (TypeError, ValueError, IndexError):
                raise DropSimulationError("drop_simulation.com_offset_m must be a 3-vector")
            if not math.isfinite(offset_value):
                raise DropSimulationError("drop_simulation.com_offset_m must be finite")
    effective_mass_scale = float(mass_scale) * unit_mass_scale
    effective_inertia_scale = (
        user_inertia_scale[0] * unit_inertia_scale[0],
        user_inertia_scale[1] * unit_inertia_scale[1],
        user_inertia_scale[2] * unit_inertia_scale[2],
    )
    base_com = (0.0, 0.0, 0.0) if com_offset_m is None else tuple(float(value) for value in com_offset_m)
    effective_com = (
        base_com[0] + unit_com[0],
        base_com[1] + unit_com[1],
        base_com[2] + unit_com[2],
    )
    effective_mass = mass_kg * effective_mass_scale
    effective_inertia = tuple(
        tuple(effective_inertia_scale[axis] * inertia[row][axis] for axis in range(3))
        for row in range(3)
    )
    effective_inverse_inertia, effective_inertia_error = _solve_inertia(effective_inertia)
    if effective_inertia_error is not None:
        raise DropSimulationError(
            "drop_simulation inertia: {}".format(effective_inertia_error)
        )
    effective_restitution_scale = float(restitution_scale) * unit_restitution_scale
    effective_friction = float(friction_scale) * unit_friction_scale
    surface_restitution = SURFACES[config["surface"]]["restitution"]
    surface_friction = SURFACES[config["surface"]]["friction"]
    drop_restitution = max(0.05, min(0.95, surface_restitution * effective_restitution_scale))
    drop_friction = surface_friction * effective_friction

    drops = []
    trajectory = []
    all_impacts = []
    all_checks = []
    drop_interval_s = 0.35
    t_offset = 0.0
    for drop_index in range(config["drop_count"]):
        explicit_quaternion = config.get("orientation_quaternion_wxyz")
        if explicit_quaternion is not None:
            # Explicit pose: drop 0 uses exactly the validated quaternion; no
            # mode mapping (the impact-test corner override included).
            orientation_q = tuple(explicit_quaternion)
        else:
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
        drop_result = _simulate_drop(
            effective_mass,
            effective_inertia,
            effective_inverse_inertia,
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
            effective_com,
            drop_restitution,
            drop_friction,
        )
        drop_trajectory = drop_result["trajectory"]
        impacts = drop_result["impacts"]
        settled = drop_result["settled_s"]
        settled_flag = drop_result["settled"]
        energy = drop_result["energy"]
        checks = drop_result["checks"]
        all_checks.extend(checks)
        peak_speed = max((item["impact_speed_m_s"] for item in impacts), default=0.0)
        peak_energy = max((item["kinetic_energy_j"] for item in impacts), default=0.0)
        peak_raw_energy = max((item["raw_kinetic_energy_j"] for item in impacts), default=0.0)
        actual_orientation = config["orientation"]
        if config["test"] == "impact" and drop_index == 0 and explicit_quaternion is None:
            actual_orientation = "corner"
        # Reproducibility record: the exact initial conditions this drop ran
        # with (world-frame quaternion, body-frame gravity at release, release
        # angular velocity, zero initial velocity, starting pose).
        gravity_body = _quaternion_rotate(
            _conjugate_quaternion(orientation_q), (0.0, 0.0, -1.0)
        )
        if config["test"] == "tumble" and config["spin_rps"]:
            release_angular = _add(
                (0.0, config["spin_rps"] * 2.0 * math.pi, 0.0), initial_angular
            )
        else:
            release_angular = initial_angular
        drops.append(
            {
                "index": drop_index,
                "start_s": round(t_offset, 3),
                "end_s": round(t_offset + settled, 3),
                "settled_s": round(settled, 3),
                "settled": settled_flag,
                "impact_count": len(impacts),
                "peak_impact_speed_m_s": round(peak_speed, 4),
                "peak_kinetic_energy_j": round(peak_energy, 6),
                "peak_raw_kinetic_energy_j": round(peak_raw_energy, 6),
                "orientation": actual_orientation,
                "orientation_quaternion_wxyz": [float(c) for c in orientation_q],
                "gravity_vector_body": [float(c) for c in gravity_body],
                "initial_angular_velocity_rad_s": [float(c) for c in release_angular],
                "initial_velocity_m_s": [0.0, 0.0, 0.0],
                "starting_pose_m": [float(c) for c in drop_result["initial_position"]],
                "tilt_deg": round(tilt_deg, 4),
                "lateral_offset_m": [
                    round(lateral_offset[0], 6),
                    round(lateral_offset[1], 6),
                ],
                "seed": seed,
                "energy": energy,
                "checks": checks,
            }
        )
        for impact in impacts:
            all_impacts.append(
                {
                    "drop": drop_index,
                    "t_s": round(impact["t_s"] + t_offset, 4),
                    "impact_speed_m_s": impact["impact_speed_m_s"],
                    "kinetic_energy_j": impact["kinetic_energy_j"],
                    "raw_kinetic_energy_j": impact["raw_kinetic_energy_j"],
                    "contact_location": impact["contact_location"],
                    "contact_normal": impact["contact_normal"],
                    "contact_point_speed": impact["contact_point_speed"],
                    "tangent_speed": impact["tangent_speed"],
                    "incidence_angle_deg": impact["incidence_angle_deg"],
                    "manifold_size": impact["manifold_size"],
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
    model = {
        "mass_kg": round(effective_mass, 6),
        "inertia_kg_m2": [list(row) for row in effective_inertia],
        "support_model": "mesh_extreme_points",
        "support_point_count": len(support),
        "integrator": "semi_implicit_euler",
        "timestep_s": dt,
        "gravity_m_s2": gravity,
        "surface": config["surface"],
        "restitution": round(drop_restitution, 6),
        "friction": round(drop_friction, 6),
        "com_offset_m": [round(value, 6) for value in effective_com],
        # Reference (drop 0) initial conditions, so the run is replayable from
        # the recorded numbers; per-drop values may differ for drops 1+.
        "orientation_quaternion_wxyz": drops[0]["orientation_quaternion_wxyz"],
        "gravity_vector_body": drops[0]["gravity_vector_body"],
        "initial_angular_velocity_rad_s": drops[0]["initial_angular_velocity_rad_s"],
        "initial_velocity_m_s": [0.0, 0.0, 0.0],
        "starting_pose_m": drops[0]["starting_pose_m"],
        "jitter": {
            "max_tilt_deg": JITTER_MAX_TILT_DEG,
            "max_lateral_fraction": JITTER_MAX_LATERAL_FRACTION,
            "max_initial_spin_rad_s": JITTER_MAX_SPIN_RAD_S,
            "seed": seed,
        },
        "variation": {
            "unit_seed": unit_seed,
            "mass_scale": round(effective_mass_scale, 6),
            "inertia_scale": [round(value, 6) for value in effective_inertia_scale],
            "com_offset_m": [round(unit_com[0] + base_com[0], 6), round(unit_com[1] + base_com[1], 6), round(unit_com[2] + base_com[2], 6)],
            "friction_scale": round(effective_friction, 6),
            "restitution_scale": round(effective_restitution_scale, 6),
        },
    }
    return {
        "config": config,
        "model": model,
        "drops": drops,
        "impacts": all_impacts,
        "checks": all_checks,
        "peak": (
            {
                "drop": peak_overall["drop"],
                "t_s": peak_overall["t_s"],
                "impact_speed_m_s": peak_overall["impact_speed_m_s"],
                "kinetic_energy_j": peak_overall["kinetic_energy_j"],
                "raw_kinetic_energy_j": peak_overall["raw_kinetic_energy_j"],
            }
            if peak_overall is not None
            else None
        ),
        "trajectory": trajectory,
    }
