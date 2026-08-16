export const LENGTH_UNITS = ['m', 'mm', 'cm', 'um', 'in', 'ft'] as const;
export type LengthUnit = (typeof LENGTH_UNITS)[number];

export const LENGTH_TO_M: Record<LengthUnit, number> = {
  m: 1,
  mm: 1e-3,
  cm: 1e-2,
  um: 1e-6,
  in: 0.0254,
  ft: 0.3048,
};

export function isLengthUnit(value: string): value is LengthUnit {
  return (LENGTH_UNITS as readonly string[]).includes(value);
}

export function lengthToM(value: number, unit: string): number {
  if (isLengthUnit(unit)) {
    return value * LENGTH_TO_M[unit];
  }
  return value;
}

export function lengthFromM(valueM: number, unit: string): number {
  if (isLengthUnit(unit)) {
    return valueM / LENGTH_TO_M[unit];
  }
  return valueM;
}

function formatSig(val: number, sigDigits = 3): string {
  if (val === 0) return '0';
  const abs = Math.abs(val);
  if (abs >= 1e6 || abs < 1e-6) {
    return val.toExponential(sigDigits - 1);
  }
  return Number(val.toPrecision(sigDigits)).toString();
}

export function formatLength(valueM: number | null, unit?: string, digits = 3): string {
  if (valueM === null || !Number.isFinite(valueM)) return '—';
  if (unit && isLengthUnit(unit)) {
    const val = lengthFromM(valueM, unit);
    return `${formatSig(val, digits)} ${unit}`;
  }
  const abs = Math.abs(valueM);
  if (abs < 1 && abs > 0) {
    return `${formatSig(valueM * 1000, digits)} mm`;
  }
  return `${formatSig(valueM, digits)} m`;
}

export function formatMass(kg: number | null): string {
  if (kg === null || !Number.isFinite(kg)) return '—';
  const abs = Math.abs(kg);
  if (abs < 1e-3 && abs > 0) {
    return `${formatSig(kg * 1e6, 3)} mg`;
  }
  if (abs < 1 && abs > 0) {
    return `${formatSig(kg * 1000, 3)} g`;
  }
  return `${formatSig(kg, 3)} kg`;
}

export function formatVolume(m3: number | null): string {
  if (m3 === null || !Number.isFinite(m3)) return '—';
  const abs = Math.abs(m3);
  if (abs === 0) return '0 m³';
  if (abs < 1e-6) {
    return `${formatSig(m3 * 1e9, 3)} mm³`;
  }
  if (abs < 1e-3) {
    return `${formatSig(m3 * 1e6, 3)} cm³`;
  }
  if (abs < 1) {
    return `${formatSig(m3 * 1000, 3)} L`;
  }
  return `${formatSig(m3, 3)} m³`;
}

export function formatForce(n: number | null): string {
  if (n === null || !Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  if (abs < 1 && abs > 0) {
    return `${formatSig(n * 1000, 3)} mN`;
  }
  if (abs >= 1e6) {
    return `${formatSig(n / 1e6, 3)} MN`;
  }
  if (abs >= 1000) {
    return `${formatSig(n / 1000, 3)} kN`;
  }
  return `${formatSig(n, 3)} N`;
}

export function formatPressure(pa: number | null): string {
  if (pa === null || !Number.isFinite(pa)) return '—';
  const abs = Math.abs(pa);
  if (abs >= 1e6) {
    return `${formatSig(pa / 1e6, 3)} MPa`;
  }
  if (abs >= 1000) {
    return `${formatSig(pa / 1000, 3)} kPa`;
  }
  return `${formatSig(pa, 3)} Pa`;
}

export function formatEnergy(j: number | null): string {
  if (j === null || !Number.isFinite(j)) return '—';
  const abs = Math.abs(j);
  if (abs < 1 && abs > 0) {
    return `${formatSig(j * 1000, 3)} mJ`;
  }
  return `${formatSig(j, 3)} J`;
}

export function formatAcceleration(acc: number | null): string {
  if (acc === null || !Number.isFinite(acc)) return '—';
  return `${formatSig(acc, 3)} m/s²`;
}

export function formatDuration(s: number | null): string {
  if (s === null || !Number.isFinite(s)) return '—';
  const abs = Math.abs(s);
  if (abs < 1 && abs > 0) {
    return `${formatSig(s * 1000, 3)} ms`;
  }
  return `${formatSig(s, 3)} s`;
}
