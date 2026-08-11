"""Deterministic lifecycle-usage model for durability screening.

The pipeline can carry a ``lifecycle`` request section describing how much
the device has been used (prior drops, accumulated impact energy, actuation
cycles, slide distance, age).  This module turns that history into honest,
documented degradation factors: Miner-rule fatigue accumulation, PTFE skate
wear, and actuation-exceedance flags.  Every model is a labeled screening
estimate; nothing here invents precision.

The degradation factors feed the drop simulator as multiplicative scales on
restitution (damaged material absorbs more energy) and friction (worn skates
expose the base polymer), and are disclosed in ``result["lifecycle"]`` so the
applied history is auditable.  The response's ``next_usage`` snapshot lets the
client persist the accumulated history for the following run.

FATIGUE MODEL (what it means, exactly):

  Each drop is one event with impact energy ``E``.  The reference law is
  ``N(E) = 1e6 * (0.5 J / E) ** 2.5`` cycles to exhaustion (energy-based S-N
  slope of 2.5, typical of engineering polymers).  Miner accumulation is the
  EVENT-WISE sum ``D = sum_i 1/N(E_i)``; fatigue is exhausted at ``D >= 1``.

  Properties of the event-wise sum (all verified by tests):
  - D is non-negative and monotone non-decreasing: appending an event never
    reduces damage;
  - D(A) + D(B) == D(A + B) and reordering invariance hold exactly below the
    saturation cap (1e15) and to floating-point associativity (1 ulp at
    1e-15 relative); at or above the cap every value means "exhausted"
    (D >= 1), so the exhaustion verdict is preserved under splitting and
    merging even though the reported number saturates;
  - zero-energy events contribute exactly 0;
  - events with ``N(E) < 1`` (E large) each contribute > 1, so D >= 1.

  WHAT THE MODEL DOES NOT CLAIM: it is not a crack-initiation/growth law, it
  makes no stress-localization claim, and it says nothing about the shell's
  actual stress state — ``E`` is the rigid-body impact energy, not a local
  stress.  The 0.5 J / 1e6 / 2.5 constants are class-level screening values
  (typical polymer S-N slope), not a validated material fatigue curve.

  When the client supplies only the aggregate history ``(prior_drops,
  prior_impact_energy_j)`` the energy distribution is unknown, so the model
  applies the documented UNIFORM-EVENT approximation ``D = n/N(E_total/n)``.
  By Jensen's inequality (1/N convex in E) this UNDER-reports the event-wise
  sum of a heterogeneous history; the approximation is disclosed in the
  diagnostics whenever it is used.  Clients with measured per-drop energies
  should supply ``prior_drop_energies_j`` for the exact event-wise path.

  Input domains (documented): drop counts are clamped to
  ``MAX_PRIOR_DROPS`` (10^9) and per-event energies to
  ``MAX_EVENT_ENERGY_J`` (1e6 J — four orders of magnitude above any
  hand-drop) with a disclosure diagnostic; non-finite (NaN/Inf) or negative
  values are rejected as invalid input, never silently converted into a
  valid physical value.
"""

import math

