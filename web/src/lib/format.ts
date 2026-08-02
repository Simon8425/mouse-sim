export function formatNumber(value: number | null, digits = 3): string {
  if (value === null || !Number.isFinite(value)) return '—';
  if (value === 0) return '0';
  const abs = Math.abs(value);
  if (abs >= 1e6 || abs < 1e-6) {
    return value.toExponential(digits - 1);
  }
  return Number(value.toPrecision(digits)).toLocaleString('en-US');
}

export function formatSigned(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—';
  if (value > 0) return `+${formatNumber(value)}`;
  if (value < 0) return `\u2212${formatNumber(Math.abs(value))}`;
  return '0';
}

export function formatVector3(v: readonly number[] | null): string {
  if (!Array.isArray(v) || v.length !== 3 || !v.every((n) => typeof n === 'number' && Number.isFinite(n))) {
    return '—';
  }
  return `(${formatNumber(v[0])}, ${formatNumber(v[1])}, ${formatNumber(v[2])})`;
}

export function formatMatrix3(m: readonly (readonly number[])[] | readonly number[] | null): string {
  if (!m || !Array.isArray(m)) return '—';
  if (m.length === 3 && Array.isArray(m[0])) {
    const rows = (m as readonly (readonly number[])[]).map(
      (row) => `[${row.map((cell) => formatNumber(cell)).join(', ')}]`,
    );
    return rows.join(' / ');
  }
  if (m.length === 9) {
    const nums = m as readonly number[];
    const r0 = `[${formatNumber(nums[0])}, ${formatNumber(nums[1])}, ${formatNumber(nums[2])}]`;
    const r1 = `[${formatNumber(nums[3])}, ${formatNumber(nums[4])}, ${formatNumber(nums[5])}]`;
    const r2 = `[${formatNumber(nums[6])}, ${formatNumber(nums[7])}, ${formatNumber(nums[8])}]`;
    return `${r0} / ${r1} / ${r2}`;
  }
  return '—';
}

export function formatPercent(fraction: number | null): string {
  if (fraction === null || !Number.isFinite(fraction)) return '—';
  return `${(fraction * 100).toFixed(1)}%`;
}

export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}\u2026`;
}
