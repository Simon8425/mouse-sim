"""Explicit unit conversion helpers used by the data model.

Quantities persisted by ``mouse_sim`` are SI-normalized.  This module keeps
the conversion table deliberately small and explicit rather than guessing a
unit from a number or a field name.
"""

from dataclasses import dataclass
import math

from .errors import UnitError


@dataclass(frozen=True)
class UnitSpec:
    name: str
    dimension: str
    factor_to_si: float
    offset_to_si: float = 0.0


def _spec(name, dimension, factor, offset=0.0):
    return UnitSpec(name, dimension, float(factor), float(offset))


# Offsets are applied as: SI = value * factor_to_si + offset_to_si.
UNIT_SPECS = {
    # Length
    "m": _spec("m", "length", 1),
    "mm": _spec("mm", "length", 1e-3),
    "cm": _spec("cm", "length", 1e-2),
    "um": _spec("um", "length", 1e-6),
    "in": _spec("in", "length", 0.0254),
    "ft": _spec("ft", "length", 0.3048),
    # Mass
    "kg": _spec("kg", "mass", 1),
    "g": _spec("g", "mass", 1e-3),
    "mg": _spec("mg", "mass", 1e-6),
    "lb": _spec("lb", "mass", 0.45359237),
    "oz": _spec("oz", "mass", 0.028349523125),
    # Time and frequency
    "s": _spec("s", "time", 1),
    "ms": _spec("ms", "time", 1e-3),
    "min": _spec("min", "time", 60),
    "Hz": _spec("Hz", "frequency", 1),
    "kHz": _spec("kHz", "frequency", 1000),
    # Force, pressure, torque, energy, and power
    "N": _spec("N", "force", 1),
    "kN": _spec("kN", "force", 1000),
    "lbf": _spec("lbf", "force", 4.4482216152605),
    "Pa": _spec("Pa", "pressure", 1),
    "kPa": _spec("kPa", "pressure", 1000),
    "MPa": _spec("MPa", "pressure", 1e6),
    "GPa": _spec("GPa", "pressure", 1e9),
    "psi": _spec("psi", "pressure", 6894.757293168),
    "N*m": _spec("N*m", "torque", 1),
    "Nmm": _spec("Nmm", "torque", 1e-3),
    "N-mm": _spec("N-mm", "torque", 1e-3),
    "lbf*in": _spec("lbf*in", "torque", 0.1129848290276167),
    "J": _spec("J", "energy", 1),
    "kJ": _spec("kJ", "energy", 1000),
    "W": _spec("W", "power", 1),
    # Acceleration, velocity, stiffness, density, area, and volume
    "m/s": _spec("m/s", "velocity", 1),
    "mm/s": _spec("mm/s", "velocity", 1e-3),
    "m/s^2": _spec("m/s^2", "acceleration", 1),
    "mm/s^2": _spec("mm/s^2", "acceleration", 1e-3),
    "g0": _spec("g0", "acceleration", 9.80665),
    "N/m": _spec("N/m", "stiffness", 1),
    "N/mm": _spec("N/mm", "stiffness", 1000),
    "lbf/in": _spec("lbf/in", "stiffness", 175.1268352468),
    "kg/m^3": _spec("kg/m^3", "density", 1),
    "g/cm^3": _spec("g/cm^3", "density", 1000),
    "kg/m^2": _spec("kg/m^2", "areal_density", 1),
    "mm^2": _spec("mm^2", "area", 1e-6),
    "m^2": _spec("m^2", "area", 1),
    "mm^3": _spec("mm^3", "volume", 1e-9),
    "cm^3": _spec("cm^3", "volume", 1e-6),
    "m^3": _spec("m^3", "volume", 1),
    # Angles and temperature
    "rad": _spec("rad", "angle", 1),
    "deg": _spec("deg", "angle", math.pi / 180.0),
    "K": _spec("K", "temperature", 1),
    "degC": _spec("degC", "temperature", 1, 273.15),
    "C": _spec("C", "temperature", 1, 273.15),
    "degF": _spec("degF", "temperature", 5.0 / 9.0, 255.3722222222222),
    "F": _spec("F", "temperature", 5.0 / 9.0, 255.3722222222222),
    # Dimensionless quantities and strain
    "1": _spec("1", "dimensionless", 1),
    "%": _spec("%", "dimensionless", 0.01),
    "strain": _spec("strain", "dimensionless", 1),
}