# Fatigue screening law (event-wise Miner accumulation, polymer-style):
# an event of the reference energy E0 = 0.5 J supports 1e6 cycles; higher
# energies shorten life with a power-law exponent of 2.5 (typical of
# energy-based S-N slopes for engineering polymers).  Damage is the
# event-wise sum D = sum 1/N(E_i).
REFERENCE_IMPACT_ENERGY_J = 0.5
REFERENCE_CYCLES = 1e6
FATIGUE_EXPONENT = 2.5
# Documented screening input domain (see module docstring).
MAX_PRIOR_DROPS = 10 ** 9
MAX_EVENT_ENERGY_J = 1e6
# Damage saturation: values above this are all "exhausted" (D >= 1); the cap
# keeps rounding and reporting safe while preserving the exhaustion verdict.
_DAMAGE_SATURATION = 1e15
# Degradation: each unit of fatigue index reduces the effective restitution
# by up to 7% (micro-cracks absorb impact energy).  Audit: 10% derate equals
# ~19% stiffness loss, the top of the defensible band; 5-7% (~10-13%
# stiffness loss) is the mid-range screening choice.
FATIGUE_RESTITUTION_DERATE = 0.07
# PTFE skate wear: surface-dependent rate (mm per slide kilometre).
# Cloth pad: 0.0001 mm/km = 0.01 mm/100 km (published PTFE-on-cloth-pad
# wear); hard pad: 0.002 mm/km = 0.2 mm/100 km.
SKATE_INITIAL_MM = 0.4
SKATE_WEAR_RATE_MM_PER_KM_CLOTH = 0.0001
SKATE_WEAR_RATE_MM_PER_KM_HARD = 0.002
# Friction rises from the PTFE value toward the exposed base polymer as the
# skate thins: friction_scale = 1 + (1 - t/t0)*(mu_polymer/mu_ptfe - 1)*0.5.
# mu_polymer = 0.35 (ABS), mu_ptfe = 0.10 gives a ceiling of ~2.25x; the 0.5
# blending factor is a documented screening choice (full exposure would be
# physically too aggressive for a worn-but-present skate).
MU_PTFE = 0.10
MU_POLYMER = 0.35
FRICTION_BLENDING_FACTOR = 0.5
# Rated switch actuations per switch class (manufacturer ratings).
RATED_SWITCH_ACTUATIONS = {
    "mechanical": 20_000_000,  # Omron D2FC class
    "optical": 60_000_000,  # Razer/Kailh class
    "unknown": 20_000_000,
}
DEFAULT_SWITCH_TYPE = "unknown"
# Rated scroll encoder rotations (ALPS EC11 mechanical encoder class).
# Ratings are quoted in full REVOLUTIONS; usage counts WHEEL STEPS (detents,
# as users perceive them), so usage is converted with the detent count per
# revolution before comparison.
SCROLL_ENCODER_RATED_ROTATIONS = 25_000
SCROLL_ENCODER_DETENTS_PER_REVOLUTION = 24


def _clamp(value, low, high):
    return max(low, min(high, value))


def _event_damage(energy_j):
    """Miner damage of ONE event at ``energy_j``: 1/N(E), overflow-safe.

    Computed in log10 space: ``log10(D) = 2.5*log10(E/0.5) - 6``.  Tiny
    events underflow to 0 (no damage); the result is saturated at
    ``_DAMAGE_SATURATION`` so reporting never overflows.
    """
    if energy_j <= 0.0:
        return 0.0
    log_damage = FATIGUE_EXPONENT * math.log10(energy_j / REFERENCE_IMPACT_ENERGY_J) - math.log10(
        REFERENCE_CYCLES
    )
    if log_damage > math.log10(_DAMAGE_SATURATION):
        return _DAMAGE_SATURATION
    return 10.0 ** log_damage


def _drop_count(value):
    """Strict drop-count coercion: absent -> 0, invalid -> ValueError."""
    if value is None:
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("drop count must be a finite number") from None
    if not math.isfinite(number) or number < 0.0:
        raise ValueError("drop count must be a finite non-negative number")
    return int(number)


def _energy_value(value):
    """Strict energy coercion: absent -> 0.0, non-finite/negative -> error."""
    if value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("impact energy must be a finite number") from None
    if not math.isfinite(number):
        raise ValueError("impact energy must be a finite number")
    if number < 0.0:
        raise ValueError("impact energy must be non-negative")
    return number


def fatigue_damage_index(prior_drop_count, prior_impact_energy_j, drop_energies_j=None):
    """Event-wise Miner damage from the prior drop history.

    With ``drop_energies_j`` (a sequence of per-event energies) the damage is
    the exact event-wise sum ``D = sum_i 1/N(E_i)``: monotone non-decreasing
    in the event set, split/merge consistent, reorder invariant.  Without it,
    the documented UNIFORM-EVENT approximation ``D = n/N(E_total/n)`` is
    applied (a lower bound for heterogeneous histories; see module docstring).
    Zero drops (or zero energy) yield no damage; values above 1 mean the
    screening life estimate is exhausted.  Raises ValueError on non-finite or
    negative inputs rather than silently converting them.
    """
    if drop_energies_j is not None:
        if isinstance(drop_energies_j, (str, bytes)) or not hasattr(drop_energies_j, "__iter__"):
            raise ValueError("drop_energies_j must be a sequence of event energies")
        total = 0.0
        for value in drop_energies_j:
            energy = _energy_value(value)
            if energy <= 0.0:
                continue
            total += _event_damage(energy)
        return min(total, _DAMAGE_SATURATION)
    count = _drop_count(prior_drop_count)
    energy = _energy_value(prior_impact_energy_j)
    if count <= 0 or energy <= 0.0:
        return 0.0
    average_energy = energy / count
    return min(count * _event_damage(average_energy), _DAMAGE_SATURATION)


