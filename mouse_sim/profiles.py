"""Deterministic usage profiles for gaming-mouse durability screening.

Each profile describes a user class as daily-average rates drawn from
gaming-industry usage studies and e-sports statistics (click/APM rates,
cursor travel, scroll use, handling events).  ``profile_usage`` projects a
profile over a lifespan into the lifecycle usage schema consumed by the
pipeline (see ``mouse_sim.lifecycle``): pure function of key + lifespan, so
identical inputs always yield identical output.
"""

import math

# Screening constants consumed by the component models: one daily thermal
# cycle of 30 K, and 3 g rms rough-transport vibration (ISTA 3A class).
TEMPERATURE_CYCLES_PER_DAY = 1
DELTA_TEMPERATURE_K = 30
TRANSPORT_VIBRATION_G_RMS = 3.0
# Drop energy reference: 0.06 kg ultralight mouse in a 0.5 m free fall
# (0.06 * 9.80665 * 0.5 = 0.2942 J, rounded to 0.29 J).
DROP_ENERGY_REFERENCE_J = 0.29
DAYS_PER_YEAR = 365.0

PROFILE_KEYS = ("esports_fps", "esports_moba", "productivity", "general")

# Daily-average usage per profile class (gaming-industry usage studies,
# e-sports statistics; "drops_every_days" is the mean handling interval).
PROFILE_CATALOG = {
    "esports_fps": {
        "name": "Esports FPS",
        "description": (
            "Competitive FPS player: clicks 8,000/day (heavy sustained "
            "clicking, ~2.9M/year), slide 2.0 km/day (fast flicks, ~600 "
            "km/year), scroll 200 wheel steps/day, drops 1 per 90 days (~4/year "
            "- tournament/transport handling), grip: claw."
        ),
        "usage": {
            "clicks_per_day": 8000,
            "slide_km_per_day": 2.0,
            "scroll_per_day": 200,
            "drops_every_days": 90,
        },
    },
    "esports_moba": {
        "name": "Esports MOBA",
        "description": (
            "MOBA/RTS player: clicks 12,000/day (very high APM, ~4.4M/year), "
            "slide 1.0 km/day, scroll 150 wheel steps/day, drops 1 per 120 days."
        ),
        "usage": {
            "clicks_per_day": 12000,
            "slide_km_per_day": 1.0,
            "scroll_per_day": 150,
            "drops_every_days": 120,
        },
    },
    "productivity": {
        "name": "Productivity",
        "description": (
            "Office/knowledge worker: clicks 3,000/day, slide 0.3 km/day, "
            "scroll 50 wheel steps/day, drops 1 per 180 days."
        ),
        "usage": {
            "clicks_per_day": 3000,
            "slide_km_per_day": 0.3,
            "scroll_per_day": 50,
            "drops_every_days": 180,
        },
    },
    "general": {
        "name": "General",
        "description": (
            "Consumer: clicks 1,500/day, slide 0.2 km/day, scroll 30 "
            "wheel steps/day, drops 1 per 180 days."
        ),
        "usage": {
            "clicks_per_day": 1500,
            "slide_km_per_day": 0.2,
            "scroll_per_day": 30,
            "drops_every_days": 180,
        },
    },
}

ASSUMPTIONS = (
    "clicks are sustained average rates; burst clicking (8-12 clicks/s) is "
    "represented through the daily average",
    "drop energy reference 0.29 J per drop (0.5 m free fall of a 0.06 kg ultralight mouse)",
    "temperature cycle 30 K/day and 3 g rms transport vibration are screening "
    "values (ISTA 3A class)",
)


def validate_profile(profile_key):
    """Return the normalized (case-folded) profile key or raise ValueError."""
    if not isinstance(profile_key, str):
        raise ValueError(
            "usage profile must be a string, got {}".format(type(profile_key).__name__)
        )
    key = profile_key.strip().casefold()
    if key not in PROFILE_CATALOG:
        raise ValueError("unknown usage profile: {!r}".format(profile_key))
    return key


def profile_usage(profile_key, lifespan_days=730):
    """Project a profile over ``lifespan_days`` into the lifecycle usage schema.

    Totals are daily_rate * lifespan (actuation/scroll/drops rounded to whole
    events, slide to one decimal); yearly summary rates are derived from the
    totals so that rate * years == total.  Deterministic.
    """
    key = validate_profile(profile_key)
    lifespan = _lifespan_days(lifespan_days)
    spec = PROFILE_CATALOG[key]["usage"]
    years = lifespan / DAYS_PER_YEAR

    actuation_cycles = round(spec["clicks_per_day"] * lifespan)
    scroll_rotations = round(spec["scroll_per_day"] * lifespan)
    slide_km = round(spec["slide_km_per_day"] * lifespan, 1)
    drops = int(math.floor(lifespan / spec["drops_every_days"] + 0.5))

    usage = {
        "prior_drops": drops,
        "prior_impact_energy_j": round(drops * DROP_ENERGY_REFERENCE_J, 6),
        "actuation_cycles": actuation_cycles,
        "slide_distance_km": slide_km,
        "age_days": lifespan,
        "scroll_encoder_rotations": scroll_rotations,
        "temperature_cycles_per_day": TEMPERATURE_CYCLES_PER_DAY,
        "transport_vibration_g_rms": TRANSPORT_VIBRATION_G_RMS,
        "delta_temperature_k": DELTA_TEMPERATURE_K,
        "pad_surface": "cloth",
    }
    summary = {
        "years": round(years, 6),
        "clicks_per_year": round(actuation_cycles / years),
        "slide_km_per_year": round(slide_km / years, 1),
        "scroll_per_year": round(scroll_rotations / years),
        "drops_per_year": round(drops / years, 1),
    }
    return {
        "profile": key,
        "name": PROFILE_CATALOG[key]["name"],
        "lifespan_days": lifespan,
        "usage": usage,
        "summary": summary,
        "assumptions": list(ASSUMPTIONS),
    }


def combine_usage(usage_a, usage_b):
    """Element-wise sum of the numeric usage fields (profile + prior usage).

    Non-numeric fields keep the value from ``usage_a`` on conflict; fields
    present in only one side pass through unchanged.
    """
    combined = dict(usage_a or {})
    for key, value in (usage_b or {}).items():
        if key not in combined:
            combined[key] = value
        elif _is_numeric(combined[key]) and _is_numeric(value):
            combined[key] = combined[key] + value
    return combined


def _is_numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _lifespan_days(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError("lifespan_days must be a positive number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("lifespan_days must be a positive number")
    if value <= 0:
        raise ValueError("lifespan_days must be a positive number")
    return value


__all__ = [
    "ASSUMPTIONS",
    "DELTA_TEMPERATURE_K",
    "DROP_ENERGY_REFERENCE_J",
    "PROFILE_CATALOG",
    "PROFILE_KEYS",
    "TEMPERATURE_CYCLES_PER_DAY",
    "TRANSPORT_VIBRATION_G_RMS",
    "combine_usage",
    "profile_usage",
    "validate_profile",
]
