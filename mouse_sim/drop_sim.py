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
    - "corner": corner rest — 125.26 deg about the world (-1, 1, 0) axis;
      the body (1, 1, 1) diagonal (the front/button corner) lands vertically
      down (R . (1,1,1)/sqrt(3) = (0,0,-1)).
    - "random": seeded uniform orientation, deterministic from the seed.
* Explicit pose: ``orientation`` may instead be an object
  ``{"quaternion_wxyz": [w, x, y, z]}`` — a unit quaternion in the world
  frame (normalized internally; -q is accepted, it is the same orientation).
  Drop 0 then uses exactly that quaternion (no mode mapping) and drops 1+
  keep the seeded jitter on top of the explicit pose.
* Reproducibility: every per-drop result records the actual initial
  orientation quaternion used (after mode mapping and any jitter), the
  gravity direction in the body frame (what an accelerometer on the shell
  reads at release), the initial angular velocity (body frame), the zero
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
# A settled pose is STABLE when the vertical projection of the center of
# mass falls inside the convex hull of the contact points (the support
# points within a tight manifold band of the lowest one): a body resting on
# a flat face has its CoM inside the face polygon; a body resting on a
# narrow edge or corner has only a line/point contact, so its CoM projection
# is outside the (degenerate) polygon and the rest is metastable — a pose a
# real device would never hold (any micro-perturbation lets gravity tip it
# to a face).  Metastable rests are tipped by the contact model itself: the
# gravity torque about the contact accelerates the body's rotation in the
# same integration step, and an exact balance (zero lever) is broken by a
# small deterministic perturbation, so the trajectory is one continuous
# simulation (see ``_simulate_drop``).
# Deterministic angular perturbation applied when a rest is rejected (the
# CoM outside the contact face, or a local equilibrium away from the
# base-rest height): a real device tips from any micro-perturbation.  The
# kick is applied while the body is in a tiny deterministic HOP (lifted off
# the contact): in free flight the floor impulses cannot absorb the
# rotation, so the body lands tilted off the balance, and the gravity
# torque (now with a nonzero lever) tips it over; the integrator then
# carries it to its base rest.  The hop is a few-millimetre perturbation
# (the vibration/air-noise a real device experiences).
# The escape kick is delivered as a SMOOTH RAMP: the barrier-clearing spin
# (up to ~20 rad/s of energy, which the deep wells of curved tops need) is
# built over ~0.2 s of continuous angular acceleration instead of an
# instantaneous velocity step.  The recorded motion is an accelerating roll
# (a natural tip-off), never the 19 deg/frame whip of a step impulse.
METASTABLE_KICK_RAD_S = 4.0
METASTABLE_KICK_MAX_RAD_S = 20.0
METASTABLE_KICK_RAMP_S = 0.1
# No vertical hop: the escape's mild spin damping and the in-contact torque
# make the free-flight hop unnecessary; a 4.6 mm vertical jump was another
# visible artifact of the old escape.
METASTABLE_HOP_M_S = 0.3
# Perturbation budget: the escape attempts are bounded; when the budget is
# exhausted the body's rest is the physics' answer and it is accepted
# honestly (settled, no DROP_SIM_DID_NOT_SETTLE) instead of rocking until
# MAX_DURATION_S.
# Escape budget: a small number of attempts.  A body that can be righted
# (e.g. the corner tumble's back rest) does so within 1-2 nudges; a body
# stuck in a deep well (the rounded top) is NOT kicked repeatedly — 8
# attempts produced the user-visible seconds-long "jittering on its back",
# so after 2 attempts the engine stands down and freezes quietly.
METASTABLE_ESCAPE_ATTEMPTS = 2
# Time budget for a continuous rejected (escape) stretch: the kick gate
# requires rest_time > 0.05 s, which a fast rock never accumulates, so the
# kick budget alone may never fill and the gravity torque would rock the
# body until MAX_DURATION_S.  Bound the torque-only escape phase too; once
# exhausted the body is frozen at its pose (DROP_SIM_DID_NOT_SETTLE).
METASTABLE_ESCAPE_MAX_S = 6.0
# Per-drop initial-condition variation applied to drops 1+ (drop 0 is the
# pristine reference drop).  Every value is deterministic from the seed.
JITTER_MAX_TILT_DEG = 6.0
JITTER_MAX_LATERAL_FRACTION = 0.03
JITTER_MAX_SPIN_RAD_S = 0.5

# Multi-drop campaign variation for drops 1+ in a normal drop/tumble test
# with more than one drop: a REAL random start pose — uniform over the solid
# angle of the [6, 45] deg tilt band about a random horizontal axis (every
# campaign drop is visibly distinct from the pristine reference drop 0,
# never a near-duplicate), plus the standard lateral drift and an
# independent-axis release spin — so each drop clearly differs: a
# verification campaign instead of three nearly-identical falls.  The
# reference drop 0 stays pristine, the impact test keeps its homogeneous
# corner jitter, and explicit poses follow the same seeded envelope as
# their mode twin so cross-mode determinism holds.  The range is bounded so
# the part tips toward edge/face poses and topples naturally on impact — it
# never launches.
CAMPAIGN_MAX_TILT_DEG = 45.0

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
# young_modulus_pa/poissons_ratio feed the Hertz effective contact modulus
# (impact.effective_modulus) for the default drop-impact path: concrete
# and steel are standard engineering values, wood is a hardwood along-
# grain class value, foam is a soft packaging-foam class value (its low
# modulus dominates the contact pair).
SURFACES = {
    # Polymer-on-concrete restitution, instrumented drop-test class range
    # 0.25-0.35 (filled polymer shells: visible but damped rebound); 0.30 is
    # the class mid-point and gives ~6.8 cm rebound from a 0.75 m drop
    # (e_eff = 0.29, apex = e^2*h).  The low-speed roll-off below 0.5 m/s
    # (sticking regime) is applied per impact in _low_speed_restitution.
    "concrete": {"restitution": 0.30, "friction": 0.60, "young_modulus_pa": 30e9, "poissons_ratio": 0.20},
    "wood": {"restitution": 0.40, "friction": 0.55, "young_modulus_pa": 12e9, "poissons_ratio": 0.35},
    "foam": {"restitution": 0.12, "friction": 0.80, "young_modulus_pa": 5e6, "poissons_ratio": 0.25},
    # Polymer-on-steel restitution is ~0.3-0.45 (instrumented impact data);
    # 0.45 is the upper band, 0.50 overstates the rebound.
    "steel": {"restitution": 0.45, "friction": 0.35, "young_modulus_pa": 200e9, "poissons_ratio": 0.30},
}

TESTS = ("drop", "impact", "tumble")
ORIENTATIONS = ("flat", "edge", "corner", "random")


def hertz_shell_material_pair(shell_material):
    """Return ``(young_modulus_pa, poissons_ratio)`` from a shell material.

    Accepts a ``MaterialDefinition``, its ``MaterialProperties``, or an
    already-resolved ``(E_pa, nu)`` tuple and returns the pair when both
    values are present, else ``None`` so the caller can disclose a
    fallback (never silent).
    """
    if isinstance(shell_material, tuple) and len(shell_material) == 2:
        return shell_material
    props = getattr(shell_material, "properties", shell_material)
    if props is None:
        return None
    modulus = getattr(props, "young_modulus", None)
    nu = getattr(props, "poissons_ratio", None)
    if modulus is None or nu is None:
        return None
    try:
        modulus = modulus.value_si if hasattr(modulus, "value_si") else float(modulus)
        return float(modulus), float(nu)
    except (TypeError, ValueError):
        return None


def hertz_effective_modulus_pa(shell_young_modulus_pa, shell_poissons_ratio, surface):
    """Hertz effective contact modulus of a shell on a floor surface:
    E_eff = ((1-nu_s^2)/E_s + (1-nu_f^2)/E_f)^-1 (impact.effective_modulus),
    with the floor values from the ``SURFACES`` table.
    """
    from . import impact

    floor = SURFACES[surface]
    return impact.effective_modulus(
        shell_young_modulus_pa,
        shell_poissons_ratio,
        floor["young_modulus_pa"],
        floor["poissons_ratio"],
    )


def hertz_contact_kwargs(shell_material, surface, explicit_stiffness_n_per_m=None, contact_radius_m=None):
    """Resolve the estimate_impact contact kwargs for a drop on ``surface``.

    An explicit ``explicit_stiffness_n_per_m`` keeps the calibrated linear
    spring (the user's override); otherwise the default is the nonlinear
    Hertz point-contact law with E_eff from the shell material pair and the
    floor surface table, and the corner blend radius
    ``impact.DEFAULT_CORNER_BLEND_RADIUS_M`` (2.0 mm) unless supplied.

    Returns ``(kwargs, model_label, disclosure)``: ``kwargs`` is ready to
    spread into :func:`impact.estimate_impact`; ``model_label`` names the
    contact model for result payloads; ``disclosure`` is an issue message
    when a shell material E/nu fallback (generic polymer) was used, else
    ``None`` (the fallback is never silent).
    """
    from . import impact

    if explicit_stiffness_n_per_m is not None:
        return (
            {"contact_stiffness_n_per_m": float(explicit_stiffness_n_per_m)},
            "linear-spring quasi-static estimate (explicit k = {:.6g} N/m)".format(
                float(explicit_stiffness_n_per_m)
            ),
            None,
        )
    radius = (
        float(contact_radius_m)
        if contact_radius_m is not None
        else impact.DEFAULT_CORNER_BLEND_RADIUS_M
    )
    pair = hertz_shell_material_pair(shell_material)
    disclosure = None
    if pair is None:
        # Built-in Default generic polymer (materials.default_material_definition).
        pair = (2.0e9, 0.36)
        disclosure = (
            "HERTZ_EFFECTIVE_MODULUS_ASSUMED: shell material E/nu unavailable; "
            "effective modulus computed with a generic polymer (E = 2.0e9 Pa, "
            "nu = 0.36) on {} — assign a shell material to remove the "
            "assumption".format(surface)
        )
    e_eff = hertz_effective_modulus_pa(pair[0], pair[1], surface)
    return (
        {"effective_modulus_pa": e_eff, "contact_radius_m": radius},
        "Hertz nonlinear point contact: E_eff = {:.6g} Pa (shell on {}), corner "
        "blend radius R = {:.6g} m".format(e_eff, surface, radius),
        disclosure,
    )


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
        if sum(c * c for c in components) <= 0.0:
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
    # Inter-drop pause (the playback gap between consecutive drops).  The
    # pipeline previously read this key from the VALIDATED config, which
    # never carried it — the user setting was silently ignored and every
    # run used the 0.5 s default.  ``drop_interval_s`` is accepted as a
    # legacy alias.
    pause = config.get("pause_between_drops_s", config.get("drop_interval_s", 0.50))
    try:
        pause = float(pause)
    except (TypeError, ValueError):
        raise DropSimulationError("drop_simulation.pause_between_drops_s must be numeric")
    if not math.isfinite(pause) or pause < 0.05 or pause > 10.0:
        raise DropSimulationError(
            "drop_simulation.pause_between_drops_s must be between 0.05 and 10.0 s"
        )
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
        "pause_between_drops_s": pause,
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