def skate_wear_rate_mm_per_km(pad_surface):
    """Wear rate (mm/km) for the pad surface ('cloth' or 'hard')."""
    if str(pad_surface or "").casefold() == "hard":
        return SKATE_WEAR_RATE_MM_PER_KM_HARD
    return SKATE_WEAR_RATE_MM_PER_KM_CLOTH


def skate_remaining_mm(slide_km, pad_surface="cloth"):
    """Remaining PTFE skate thickness after linear wear over ``slide_km``."""
    rate = skate_wear_rate_mm_per_km(pad_surface)
    return max(0.0, SKATE_INITIAL_MM - rate * max(0.0, float(slide_km or 0.0)))


def _switch_type(value):
    if value in ("mechanical", "optical", "unknown"):
        return value
    return DEFAULT_SWITCH_TYPE


def _pad_surface(value):
    if value in ("cloth", "hard"):
        return value
    return "cloth"


def degradation_factors(usage):
    """Compute degradation factors from a usage snapshot.

    Returns (restitution_scale, friction_scale, damage, diagnostics) where
    ``damage`` is a dict of screening metrics and ``diagnostics`` lists the
    models applied.  Deterministic: a pure function of the usage snapshot.

    Raises ValueError on non-finite or negative usage values (invalid input);
    huge-but-finite values are clamped to the documented screening domain
    (``MAX_PRIOR_DROPS`` drops, ``MAX_EVENT_ENERGY_J`` per event) with an
    explicit disclosure diagnostic.
    """
    usage = dict(usage or {})
    prior_drops = _drop_count(usage.get("prior_drops"))
    prior_energy = _energy_value(usage.get("prior_impact_energy_j"))
    prior_energies = usage.get("prior_drop_energies_j")
    clamped_notes = []
    if prior_drops > MAX_PRIOR_DROPS:
        clamped_notes.append(
            "prior_drops {} exceeds the documented screening maximum {}; clamped".format(
                prior_drops, MAX_PRIOR_DROPS
            )
        )
        prior_drops = MAX_PRIOR_DROPS
    energies = None
    if prior_energies is not None:
        energies = []
        for value in prior_energies:
            energy = _energy_value(value)
            if energy > MAX_EVENT_ENERGY_J:
                clamped_notes.append(
                    "event energy {} J exceeds the documented screening maximum {} J; clamped".format(
                        energy, MAX_EVENT_ENERGY_J
                    )
                )
                energy = MAX_EVENT_ENERGY_J
            energies.append(energy)
    elif prior_energy > MAX_EVENT_ENERGY_J:
        clamped_notes.append(
            "prior_impact_energy_j {} J exceeds the documented screening maximum {} J; clamped".format(
                prior_energy, MAX_EVENT_ENERGY_J
            )
        )
        prior_energy = MAX_EVENT_ENERGY_J
    actuation_cycles = _drop_count(usage.get("actuation_cycles"))
    slide_km = _energy_value(usage.get("slide_distance_km"))
    age_days = _energy_value(usage.get("age_days"))
    switch_type = _switch_type(usage.get("switch_type"))
    pad_surface = _pad_surface(usage.get("pad_surface"))
    scroll_rotations = _drop_count(usage.get("scroll_encoder_rotations"))

    fatigue = fatigue_damage_index(prior_drops, prior_energy, drop_energies_j=energies)
    restitution_scale = 1.0 - FATIGUE_RESTITUTION_DERATE * _clamp(fatigue, 0.0, 1.0)
    restitution_scale = _clamp(restitution_scale, 0.5, 1.0)

    skate = skate_remaining_mm(slide_km, pad_surface)
    skate_fraction = skate / SKATE_INITIAL_MM
    friction_scale = 1.0 + (1.0 - skate_fraction) * (MU_POLYMER / MU_PTFE - 1.0) * FRICTION_BLENDING_FACTOR
    friction_scale = _clamp(
        friction_scale, 1.0, 1.0 + (MU_POLYMER / MU_PTFE - 1.0) * FRICTION_BLENDING_FACTOR
    )

    rated_switch = RATED_SWITCH_ACTUATIONS.get(switch_type, RATED_SWITCH_ACTUATIONS[DEFAULT_SWITCH_TYPE])
    damage = {
        "fatigue_index": round(fatigue, 6),
        "fatigue_exhausted": fatigue >= 1.0,
        "fatigue_model": (
            "event_wise_energies"
            if energies is not None
            else "uniform_event_approximation"
        ),
        "skate_remaining_mm": round(skate, 4),
        "actuation_cycles": actuation_cycles,
        "actuation_exceeded": actuation_cycles > rated_switch,
        "switch_type": switch_type,
        "pad_surface": pad_surface,
        "scroll_encoder_rotations": scroll_rotations,
        "scroll_encoder_exceeded": scroll_rotations / SCROLL_ENCODER_DETENTS_PER_REVOLUTION
        > SCROLL_ENCODER_RATED_ROTATIONS,
        "age_days": age_days,
    }
    diagnostics = list(clamped_notes)
    if fatigue > 0.0:
        if energies is not None:
            diagnostics.append(
                "Miner-rule fatigue accumulation: {} prior drop event(s) with "
                "per-event energies (event-wise sum, fatigue index {:.6f}); "
                "restitution derated {:.1f}%".format(
                    len(energies), fatigue, 100.0 * (1.0 - restitution_scale)
                )
            )
        else:
            diagnostics.append(
                "Miner-rule fatigue accumulation: {} prior drop(s), {:.2f} J total "
                "impact energy; UNIFORM-EVENT APPROXIMATION ({:.4f} J average per event) "
                "— a lower bound for heterogeneous histories; restitution derated {:.1f}%".format(
                    prior_drops, prior_energy, prior_energy / prior_drops,
                    100.0 * (1.0 - restitution_scale),
                )
            )
    if slide_km > 0.0:
        diagnostics.append(
            "PTFE skate wear over {:.1f} km on a {} pad leaves {:.2f} mm; "
            "friction scaled x{:.3f}".format(slide_km, pad_surface, skate, friction_scale)
        )
    if damage["actuation_exceeded"]:
        diagnostics.append(
            "{} actuation cycles exceed the rated {} for {} switches".format(
                actuation_cycles, rated_switch, switch_type
            )
        )
    if damage["scroll_encoder_exceeded"]:
        diagnostics.append(
            "{} scroll wheel steps exceed the rated {} encoder rotations "
            "({} steps per revolution)".format(
                scroll_rotations,
                SCROLL_ENCODER_RATED_ROTATIONS,
                SCROLL_ENCODER_DETENTS_PER_REVOLUTION,
            )
        )
    if age_days > 0.0:
        diagnostics.append(
            "age_days is recorded but has no mechanical effect; creep derating "
            "requires documented stress/time/temperature (ISO 899-1)"
        )
    return restitution_scale, friction_scale, damage, diagnostics


