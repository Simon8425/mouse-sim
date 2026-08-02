import { describe, it, expect } from 'vitest';
import {
  isLengthUnit,
  lengthToM,
  lengthFromM,
  formatLength,
  formatMass,
  formatForce,
  formatPressure,
  formatEnergy,
  formatAcceleration,
  formatDuration,
} from '../lib/units';

describe('units library', () => {
  it('identifies length units', () => {
    expect(isLengthUnit('mm')).toBe(true);
    expect(isLengthUnit('km')).toBe(false);
  });

  it('converts to and from meters', () => {
    expect(lengthToM(110, 'mm')).toBeCloseTo(0.11);
    expect(lengthFromM(0.11, 'mm')).toBeCloseTo(110);
  });

  it('formats length with auto and explicit units', () => {
    expect(formatLength(0.11)).toContain('110 mm');
    expect(formatLength(1.5)).toContain('1.5 m');
    expect(formatLength(null)).toBe('—');
  });

  it('formats mass', () => {
    expect(formatMass(0.0696)).toBe('69.6 g');
    expect(formatMass(1.5)).toBe('1.5 kg');
    expect(formatMass(null)).toBe('—');
  });

  it('formats force, pressure, energy, acceleration, duration', () => {
    expect(formatForce(1000)).toBe('1 kN');
    expect(formatPressure(1000000)).toBe('1 MPa');
    expect(formatEnergy(0.5)).toBe('500 mJ');
    expect(formatAcceleration(9.81)).toBe('9.81 m/s²');
    expect(formatDuration(0.012)).toBe('12 ms');
  });
});