_ALIASES = {
    "meter": "m",
    "meters": "m",
    "millimeter": "mm",
    "millimeters": "mm",
    "centimeter": "cm",
    "centimeters": "cm",
    "micrometer": "um",
    "micrometers": "um",
    "kilogram": "kg",
    "kilograms": "kg",
    "gram": "g",
    "grams": "g",
    "newton": "N",
    "newtons": "N",
    "pascal": "Pa",
    "pascals": "Pa",
    "degree": "deg",
    "degrees": "deg",
    "celsius": "degC",
    "fahrenheit": "degF",
    "m/s²": "m/s^2",
    "mm/s²": "mm/s^2",
    # ``g`` is the canonical gram spelling above.  Gravity has its own
    # explicit spelling so the mass-unit alias cannot shadow it.
    "gravity": "g0",
    "standardgravity": "g0",
}


def normalize_unit(unit):
    """Return the canonical spelling for a known unit."""

    if not isinstance(unit, str) or not unit.strip():
        raise UnitError("unit must be a non-empty string")
    candidate = unit.strip().replace(" ", "")
    candidate = _ALIASES.get(candidate, candidate)
    if candidate not in UNIT_SPECS:
        raise UnitError("unknown unit: {!r}".format(unit))
    return candidate


def unit_spec(unit):
    """Return the explicit specification for ``unit``."""

    return UNIT_SPECS[normalize_unit(unit)]


def unit_dimension(unit):
    return unit_spec(unit).dimension


def si_unit_for_dimension(dimension):
    """Return the persisted SI unit spelling for a dimension."""

    values = {
        "length": "m",
        "mass": "kg",
        "time": "s",
        "frequency": "Hz",
        "force": "N",
        "pressure": "Pa",
        "torque": "N*m",
        "energy": "J",
        "power": "W",
        "velocity": "m/s",
        "acceleration": "m/s^2",
        "stiffness": "N/m",
        "density": "kg/m^3",
        "areal_density": "kg/m^2",
        "area": "m^2",
        "volume": "m^3",
        "angle": "rad",
        "temperature": "K",
        "dimensionless": "1",
    }
    try:
        return values[dimension]
    except KeyError:
        raise UnitError("unknown dimension: {!r}".format(dimension))


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise UnitError("unit value must be numeric")
    if not math.isfinite(number):
        raise UnitError("unit value must be finite")
    return number


def to_si(value, unit, expected_dimension=None):
    """Convert an explicitly supplied value to SI."""

    spec = unit_spec(unit)
    if expected_dimension is not None and spec.dimension != expected_dimension:
        raise UnitError(
            "unit {!r} has dimension {}, expected {}".format(
                unit, spec.dimension, expected_dimension
            )
        )
    return _finite(value) * spec.factor_to_si + spec.offset_to_si


def from_si(value_si, unit, expected_dimension=None):
    """Convert an SI value to an explicitly requested unit."""

    spec = unit_spec(unit)
    if expected_dimension is not None and spec.dimension != expected_dimension:
        raise UnitError(
            "unit {!r} has dimension {}, expected {}".format(
                unit, spec.dimension, expected_dimension
            )
        )
    return (_finite(value_si) - spec.offset_to_si) / spec.factor_to_si


def convert(value, from_unit, to_unit, expected_dimension=None):
    """Convert between two units of the same explicit dimension."""

    source = unit_spec(from_unit)
    target = unit_spec(to_unit)
    if source.dimension != target.dimension:
        raise UnitError(
            "cannot convert {} to {}".format(source.dimension, target.dimension)
        )
    if expected_dimension is not None and source.dimension != expected_dimension:
        raise UnitError(
            "unit dimension {} does not match {}".format(
                source.dimension, expected_dimension
            )
        )
    return from_si(to_si(value, source.name), target.name)


def normalize_value(value, unit):
    """Return ``(value_si, canonical_si_unit)`` for a source value."""

    source = unit_spec(unit)
    return to_si(value, source.name), si_unit_for_dimension(source.dimension)
