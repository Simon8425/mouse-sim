import { describe, it, expect } from 'vitest';
import {
  formatNumber,
  formatSigned,
  formatVector3,
  formatMatrix3,
  formatPercent,
  truncate,
} from '../lib/format';

describe('format library', () => {
  it('formats numbers with 3 sig figs', () => {
    expect(formatNumber(12.3456)).toBe('12.3');
    expect(formatNumber(null)).toBe('—');
  });

  it('formats signed numbers', () => {
    expect(formatSigned(5.2)).toContain('+');
    expect(formatSigned(-5.2)).toContain('\u2212');
    expect(formatSigned(0)).toBe('0');
  });

  it('formats vector3 and matrix3', () => {
    expect(formatVector3([1, 2, 3])).toBe('(1, 2, 3)');
    expect(formatVector3(null)).toBe('—');
    expect(formatMatrix3([[1, 0, 0], [0, 1, 0], [0, 0, 1]])).toContain('[1, 0, 0]');
  });

  it('formats percent and truncate', () => {
    expect(formatPercent(0.425)).toBe('42.5%');
    expect(truncate('hello world', 5)).toBe('hell…');
  });
});
