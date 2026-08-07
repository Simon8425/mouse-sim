import math
import unittest

from mouse_sim.errors import UnitError
from mouse_sim.units import (
    convert,
    from_si,
    normalize_unit,
    normalize_value,
    si_unit_for_dimension,
    to_si,
    unit_dimension,
)


class LengthConversionTests(unittest.TestCase):
    def test_mm_cm_m(self):
        self.assertAlmostEqual(to_si(1000.0, "mm"), 1.0, places=12)
        self.assertAlmostEqual(to_si(100.0, "cm"), 1.0, places=12)
        self.assertAlmostEqual(to_si(1.0, "m"), 1.0, places=12)
        self.assertAlmostEqual(from_si(1.0, "mm"), 1000.0, places=12)
        self.assertAlmostEqual(convert(1000.0, "mm", "m"), 1.0, places=12)
        self.assertAlmostEqual(convert(1.0, "m", "cm"), 100.0, places=12)
        self.assertAlmostEqual(convert(1.0, "m", "mm"), 1000.0, places=12)

    def test_in_and_ft(self):
        self.assertAlmostEqual(to_si(1.0, "in"), 0.0254, places=12)
        self.assertAlmostEqual(to_si(1.0, "ft"), 0.3048, places=12)
        self.assertAlmostEqual(convert(12.0, "in", "ft"), 1.0, places=12)
        self.assertAlmostEqual(convert(1.0, "ft", "in"), 12.0, places=12)

    def test_um(self):
        self.assertAlmostEqual(to_si(1000.0, "um"), 1e-3, places=12)

    def test_length_alias_spellings(self):
        self.assertEqual(normalize_unit("millimeter"), "mm")
        self.assertEqual(normalize_unit("meters"), "m")
        self.assertEqual(normalize_unit("centimeters"), "cm")


class MassAndDensityConversionTests(unittest.TestCase):
    def test_mass_units(self):
        self.assertAlmostEqual(to_si(1.0, "kg"), 1.0, places=12)
        self.assertAlmostEqual(to_si(1000.0, "g"), 1.0, places=12)
        self.assertAlmostEqual(to_si(1e6, "mg"), 1.0, places=12)
        self.assertAlmostEqual(to_si(1.0, "lb"), 0.45359237, places=12)
        self.assertAlmostEqual(to_si(1.0, "oz"), 0.028349523125, places=12)

    def test_density_units(self):
        self.assertAlmostEqual(to_si(1.0, "g/cm^3"), 1000.0, places=12)
        self.assertAlmostEqual(to_si(1.0, "kg/m^3"), 1.0, places=12)
        self.assertAlmostEqual(convert(1.0, "g/cm^3", "kg/m^3"), 1000.0, places=12)

    def test_area_and_volume(self):
        self.assertAlmostEqual(to_si(1.0, "mm^2"), 1e-6, places=12)
        self.assertAlmostEqual(to_si(1.0, "cm^3"), 1e-6, places=12)
        self.assertAlmostEqual(to_si(1.0, "mm^3"), 1e-9, places=12)


class PressureAndStiffnessConversionTests(unittest.TestCase):
    def test_pressure_units(self):
        self.assertAlmostEqual(to_si(1.0, "kPa"), 1000.0, places=12)
        self.assertAlmostEqual(to_si(1.0, "MPa"), 1e6, places=12)
        self.assertAlmostEqual(to_si(1.0, "GPa"), 1e9, places=12)
        self.assertAlmostEqual(to_si(1.0, "psi"), 6894.757293168, places=12)
        self.assertAlmostEqual(convert(1.0, "GPa", "MPa"), 1000.0, places=12)

    def test_stiffness_units(self):
        self.assertAlmostEqual(to_si(1.0, "N/mm"), 1000.0, places=12)
        self.assertAlmostEqual(to_si(1.0, "N/m"), 1.0, places=12)


class GravityUnitTests(unittest.TestCase):
    def test_gravity_alias(self):
        self.assertEqual(normalize_unit("gravity"), "g0")
        self.assertAlmostEqual(to_si(1.0, "g0"), 9.80665, places=12)
        self.assertEqual(unit_dimension("g0"), "acceleration")

    def test_gravity_alias_cannot_shadow_grams(self):
        self.assertEqual(normalize_unit("g"), "g")
        self.assertEqual(unit_dimension("g"), "mass")


class DimensionGuardTests(unittest.TestCase):
    def test_dimension_mismatch_raises(self):
        with self.assertRaises(UnitError):
            to_si(1.0, "mm", expected_dimension="mass")
        with self.assertRaises(UnitError):
            to_si(1.0, "N", expected_dimension="pressure")
        with self.assertRaises(UnitError):
            convert(1.0, "mm", "kg")

    def test_unknown_unit_raises(self):
        with self.assertRaises(UnitError):
            normalize_unit("furlong")
        with self.assertRaises(UnitError):
            to_si(1.0, "")

    def test_nan_and_inf_values_rejected(self):
        with self.assertRaises(UnitError):
            to_si(float("nan"), "m")
        with self.assertRaises(UnitError):
            to_si(float("inf"), "m")
        with self.assertRaises(UnitError):
            from_si(float("nan"), "m")

    def test_non_numeric_value_rejected(self):
        with self.assertRaises(UnitError):
            to_si("abc", "m")


class TemperatureAndMiscTests(unittest.TestCase):
    def test_temperature_offset(self):
        self.assertAlmostEqual(to_si(0.0, "degC"), 273.15, places=12)
        self.assertAlmostEqual(to_si(100.0, "degC"), 373.15, places=12)
        self.assertAlmostEqual(to_si(32.0, "degF"), 273.15, places=12)

    def test_normalize_value(self):
        value, unit = normalize_value(1.0, "cm")
        self.assertAlmostEqual(value, 0.01, places=12)
        self.assertEqual(unit, "m")
        value, unit = normalize_value(2.0, "g")
        self.assertAlmostEqual(value, 0.002, places=12)
        self.assertEqual(unit, "kg")

    def test_si_unit_for_dimension(self):
        self.assertEqual(si_unit_for_dimension("length"), "m")
        self.assertEqual(si_unit_for_dimension("density"), "kg/m^3")
        self.assertEqual(si_unit_for_dimension("pressure"), "Pa")
        with self.assertRaises(UnitError):
            si_unit_for_dimension("bogus")

    def test_round_trip(self):
        self.assertAlmostEqual(convert(2.5, "m", "in"), to_si(2.5, "m") / 0.0254, places=12)


if __name__ == "__main__":
    unittest.main()