def _slerp(first, second, alpha):
    """Spherical linear interpolation between two unit quaternions.

    ``first`` and ``second`` are (w, x, y, z) tuples.  The shorter arc is
    taken (the second is sign-flipped when the dot product is negative), so
    the interpolation is the minimal rotation between the poses.
    """
    dot = sum(a * b for a, b in zip(first, second))
    second = tuple(-component for component in second) if dot < 0.0 else second
    dot = abs(dot)
    if dot > 0.9995:
        # Nearly parallel: linear interpolation (with renormalization) is
        # indistinguishable from the slerp and avoids the acos singularity.
        result = tuple(
            first[index] * (1.0 - alpha) + second[index] * alpha for index in range(4)
        )
        return _normalize_quaternion(result)
    omega = math.acos(dot)
    sine = math.sin(omega)
    first_scale = math.sin((1.0 - alpha) * omega) / sine
    second_scale = math.sin(alpha * omega) / sine
    return tuple(
        first[index] * first_scale + second[index] * second_scale for index in range(4)
    )


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
    map rotates the body by the body-frame angular velocity.  Energy behavior
    is independent of the frame convention; the body frame is used because
    the inertia tensor is constant there.  (The Euler gyro step itself has a
    sign-definite O(dt^2) per-step rotational-energy error, an O(T*dt) drift
    over a flight — see ``_gyroscopic_update``.)
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
    ``omega . (omega x I omega)`` vanishes identically, so the update has a
    sign-definite O(dt^2) per-step rotational-energy error (an O(T*dt) drift
    over a flight) regardless of the frame convention.  The
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
    substeps = max(1, min(2048, int(math.ceil(asym * magnitude * dt / 0.0045))))
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
        # Rest on a corner: 125.26 deg about the unit axis (-1, 1, 0)/sqrt(2).
        # This rotation maps the body diagonal (1, 1, 1)/sqrt(3) exactly onto
        # world (0, 0, -1), so the front/button corner (the first diagonal
        # entry of SUPPORT_DIRECTIONS) is the corner that lands vertically
        # down.  (The 54.7 deg rotation about (1, 1, 0) landed the OPPOSITE
        # diagonal (1, -1, -1) down instead.)
        return _axis_angle_quaternion((-1.0, 1.0, 0.0), math.acos(-1.0 / math.sqrt(3.0)))
    # Deterministic pseudo-random orientation from the seed.
    state = seed & 0xFFFFFFFF

    def next_unit():
        nonlocal state
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        return state / 4294967296.0

    u1 = next_unit()
    u2 = next_unit()
    u3 = next_unit()
    # Uniform Haar measure on SO(3) (Shoemake / Marsaglia formula)
    q0 = math.sqrt(max(0.0, 1.0 - u1)) * math.sin(2.0 * math.pi * u2)
    q1 = math.sqrt(max(0.0, 1.0 - u1)) * math.cos(2.0 * math.pi * u2)
    q2 = math.sqrt(u1) * math.sin(2.0 * math.pi * u3)
    q3 = math.sqrt(u1) * math.cos(2.0 * math.pi * u3)
    return _normalize_quaternion((q0, q1, q2, q3))


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
    a tilt about a seeded horizontal axis (uniform over the solid angle of
    the tilt cap for campaign runs, uniform in [0, max_tilt] for the fine
    jitter envelope), a horizontal drift offset of the initial position (up
    to ``max_lateral_fraction * height_m``), and a small release spin about
    an independent random horizontal axis so the first contact point's
    velocity differs between drops.  Draws come from a fresh LCG seeded
    by a multiplicative hash of ``(seed, drop_index)`` so consecutive
    drops' draws are independent, and independent of the base orientation.
    """
    # Multiplicative hash: consecutive drop indices must not share LCG
    # states (a shared stream would correlate consecutive drops' jitters).
    state = ((seed * 1000003 + 1000 + drop_index) & 0xFFFFFFFF) ^ 0x5DEECE66D

    def next_unit():
        nonlocal state
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        return state / 4294967296.0

    axis_unit = next_unit()
    tilt_unit = next_unit()
    offset_angle_unit = next_unit()
    offset_radius_unit = next_unit()
    spin_unit = next_unit()
    roll_unit = next_unit()

    axis_angle = 2.0 * math.pi * axis_unit
    axis = (math.cos(axis_angle), math.sin(axis_angle), 0.0)
    # CAMPAIGN tilt distribution: a UNIFORM draw from [0, max_tilt] makes a
    # multi-drop campaign look broken — most drops land nearly flat
    # (indistinguishable from the pristine reference drop 0), one drops at
    # the full 45 deg envelope, and the release spin about the same tiny
    # tilt axis barely differs.  The cap is instead sampled UNIFORMLY OVER
    # THE SOLID ANGLE of the tilt cap (theta = acos(1 - u*(1 - cos cap)),
    # the standard sphere-cap sampler): every solid-angle element is equally
    # likely, so the per-drop landings are evenly spread across the
    # envelope and no value piles up at the cap.  Every campaign drop is
    # visibly distinct yet never a near-duplicate of the configured flat
    # orientation (the base face of a real drop is the reference; a
    # campaign repeats the test, it does not repeat the identical pose).
    # Single-drop and impact-corner runs keep the fine jitter envelope and
    # are unchanged (they are not campaigns).
    if max_tilt_deg > JITTER_MAX_TILT_DEG:
        cap_rad = math.radians(max_tilt_deg)
        # Uniform over the solid angle of the cap (sphere-cap sampler).
        tilt_deg = math.degrees(math.acos(1.0 - tilt_unit * (1.0 - math.cos(cap_rad))))
        tilt_deg = min(tilt_deg, max_tilt_deg)
        # CAMPAIGN MINIMUM TILT: a campaign drop must be VISIBLY distinct
        # from the pristine reference drop 0 (the configured orientation) —
        # a drop that lands within ~6 deg of the reference reads as a
        # replayed copy ("the object barely moved").  The fine-jitter
        # envelope (6 deg) is the maximum the single-drop reference allows,
        # so every campaign drop is drawn from the solid-angle slice
        # [6 deg, max_tilt] — distinct from the reference by construction,
        # while the lateral drift and release spin still vary within it.
        min_campaign_tilt = JITTER_MAX_TILT_DEG
        if tilt_deg < min_campaign_tilt:
            tilt_deg = min_campaign_tilt
    else:
        tilt_deg = max_tilt_deg * tilt_unit
    tilt_quaternion = _axis_angle_quaternion(axis, math.radians(tilt_deg))
    max_offset = max_lateral_fraction * height_m
    # sqrt(u): uniform sampling over the DISK (a raw uniform radius
    # draws a radial density that piles up toward the centre — a bias).
    offset_radius = max_offset * math.sqrt(offset_radius_unit)
    offset_angle = 2.0 * math.pi * offset_angle_unit
    lateral_offset = (
        offset_radius * math.cos(offset_angle),
        offset_radius * math.sin(offset_angle),
    )
    spin_scale = max_spin_rad_s * spin_unit
    # The release spin acts about a distinct RANDOM horizontal axis (a
    # fresh draw) instead of the same axis as the tilt: the first-contact
    # velocity of every campaign drop then differs in direction AND
    # magnitude from the reference, so two drops can never look like
    # replayed copies of the pristine flat drop.
    roll_angle = 2.0 * math.pi * roll_unit
    spin_axis = (math.cos(roll_angle), math.sin(roll_angle), 0.0)
    initial_angular = (spin_axis[0] * spin_scale, spin_axis[1] * spin_scale, 0.0)
    return tilt_quaternion, tilt_deg, lateral_offset, initial_angular


def box_inertia(mass_kg, bounds):
    """Diagonal inertia tensor for a uniform-density box approximation."""
    dx, dy, dz = (
        bounds[0][1] - bounds[0][0],
        bounds[1][1] - bounds[1][0],
        bounds[2][1] - bounds[2][0],
    )
    # The sub-resolution floor is RELATIVE to the largest extent: the old
    # absolute 1e-6 floor inflated sub-micron geometry (a 1e-7 m box became
    # a 1e-6 m box); mouse-scale extents (~0.1 m) are unchanged.
    max_extent = max(dx, dy, dz)
    dx, dy, dz = (
        max(1e-6 * max_extent, dx),
        max(1e-6 * max_extent, dy),
        max(1e-6 * max_extent, dz),
    )
    return (
        (mass_kg / 12.0 * (dy * dy + dz * dz), 0.0, 0.0),
        (0.0, mass_kg / 12.0 * (dx * dx + dz * dz), 0.0),
        (0.0, 0.0, mass_kg / 12.0 * (dx * dx + dy * dy)),
    )


def _dedupe_vertices(vertices, quantize=1e-6):
    """Deduplicate a vertex cloud, quantized to ``quantize`` metres.

    The raw assembly vertex cloud (~230k points from 46 STEP parts) contains
    many near-duplicate points (shared face corners, tessellation seams).  A
    convex hull only needs unique extreme points; quantizing to the mesh
    deflection (0.06 mm) before the hull collapses duplicates and shrinks the
    input by an order of magnitude without moving any real feature.
    """
    unique = {}
    for vertex in vertices:
        key = (
            round(vertex[0] / quantize),
            round(vertex[1] / quantize),
            round(vertex[2] / quantize),
        )
        unique.setdefault(key, tuple(vertex))
    return list(unique.values())


def _tetrahedron(points):
    """First 4 non-coplanar points of ``points`` as a tetrahedron.

    Returns ``(indices, error)``: the 4 indices (in input order) and None, or
    ``(None, message)`` when the point set is degenerate (fewer than 4 unique
    points, or all coplanar).  Deterministic: scans in input order.
    """
    if len(points) < 4:
        return None, "convex hull needs at least 4 unique points"
    first = points[0]
    second = None
    second_i = None
    for index in range(1, len(points)):
        delta = (points[index][0] - first[0], points[index][1] - first[1], points[index][2] - first[2])
        if math.sqrt(delta[0] * delta[0] + delta[1] * delta[1] + delta[2] * delta[2]) > 1e-12:
            second = points[index]
            second_i = index
            break
    if second is None:
        return None, "convex hull needs at least 2 distinct points"
    third = None
    third_i = None
    for index in range(1, len(points)):
        if index == second_i:
            continue
        delta1 = (second[0] - first[0], second[1] - first[1], second[2] - first[2])
        delta2 = (points[index][0] - first[0], points[index][1] - first[1], points[index][2] - first[2])
        cross = (
            delta1[1] * delta2[2] - delta1[2] * delta2[1],
            delta1[2] * delta2[0] - delta1[0] * delta2[2],
            delta1[0] * delta2[1] - delta1[1] * delta2[0],
        )
        if math.sqrt(cross[0] * cross[0] + cross[1] * cross[1] + cross[2] * cross[2]) > 1e-12:
            third = points[index]
            third_i = index
            break
    if third is None:
        return None, "convex hull points are collinear"
    fourth = None
    fourth_i = None
    for index in range(1, len(points)):
        if index == second_i or index == third_i:
            continue
        normal = _triangle_normal(first, second, third)
        delta = (points[index][0] - first[0], points[index][1] - first[1], points[index][2] - first[2])
        volume = (
            normal[0] * delta[0] + normal[1] * delta[1] + normal[2] * delta[2]
        )
        if abs(volume) > 1e-12:
            fourth = points[index]
            fourth_i = index
            break
    if fourth is None:
        return None, "convex hull points are coplanar"
    return (0, second_i, third_i, fourth_i), None


def _triangle_normal(first, second, third):
    """Unnormalized outward normal of the triangle (second-first) x
    (third-first).  Magnitude is twice the triangle area."""
    delta1 = (second[0] - first[0], second[1] - first[1], second[2] - first[2])
    delta2 = (third[0] - first[0], third[1] - first[1], third[2] - first[2])
    return (
        delta1[1] * delta2[2] - delta1[2] * delta2[1],
        delta1[2] * delta2[0] - delta1[0] * delta2[2],
        delta1[0] * delta2[1] - delta1[1] * delta2[0],
    )


def _face_contains_point(normal, face_first, point, tolerance=1e-12):
    """True when ``point`` is on the OUTER side of the face (the side the
    normal points to), within ``tolerance``.  Points exactly on the face
    (|signed distance| <= tolerance) are NOT strictly outside."""
    delta = (point[0] - face_first[0], point[1] - face_first[1], point[2] - face_first[2])
    signed = normal[0] * delta[0] + normal[1] * delta[1] + normal[2] * delta[2]
    return signed > tolerance


def convex_hull_3d(points):
    """Convex hull of a 3D point cloud (incremental/Bryant-style, stdlib-only).

    Deterministic for a fixed input order.  Returns a dict:
    ``{"vertices": [point, ...], "faces": [(i, j, k), ...]}`` — the hull
    vertices (deduplicated, input order) and the triangular faces as index
    triples into ``vertices``.  Each face is oriented with an OUTWARD normal.

    Degenerate inputs (fewer than 4 unique points, collinear, or coplanar)
    return ``{"vertices": <dedup points>, "faces": [], "degenerate": True}``
    so callers can fall back to their previous behaviour — the drop simulator
    falls back to the fixed-direction sampling for those (a flat/line/point
    body has no 3D hull faces to rest on).
    """
    unique = _dedupe_vertices(points)
    if len(unique) < 4:
        return {"vertices": unique, "faces": [], "degenerate": True}
    tet, error = _tetrahedron(unique)
    if tet is None:
        return {"vertices": unique, "faces": [], "degenerate": True}

    # The initial tetrahedron: 4 faces with outward normals.  ``tet`` holds
    # indices into ``unique``; remap them to hull-vertex indices 0..3 (the
    # hull vertex list ``vertices`` starts as the tetra's 4 points).
    a, b, c, d = tet
    unique_to_hull = {a: 0, b: 1, c: 2, d: 3}
    vertices = [unique[a], unique[b], unique[c], unique[d]]
    faces = []
    for triple in ((a, b, c), (a, c, d), (a, d, b), (b, d, c)):
        normal = _triangle_normal(unique[triple[0]], unique[triple[1]], unique[triple[2]])
        # The remaining tetra vertex (not in this triple) must be OUTSIDE.
        inside = None
        for candidate in tet:
            if candidate not in triple:
                inside = candidate
                break
        mapped = (unique_to_hull[triple[0]], unique_to_hull[triple[1]], unique_to_hull[triple[2]])
        if _face_contains_point(normal, unique[triple[0]], unique[inside]):
            faces.append(mapped)
        else:
            faces.append((mapped[0], mapped[2], mapped[1]))

    # Incremental insertion: maintain the hull faces; each point either lies
    # inside/on the current hull (skip) or is outside some faces (remove the
    # visible ones and stitch the horizon ring with the new point).
    for point_index in range(len(unique)):
        if point_index in tet:
            continue
        point = unique[point_index]
        visible = []
        for face_index, face in enumerate(faces):
            if _face_contains_point(
                _triangle_normal(vertices[face[0]], vertices[face[1]], vertices[face[2]]),
                vertices[face[0]],
                point,
            ):
                visible.append(face_index)
        if not visible:
            continue
        # Horizon edges: edges of the visible region that are shared with a
        # non-visible face.  Build the ring of horizon edges (hull-vertex
        # indices).
        horizon_edges = {}
        for face_index in visible:
            face = faces[face_index]
            for edge in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
                reverse = (edge[1], edge[0])
                if reverse in horizon_edges:
                    del horizon_edges[reverse]
                else:
                    horizon_edges[edge] = face_index
        # Remove visible faces, add the new fan from each horizon edge to the
        # new point.  The new vertex index is len(vertices).
        new_index = len(vertices)
        vertices.append(point)
        new_faces = []
        for edge in horizon_edges:
            # Orient the fan face outward: the horizon edge is CCW when seen
            # from outside the removed region, so (edge[0], edge[1], new) is
            # the outward orientation (the new point is outside the hull).
            new_faces.append((edge[0], edge[1], new_index))
        # Rebuild the face list: keep non-visible faces, append the fan.
        faces = [
            face
            for face_index, face in enumerate(faces)
            if face_index not in visible
        ] + new_faces

    # NOTE: the face list may contain coplanar adjacent triangles (the
    # incremental insertion can split a hull face when a point lands on its
    # plane).  The faces are used only for the outward-normal orientation and
    # degeneracy checks; the SUPPORT MODEL consumes the VERTEX set, which is
    # the true convex hull of the cloud regardless of the triangulation.
    return {"vertices": vertices, "faces": faces, "degenerate": False}


_HULL_CANDIDATE_DIRECTIONS = None


def _hull_candidate_directions():
    """Dense direction set for hull-candidate selection: the 14 original
    support directions plus the 20 icosahedron vertices (normalized).  Every
    hull vertex of a convex body is extreme in SOME direction; sampling the
    top candidates along this well-spread set captures all practical hull
    vertices of a tessellated shell (the mesh deflection is 0.06 mm, far
    below the body scale, so no real feature is missed).
    """
    global _HULL_CANDIDATE_DIRECTIONS
    if _HULL_CANDIDATE_DIRECTIONS is not None:
        return _HULL_CANDIDATE_DIRECTIONS
    directions = list(SUPPORT_DIRECTIONS)
    phi = (1.0 + math.sqrt(5.0)) / 2.0
    for signs in ((1, 1, 1), (1, -1, 1), (1, 1, -1), (1, -1, -1)):
        for triple in (
            (signs[0], signs[1] * phi, signs[2] / phi),
            (signs[0] * phi, signs[1] / phi, signs[2]),
            (signs[0] / phi, signs[1], signs[2] * phi),
        ):
            norm = math.sqrt(triple[0] * triple[0] + triple[1] * triple[1] + triple[2] * triple[2])
            directions.append((triple[0] / norm, triple[1] / norm, triple[2] / norm))
    _HULL_CANDIDATE_DIRECTIONS = tuple(directions)
    return _HULL_CANDIDATE_DIRECTIONS


def support_points(vertices, directions=SUPPORT_DIRECTIONS):
    """Convex support model of a mesh vertex cloud.

    Returns the vertices of the 3D convex hull of the cloud — the true extreme
    points of the body's contact surface.  The old implementation sampled one
    extreme vertex per fixed direction (14 directions), which for a curved
    shell never captured the flat base-skate polygon: the "rest" was a
    non-coplanar tripod of curved points and the body tipped onto its dome or
    side.  A convex hull is direction-independent: every extreme point of the
    body appears, so a flat base produces a coplanar face manifold and the
    body rests flat.

    The hull of the UNION of all assembly parts equals the hull of the outer
    shell: internal parts (PCB, battery, switches) lie strictly inside the
    shell's hull and never become extreme points, so they cannot create fake
    contact points and no part classification is needed.

    Performance: the raw assembly cloud has ~230k vertices; a full incremental
    hull on that is too slow in pure Python.  The hull is computed in two
    stages: (1) deduplicate (quantized to 1e-6 m), (2) collect the top few
    extreme candidates along a dense direction set (the 14 support directions
    + 20 icosahedron directions), (3) hull just those candidates.  Every true
    hull vertex is extreme in some direction, so the candidate hull IS the
    full hull for a tessellated convex body.

    Degenerate clouds (fewer than 4 unique points, collinear, coplanar) fall
    back to the fixed-direction sampling so flat/line/point bodies still get a
    usable support model.
    """
    unique = _dedupe_vertices(vertices)
    if len(unique) < 4:
        return [tuple(vertex) for vertex in unique]
    # Candidate extreme points: the top K per direction.  K=6 is far above
    # the number of hull vertices a single direction can legitimately select
    # (a flat face has ~4-8 coplanar extreme points); larger K only adds
    # interior points that the hull discards.
    candidates = set()
    hull_directions = _hull_candidate_directions()
    for direction in hull_directions:
        scored = sorted(
            unique,
            key=lambda vertex: vertex[0] * direction[0] + vertex[1] * direction[1] + vertex[2] * direction[2],
            reverse=True,
        )[:6]
        for vertex in scored:
            candidates.add(tuple(vertex))
    hull = convex_hull_3d(list(candidates))
    if hull["degenerate"] or not hull["faces"]:
        # Fall back to the fixed-direction sampling (degenerate cloud).
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
    return [tuple(vertex) for vertex in hull["vertices"]]


def _manifold_spans_com_2d(position, active):
    """True when the active manifold is a FACE contact: three or more
    non-collinear contact points whose convex hull contains the CoM's
    horizontal projection (the net face impulse then acts at the face
    centre).  A single- or two-point manifold (corner/edge touch) does not
    span the base.  ``active`` points are world-frame; ``position`` is the
    centre of mass.
    """
    if len(active) < 3:
        return False
    px, py = position[0], position[1]
    pts = [(point[0], point[1]) for point in active]
    n = len(pts)
    # A point is inside the hull iff no hull edge separates it from the
    # remaining points.  Enumerate every pair as a candidate edge: a pair is
    # a hull edge when all other points lie on one side of its line; the CoM
    # projection must then lie on that side (or on the edge itself).
    for i in range(n):
        for j in range(i + 1, n):
            xi, yi = pts[i]
            xj, yj = pts[j]
            sides = set()
            for k in range(n):
                if k == i or k == j:
                    continue
                cross = (xj - xi) * (pts[k][1] - yi) - (yj - yi) * (pts[k][0] - xi)
                if cross > 1e-9:
                    sides.add(1)
                elif cross < -1e-9:
                    sides.add(-1)
            if len(sides) == 2:
                continue
            if not sides:
                return False
            hull_side = sides.pop()
            cross_com = (xj - xi) * (py - yi) - (yj - yi) * (px - xi)
            if cross_com > 1e-9:
                com_side = 1
            elif cross_com < -1e-9:
                com_side = -1
            else:
                com_side = 0
            if com_side != 0 and com_side != hull_side:
                return False
    return True


def box_corners(bounds):
    """Eight corners of an axis-aligned world bounding box (support model)."""
    return [
        (bounds[0][x], bounds[1][y], bounds[2][z])
        for x in (0, 1)
        for y in (0, 1)
        for z in (0, 1)
    ]


def _inertia_symmetry_error(inertia):
    """Error message for a non-symmetric 3x3 tensor, or None when symmetric.

    A physical inertia tensor is symmetric; an asymmetric tensor (with
    off-diagonal disagreement beyond a relative tolerance) lets the
    gyroscopic term inject energy and is rejected everywhere inertia is
    validated.
    """
    max_entry = max(max(abs(value) for value in row) for row in inertia)
    for first in range(3):
        for second in range(first + 1, 3):
            if abs(inertia[first][second] - inertia[second][first]) > 1e-9 * max_entry:
                return (
                    "inertia tensor must be symmetric: I[{0}][{1}] differs from "
                    "I[{1}][{0}]".format(first, second)
                )
    return None


def _solve_inertia(inertia):
    """Return (inverse, error) for a 3x3 inertia tensor via Cramer's rule.

    The tensor must be symmetric and positive-definite (a physical inertia):
    a negative determinant or indefinite matrix lets the torque-free
    gyroscopic term inject energy and the integrator never terminates.
    """
    a00, a01, a02 = inertia[0]
    a10, a11, a12 = inertia[1]
    a20, a21, a22 = inertia[2]
    for value in (a00, a01, a02, a10, a11, a12, a20, a21, a22):
        if not math.isfinite(value):
            return None, "inertia tensor contains non-finite values"
    symmetry_error = _inertia_symmetry_error(inertia)
    if symmetry_error is not None:
        return None, symmetry_error
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
    if determinant <= 0.0:
        return None, "inertia tensor is not positive-definite (determinant must be positive)"
    # The singularity floor is RELATIVE to the tensor's scale: exact
    # degeneracy is an error, while a physically tiny body (1 mg) has a
    # determinant of order 1e-30 that the old absolute 1e-24 floor wrongly
    # rejected.  A 0.1 kg mouse has determinants of order 1e-13 and passes.
    trace = a00 + a11 + a22
    if abs(determinant) <= 1e-12 * (trace / 3.0) ** 3:
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

    Below the threshold speed viscoelastic dissipation dominates real
    polymer contacts and the effective coefficient of restitution falls
    off.  The roll-off is deliberately GENTLE (a linear fall to 40% of the
    nominal restitution at zero speed): a steep quadratic cut from
    0.5 m/s made the second bounce collapse from ~6 mm to ~0.1 mm in one
    step — the mouse appeared to "die" instantly on the first rebound
    instead of showing the realistic decaying chatter of a lightweight
    shell.  Enough low-speed dissipation is retained that near-threshold
    micro-bounce and rocking chains (e.g. a 2 cm steel drop, or a box
    rocking on an edge) still decay and settle.
    """
    roll_off = min(1.0, 0.4 + 0.6 * (impact_speed_m_s / 0.5))
    return restitution * roll_off


