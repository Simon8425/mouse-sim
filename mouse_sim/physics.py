"""Closed-form structural screening estimates for mouse-scale parts.

Surrogate closed-form solvers: Navier simply-supported thin plate response,
elementary Euler-Bernoulli beam response, load-case dispatch, preflight
issue reporting, and the shared load template catalog.  Pure stdlib, SI
values, deterministic.
"""

from dataclasses import dataclass, field, replace
import math
from typing import Any, Mapping, Optional, Tuple

from .model import MaterialDefinition, MaterialProperties
from .errors import UnitError
from .units import to_si

SERIES_NOT_CONVERGED = "SERIES_NOT_CONVERGED"
NUMERIC_OVERFLOW = "NUMERIC_OVERFLOW"
THIN_SHELL_OUT_OF_RANGE = "THIN_SHELL_OUT_OF_RANGE"
SMALL_DEFLECTION_VIOLATED = "SMALL_DEFLECTION_VIOLATED"
POINT_LOAD_SINGULARITY = "POINT_LOAD_SINGULARITY"
UNSUPPORTED_STIFFNESS_REDUCTION = "UNSUPPORTED_STIFFNESS_REDUCTION"
UNSUPPORTED_ANISOTROPY = "UNSUPPORTED_ANISOTROPY"
UNDERCONSTRAINED_REACTIONS = "UNDERCONSTRAINED_REACTIONS"
INVALID_LOAD_UNITS = "INVALID_LOAD_UNITS"
INVALID_LOAD_VALUE = "INVALID_LOAD_VALUE"
INVALID_LOAD_LOCATION = "INVALID_LOAD_LOCATION"
INVALID_POISSON_RATIO = "INVALID_POISSON_RATIO"

# The Navier double-sine series is capped so a hostile ``series_order``
# cannot trigger an effectively unbounded loop; 49 keeps 25x25 terms, far
# beyond screening accuracy needs.
SERIES_ORDER_CAP = 49

SCREENING_SURROGATE_MODEL_ID = "screening_surrogate_v1"
_STRUCTURAL_SOLVER_METADATA = {
    "model_family": "closed_form_screening",
    "model_id": SCREENING_SURROGATE_MODEL_ID,
    "backend": "surrogate_closed_form",
    "description": "closed-form surrogate solver; screening-quality estimates, not validated FEA",
}

SHELL_PANEL_METHOD = "shell_navier_v1"
BEAM_METHOD = "beam_closed_form_v1"
CLOSED_FORM_METHOD = "closed_form_v1"

SHELL_PANEL_ASSUMPTIONS = (
    "simply supported thin plate, linear elastic isotropic material",
    "Navier double-sine series with odd terms m,n = 1,3,5,...",
    "small-deflection linear plate theory; stresses from moment resultants",
    "uniform pressure over the full panel",
    "response evaluated on a fixed 5x5 grid",
)
BEAM_ASSUMPTIONS = (
    "Euler-Bernoulli beam, small-deflection linear theory",
    "closed-form end reactions and maximum moment",
    "shear stress approximated as tau = 1.5*V/A",
)
SHELL_UNSUPPORTED_FAILURE_MODES = (
    "UNSUPPORTED_BUCKLING",
    "UNSUPPORTED_YIELD_LOCALIZATION",
    "UNSUPPORTED_CRACK_PROPAGATION",
    "UNSUPPORTED_SNAP_THROUGH",
    "UNSUPPORTED_VIBRATION_FATIGUE",
)
BEAM_UNSUPPORTED_FAILURE_MODES = (
    "UNSUPPORTED_BUCKLING",
    "UNSUPPORTED_FATIGUE_CRACK",
    "UNSUPPORTED_JOINT_FAILURE",
    "UNSUPPORTED_TORSION_BUCKLING",
)
Vector3 = Tuple[float, float, float]


@dataclass(frozen=True)
class StructuralResponse:
    """Immutable result of a closed-form structural screening estimate.

    ``solver_metadata`` identifies the model as ``screening_surrogate_v1``:
    a coarse closed-form screening surrogate, not a validated FEA solver.
    """

    method_id: str
    max_displacement_m: Optional[float] = None
    max_displacement_location: Optional[Vector3] = None
    max_stress_pa: Optional[float] = None
    max_stress_filtered_pa: Optional[float] = None
    filtered_location: Optional[Vector3] = None
    safety_factor: Optional[float] = None
    safety_factor_status: str = "not_available"
    feature_peak_stress_pa: Optional[float] = None
    weld_line_derated_allowable_pa: Optional[float] = None
    reactions: Mapping[str, float] = field(default_factory=dict)
    force_residual_n: Optional[float] = None
    moment_residual_n_m: Optional[float] = None
    flags: Tuple[str, ...] = ()
    assumptions: Tuple[str, ...] = ()
    unsupported_failure_modes: Tuple[str, ...] = ()
    validity: str = "valid"
    validity_reasons: Tuple[str, ...] = ()
    solver_metadata: Mapping = field(default_factory=lambda: dict(_STRUCTURAL_SOLVER_METADATA))

    def to_dict(self):
        location = None if self.max_displacement_location is None else list(self.max_displacement_location)
        filtered = None if self.filtered_location is None else list(self.filtered_location)
        return {
            "method_id": self.method_id,
            "max_displacement_m": self.max_displacement_m,
            "max_displacement_location": location,
            "max_stress_pa": self.max_stress_pa,
            "max_stress_filtered_pa": self.max_stress_filtered_pa,
            "filtered_location": filtered,
            "safety_factor": self.safety_factor,
            "safety_factor_status": self.safety_factor_status,
            "feature_peak_stress_pa": self.feature_peak_stress_pa,
            "weld_line_derated_allowable_pa": self.weld_line_derated_allowable_pa,
            "reactions": dict(self.reactions),
            "force_residual_n": self.force_residual_n,
            "moment_residual_n_m": self.moment_residual_n_m,
            "flags": list(self.flags),
            "assumptions": list(self.assumptions),
            "unsupported_failure_modes": list(self.unsupported_failure_modes),
            "validity": self.validity,
            "validity_reasons": list(self.validity_reasons),
            "solver_metadata": dict(self.solver_metadata),
        }


@dataclass(frozen=True)
class SolverCapabilities:
    """Declared capabilities of the surrogate closed-form solver backend."""

    backend: str = "surrogate_closed_form"
    version: str = "0.1.0"
    capability_keys: Tuple[str, ...] = (
        "shell_panel_response",
        "beam_response",
        "solve_load_case",
        "preflight_structural_case",
        "MOUSE_LOAD_TEMPLATES",
    )
    deterministic: bool = True

    def to_dict(self):
        return {
            "backend": self.backend,
            "version": self.version,
            "capability_keys": list(self.capability_keys),
            "deterministic": self.deterministic,
        }


