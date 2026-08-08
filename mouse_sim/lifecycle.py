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
"""

import math

# Fatigue screening law (event-wise Miner accumulation, polymer-style):
# each prior drop is one event at the AVERAGE event energy Ebar = E_total/n;
# an event of the reference energy E0 = 0.5 J supports 1e6 cycles and higher
# energies shorten life with a power-law exponent of 2.5 (typical of
# energy-based S-N slopes for engineering polymers).  Damage D = n/N(Ebar)
# therefore scales linearly with drop count; a lumped law applied to the
# accumulated total inflates damage by n^2.5 (the audited 33,000x at 64
# drops).  The law is disclosed as a screening estimate.
REFERENCE_IMPACT_ENERGY_J = 0.5
REFERENCE_CYCLES = 1e6
FATIGUE_EXPONENT = 2.5
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


def fatigue_damage_index(prior_drop_count, prior_impact_energy_j):
    """Event-wise Miner damage from the prior drop count and total energy.

    ``D = n/N(Ebar)`` with ``Ebar = E_total/n`` and the energy-based cycles
    law above; each prior drop counts as one event at the average event
    energy, so damage scales linearly with the number of drops.  Zero drops
    (or zero total energy) yield no damage; values above 1 mean the
    screening life estimate is exhausted.
    """
    count = max(0, int(prior_drop_count or 0))
    energy = max(0.0, float(prior_impact_energy_j or 0.0))
    if count <= 0 or energy <= 0.0:
        return 0.0
    average_energy = energy / count
    cycles = REFERENCE_CYCLES * (REFERENCE_IMPACT_ENERGY_J / average_energy) ** FATIGUE_EXPONENT
    return count / max(cycles, 1.0)


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
    """
    usage = dict(usage or {})
    prior_drops = _positive_int(usage.get("prior_drops"))
    prior_energy = _positive_float(usage.get("prior_impact_energy_j"))
    actuation_cycles = _positive_int(usage.get("actuation_cycles"))
    slide_km = _positive_float(usage.get("slide_distance_km"))
    age_days = _positive_float(usage.get("age_days"))
    switch_type = _switch_type(usage.get("switch_type"))
    pad_surface = _pad_surface(usage.get("pad_surface"))
    scroll_rotations = _positive_int(usage.get("scroll_encoder_rotations"))

    fatigue = fatigue_damage_index(prior_drops, prior_energy)
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
    diagnostics = []
    if fatigue > 0.0:
        diagnostics.append(
            "Miner-rule fatigue accumulation: {} prior drop(s), {:.2f} J total "
            "impact energy ({:.4f} J average per event); restitution derated {:.1f}%".format(
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


def next_usage(usage, drop_count, drop_energy_j):
    """Accumulate the just-run test into the usage snapshot for the client."""
    usage = dict(usage or {})
    return {
        "prior_drops": _positive_int(usage.get("prior_drops")) + int(drop_count),
        "prior_impact_energy_j": round(
            _positive_float(usage.get("prior_impact_energy_j")) + max(0.0, float(drop_energy_j)), 6
        ),
        "actuation_cycles": _positive_int(usage.get("actuation_cycles")),
        "slide_distance_km": round(_positive_float(usage.get("slide_distance_km")), 4),
        "age_days": round(_positive_float(usage.get("age_days")), 3),
        "switch_type": _switch_type(usage.get("switch_type")),
        "pad_surface": _pad_surface(usage.get("pad_surface")),
        "scroll_encoder_rotations": _positive_int(usage.get("scroll_encoder_rotations")),
    }


def _positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return number if number > 0 else 0


def _positive_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or number <= 0.0:
        return 0.0
    return number


__all__ = [
    "degradation_factors",
    "fatigue_damage_index",
    "next_usage",
    "skate_remaining_mm",
    "skate_wear_rate_mm_per_km",
    "RATED_SWITCH_ACTUATIONS",
]