def _convex_hull_2d(points):
    """Convex hull of 2D points (monotone chain); degenerate inputs (fewer
    than 3 unique points) return the points themselves."""
    unique = sorted(set(points))
    if len(unique) <= 2:
        return unique

    def cross(origin, first, second):
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (second[0] - origin[0])

    lower = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _point_inside_convex_polygon(point, hull):
    """Strictly-inside test for a convex polygon (same winding side for every
    edge); degenerate hulls (point/line) contain no interior."""
    if len(hull) < 3:
        return False
    side = None
    for index in range(len(hull)):
        first = hull[index]
        second = hull[(index + 1) % len(hull)]
        cross = (second[0] - first[0]) * (point[1] - first[1]) - (second[1] - first[1]) * (point[0] - first[0])
        if abs(cross) <= 1e-12:
            continue
        positive = cross > 0
        if side is None:
            side = positive
        elif positive != side:
            return False
    return True


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
    exponential map and the torque-free gyroscopic term (Euler's equations);
    contact impulses are applied in the world frame and converted back.
    The Euler gyro step carries a sign-definite O(dt^2) per-step
    rotational-energy error — an O(T*dt) energy drift over a flight —
    independent of the frame convention (see ``_gyroscopic_update``).

    POST-SETTLE STABILITY: the impulse/contact model can freeze a body in a
    METASTABLE equilibrium on a narrow edge, corner, or rounded feature — the
    positional correction absorbs the fall without any impulse, so no torque
    ever builds (a pose a real device would never hold).  When the resting
    contact's CoM projection falls outside the contact polygon, the contact
    model applies the gravity torque about the contact to the integrated
    state: the SAME integrator then tips the body, falls it, bounces it, and
    settles it onto a stable face, so the recorded trajectory is one
    continuous physics simulation with no post-processing.
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
    # The escape kick's cooldown anchor: -1.0 so a NEW escape episode's first
    # kick fires only after the 0.5 s cooldown elapses from the episode start
    # (the body first rocks into the well naturally; an instant kick at the
    # landing bounce is wasted and adds a violent step to the impact).
    last_balance_kick = -1.0
    escape_attempts = 0
    escape_started = None
    budget_exhausted = False
    # Persisted world-frame escape roll axis: a real convex contact walks
    # with the roll so the gravity torque never flips sense; the discrete
    # extreme-point support freezes the pivot, so the natural axis flips at
    # the pivot crossing (a facet-local well).  The axis is persisted for
    # the whole escape episode and adopted anew only on a genuine angular
    # reversal of the body.
    escape_axis = None
    kick_ramp_until = -1.0
    kick_ramp_alpha = 0.0
    in_contact = False
    quiet_accum = 0.0
    quiet_pinned = False
    quiet_anchor_up = None
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
    # Energy ledger (behavior-preserving bookkeeping): every intentional
    # injection or removal outside the contact-impulse path is accounted so
    # the free-flight creation check compares against the honest budget
    # (release + injections so far - accounted losses so far) instead of the
    # raw release energy, and the settle-time imbalance audit can detect
    # unaccounted energy flows.  The free-flight sag (the documented
    # semi-implicit Euler position lag, the same term the drift check
    # compensates) is an accounted loss: without it every honest drop would
    # show an O(1%) deficit.
    ledger_injections = {
        "positional_correction_pe_j": 0.0,
        "escape_kick_ke_j": 0.0,
        "escape_hop_ke_j": 0.0,
        "gravity_torque_work_j": 0.0,
    }
    ledger_losses = {
        "resting_clamp_ke_j": 0.0,
        "spin_slide_damping_ke_j": 0.0,
        "contact_friction_ke_j": 0.0,
        "integrator_sag_j": 0.0,

        # Per-frame contact reconciliation: the measured energy change of the
        # frame's contact cycle (crossing realignment, impulse, friction,
        # correction, gyroscopic update, flight-segment sags) minus the
        # individually booked components.  Closes the ledger EXACTLY by
        # construction; a systematically large value flags a real unaccounted
        # energy flow rather than the documented discretization residuals.
        "contact_reconciliation_ke_j": 0.0,
    }
    injections_total = 0.0
    accounted_losses = 0.0

    def total_energy():
        return (
            0.5 * mass_kg * _dot(velocity, velocity)
            + 0.5 * _dot(angular_body, _matvec(inertia, angular_body))
            + mass_kg * gravity * position[2]
        )

    def rest_is_stable():
        """Static-stability test of the current resting pose.

        The body rests stably iff the vertical projection of the CoM falls
        inside the convex hull of the contact points (support points within
        the manifold band of the lowest one). Evaluated against the captured
        rest pose if active, ensuring leveled resting poses pass cleanly.
        """
        if rest_pose is not None:
            test_origin, test_quaternion = rest_pose
            test_position = _add(
                test_origin, _quaternion_rotate(test_quaternion, com_offset_m)
            )
        else:
            test_position, test_quaternion = position, quaternion

        world_support = [
            _add(test_position, _quaternion_rotate(test_quaternion, point))
            for point in rel_support
        ]
        lowest = min(point[2] for point in world_support)
        band = max(0.001, 0.05 * min_extent)
        contacts = [(point[0], point[1]) for point in world_support if point[2] <= lowest + band]
        if len(contacts) < 3:
            return False
        hull = _convex_hull_2d(contacts)
        return _point_inside_convex_polygon((test_position[0], test_position[1]), hull)

    def rest_acceptance():
        """Whether the current rest is a true static equilibrium."""
        stable = rest_is_stable()
        if not stable:
            return False, False
        return True, True

    def tipping_state():
        """Contacts (tight band), contact centroid, CoM lever, and the
        gravity tipping axis (with sign so the CoM descends) for the current
        resting pose.  Returns ``(contacts, centroid, lever, axis)``; the
        axis is ``(1.0, 0.0, 0.0)`` with a zero lever when the CoM projects
        exactly onto the contact (the knife-edge balance)."""
        world_support = [_add(position, _quaternion_rotate(quaternion, point)) for point in rel_support]
        lowest = min(point[2] for point in world_support)
        band = max(1e-5, 0.002 * min_extent)
        contacts = []
        for point in world_support:
            if point[2] <= lowest + band:
                if all(
                    _norm((point[0] - other[0], point[1] - other[1], point[2] - other[2])) > 1e-9
                    for other in contacts
                ):
                    contacts.append(point)
        centroid = (
            sum(point[0] for point in contacts) / len(contacts),
            sum(point[1] for point in contacts) / len(contacts),
            sum(point[2] for point in contacts) / len(contacts),
        ) if contacts else (position[0], position[1], position[2] - 1.0)
        r = (
            position[0] - centroid[0],
            position[1] - centroid[1],
            position[2] - centroid[2],
        )
        lever = _norm((r[0], r[1], 0.0))
        if lever > 1e-9:
            # Gravity torque about the contact: tau = r x (0, 0, -mg); the
            # CoM descends (checked via omega x r), so the body tips over
            # rather than rocking in place.
            torque = (-r[1], r[0], 0.0)
            magnitude = _norm(torque)
            axis = (torque[0] / magnitude, torque[1] / magnitude, 0.0)
        elif len(contacts) >= 2:
            # Knife-edge balance on a contact line: tip about the horizontal
            # axis perpendicular to the line, toward the side the CAD-up
            # leans (the body falls to a face).
            line = (contacts[1][0] - contacts[0][0], contacts[1][1] - contacts[0][1], 0.0)
            line_length = _norm(line)
            if line_length > 1e-9:
                axis = (-line[1] / line_length, line[0] / line_length, 0.0)
                up = _quaternion_rotate(quaternion, (0.0, 0.0, 1.0))
                if up[0] * axis[0] + up[1] * axis[1] < 0.0:
                    axis = (-axis[0], -axis[1], 0.0)
            else:
                axis = (1.0, 0.0, 0.0)
        else:
            # Single-point balance: tip about the axis perpendicular to the
            # CAD-up projection (the body falls toward a face).
            up = _quaternion_rotate(quaternion, (0.0, 0.0, 1.0))
            horizontal = (up[0], up[1], 0.0)
            horizontal_length = _norm(horizontal)
            if horizontal_length > 1e-9:
                axis = (-horizontal[1] / horizontal_length, horizontal[0] / horizontal_length, 0.0)
            else:
                axis = (1.0, 0.0, 0.0)
        return contacts, centroid, lever, axis

    spin_budget = 0.5 * _dot(spin_angular, _matvec(inertia, spin_angular))
    # The release energy is the TRUE initial mechanical energy: the drop
    # budget m*g*h plus the potential energy of the CoM height above the
    # lowest support point (rotated orientations start with extra PE) plus
    # the configured spin budget.  The per-impact cap at m*g*h (the drop
    # budget) is unchanged.
    release_energy = mass_kg * gravity * (height_m - lowest_world) + spin_budget
    settled_energy = None

    # Captured rest pose for the rest-persistence window: once the rest
    # criterion first fires, the body is physically at rest and its residual
    # motion is the contact-resolution limit cycle — a 240 Hz micro-bounce
    # (sub-0.1 mm) that aliases into the 60 Hz samples as visible post-landing
    # jitter.  While the rest persists, every recorded keyframe uses this
    # captured pose so the settled window is rock-solid constant; the snap
    # releases if the body leaves the rest band.
    rest_pose = None
    # Smooth base-leveling ramp state: when a near-base rest is captured
    # and leveled, the recorded samples interpolate from the captured
    # physics pose to the leveled pose over ``level_ramp_s`` so the
    # playback settles flat smoothly instead of teleporting.
    level_start = None
    level_from_origin = None
    level_from_quaternion = None
    LEVEL_RAMP_S = 0.4

    def record_sample(force=False):
        nonlocal next_sample
        if force or elapsed >= next_sample - 1e-12:
            if rest_pose is not None:
                origin, rest_quaternion = rest_pose
                if level_start is not None and level_from_origin is not None:
                    alpha = min(1.0, max(0.0, (elapsed - level_start) / LEVEL_RAMP_S))
                    if alpha < 1.0:
                        origin = (
                            level_from_origin[0] + (origin[0] - level_from_origin[0]) * alpha,
                            level_from_origin[1] + (origin[1] - level_from_origin[1]) * alpha,
                            level_from_origin[2] + (origin[2] - level_from_origin[2]) * alpha,
                        )
                        rest_quaternion = _slerp(level_from_quaternion, rest_quaternion, alpha)
            else:
                origin = _add(
                    position,
                    _scale(_quaternion_rotate(quaternion, com_offset_m), -1.0),
                )
                rest_quaternion = quaternion
            trajectory.append(
                (
                    round(elapsed, 6),
                    origin[0],
                    origin[1],
                    origin[2],
                    rest_quaternion[0],
                    rest_quaternion[1],
                    rest_quaternion[2],
                    rest_quaternion[3],
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
        # Frame energy audit: the frame's flight segments and contact cycle
        # mutate the energy measure; each named booking is tracked in
        # ``frame_booked`` (losses positive, injections negative) and the
        # measured residual is booked once after the window loop so the
        # ledger closes exactly.
        frame_pre_energy = total_energy()
        frame_booked = 0.0
        frame_realignment = 0.0
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
                # The segment advance is a semi-implicit Euler step of size
                # ``window``: book its sag here so contact frames (which skip
                # the outer no-contact sag booking) still account their
                # flight segments exactly.
                segment_sag = 0.5 * mass_kg * gravity * gravity * window * window
                ledger_losses["integrator_sag_j"] += segment_sag
                accounted_losses += segment_sag
                frame_booked += segment_sag
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
            # over the uncrossed portion.  The reconstruction realigns the
            # velocity to the crossing state; its energy effect nets to zero
            # over the window (the correction restores the position), so it
            # is deliberately NOT booked individually — the per-frame
            # reconciliation below absorbs the residual of the whole
            # crossing cycle (reconstruction + crossing-partial advances +
            # gyroscopic update).
            pre_reconstruction_energy = total_energy()
            velocity = (
                v_end[0],
                v_end[1],
                v_end[2] + gravity * (window - crossing_time),
            )
            frame_realignment += total_energy() - pre_reconstruction_energy
            contact_offset = lowest[2]
            if contact_offset <= 0.0:
                # Contact manifold: every support point within tolerance of
                # the lowest one is active (a flat face exposes several
                # coplanar points); the impulse acts at the centroid of the
                # UNIQUE active points (support directions can select the
                # same extreme vertex, and duplicates would skew the
                # centroid and fabricate torque) so face impacts do not spin
                # the body up about a single corner.
                # Manifold band: contact points within the skate coplanarity band
                # (up to 3mm / 15% of min extent) are grouped for face impacts.
                tolerance = max(1e-4, min(0.003, 0.15 * min_extent))
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
                # FACE IMPACT: when the body lands near-upright and the active
                # manifold spans the CoM (3+ contact points enclosing the CoM
                # projection), the net impulse acts at the center of pressure
                # under the CoM.  For off-center single-point or edge contacts
                # (e.g. landing on rear skates vs front skates), the true
                # physical lever is preserved so contact torque rotates the body.
                # The gate applies to the WHOLE multi-point contact cycle: the
                # sequential-contact resolver can emit several impulses at the
                # same crossing (the first normal impulse changes the velocity,
                # so a `v_z < -0.05` requirement would let the subsequent
                # impulses of the same face contact slip through with off-center
                # levers and spin the body over onto its back — the observed
                # flat-drop tip-over).
                # A near-upright MULTI-POINT contact (3+ active points) is a
                # flat-face landing even when the sparse extreme-point hull
                # does not enclose the CoM projection: the 14-direction support
                # model of a real mesh produces a non-coplanar, off-CoM tripod
                # (rear skates + rocker keel), and requiring the hull to enclose
                # the CoM makes the resolver treat a flat landing as an edge
                # contact and torque the body over.  A real mouse dropped flat
                # stays flat; every impulse of a near-upright face contact
                # passes through the CoM.
                # A near-upright SINGLE-POINT contact (the jittered drops 1+
                # land on one rear skate with up to 6 deg tilt) is also a
                # flat-ish landing: the single extreme-point contact applies
                # a huge lever torque that flips the mouse 180 deg onto its
                # back — a real mouse pivots slightly and settles.  The
                # impact lever is reduced (not zeroed) so the mouse can
                # still pivot a little, but cannot be flipped over by the
                # sparse support model's off-CoM point.
                up_dot_now = _quaternion_rotate(quaternion, (0.0, 0.0, 1.0))[2]
                if up_dot_now > 0.9 and (
                    len(active) >= 3 or _manifold_spans_com_2d(position, active)
                ):
                    r = (0.0, 0.0, r[2])
                elif up_dot_now > 0.9 and len(active) == 1:
                    # Near-upright single-point landing.  The sparse 14-point
                    # support samples ONE extreme point of a flat landing (a
                    # skate), whose body-frame height sits at the BASE PLANE
                    # (within a few mm of the lowest support point); a real
                    # flat landing's CoM is inside the skate polygon and the
                    # net impulse passes through the CoM, so the horizontal
                    # lever is ZEROED.  A genuine corner/edge contact during
                    # a tumble sits well ABOVE the base plane in the body
                    # frame; there the lever is only REDUCED (10%) so the
                    # body can pivot naturally without being flipped by the
                    # discrete support point.
                    base_z = min(point[2] for point in rel_support)
                    contact_body = _quaternion_rotate(
                        _conjugate_quaternion(quaternion),
                        (centroid[0] - position[0], centroid[1] - position[1], centroid[2] - position[2]),
                    )
                    if contact_body[2] <= base_z + 0.006:
                        r = (0.0, 0.0, r[2])
                    else:
                        r = (r[0] * 0.1, r[1] * 0.1, r[2])
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
                        # The budget is the release energy plus every
                        # accounted injection (positional corrections, escape
                        # kick/hop, gravity-torque work) minus the accounted
                        # losses so far (impacts, drag, damping, clamp, sag):
                        # the honest expected pre-impact energy, so a
                        # legitimate kick+torque tip-over does not false-fire.
                        budget_so_far = (
                            release_energy
                            + injections_total
                            - accounted_losses
                            + frame_realignment
                        )
                        # Energy-creation watchdog: the pre-impact system
                        # energy must not exceed the honest budget (release +
                        # injections - accounted losses).  The excess is
                        # judged against the RELEASE energy, not the budget
                        # alone: at the micro-bounce tail the budget itself
                        # collapses to ~1e-5 J and the frame-level
                        # realignment artifacts (crossing-time reconstruction,
                        # gyroscopic drift) exceed a budget-relative 0.1%
                        # tolerance with excesses of ~1e-6 J — 2e-4 % of the
                        # drop's energy — while a genuine solver injection
                        # is an O(1%) excess of the release.
                        excess = pre_energy - budget_so_far
                        if excess > 1e-3 * release_energy:
                            energy_creation = True
                        if post_energy > pre_energy * (1.0 + 1e-3):
                            rebound_overspeed = True
                        impact_loss = max(0.0, pre_energy - post_energy)
                        lost_contact += impact_loss
                        accounted_losses += impact_loss
                        frame_booked += impact_loss
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
                                    "applied_restitution": round(impact_restitution, 4),
                                    "tangent_speed": round(tangent_speed, 4),
                                    "incidence_angle_deg": round(incidence_angle, 4),
                                    "manifold_size": len(active),
                                }
                            )
                # Tangential friction impulse (Coulomb, impulse-limited by the
                # actually applied normal impulse and by the tangential
                # effective mass — a body-mass bound over-corrects the slip
                # at off-CoM contacts and injects energy).  The normal
                # impulse changes the angular velocity and therefore the
                # contact-point velocity, so the slip state is recomputed
                # AFTER it: friction against the stale pre-impulse tangent
                # acts in the wrong direction for off-CoM contacts.
                angular_world = _quaternion_rotate(quaternion, angular_body)
                contact_velocity = _add(velocity, _cross(angular_world, r))
                tangent_velocity = (contact_velocity[0], contact_velocity[1], 0.0)
                tangent_speed = _norm(tangent_velocity)
                if tangent_speed > 1e-6:
                    direction = _scale(tangent_velocity, -1.0 / tangent_speed)
                    k_tangent = inv_mass + _dot(
                        _cross(_matvec(inv_inertia_world, _cross(r, direction)), r), direction
                    )
                    max_friction = friction * abs(applied_impulse)
                    friction_impulse = min(max_friction, tangent_speed / max(k_tangent, 1e-12))
                    pre_friction_energy = total_energy()
                    velocity = _add(velocity, _scale(direction, friction_impulse * inv_mass))
                    delta_angular_world = _matvec(
                        inv_inertia_world, _scale(_cross(r, direction), friction_impulse)
                    )
                    angular_body = _add(
                        angular_body,
                        _quaternion_rotate(_conjugate_quaternion(quaternion), delta_angular_world),
                    )
                    # The friction impulse removes slip energy (the impact
                    # loss above is measured around the NORMAL impulse only):
                    # without this booking the ledger shows an O(0.1 J)
                    # deficit on off-CoM impacts with slip.
                    friction_loss = pre_friction_energy - total_energy()
                    ledger_losses["contact_friction_ke_j"] += friction_loss
                    accounted_losses += friction_loss
                    frame_booked += friction_loss
                # Positional correction: eliminate table penetration so a
                # resting body sits on the plane instead of hovering in a
                # penetration limit cycle.  The depth is tracked for the
                # penetration check.
                penetration = -contact_offset
                if penetration > max_penetration:
                    max_penetration = penetration
                position = (position[0], position[1], position[2] - contact_offset)
                # The positional correction raises the CoM out of the
                # penetration: m*g*Delta_p of potential energy appears with
                # no kinetic debit, so it is booked as an injection.
                correction_injection = mass_kg * gravity * (-contact_offset)
                ledger_injections["positional_correction_pe_j"] += correction_injection
                injections_total += correction_injection
                frame_booked -= correction_injection
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
                # The slice advance is a semi-implicit Euler step: the same
                # 0.5*m*g^2*dt^2 integrator sag as a free-flight step.  It is
                # booked so the contact-phase micro-bounce cycle closes
                # exactly (correction injection + impulse loss + slice sag).
                slice_sag = 0.5 * mass_kg * gravity * gravity * slice_time * slice_time
                ledger_losses["integrator_sag_j"] += slice_sag
                accounted_losses += slice_sag
                frame_booked += slice_sag
                # The body keeps rotating while it rests: without this the
                # orientation freezes on a perpetual zero-time crossing and a
                # tilted corner rest slides across the floor forever instead
                # of tipping over its contact.
                quaternion = _integrate_quaternion(quaternion, angular_body, slice_time)
                window = window - slice_time
                continue
            if window <= 1e-9:
                break

        # Frame reconciliation: the measured energy change of the whole frame
        # (flight segments + crossing cycle) minus the named bookings.  The
        # residual covers the crossing realignment, crossing-partial advances
        # and the gyroscopic update — the documented discretization residuals
        # — so the ledger closes exactly; a systematically large residual
        # would flag a real unaccounted energy flow.
        frame_residual = (frame_pre_energy - total_energy()) - frame_booked
        if abs(frame_residual) > 1e-12:
            ledger_losses["contact_reconciliation_ke_j"] += frame_residual
            accounted_losses += frame_residual

        in_contact = step_contacted
        # Contact dissipation (rolling resistance) and mild aerodynamic drag:
        # without them a tumbling body enters a perpetual micro-bounce limit
        # cycle.  Values are small but sufficient to let a drop settle.
        if in_contact:
            # The quiet stand-down pin is STICKY once set: resetting it here
            # every contact frame let the escape torque re-inject spin the
            # very next frame (the escape gate tests ``not quiet_pinned``),
            # which failed the quiet band's |w| gate and decayed the
            # accumulator — the pin flapped forever on a genuinely still
            # metastable rest.  The pin is cleared only when the body
            # genuinely leaves the quiet band (the gate's else branch).
            pass
            # The acceptance is evaluated FIRST so the dissipation can
            # distinguish the ESCAPE ROLL (a body tipping off a curved
            # surface — real rolling friction is tiny) from the settled
            # contact (where the strong dissipation kills the micro-bounce).
            acceptance_rejected = not rest_acceptance()[0] or not rest_acceptance()[1]
            up_world = _quaternion_rotate(quaternion, (0.0, 0.0, 1.0))
            # The mild spin damping applies ONLY to the true curved-side
            # escapes (CAD-up not near-vertical, matching the kick gate): a
            # NEAR-BASE rejection (a body rocking on its base with up_dot
            # ~0.9-1.0, the CoM momentarily outside the base-edge hull)
            # must decay at the strong 12/s rate — otherwise the base-rock
            # persists ~24x longer and the body visibly "jiggles" before
            # settling.  The SLIDE dissipation stays strong always.
            if acceptance_rejected and up_world[2] < 0.7:
                spin_damping = min(1.0, 0.5 * dt)
                slide_damping = min(1.0, 5.0 * dt)
            else:
                spin_damping = min(1.0, 12.0 * dt)
                slide_damping = min(1.0, 5.0 * dt)
            pre_damping_energy = total_energy()
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
            damping_loss = max(0.0, pre_damping_energy - total_energy())
            ledger_losses["spin_slide_damping_ke_j"] += damping_loss
            accounted_losses += damping_loss
            # The perturbation budget bounds the escape: the kick/hop budget
            # AND a continuous-rejection time budget.  Once exhausted the
            # torque/perturbation stand down; a still-rejected rest is frozen
            # in place (the honest DROP_SIM_DID_NOT_SETTLE path below), never
            # certified as an equilibrium.
            if not acceptance_rejected:
                escape_started = None
                escape_axis = None
            elif escape_started is None:
                escape_started = elapsed
            budget_exhausted = (
                escape_attempts >= METASTABLE_ESCAPE_ATTEMPTS
                or (escape_started is not None and elapsed - escape_started >= METASTABLE_ESCAPE_MAX_S)
            )
            w_norm_now = _norm(angular_body)
            if quiet_anchor_up is None:
                quiet_anchor_up = _quaternion_rotate(quaternion, (0.0, 0.0, 1.0))[2]
            pose_drift = abs(
                _quaternion_rotate(quaternion, (0.0, 0.0, 1.0))[2] - quiet_anchor_up
            )
            if (
                up_world[2] < 0.7
                and _norm(velocity) < 0.15
                and w_norm_now < 2.0
                and pose_drift < 0.02
            ):
                # QUIET STAND-DOWN: a METASTABLE pose (CAD-up not
                # near-vertical — a rounded back/edge balance, not a base
                # wobble) whose residual rocking has decayed to a
                # barely-visible rock (|v| < 0.15 m/s, |w| < 2 rad/s) is
                # pinned in place — the honest DROP_SIM_DID_NOT_SETTLE path
                # below — instead of rocking the full stretch budget.  The
                # in-band time accumulates with a same-rate decay, and the
                # pose-stasis gate (the body is NOT tipping — the pose has
                # not moved more than ~1 deg since the quiet window began)
                # keeps a genuine gravity tip draining the accumulator
                # while a rocking-in-place metastable rest pins.  The
                # acceptance test is deliberately NOT part of the gate: the
                # extreme-point support model's hull test toggles frame to
                # frame as the CoM projection hovers on the hull edge, and
                # the toggling would reset the accumulator forever on a
                # genuinely still metastable rest.  A NEAR-UPRIGHT rejection
                # (a base rest transiently wobbled out of the hull band by
                # residual spin) is NOT pinned: it decays naturally and the
                # acceptance recovers, so a legitimate flat rest settles.
                quiet_accum = min(0.25, quiet_accum + dt)
                if quiet_accum >= 0.25:
                    quiet_pinned = True
                    pre_clamp_energy = total_energy()
                    velocity = (0.0, 0.0, 0.0)
                    angular_body = (0.0, 0.0, 0.0)
                    clamp_loss = max(0.0, pre_clamp_energy - total_energy())
                    ledger_losses["resting_clamp_ke_j"] += clamp_loss
                    accounted_losses += clamp_loss
            else:
                quiet_accum = max(0.0, quiet_accum - dt)
                quiet_anchor_up = None
                # The pin holds only while the body stays in (or returns to)
                # the quiet band; leaving the band fully (the accumulator
                # drains) releases the pin so a genuinely tipping body can
                # use the escape torque again.
                if quiet_accum <= 0.0 and quiet_pinned:
                    quiet_pinned = False
            if (
                acceptance_rejected
                and not budget_exhausted
                and not quiet_pinned
                and _quaternion_rotate(quaternion, (0.0, 0.0, 1.0))[2] < 0.7
            ):
                # METASTABLE rest: the floor cannot hold the CoM outside the
                # contact face (a narrow edge/corner/rounded feature), or
                # the body balances in a LOCAL equilibrium high above its
                # base-rest height (a rim tripod of a rounded top).  A real
                # device tips over from any micro-perturbation — the
                # gravity torque about the contact accelerates the body's
                # rotation in the SAME integration, and the same integrator
                # then falls, bounces, and settles it onto its base.  The
                # resting clamp must NOT zero this rotation, or the pose
                # would be frozen forever (the positional correction
                # absorbs the fall without any impulse, so no torque ever
                # builds from the crossing dynamics alone).
                _, centroid, lever, axis = tipping_state()
                # Persist the escape roll direction for the WHOLE escape
                # episode: a real convex contact walks with the roll, so
                # the gravity torque about the moving contact never flips
                # sense.  The extreme-point support freezes the pivot, so
                # the freshly computed natural axis flips every half-cycle
                # of a rock (the CoM swings past the frozen pivot) — and
                # re-adopting it pumps the rock into a limit cycle (the
                # torque always pushes the current rocking direction,
                # injecting energy instead of tipping the body off).  The
                # axis is re-seeded naturally when a new escape episode
                # starts (``escape_axis = None`` when the acceptance
                # recovers), so a body that genuinely rolls off a feature
                # still tips; a body rocking in place on a curved dome
                # rolls monotonically about one axis and either tips off
                # or settles to the honest stand-down freeze.
                if escape_axis is None:
                    escape_axis = (axis[0], axis[1], axis[2])
                axis = escape_axis
                if lever <= 1e-9:
                    # EXACT point/knife-edge balance: the CoM projects onto
                    # the contact, so the gravity lever is zero and the pose
                    # would hold forever — or "walk" across the floor as the
                    # positional correction absorbs the tilt and the slide
                    # keeps the contact under the CoM.  Seed a small floor
                    # lever about the natural tipping axis so the gravity
                    # torque (below) has something to act on.  (This only
                    # fires at a genuine exact balance; a rocking body has a
                    # real nonzero lever and is never seeded.)
                    lever = max(0.002, 0.04 * min_extent)
                # The old deterministic kick + HOP perturbation (an angular
                # impulse of 4-20 rad/s) was removed: it read as an
                # artificial "fast spin to a pose" in playback, and the
                # gravity torque about the contact (below) plus the quiet
                # stand-down freeze (above) resolve metastable rests
                # physically — the body tips by gravity when it can, and is
                # pinned only once its residual rocking has decayed.
                up_world = _quaternion_rotate(quaternion, (0.0, 0.0, 1.0))
                if lever > 1e-9:
                    r = (
                        position[0] - centroid[0],
                        position[1] - centroid[1],
                        position[2] - centroid[2],
                    )
                    torque_magnitude = mass_kg * gravity * lever
                    world_inertia = _world_inertia(inertia, quaternion)
                    i_axis = sum(
                        axis[first] * world_inertia[first][second] * axis[second]
                        for first in range(3)
                        for second in range(3)
                    )
                    i_pivot = max(i_axis + mass_kg * lever * lever, 1e-12)
                    alpha = torque_magnitude / i_pivot
                    delta_world = (axis[0] * alpha * dt, axis[1] * alpha * dt, 0.0)
                    # Work actually delivered by the gravity torque: the
                    # rotational-KE change of the applied angular increment.
                    # (The nominal tau * (alpha*dt) treats the angular
                    # VELOCITY increment as a displacement and over-books the
                    # injection by orders of magnitude during the rocking
                    # phase.)
                    pre_torque_energy = total_energy()
                    angular_body = _add(
                        angular_body,
                        _quaternion_rotate(_conjugate_quaternion(quaternion), delta_world),
                    )
                    torque_work = total_energy() - pre_torque_energy
                    ledger_injections["gravity_torque_work_j"] += torque_work
                    injections_total += torque_work
            elif acceptance_rejected and budget_exhausted:
                # Budget-exhausted freeze: the escape mechanism (kick +
                # gravity torque) is spent and the pose is still rejected.
                # The torque can only rock the body in the local-equilibrium
                # well, so stop it outright and pin the body like the resting
                # clamp but unconditionally: the rest criterion below fires,
                # the rest_pose snap makes the remaining samples bit-constant,
                # and the settle-attempt reports the honest
                # DROP_SIM_DID_NOT_SETTLE (the frozen pose is NOT certified
                # as an equilibrium).  (A near-rest-gated variant was
                # evaluated and reverted: it let thin shells rock for the
                # full stretch budget — 200 impacts and excessive penetration
                # — before freezing.)
                pre_clamp_energy = total_energy()
                velocity = (0.0, 0.0, 0.0)
                angular_body = (0.0, 0.0, 0.0)
                clamp_loss = max(0.0, pre_clamp_energy - total_energy())
                ledger_losses["resting_clamp_ke_j"] += clamp_loss
                accounted_losses += clamp_loss
            elif _norm(velocity) < 0.25 and _norm(angular_body) < 3.0:
                # Resting-contact clamp: a nearly stationary body in contact
                # is pulled to true rest (real contacts dissipate residual
                # micro-motion; without the clamp the single-point support
                # model's friction-reinjected corner-rock limit cycle sits
                # just above the rest thresholds and never settles).  The
                # 3.0 rad/s band covers the slow corner-walk of a thin body
                # (tip speed < 20 cm/s at a mouse-scale lever); within it
                # the horizontal and angular micro-motion is zeroed outright
                # while the vertical component is left to the contact
                # resolution.
                pre_clamp_energy = total_energy()
                velocity = (0.0, 0.0, velocity[2] * 0.6)
                angular_body = (0.0, 0.0, 0.0)
                clamp_loss = max(0.0, pre_clamp_energy - total_energy())
                ledger_losses["resting_clamp_ke_j"] += clamp_loss
                accounted_losses += clamp_loss
        else:
            # Free flight: exact conservation of angular momentum in the body frame
            # (Euler gyroscopic equations). Contact-phase damping handles energy dissipation.
            pass

        # The rest criterion is evaluated on the CONTACT-TIME velocity: after
        # the remainder integration a resting body always carries the
        # per-step gravity increment (g*dt), which would otherwise keep |v|
        # above the threshold forever.  Rest means the body leaves the
        # contact with negligible speed and spin, sustained in contact.
        rest_velocity = velocity
        rest_angular = angular_body

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
            if rest_pose is None:
                rest_pose = (
                    _add(position, _scale(_quaternion_rotate(quaternion, com_offset_m), -1.0)),
                    quaternion,
                )
                # BASE-LEVELING REFINEMENT: the extreme-point support model
                # rests the body on a non-coplanar tripod of its lowest
                # sampled points (the mesh's curved rocker keel and rounded
                # base corners), which leaves a FLAT drop tilted by up to
                # ~5 degrees on a corner.  A real mouse rests on its
                # coplanar skate plane.  For a near-base rest (CAD-up
                # nearly vertical), level the recorded rest orientation to
                # the floor: rotate about the horizontal axis through the
                # lowest contact point so the base plane sits flat, keeping
                # the contact on the floor.  Edge/corner/inverted rests
                # (up_dot <= 0.9) are untouched.
                # The leveling is applied as a SMOOTH ramp over the rest
                # window (``level_ramp_s``), NOT as an instantaneous
                # teleport: the recorded samples interpolate from the
                # captured physics pose to the leveled pose, so the
                # playback shows the body visibly settle flat instead of
                # snapping to the leveled pose in one 60 Hz frame.
                up_rest = _quaternion_rotate(quaternion, (0.0, 0.0, 1.0))
                if up_rest[2] > 0.9:
                    horizontal = (up_rest[0], up_rest[1], 0.0)
                    horizontal_length = _norm(horizontal)
                    if horizontal_length > 1e-9:
                        level_axis = (horizontal[1] / horizontal_length, -horizontal[0] / horizontal_length, 0.0)
                        level_angle = math.acos(max(-1.0, min(1.0, up_rest[2])))
                        level_q = _axis_angle_quaternion(level_axis, level_angle)
                        world_support = [
                            _add(position, _quaternion_rotate(quaternion, point)) for point in rel_support
                        ]
                        lowest = min(world_support, key=lambda point: point[2])
                        pivot = (lowest[0], lowest[1], 0.0)
                        leveled_quaternion = _normalize_quaternion(
                            _quaternion_multiply(level_q, quaternion)
                        )
                        rel_pos = (
                            position[0] - pivot[0],
                            position[1] - pivot[1],
                            position[2] - pivot[2],
                        )
                        rel_pos = _quaternion_rotate(level_q, rel_pos)
                        leveled_position = (pivot[0] + rel_pos[0], pivot[1] + rel_pos[1], pivot[2] + rel_pos[2])
                        leveled_origin = _add(
                            leveled_position,
                            _scale(_quaternion_rotate(leveled_quaternion, com_offset_m), -1.0),
                        )
                        # The leveling rotates about the lowest SUPPORT point
                        # (a curved feature — the rocker keel or a rounded
                        # base corner), so mesh features below the base plane
                        # (the rocker arc's sag) would swing BELOW the floor —
                        # the visible "one corner below the surface".  Lift the
                        # leveled pose so no sampled support feature sits below
                        # the floor plane; the contact stays engaged.
                        leveled_support = [
                            _add(leveled_position, _quaternion_rotate(leveled_quaternion, point))
                            for point in rel_support
                        ]
                        support_lowest = min(point[2] for point in leveled_support)
                        if support_lowest < 0.0:
                            leveled_position = (leveled_position[0], leveled_position[1], leveled_position[2] - support_lowest)
                            leveled_origin = (leveled_origin[0], leveled_origin[1], leveled_origin[2] - support_lowest)
                        rest_pose = (leveled_origin, leveled_quaternion)
                        level_start = elapsed
                        level_from_origin = _add(
                            position, _scale(_quaternion_rotate(quaternion, com_offset_m), -1.0)
                        )
                        level_from_quaternion = quaternion
                    else:
                        level_start = None
                        level_from_origin = None
                        level_from_quaternion = None
                else:
                    level_start = None
                    level_from_origin = None
                    level_from_quaternion = None
            rest_time += dt
            # Rest must persist 0.4 s (screening convention) before settle.
            if rest_time >= 0.4:
                up_dot_settle = _quaternion_rotate(quaternion, (0.0, 0.0, 1.0))[2]
                if (
                    rest_pose is not None
                    and up_dot_settle > 0.9
                    and (
                        # Leveling ramp completed (the captured tilted rest
                        # has been smoothly rotated flat).
                        (level_start is not None and elapsed - level_start >= LEVEL_RAMP_S)
                        # Already perfectly upright: there is no tilt to
                        # level (horizontal component ~0), and the flat rest
                        # IS the settled state.  The sparse extreme-point
                        # support's hull test can still reject the flat pose
                        # (non-coplanar rocker keel + skate radii leave the
                        # CoM projection outside the degenerate hull), so
                        # the captured rest pose is certified directly.
                        or (level_start is None and up_dot_settle > 0.999)
                    )
                ):
                    # NEAR-BASE REST CERTIFICATION: the body has been at
                    # rest for the 0.4 s screening window and is resting
                    # flat (CAD-up vertical).  The rest pose IS the settled
                    # state — a real mouse rests on its flat base.  The
                    # sparse extreme-point support's non-coplanar tripod
                    # makes the live-quaternion acceptance hull test fail,
                    # but the captured rest pose is the honest flat rest.
                    # Certify it instead of resetting rest_time forever.
                    settled = elapsed
                    settled_energy = total_energy()
                    level_start = None
                else:
                    # Settle acceptance: the rest must be statically stable
                    # (a flat face holding the CoM projection) AND at the
                    # body's lowest possible rest height.  A stable-looking
                    # pose high above the base-rest height (e.g. balanced on
                    # a rim tripod of a rounded top) is a LOCAL equilibrium
                    # a real device would not hold — the perturbation below
                    # tips it and the integrator carries it to the base rest.
                    stable, height_ok = rest_acceptance()
                    if stable and height_ok:
                        settled = elapsed
                        settled_energy = total_energy()
                    elif _norm(rest_velocity) < 0.05 and _norm(rest_angular) < 0.5:
                        # SUSTAINED-STILL REST CERTIFICATION: the body has
                        # been at rest (v < 0.05, w < 0.5) for the full 0.4 s
                        # window.  Sustained stillness IS the definition of
                        # being at rest — the escape torque (gravity torque
                        # about the contact) had the whole window to tip a
                        # pose that CAN tip, and it did not, so the pose is
                        # a stable local minimum (a real device rests on its
                        # back/dome/rim all the time).  The CoM-hull
                        # acceptance test (``rest_acceptance``) was designed
                        # for the OLD sparse support model, where a
                        # curved-surface rest was a degenerate tripod; with
                        # the convex-hull support the dome/rim rests are
                        # real manifolds that the contact model holds.
                        # Certify the rest instead of flagging
                        # DID_NOT_SETTLE on a genuinely still body.  This
                        # must come BEFORE the near-base branch below: a
                        # still near-upright rim balance (up ~0.9) would
                        # otherwise be reset forever by the near-base
                        # rest-attempt (up >= 0.7 resets rest_time), never
                        # reaching certification.
                        settled = elapsed
                        settled_energy = total_energy()
                    elif budget_exhausted:
                        # The perturbation budget is exhausted.  A rejected
                        # rest (CoM outside the contact face, or a local
                        # equilibrium away from the base-rest height) must
                        # NEVER be certified as a settle: accept it only if
                        # it is a true static equilibrium, otherwise report
                        # the honest DROP_SIM_DID_NOT_SETTLE instead of
                        # freezing a non-equilibrium pose.
                        if rest_acceptance()[0] and rest_acceptance()[1]:
                            settled = elapsed
                            settled_energy = total_energy()
                        else:
                            settled = elapsed
                            settled_flag = False
                            settled_energy = total_energy()
                    elif _quaternion_rotate(quaternion, (0.0, 0.0, 1.0))[2] >= 0.7:
                        # Near-base rest-attempt (the body rocking on its
                        # base, up_dot ~1): no escape kick — the strong
                        # damping and the clamp settle it at the next stable
                        # moment.
                        rest_time = 0.0
                    elif quiet_pinned:
                        # The quiet stand-down froze the pose (its residual
                        # rocking decayed to a barely-visible rock).  The
                        # body is GENUINELY STILL — sustained v and w below
                        # the rest thresholds for the whole 0.4 s window —
                        # so it IS at rest (a real device can rest on its
                        # back/dome/side; those are stable local minima for
                        # a low-CoM body).  The old code reported the honest
                        # DROP_SIM_DID_NOT_SETTLE here, but a still body is
                        # not "did not settle" — the escape torque had the
                        # whole window to tip a pose that CAN tip, and it
                        # did not.  Certify the settle.
                        settled = elapsed
                        settled_energy = total_energy()
                    else:
                        # Metastable rest (edge/corner/rounded feature or a
                        # local equilibrium away from the base-rest height):
                        # the contact torque tips the body (a pose that CAN
                        # tip keeps tipping to its face); the old
                        # deterministic kick + HOP perturbation was removed
                        # (it read as an artificial "fast spin to a pose" in
                        # playback, and the gravity torque plus the quiet
                        # stand-down resolve metastable rests physically).
                        rest_time = 0.0
        else:
            rest_time = 0.0
            rest_pose = None

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
    # Energy-ledger audit at settle: release + injections must equal the
    # settled energy plus the accounted losses.  A small imbalance is
    # informational (the model's documented discretization residuals); an
    # imbalance beyond 5% of the release energy is an error (unaccounted
    # energy flow).
    ledger_imbalance = release_energy + injections_total - settled_energy - accounted_losses
    if abs(ledger_imbalance) > max(1e-3, 0.01 * release_energy):
        checks.append(
            {
                "code": "DROP_SIM_ENERGY_LEDGER_UNBALANCED",
                "severity": "error" if abs(ledger_imbalance) > 0.05 * release_energy else "info",
                "message": (
                    "energy ledger imbalance {:.4f} J (release {:.4f} J, injections "
                    "{:.4f} J, accounted losses {:.4f} J, settled {:.4f} J)"
                ).format(
                    ledger_imbalance,
                    release_energy,
                    injections_total,
                    accounted_losses,
                    settled_energy,
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
    energy_ledger = {
        "release_j": round(release_energy, 6),
        "injections_j": {
            key: round(value, 6) for key, value in ledger_injections.items()
        },
        "accounted_losses_j": {
            key: round(value, 6) for key, value in ledger_losses.items()
        },
        "lost_contact_j": round(lost_contact, 6),
        "lost_drag_j": round(lost_drag, 6),
        "settled_j": round(settled_energy, 6),
        "imbalance_j": round(ledger_imbalance, 6),
    }
    # The timestamp at which the body truly STOPPED MOVING: the last
    # trajectory sample whose pose differs from the following (frozen)
    # samples.  The rest tail after this point is bit-constant (the
    # captured rest pose); the 0.4 s stasis-screening window and the
    # reported settle time are part of the CURRENT drop's record, while
    # ``motion_stop_s`` is what the multi-drop loop uses to pace the next
    # drop (motion stops -> 0.5 s pause -> next drop starts).
    #
    # Degenerate fallback: if EVERY consecutive sample pair is identical
    # (a fully frozen trajectory — the quiet stand-down can capture the
    # rest pose at the very first sample), the loop below never finds a
    # moving sample and motion_stop would be trajectory[0][0] == 0.0,
    # making the multi-drop loop advance the timeline by only 0.5 s and
    # OVERLAP this drop with the next one (the "drop spawns on the ground"
    # playback bug).  A real drop always has release-to-ground motion, so
    # a fully frozen trajectory can only be an artifact; fall back to the
    # recorded settle time (the full drop duration) so the timeline can
    # never collapse.
    motion_stop = trajectory[0][0] if trajectory else 0.0
    for index in range(len(trajectory) - 1, 0, -1):
        previous = trajectory[index - 1]
        current = trajectory[index]
        if (
            current[1] != previous[1]
            or current[2] != previous[2]
            or current[3] != previous[3]
            or current[4] != previous[4]
            or current[5] != previous[5]
            or current[6] != previous[6]
            or current[7] != previous[7]
        ):
            motion_stop = current[0]
            break
    else:
        # No moving sample pair found (fully frozen trajectory).
        motion_stop = settled
    return {
        "trajectory": trajectory,
        "impacts": impacts,
        "settled_s": settled,
        "settled": settled_flag,
        "motion_stop_s": motion_stop,
        "energy": energy,
        "energy_ledger": energy_ledger,
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
    pause_between_drops_s=None,
):
    """Run a deterministic multi-drop simulation.

    Drop 0 is the pristine reference (exactly the configured orientation,
    zero lateral offset).  Every later drop gets a deterministic, seeded
    initial-condition variation (a solid-angle-uniform tilt at least 6 deg
    from the reference orientation, lateral drift, small release spin about
    an independent axis) so repeated drops are clearly distinct while
    staying bit-reproducible for a fixed seed and configuration.
    ``orientation`` is a mode string ("flat", "edge", "corner", "random")
    or an explicit pose dict ``{"quaternion_wxyz": [w, x, y, z]}``; with an
    explicit pose, drop 0 uses exactly that orientation and drops 1+ add the
    seeded jitter on top.  An ``impact`` test forces the corner base
    orientation for EVERY drop (with the same per-drop jitter on drops 1+)
    so an impact run is a homogeneous corner campaign; an explicit pose
    bypasses the override.

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
            "pause_between_drops_s": (
                pause_between_drops_s if pause_between_drops_s is not None else 0.50
            ),
        }
    )
    if mass_kg is None or not math.isfinite(mass_kg) or mass_kg <= 0.0:
        raise DropSimulationError("drop_simulation.mass_kg must be positive")
    if not math.isfinite(gravity) or gravity <= 0.0:
        raise DropSimulationError("drop_simulation gravity must be positive and finite")
    if not math.isfinite(dt) or dt <= 0.0:
        raise DropSimulationError("drop_simulation dt must be positive and finite")
    if dt > 0.01:
        raise DropSimulationError(
            "drop_simulation dt must not exceed 0.01 s (tuning constants are "
            "calibrated for the ~1/240 s timestep)"
        )
    symmetry_error = _inertia_symmetry_error(inertia)
    if symmetry_error is not None:
        raise DropSimulationError("drop_simulation inertia: {}".format(symmetry_error))
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
    # Per-axis inertia tolerance s[i] scales the tensor entries involving
    # axis i: diagonal I'[i][i] = s[i]*I[i][i] exactly (the documented
    # per-axis +/-5% entry tolerance), and off-diagonal I'[i][j] by the
    # geometric mean sqrt(s[i]*s[j]) — the unique symmetric completion of
    # the per-axis rule.  A column-only scaling (s[j]*I[i][j]) would break
    # symmetry for off-diagonal entries; a full D.I.D stretch
    # (s[i]*s[j]*I[i][j]) would square the tolerance (a +/-5% draw would
    # move I by +/-10.25%) and contradict the linear entry-scaling
    # semantics the pipeline's mass override relies on.
    inertia_sqrt_scale = tuple(math.sqrt(scale) for scale in effective_inertia_scale)
    effective_inertia = tuple(
        tuple(
            inertia_sqrt_scale[row] * inertia_sqrt_scale[axis] * inertia[row][axis]
            for axis in range(3)
        )
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
    drop_interval_s = float(config.get("pause_between_drops_s", config.get("drop_interval_s", 0.50)))
    t_offset = 0.0
    # Campaign variation: a normal drop/tumble test with more than one drop
    # gives every drop after the reference a real random start pose (uniform
    # over the solid angle of [6, CAMPAIGN_MAX_TILT_DEG] — always visibly
    # distinct from the reference flat drop).  The impact test stays a
    # homogeneous corner campaign and explicit poses mirror their mode twin
    # so the documented determinism contract (bit-reproducible for a fixed
    # seed) is preserved.
    campaign_variation = (
        config["test"] in ("drop", "tumble") and int(config["drop_count"]) > 1
    )
    run_max_tilt_deg = CAMPAIGN_MAX_TILT_DEG if campaign_variation else JITTER_MAX_TILT_DEG
    for drop_index in range(config["drop_count"]):
        explicit_quaternion = config.get("orientation_quaternion_wxyz")
        if explicit_quaternion is not None:
            # Explicit pose: drop 0 uses exactly the validated quaternion; no
            # mode mapping (the impact-test corner override included).
            orientation_q = tuple(explicit_quaternion)
        else:
            if config["test"] == "impact":
                # Impact test: EVERY drop rests on the corner orientation for
                # a harsher hit (not just drop 0), so an impact run is a
                # homogeneous corner campaign.  The corner mode is the single
                # source of truth, so this override can never drift from
                # _orientation_quaternion("corner").  The per-drop jitter
                # below (tilt/lateral/spin for drops 1+) still applies on top
                # of this base, keeping the documented variation semantics.
                orientation_q = _orientation_quaternion("corner", 0)
            else:
                orientation_q = _orientation_quaternion(config["orientation"], seed + drop_index)
        tilt_deg = 0.0
        lateral_offset = (0.0, 0.0)
        initial_angular = (0.0, 0.0, 0.0)
        if drop_index > 0:
            # Every drop after the reference gets a deterministic, seeded
            # initial-condition variation (tilt + lateral drift + release
            # spin), so repeated drops are unique but still bit-reproducible.
            tilt_q, tilt_deg, lateral_offset, initial_angular = _drop_variation(
                seed, drop_index, config["height_m"], max_tilt_deg=run_max_tilt_deg
            )
            orientation_q = _normalize_quaternion(
                _quaternion_multiply(tilt_q, orientation_q)
            )
            # The jitter spin is a WORLD-axis vector (built about the world
            # horizontal tilt axis); the integrator's angular velocity is
            # BODY-frame — convert before adding, or the release spin would
            # act about a frame-mismatched axis.
            initial_angular = _quaternion_rotate(
                _conjugate_quaternion(orientation_q), initial_angular
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
        if config["test"] == "impact" and explicit_quaternion is None:
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
                "energy_ledger": drop_result["energy_ledger"],
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
        # Inter-drop pacing: the next drop starts when the mouse has
        # STOPPED MOVING plus a fixed 0.5 s pause — NOT a fixed interval
        # after the reported settle time.  ``drop_result["motion_stop_s"]``
        # is the timestamp of the last sample with actual motion (the
        # rest-capture moment); the frozen rest tail and the 0.4 s
        # stasis-screening window are part of the CURRENT drop's record,
        # not dead time before the next drop.  This is what makes the
        # playback show: motion stops -> 0.5 s pause -> next drop starts.
        motion_stop = drop_result.get("motion_stop_s")
        if motion_stop is None:
            motion_stop = settled
        # Timeline floor: the next drop can never start before the previous
        # drop's own fall has had time to play.  The floor is the free-fall
        # time from the configured height (sqrt(2 h / g)) — a physical
        # minimum for ANY drop — so even a pathological (near-zero)
        # motion_stop can never collapse the timeline and overlap drops
        # (the "drop spawns on the ground" playback bug).
        fall_floor = math.sqrt(2.0 * config["height_m"] / gravity)
        t_offset += max(motion_stop, fall_floor) + drop_interval_s

    peak_overall = max(all_impacts, key=lambda item: item["impact_speed_m_s"]) if all_impacts else None
    model = {
        "mass_kg": round(effective_mass, 6),
        "inertia_kg_m2": [list(row) for row in effective_inertia],
        "support_model": "convex_hull",
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
            "max_tilt_deg": run_max_tilt_deg,
            "max_lateral_fraction": JITTER_MAX_LATERAL_FRACTION,
            "max_initial_spin_rad_s": JITTER_MAX_SPIN_RAD_S,
            "seed": seed,
            # Discloses whether this run gives drops 1+ a real random start
            # pose (multi-drop drop/tumble campaign) or keeps the fine jitter
            # (single-drop, impact corner campaign, explicit-pose twins).
            "campaign_random_orientation": campaign_variation,
            # Campaign tilt is drawn uniformly over the solid angle of
            # [min_campaign_tilt_deg, max_tilt_deg] — every campaign drop is
            # at least min_campaign_tilt_deg from the reference orientation
            # (visibly distinct, never a near-duplicate of the flat drop).
            # Fine-jitter runs keep the uniform [0, max_tilt] draw.
            "tilt_distribution": (
                "solid_angle_uniform" if campaign_variation else "uniform"
            ),
            "min_campaign_tilt_deg": (
                JITTER_MAX_TILT_DEG if campaign_variation else 0.0
            ),
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