SOLVER_CAPABILITIES = SolverCapabilities()


def _finite(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("{} must be numeric".format(label))
    if not math.isfinite(number):
        raise ValueError("{} must be finite".format(label))
    return number


def _positive(value, label):
    number = _finite(value, label)
    if number <= 0.0:
        raise ValueError("{} must be positive; got {!r}".format(label, value))
    return number


def _poisson(value):
    number = _finite(value, "nu")
    if not -1.0 < number < 0.5:
        raise ValueError("nu must be between -1 and 0.5")
    return number


def _vm(sx, sy, txy):
    return math.sqrt(max(0.0, sx * sx + sy * sy - sx * sy + 3.0 * txy * txy))


def _safety(allowable, stress):
    if allowable is None or stress is None or stress <= 0.0:
        return None, "not_available"
    try:
        allowable = _finite(allowable, "allowable")
        stress = _finite(stress, "stress")
    except ValueError:
        return None, "not_available"
    if stress <= 0.0:
        return None, "not_available"
    factor = allowable / stress
    return factor, ("pass" if factor >= 1.0 else "warn")


def _blank_response(flags, assumptions, validity="inconclusive", method_id=CLOSED_FORM_METHOD):
    return StructuralResponse(
        method_id=method_id,
        flags=tuple(flags),
        assumptions=tuple(assumptions),
        validity=validity,
    )


def _overflow_response(method_id):
    return _blank_response(
        (NUMERIC_OVERFLOW,),
        (
            "closed-form evaluation overflowed or produced non-finite values; "
            "dimensions or loads out of range for the screening surrogate",
        ),
        validity="inconclusive",
        method_id=method_id,
    )


def _series_fields_finite(w, stress):
    for row in w:
        for value in row:
            if not math.isfinite(value):
                return False
    for face in stress:
        for row in face:
            for value in row:
                if not math.isfinite(value):
                    return False
    return True


def _shell_dims(structure):
    a = structure.get("a_m", structure.get("length_m"))
    b = structure.get("b_m", structure.get("width_m"))
    t = structure.get("t_m", structure.get("thickness_m"))
    return a, b, t


def _beam_dims(structure):
    L = structure.get("L_m", structure.get("length_m"))
    I = structure.get("I_m4")
    A = structure.get("A_m2")
    Z = structure.get("section_modulus_m3")
    return L, I, A, Z


# Linear modulus/allowable derating coefficients per degree above 293.15 K
# (K == degC for temperature differences). Values are conservative fits to
# supplier modulus-vs-temperature curves (polymer datasheet class: SABIC /
# Covestro / BASF / DuPont); materials without a coefficient are not derated.
_FAMILY_DERATE_PER_K = {"generic_polymer": 0.0045}
_DERATE_REFERENCE_TEMP_K = 293.15


def _derate_coefficient(name, family, material_key=None):
    """Linear modulus-derating coefficient (1/K above 293.15 K) per material.

    Match the catalog KEY first (e.g. "PC/ABS", "FR-4"), then the display
    name, with the most specific rule first: a naive substring scan would
    match "PC/ABS blend" to "abs" and miss "Polycarbonate"/"Polyoxymethylene"
    entirely.
    """
    candidates = []
    if material_key:
        candidates.append(str(material_key))
    if name:
        candidates.append(str(name))
    if not candidates:
        return None
    rules = (
        ("pcabs", 0.0035),
        ("polycarbonate", 0.0022),
        ("pc", 0.0022),
        ("polyoxymethylene", 0.0050),
        ("pom", 0.0050),
        ("fr4", 0.0010),
        ("glassepoxy", 0.0010),
        ("abs", 0.0045),
        ("acrylonitrile", 0.0045),
        ("default", 0.0045),
        ("genericpolymer", 0.0045),
    )
    for candidate in candidates:
        folded = str(candidate).strip().casefold()
        folded = folded.replace("_", "").replace("-", "").replace(" ", "").replace("/", "")
        for fragment, coefficient in rules:
            if fragment in folded:
                return coefficient
    if family:
        return _FAMILY_DERATE_PER_K.get(str(family).strip().casefold())
    return None


def _material_props(material, temperature_k=None):
    """Extract (E, nu, allowable, info) from a material payload.

    ``info`` carries directional (orthotropic) stiffness data, anisotropy
    flags, weld-line knockdown, continuous-use temperature range, and the
    linear derating coefficient; modulus and allowable are derated in place
    when ``temperature_k`` is above the 293.15 K reference.
    """
    name = ""
    family = ""
    material_key = None
    anisotropy_supported = False
    if isinstance(material, MaterialDefinition):
        name = material.name
        family = material.family
        material_key = str(material.meta.id).removeprefix("mat_").removesuffix("_v1")
        anisotropy_supported = material.anisotropy_supported
        material = material.properties
    if isinstance(material, MaterialProperties):
        E = material.young_modulus.value_si if material.young_modulus is not None else None
        nu = material.poissons_ratio
        allowable = material.tensile_allowable.value_si if material.tensile_allowable is not None else None
        E2 = material.young_modulus_transverse_pa.value_si if material.young_modulus_transverse_pa is not None else None
        E3 = material.young_modulus_thickness_pa.value_si if material.young_modulus_thickness_pa is not None else None
        G12 = material.shear_modulus_xy_pa.value_si if material.shear_modulus_xy_pa is not None else None
        G13 = material.shear_modulus_thickness_pa.value_si if material.shear_modulus_thickness_pa is not None else None
        nu12 = material.poissons_ratio_xy
        nu13 = material.poissons_ratio_xz
        weld_line_factor = material.weld_line_factor
        use_min = material.continuous_use_temperature_min_k
        use_max = material.continuous_use_temperature_max_k
    else:
        E = material.get("young_modulus_pa")
        if E is None:
            E = material.get("youngs_modulus_pa")
        nu = material.get("poissons_ratio")
        allowable = material.get("tensile_allowable_pa")
        E2 = material.get("young_modulus_transverse_pa")
        E3 = material.get("young_modulus_thickness_pa")
        G12 = material.get("shear_modulus_xy_pa")
        G13 = material.get("shear_modulus_thickness_pa")
        nu12 = material.get("poissons_ratio_xy")
        nu13 = material.get("poissons_ratio_xz")
        weld_line_factor = material.get("weld_line_factor")
        use_min = material.get("continuous_use_temperature_min_k")
        use_max = material.get("continuous_use_temperature_max_k")
    directional = all(value is not None for value in (E2, E3, G12, nu12))
    k_derate = _derate_coefficient(name, family, material_key)
    derating_applied = False
    if (
        temperature_k is not None
        and k_derate is not None
        and temperature_k > _DERATE_REFERENCE_TEMP_K
    ):
        factor = 1.0 - k_derate * (temperature_k - _DERATE_REFERENCE_TEMP_K)
        if E is not None:
            E = E * factor
        if allowable is not None:
            allowable = allowable * factor
        derating_applied = True
    info = {
        "name": name,
        "anisotropy_supported": anisotropy_supported,
        "directional": directional,
        "E2": E2,
        "E3": E3,
        "G12": G12,
        "G13": G13,
        "nu12": nu12,
        "nu13": nu13,
        "weld_line_factor": weld_line_factor,
        "continuous_use_min_k": use_min,
        "continuous_use_max_k": use_max,
        "k_derate": k_derate,
        "temperature_k": temperature_k,
        "derating_applied": derating_applied,
    }
    return E, nu, allowable, info


def _load_magnitude(load, key, expected_dimension=None):
    """Read a load magnitude, converting an explicitly unit-annotated value.

    ``expected_dimension`` (e.g. ``"pressure"`` or ``"force"``) is enforced
    so a force value cannot be silently accepted as a pressure and vice
    versa; a mismatch raises :class:`mouse_sim.errors.UnitError`.
    """
    value = load.get(key)
    if isinstance(value, Mapping):
        if "unit" in value:
            return to_si(value.get("value", 0.0), value["unit"], expected_dimension)
        value = value.get("value")
    return value


def _isotropic_plate_stiffness(E, nu, t):
    D = E * t ** 3 / (12.0 * (1.0 - nu * nu))
    return D, nu * D, D, D * (1.0 - nu) / 2.0


def _orthotropic_plate_stiffness(E, nu12, G12, t):
    """Plate stiffnesses for a unidirectional/laminate orthotropic shell.

    D11 = D22 = E1*t^3/(12*(1-nu12^2)), D12 = nu12*D11, D66 = G12*t^3/12
    (classical lamination plate theory; E3/G13 enter only transverse shear,
    which the thin-plate Kirchhoff model does not carry).
    """
    D11 = E * t ** 3 / (12.0 * (1.0 - nu12 * nu12))
    D12 = nu12 * D11
    D66 = G12 * t ** 3 / 12.0
    return D11, D12, D11, D66


def _shell_fields(a, b, t, E, nu, wmn, series_order, D11=None, D12=None, D22=None, D66=None):
    if D11 is None:
        D11, D12, D22, D66 = _isotropic_plate_stiffness(E, nu, t)
    terms = tuple(range(1, max(int(series_order), 1) + 2, 2))
    xs = [a * i / 4.0 for i in range(5)]
    ys = [b * j / 4.0 for j in range(5)]
    w = [[0.0] * 5 for _ in range(5)]
    mxx = [[0.0] * 5 for _ in range(5)]
    myy = [[0.0] * 5 for _ in range(5)]
    mxy = [[0.0] * 5 for _ in range(5)]
    for m in terms:
        alpha = math.pi * m / a
        alpha2 = alpha * alpha
        for n in terms:
            beta = math.pi * n / b
            beta2 = beta * beta
            coeff = wmn(m, n)
            for j, y in enumerate(ys):
                siny = math.sin(beta * y)
                cosy = math.cos(beta * y)
                for i, x in enumerate(xs):
                    sinx = math.sin(alpha * x)
                    s = sinx * siny
                    w[j][i] += coeff * s
                    mxx[j][i] += coeff * alpha2 * s
                    myy[j][i] += coeff * beta2 * s
                    mxy[j][i] += coeff * alpha * beta * math.cos(alpha * x) * cosy
    factor = 6.0 / (t * t)
    stress = [[[0.0] * 5 for _ in range(5)] for _ in range(2)]
    for j in range(5):
        for i in range(5):
            # Orthotropic moment resultants Mx = -(D11*kx + D12*ky),
            # My = -(D12*kx + D22*ky), Mxy = 2*D66*kxy; reduce exactly to the
            # isotropic forms for D11=D22=D, D12=nu*D, D66=D*(1-nu)/2.
            mx = -(D11 * mxx[j][i] + D12 * myy[j][i])
            my = -(D12 * mxx[j][i] + D22 * myy[j][i])
            txy = 2.0 * D66 * mxy[j][i]
            stress[0][j][i] = _vm(mx * factor, my * factor, txy * factor)
            stress[1][j][i] = _vm(-mx * factor, -my * factor, -txy * factor)
    return w, stress


def _grid_max(w):
    best = None
    location = (0.0, 0.0)
    for j in range(5):
        for i in range(5):
            value = w[j][i]
            if best is None or abs(value) > abs(best):
                best = value
                location = (i / 4.0, j / 4.0)
    return (0.0 if best is None else best), location


def _box_filter(field):
    filtered = [[0.0] * 5 for _ in range(5)]
    for j in range(5):
        for i in range(5):
            total = 0.0
            for dj in (-1, 0, 1):
                jj = min(4, max(0, j + dj))
                for di in (-1, 0, 1):
                    ii = min(4, max(0, i + di))
                    total += field[jj][ii]
            filtered[j][i] = total / 9.0
    return filtered


def _stress_peak(fields, t, a=None, b=None):
    """Peak von Mises stress and location in metres.

    Grid indices i,j are converted to physical coordinates with the plate
    dimensions ``a``, ``b`` when supplied (x = a*i/4, y = b*j/4, z in
    metres); without dimensions the grid fractions are returned.
    """
    best = -1.0
    location = (0.0, 0.0, 0.0)
    for z in (0, 1):
        z_loc = t / 2.0 if z else -t / 2.0
        for j in range(5):
            for i in range(5):
                value = fields[z][j][i]
                if value > best:
                    best = value
                    gx = i / 4.0
                    gy = j / 4.0
                    if a is not None:
                        gx = a * gx
                    if b is not None:
                        gy = b * gy
                    location = (gx, gy, z_loc)
    return best, location


def _shell_response(method_id, a, b, t, E, nu, w, stress, flags, assumptions,
                    validity, allowable_pa, reactions, force_residual,
                    moment_residual, unsupported, displacement_location=None):
    w_max, (gx, gy) = _grid_max(w)
    raw, _ = _stress_peak(stress, t, a, b)
    smoothed = [_box_filter(stress[z]) for z in (0, 1)]
    filtered, filtered_loc = _stress_peak(smoothed, t, a, b)
    if displacement_location is None:
        displacement_location = (a * gx, b * gy, 0.0)
    # Safety factor uses the raw peak stress; the box-filtered value is
    # reported separately as a screening diagnostic.
    factor, status = _safety(allowable_pa, raw)
    return StructuralResponse(
        method_id=method_id,
        max_displacement_m=w_max,
        max_displacement_location=displacement_location,
        max_stress_pa=raw,
        max_stress_filtered_pa=filtered,
        filtered_location=filtered_loc,
        safety_factor=factor,
        safety_factor_status=status,
        reactions=reactions,
        force_residual_n=force_residual,
        moment_residual_n_m=moment_residual,
        flags=tuple(flags),
        assumptions=tuple(assumptions),
        unsupported_failure_modes=unsupported,
        validity=validity,
    )


def shell_panel_response(a_m, b_m, t_m, E_pa, nu, pressure_pa, series_order=9, allowable_pa=None,
                         D11=None, D12=None, D22=None, D66=None):
    """Simply supported thin plate under uniform pressure (Navier series).

    ``D11/D12/D22/D66`` select the orthotropic plate stiffnesses (classical
    lamination theory); when omitted the isotropic stiffnesses are derived
    from ``E_pa`` and ``nu`` and the orthotropic forms reduce exactly to the
    isotropic Navier solution.
    """
    a = _positive(a_m, "a_m")
    b = _positive(b_m, "b_m")
    t = _positive(t_m, "t_m")
    E = _positive(E_pa, "E_pa")
    p = _finite(pressure_pa, "pressure_pa")
    nu = _poisson(nu)
    order = max(1, int(series_order))
    if order % 2 == 0:
        order -= 1
    order = min(order, SERIES_ORDER_CAP)
    if D11 is None:
        D11, D12, D22, D66 = _isotropic_plate_stiffness(E, nu, t)

    def wmn(m, n):
        den = (D11 * (m / a) ** 4
               + 2.0 * (D12 + 2.0 * D66) * (m / a) ** 2 * (n / b) ** 2
               + D22 * (n / b) ** 4)
        return 16.0 * p / (math.pi ** 6 * m * n * den)

    try:
        w, stress = _shell_fields(a, b, t, E, nu, wmn, order, D11, D12, D22, D66)
    except (OverflowError, ZeroDivisionError):
        return _overflow_response(SHELL_PANEL_METHOD)
    if not _series_fields_finite(w, stress):
        return _overflow_response(SHELL_PANEL_METHOD)
    flags = []
    validity = "valid"
    ratio = t / min(a, b)
    if ratio > 0.1:
        flags.append(THIN_SHELL_OUT_OF_RANGE)
        validity = "approximate" if ratio <= 0.25 else "inconclusive"
    # Small-deflection plausibility: linear plate theory requires the peak
    # deflection to be small against the span.  An absurdly thin wall can
    # produce w/span ~ 1e6 with everything finite and "valid" — the linear
    # theory is then violated by orders of magnitude and the result must be
    # downgraded, not presented as valid.
    w_peak = _grid_max(w)[0]
    if w_peak / min(a, b) > 0.05:
        flags.append(SMALL_DEFLECTION_VIOLATED)
        if validity == "valid":
            validity = "approximate"
    order_low = order - 4
    if order_low >= 1:
        try:
            w_low, _ = _shell_fields(a, b, t, E, nu, wmn, order_low, D11, D12, D22, D66)
        except (OverflowError, ZeroDivisionError):
            return _overflow_response(SHELL_PANEL_METHOD)
        w_high = _grid_max(w)[0]
        w_low_max = _grid_max(w_low)[0]
        if abs(w_high - w_low_max) / max(w_high, 1e-30) > 0.05:
            flags.append(SERIES_NOT_CONVERGED)
            if validity == "valid":
                validity = "approximate"
    stress_low = order - 2
    if stress_low >= 1:
        try:
            _, stress_low_fields = _shell_fields(a, b, t, E, nu, wmn, stress_low, D11, D12, D22, D66)
        except (OverflowError, ZeroDivisionError):
            return _overflow_response(SHELL_PANEL_METHOD)
        stress_high_peak = _stress_peak(stress, t)[0]
        stress_low_peak = _stress_peak(stress_low_fields, t)[0]
        if abs(stress_high_peak - stress_low_peak) / max(stress_high_peak, 1e-30) > 0.05:
            if SERIES_NOT_CONVERGED not in flags:
                flags.append(SERIES_NOT_CONVERGED)
            if validity == "valid":
                validity = "approximate"
    total = p * a * b
    if not math.isfinite(total):
        return _overflow_response(SHELL_PANEL_METHOD)
    corner = total / 4.0
    reactions = {"R1": corner, "R2": corner, "R3": corner, "R4": corner}
    force_residual = total - sum(reactions.values())
    rx = total * b / 2.0 - (reactions["R3"] * b + reactions["R4"] * b)
    ry = total * a / 2.0 - (reactions["R2"] * a + reactions["R4"] * a)
    moment_residual = math.hypot(rx, ry)
    assumptions = SHELL_PANEL_ASSUMPTIONS + (
        "series order {} (odd terms); deflection convergence checked at order {},"
        " stress convergence checked at order {}".format(
            order, order_low if order_low >= 1 else order,
            stress_low if stress_low >= 1 else order,
        ),
        "equilibrium residual from four-corner reaction distribution",
    )
    return _shell_response(
        SHELL_PANEL_METHOD, a, b, t, E, nu, w, stress, flags, assumptions,
        validity, allowable_pa, reactions, force_residual, moment_residual,
        SHELL_UNSUPPORTED_FAILURE_MODES,
    )


def _shell_point_load_response(a, b, t, E, nu, force_n, location, allowable_pa=None, series_order=9,
                               D11=None, D12=None, D22=None, D66=None):
    a = _positive(a, "a_m")
    b = _positive(b, "b_m")
    t = _positive(t, "t_m")
    E = _positive(E, "E_pa")
    nu = _poisson(nu)
    force_n = _finite(force_n, "force_n")
    if D11 is None:
        D11, D12, D22, D66 = _isotropic_plate_stiffness(E, nu, t)
    x0 = _finite(location[0], "location[0]")
    y0 = _finite(location[1], "location[1]")
    if not 0.0 <= x0 <= a or not 0.0 <= y0 <= b:
        raise ValueError("point load location must lie within the shell panel bounds")
    order = max(1, int(series_order))
    if order % 2 == 0:
        order -= 1
    order = min(order, SERIES_ORDER_CAP)

    def wmn(m, n):
        den = (D11 * (m / a) ** 4
               + 2.0 * (D12 + 2.0 * D66) * (m / a) ** 2 * (n / b) ** 2
               + D22 * (n / b) ** 4)
        return (4.0 * force_n * math.sin(m * math.pi * x0 / a)
                * math.sin(n * math.pi * y0 / b)) / (a * b * math.pi ** 4 * den)

    try:
        w, stress = _shell_fields(a, b, t, E, nu, wmn, order, D11, D12, D22, D66)
    except (OverflowError, ZeroDivisionError):
        return _overflow_response(SHELL_PANEL_METHOD)
    if not _series_fields_finite(w, stress):
        return _overflow_response(SHELL_PANEL_METHOD)
    corner = force_n / 4.0
    reactions = {"R1": corner, "R2": corner, "R3": corner, "R4": corner}
    force_residual = force_n - sum(reactions.values())
    moment_residual = math.hypot(force_n * (y0 - b / 2.0), force_n * (x0 - a / 2.0))
    if not math.isfinite(force_residual) or not math.isfinite(moment_residual):
        return _overflow_response(SHELL_PANEL_METHOD)
    assumptions = SHELL_PANEL_ASSUMPTIONS + (
        "point load applied at ({:.6g}, {:.6g}) via Navier point-load series".format(x0, y0),
        "point-load series converges slowly; stresses are screening-quality only",
    )
    return _shell_response(
        SHELL_PANEL_METHOD, a, b, t, E, nu, w, stress, (),
        assumptions, "approximate", allowable_pa, reactions, force_residual,
        moment_residual, SHELL_UNSUPPORTED_FAILURE_MODES,
        # The truncated series cannot resolve the deflection peak at the
        # load point (orders up to 49 still peak at the panel center); the
        # critical region of a point-loaded plate is the LOAD POINT, so the
        # location is reported there with the convergence caveat above.
        displacement_location=(x0, y0, 0.0),
    )


def _orthotropic_shell_stiffness(E, info, t):
    """Plate stiffnesses when the material carries directional data, else None."""
    if info.get("directional"):
        return _orthotropic_plate_stiffness(E, info["nu12"], info["G12"], t)
    return None, None, None, None


def _feature_stress_concentration(load):
    """Feature-level stress concentration factor K_f = 1 + q*(K_t - 1).

    Per-template notch geometry (Peterson's stress concentration factors;
    notch sensitivity q = 0.6 for PC/ABS class polymers): button_press
    (point force) K_t = 3.0, localized_pressure K_t = 2.0, all other
    templates K_t = 1.0 (no concentration).
    """
    kind = load.get("kind") if isinstance(load, Mapping) else None
    if kind == "force" and load.get("point_load"):
        k_t, q = 3.0, 0.6
    elif kind == "pressure" and load.get("distribution") == "localized":
        k_t, q = 2.0, 0.6
    else:
        k_t, q = 1.0, 0.0
    return 1.0 + q * (k_t - 1.0)


def _disclose(result, info, allowable, shell, load=None):
    """Attach disclosed engineering caveats without re-verdicting the SF.

    Derating, temperature-out-of-range, anisotropy honesty, weld-line
    knockdown and feature stress concentration are reported on the response
    (flags/assumptions/validity reasons/new fields) but never change the
    primary safety factor or max_stress_pa.
    """
    if result.validity == "inconclusive":
        return result
    flags = list(result.flags)
    assumptions = list(result.assumptions)
    reasons = list(result.validity_reasons)
    extra = {}
    validity = result.validity

    temperature_k = info.get("temperature_k")
    if info.get("derating_applied") and temperature_k is not None:
        assumptions.append(
            "linear temperature derating of modulus/allowables applied at T={} K"
            " (source: supplier modulus-vs-temperature curves)".format(temperature_k)
        )
        reasons.append("temperature derating applied at T={} K".format(temperature_k))
    use_min = info.get("continuous_use_min_k")
    use_max = info.get("continuous_use_max_k")
    if (
        temperature_k is not None
        and use_min is not None
        and use_max is not None
        and not (use_min <= temperature_k <= use_max)
    ):
        reasons.append("usage temperature outside continuous-use range")
    anisotropy_supported = info.get("anisotropy_supported")
    if anisotropy_supported and (not shell or not info.get("directional")):
        flags.append(UNSUPPORTED_ANISOTROPY)
        assumptions.append(
            "material is anisotropic (laminate/flow-oriented polymer); isotropic"
            " closed-form solver under-predicts deflection by ~10-70% depending on"
            " orientation and weld lines; local weld-line stress is not resolvable"
        )
        reasons.append(
            "anisotropic material evaluated with an isotropic closed-form model"
        )
    weld = info.get("weld_line_factor")
    if (
        weld is not None
        and weld < 1.0
        and allowable is not None
    ):
        derated = allowable * weld
        extra["weld_line_derated_allowable_pa"] = derated
        assumptions.append(
            "weld-line strength knockdown factor {} disclosed:"
            " weld_line_derated_allowable_pa = {:.6g} Pa = allowable * {};"
            " screen against this value for weld-line risk; primary safety"
            " factor unchanged".format(weld, derated, weld)
        )
    if shell and load is not None:
        k_f = _feature_stress_concentration(load)
        if k_f > 1.0 and result.max_stress_pa is not None:
            extra["feature_peak_stress_pa"] = result.max_stress_pa * k_f
            assumptions.append(
                "feature_peak_stress_pa applies stress-concentration K_f={:.6g}"
                " to the nominal peak (Peterson's stress concentration factors,"
                " notch sensitivity q=0.6 for PC/ABS); screening only".format(k_f)
            )
    if reasons and validity == "valid":
        validity = "approximate"
    if not flags and not assumptions and not reasons and not extra:
        return result
    return replace(
        result,
        validity=validity,
        validity_reasons=tuple(reasons),
        flags=tuple(flags),
        assumptions=tuple(assumptions),
        **extra,
    )


def beam_response(load_type, L_m, E_pa, I_m4, A_m2, nu, force_n=None,
                  q_n_per_m=None, section_modulus_m3=None, allowable_pa=None):
    """Closed-form Euler-Bernoulli beam response for four load cases."""
    L = _positive(L_m, "L_m")
    E = _positive(E_pa, "E_pa")
    I = _positive(I_m4, "I_m4")
    A = _positive(A_m2, "A_m2")
    nu = _poisson(nu)
    Z = _positive(section_modulus_m3, "section_modulus_m3") if section_modulus_m3 is not None else None
    specs = {
        "cantilever_point": ("point", "cantilever"),
        "cantilever_uniform": ("uniform", "cantilever"),
        "simply_supported_point": ("point", "simple"),
        "simply_supported_uniform": ("uniform", "simple"),
    }
    if load_type not in specs:
        raise ValueError("unknown load_type {!r}".format(load_type))
    shape, support = specs[load_type]
    cantilever = support == "cantilever"
    try:
        if shape == "point":
            if force_n is None:
                raise ValueError("force_n required for a point load")
            P = _finite(force_n, "force_n")
            deflection = P * L ** 3 / (3.0 * E * I) if cantilever else P * L ** 3 / (48.0 * E * I)
            moment = P * L if cantilever else P * L / 4.0
            shear = P if cantilever else P / 2.0
            reactions = {"R1": P, "R2": 0.0} if cantilever else {"R1": P / 2.0, "R2": P / 2.0}
            applied = P
            formula = "w = P*L^3/(3*E*I), Mmax = P*L" if cantilever else "w = P*L^3/(48*E*I), Mmax = P*L/4"
        else:
            if q_n_per_m is None:
                raise ValueError("q_n_per_m required for a uniform load")
            q = _finite(q_n_per_m, "q_n_per_m")
            deflection = q * L ** 4 / (8.0 * E * I) if cantilever else 5.0 * q * L ** 4 / (384.0 * E * I)
            moment = q * L * L / 2.0 if cantilever else q * L * L / 8.0
            shear = q * L if cantilever else q * L / 2.0
            reactions = {"R1": q * L, "R2": 0.0} if cantilever else {"R1": q * L / 2.0, "R2": q * L / 2.0}
            applied = q * L
            formula = "w = q*L^4/(8*E*I), Mmax = q*L^2/2" if cantilever else "w = 5*q*L^4/(384*E*I), Mmax = q*L^2/8"
    except OverflowError:
        return _overflow_response(BEAM_METHOD)
    if cantilever:
        reactions["M1"] = -moment
    stress = moment / Z if Z is not None else None
    tau = 1.5 * shear / A
    vm = math.sqrt(stress * stress + 3.0 * tau * tau) if stress is not None else None
    computed = (deflection, moment, shear, applied, stress, tau, vm)
    if any(
        value is not None and not math.isfinite(value) for value in computed
    ) or any(not math.isfinite(value) for value in reactions.values()):
        return _overflow_response(BEAM_METHOD)
    factor, status = _safety(allowable_pa, vm)
    peak_loc = (0.0, 0.0, 0.0) if cantilever else (L / 2.0, 0.0, 0.0)
    tip_loc = (L, 0.0, 0.0) if cantilever else (L / 2.0, 0.0, 0.0)
    assumptions = BEAM_ASSUMPTIONS + (
        formula,
        "force balance via reactions: sum(R) = {:.6g} N; moment residual zero by statics".format(applied),
    )
    return StructuralResponse(
        method_id=BEAM_METHOD,
        max_displacement_m=deflection,
        max_displacement_location=tip_loc,
        max_stress_pa=vm,
        max_stress_filtered_pa=vm,
        filtered_location=peak_loc,
        safety_factor=factor,
        safety_factor_status=status,
        reactions=reactions,
        force_residual_n=applied - (reactions["R1"] + reactions["R2"]),
        moment_residual_n_m=0.0,
        flags=(),
        assumptions=assumptions,
        unsupported_failure_modes=BEAM_UNSUPPORTED_FAILURE_MODES,
        validity="valid",
    )


def solve_load_case(load_case_dict, structure_dict, material_dict, fixtures=None, solver=None):
    """Dispatch a load case to the appropriate closed-form surrogate."""
    load = load_case_dict if isinstance(load_case_dict, Mapping) else {}
    structure = structure_dict if isinstance(structure_dict, Mapping) else {}
    material = material_dict if isinstance(material_dict, (Mapping, MaterialProperties, MaterialDefinition)) else {}
    temperature_k = structure.get("temperature_k")
    if temperature_k is None:
        temperature_k = load.get("temperature_k")
    if temperature_k is not None:
        try:
            temperature_k = _finite(temperature_k, "temperature_k")
        except (TypeError, ValueError):
            temperature_k = None
    E, nu, allowable, info = _material_props(material, temperature_k)
    flags = []
    assumptions = []
    if E is None:
        flags.append(UNSUPPORTED_STIFFNESS_REDUCTION)
        assumptions.append("young modulus unavailable; stiffness cannot be computed")
        return _blank_response(flags, assumptions)
    try:
        _poisson(nu)
    except ValueError as exc:
        flags.append(INVALID_POISSON_RATIO)
        assumptions.append(str(exc))
        return _blank_response(flags, assumptions)
    s_type = structure.get("type")
    if s_type not in ("shell_panel", "beam"):
        flags.append(UNSUPPORTED_STIFFNESS_REDUCTION)
        assumptions.append("structure_dict must have type shell_panel or beam")
        return _blank_response(flags, assumptions)
    dims_ok = True
    if s_type == "shell_panel":
        a, b, t = _shell_dims(structure)
        for name, value in (("a_m", a), ("b_m", b), ("t_m", t)):
            if value is None:
                flags.append(UNSUPPORTED_STIFFNESS_REDUCTION)
                assumptions.append("shell_panel structure requires {}".format(name))
                dims_ok = False
                break
            try:
                _positive(value, name)
            except ValueError as exc:
                flags.append(UNSUPPORTED_STIFFNESS_REDUCTION)
                assumptions.append(str(exc))
                dims_ok = False
                break
    else:
        L, I, A, Z = _beam_dims(structure)
        for name, value in (("L_m", L), ("I_m4", I), ("A_m2", A)):
            if value is None:
                flags.append(UNSUPPORTED_STIFFNESS_REDUCTION)
                assumptions.append("beam structure requires {}".format(name))
                dims_ok = False
                break
            try:
                _positive(value, name)
            except ValueError as exc:
                flags.append(UNSUPPORTED_STIFFNESS_REDUCTION)
                assumptions.append(str(exc))
                dims_ok = False
                break
        if dims_ok and Z is not None:
            try:
                _positive(Z, "section_modulus_m3")
            except ValueError as exc:
                flags.append(UNSUPPORTED_STIFFNESS_REDUCTION)
                assumptions.append(str(exc))
                dims_ok = False
    if not dims_ok:
        return _blank_response(flags, assumptions)
    kind = load.get("kind")
    if kind is None:
        flags.append(UNSUPPORTED_STIFFNESS_REDUCTION)
        return _blank_response(flags, assumptions + ["load_case_dict missing kind",])
    if kind == "pressure":
        try:
            p = _load_magnitude(load, "magnitude_pa", "pressure")
        except UnitError as exc:
            flags.append(INVALID_LOAD_UNITS)
            return _blank_response(flags, assumptions + [str(exc)])
        if p is None:
            flags.append(UNSUPPORTED_STIFFNESS_REDUCTION)
            return _blank_response(flags, assumptions + ["pressure load requires magnitude_pa",])
        try:
            p = _finite(p, "pressure_pa")
        except ValueError as exc:
            flags.append(INVALID_LOAD_VALUE)
            return _blank_response(flags, assumptions + [str(exc)])
        if s_type == "shell_panel":
            a, b, t = _shell_dims(structure)
            D11, D12, D22, D66 = _orthotropic_shell_stiffness(E, info, t)
            if load.get("distribution") == "localized":
                assumptions.append(
                    "localized pressure approximated as uniform full-panel pressure;"
                    " local stress concentrations not resolved"
                )
            result = shell_panel_response(a, b, t, E, nu, p, allowable_pa=allowable,
                                          D11=D11, D12=D12, D22=D22, D66=D66)
        else:
            L, I, A, Z = _beam_dims(structure)
            width = structure.get("width_m")
            if width is None:
                flags.append(UNSUPPORTED_STIFFNESS_REDUCTION)
                return _blank_response(flags, assumptions + ["beam pressure load requires width_m",])
            try:
                q = p * _positive(width, "width_m")
            except (TypeError, ValueError) as exc:
                flags.append(INVALID_LOAD_VALUE)
                return _blank_response(flags, assumptions + [str(exc)])
            support = structure.get("support", "cantilever")
            beam_type = "cantilever_uniform" if support == "cantilever" else "simply_supported_uniform"
            result = beam_response(beam_type, L, E, I, A, nu, q_n_per_m=q,
                                   section_modulus_m3=Z, allowable_pa=allowable)
        result = _disclose(result, info, allowable, s_type == "shell_panel", load)
        return _merged(result, flags, assumptions)
    if kind == "force":
        try:
            F = _load_magnitude(load, "force_n", "force")
        except UnitError as exc:
            flags.append(INVALID_LOAD_UNITS)
            return _blank_response(flags, assumptions + [str(exc)])
        if F is None:
            flags.append(UNSUPPORTED_STIFFNESS_REDUCTION)
            return _blank_response(flags, assumptions + ["force load requires force_n",])
        try:
            F = _finite(F, "force_n")
        except ValueError as exc:
            flags.append(INVALID_LOAD_VALUE)
            return _blank_response(flags, assumptions + [str(exc)])
        point = bool(load.get("point_load", False))
        if point:
            flags.append(POINT_LOAD_SINGULARITY)
            assumptions.append("point load singularity: local contact stress not resolved")
        if s_type == "shell_panel":
            a, b, t = _shell_dims(structure)
            D11, D12, D22, D66 = _orthotropic_shell_stiffness(E, info, t)
            if point:
                location = load.get("location", (a / 2.0, b / 2.0))
                if not isinstance(location, (tuple, list)) or len(location) < 2:
                    flags.append(INVALID_LOAD_LOCATION)
                    return _blank_response(
                        flags,
                        assumptions + ["point load location must contain x and y coordinates"],
                    )
                try:
                    x0 = _finite(location[0], "location[0]")
                    y0 = _finite(location[1], "location[1]")
                except ValueError as exc:
                    flags.append(INVALID_LOAD_LOCATION)
                    return _blank_response(flags, assumptions + [str(exc)])
                if not 0.0 <= x0 <= a or not 0.0 <= y0 <= b:
                    flags.append(INVALID_LOAD_LOCATION)
                    return _blank_response(
                        flags,
                        assumptions + ["point load location must lie within the shell panel bounds"],
                    )
                result = _shell_point_load_response(a, b, t, E, nu, F, tuple(location),
                                                    allowable_pa=allowable,
                                                    D11=D11, D12=D12, D22=D22, D66=D66)
            else:
                pressure = F / (a * b)
                assumptions.append(
                    "force distributed as full-panel uniform pressure: p = F/(a*b) = {:.6g} Pa".format(pressure)
                )
                result = shell_panel_response(a, b, t, E, nu, pressure, allowable_pa=allowable,
                                              D11=D11, D12=D12, D22=D22, D66=D66)
        else:
            L, I, A, Z = _beam_dims(structure)
            support = structure.get("support", "cantilever")
            if point:
                beam_type = "cantilever_point" if support == "cantilever" else "simply_supported_point"
                result = beam_response(beam_type, L, E, I, A, nu, force_n=F,
                                       section_modulus_m3=Z, allowable_pa=allowable)
            else:
                q = F / L
                assumptions.append(
                    "force distributed as uniform line load: q = F/L = {:.6g} N/m".format(q)
                )
                beam_type = "cantilever_uniform" if support == "cantilever" else "simply_supported_uniform"
                result = beam_response(beam_type, L, E, I, A, nu, q_n_per_m=q,
                                       section_modulus_m3=Z, allowable_pa=allowable)
        if point:
            result = replace(result, validity="approximate")
        result = _disclose(result, info, allowable, s_type == "shell_panel", load)
        return _merged(result, flags, assumptions)
    if kind == "gravity":
        if not fixtures:
            flags.append(UNDERCONSTRAINED_REACTIONS)
            assumptions.append("gravity load requires fixtures; none supplied")
            return _blank_response(flags, assumptions, validity="approximate")
        flags.append(UNSUPPORTED_STIFFNESS_REDUCTION)
        assumptions.append("gravity load needs a mass model; not supported by closed-form surrogate")
        return _blank_response(flags, assumptions, validity="approximate")
    if kind == "torque":
        flags.append(UNSUPPORTED_STIFFNESS_REDUCTION)
        assumptions.append("torque loads not supported by closed-form surrogate")
        return _blank_response(flags, assumptions, validity="inconclusive")
    flags.append(UNSUPPORTED_STIFFNESS_REDUCTION)
    return _blank_response(flags, assumptions + ["unknown load kind {!r}".format(kind),])


def _merged(result, flags, assumptions):
    if not flags and not assumptions:
        return result
    return replace(
        result,
        flags=tuple(flags) + result.flags,
        assumptions=result.assumptions + tuple(assumptions),
    )


def preflight_structural_case(load_case_dict, structure_dict, material_dict, fixtures=None):
    """Return preflight issues as (code, severity, message) dicts."""
    issues = []
    load = load_case_dict if isinstance(load_case_dict, Mapping) else {}
    structure = structure_dict if isinstance(structure_dict, Mapping) else {}
    material = material_dict if isinstance(material_dict, (Mapping, MaterialProperties, MaterialDefinition)) else {}
    s_type = structure.get("type")
    if s_type not in ("shell_panel", "beam"):
        issues.append({
            "code": "UNSUPPORTED_STRUCTURE_TYPE",
            "severity": "error",
            "message": "structure_dict type must be shell_panel or beam",
        })
    elif s_type == "shell_panel":
        a, b, t = _shell_dims(structure)
        dimensions_valid = True
        for name, value in (("a_m", a), ("b_m", b), ("t_m", t)):
            try:
                _positive(value, name)
            except (TypeError, ValueError) as exc:
                dimensions_valid = False
                issues.append({
                    "code": "INVALID_STRUCTURE_DIMENSION",
                    "severity": "error",
                    "message": str(exc),
                })
        if dimensions_valid and t / min(a, b) > 0.1:
            issues.append({
                "code": THIN_SHELL_OUT_OF_RANGE,
                "severity": "warning",
                "message": "panel thickness exceeds 10% of span; thin-plate theory approximate",
            })
    E, nu, _, _ = _material_props(material)
    if E is None:
        issues.append({
            "code": UNSUPPORTED_STIFFNESS_REDUCTION,
            "severity": "error",
            "message": "young_modulus_pa missing; stiffness cannot be computed",
        })
    try:
        _poisson(nu)
    except ValueError as exc:
        issues.append({
            "code": INVALID_POISSON_RATIO,
            "severity": "error",
            "message": str(exc),
        })
    kind = load.get("kind")
    if kind == "pressure" and load.get("magnitude_pa") is None:
        issues.append({"code": "MISSING_LOAD_MAGNITUDE", "severity": "error",
                       "message": "pressure load requires magnitude_pa"})
    if kind == "force" and load.get("force_n") is None:
        issues.append({"code": "MISSING_LOAD_MAGNITUDE", "severity": "error",
                       "message": "force load requires force_n"})
    if load.get("point_load"):
        issues.append({
            "code": POINT_LOAD_SINGULARITY,
            "severity": "warning",
            "message": "point loads produce singular stress fields; treat results as approximate",
        })
    if not fixtures and kind in ("gravity", "torque"):
        issues.append({
            "code": UNDERCONSTRAINED_REACTIONS,
            "severity": "warning",
            "message": "no fixtures supplied; reactions underdetermined",
        })
    return tuple(issues)


MOUSE_LOAD_TEMPLATES = {
    "shell_flex": {
        "name": "Shell flex",
        "description": "Uniform pressure flexing the bottom shell panel.",
        "default_loads": {"kind": "pressure", "magnitude_pa": 1000.0,
                          "distribution": "uniform", "direction": (0, 0, -1)},
        "fixture_assumptions": "panel simply supported on all four edges",
        "acceptance_notes": "accept when safety_factor >= 1.0 against tensile_allowable; recheck edge stress with a detailed model",
    },
    "side_grip": {
        "name": "Side grip squeeze",
        "description": "Lateral squeeze force applied to the side walls.",
        "default_loads": {"kind": "force", "force_n": 8.0, "point_load": False,
                          "distribution": "uniform", "direction": (1, 0, 0)},
        "fixture_assumptions": "side walls fixed along the bottom edge",
        "acceptance_notes": "watch combined bending plus shear; keep safety_factor >= 1.0",
    },
    "button_press": {
        "name": "Button press",
        "description": "Point press on a mouse button actuator.",
        "default_loads": {"kind": "force", "force_n": 5.0, "point_load": True,
                          "direction": (0, 0, -1)},
        "fixture_assumptions": "button pivot modeled as a simply supported edge",
        "acceptance_notes": "flagged POINT_LOAD_SINGULARITY; verify local contact with a dedicated model",
    },
    "torsion": {
        "name": "Torsion twist",
        "description": "Twisting torque applied between shell halves.",
        "default_loads": {"kind": "torque", "torque_n_m": 0.05, "direction": (0, 0, 1)},
        "fixture_assumptions": "one half held fixed, torque applied to the other",
        "acceptance_notes": "closed-form surrogate does not support torque; requires FE or a dedicated method",
    },
    "localized_pressure": {
        "name": "Localized pressure",
        "description": "High pressure over a small region (e.g., thumb rest).",
        "default_loads": {"kind": "pressure", "magnitude_pa": 5000.0,
                          "distribution": "localized", "direction": (0, 0, -1)},
        "fixture_assumptions": "panel simply supported on all four edges",
        "acceptance_notes": "localized pressure approaches point-load behavior; check the singularity flag",
        "limitations": "dispatched as uniform full-panel pressure; localized distribution is not resolved",
    },
}


__all__ = [
    "BEAM_METHOD",
    "BEAM_UNSUPPORTED_FAILURE_MODES",
    "CLOSED_FORM_METHOD",
    "INVALID_LOAD_LOCATION",
    "INVALID_LOAD_VALUE",
    "INVALID_LOAD_UNITS",
    "INVALID_POISSON_RATIO",
    "MOUSE_LOAD_TEMPLATES",
    "NUMERIC_OVERFLOW",
    "POINT_LOAD_SINGULARITY",
    "SCREENING_SURROGATE_MODEL_ID",
    "SERIES_NOT_CONVERGED",
    "SERIES_ORDER_CAP",
    "SHELL_PANEL_ASSUMPTIONS",
    "SHELL_PANEL_METHOD",
    "SHELL_UNSUPPORTED_FAILURE_MODES",
    "SOLVER_CAPABILITIES",
    "SolverCapabilities",
    "StructuralResponse",
    "THIN_SHELL_OUT_OF_RANGE",
    "UNDERCONSTRAINED_REACTIONS",
    "UNSUPPORTED_ANISOTROPY",
    "UNSUPPORTED_STIFFNESS_REDUCTION",
    "beam_response",
    "preflight_structural_case",
    "shell_panel_response",
    "solve_load_case",
]