def next_usage(usage, drop_count, drop_energy_j, drop_energies_j=None):
    """Accumulate the just-run test into the usage snapshot for the client.

    When the incoming snapshot carries per-event energies
    (``prior_drop_energies_j``), the just-run drops' energies are appended
    event-wise (exact chaining, full precision).  Otherwise the aggregate
    counters are incremented under the same uniform-event approximation the
    fatigue model uses.  ``drop_count`` must be non-negative and
    ``drop_energy_j`` must be finite and non-negative.
    """
    if drop_count is None:
        drop_count = 0
    try:
        count = int(drop_count)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("drop_count must be a finite number") from None
    if count < 0:
        raise ValueError("drop_count must be non-negative")
    run_energy = _energy_value(drop_energy_j)
    usage = dict(usage or {})
    snapshot = {
        "prior_drops": _drop_count(usage.get("prior_drops")) + count,
        "prior_impact_energy_j": round(
            _energy_value(usage.get("prior_impact_energy_j")) + run_energy, 6
        ),
        "actuation_cycles": _drop_count(usage.get("actuation_cycles")),
        "slide_distance_km": round(_energy_value(usage.get("slide_distance_km")), 4),
        "age_days": round(_energy_value(usage.get("age_days")), 3),
        "switch_type": _switch_type(usage.get("switch_type")),
        "pad_surface": _pad_surface(usage.get("pad_surface")),
        "scroll_encoder_rotations": _drop_count(usage.get("scroll_encoder_rotations")),
    }
    prior_energies = usage.get("prior_drop_energies_j")
    if prior_energies is not None:
        events = [_energy_value(value) for value in prior_energies]
        if drop_energies_j is not None:
            events.extend(_energy_value(value) for value in drop_energies_j)
        elif count > 0:
            per_event = run_energy / count
            events.extend([per_event] * count)
        # Full precision: rounding stored event energies would destroy tiny
        # events and break split/merge consistency of the chained history.
        snapshot["prior_drop_energies_j"] = events
    return snapshot


__all__ = [
    "degradation_factors",
    "fatigue_damage_index",
    "next_usage",
    "skate_remaining_mm",
    "skate_wear_rate_mm_per_km",
    "RATED_SWITCH_ACTUATIONS",
]
