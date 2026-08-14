import type { FeaResult, RenderMode } from '../api/contracts';

/** Format a stress magnitude in MPa with adaptive units (kPa below 0.1 MPa). */
export function formatStress(mpa: number): string {
  if (!Number.isFinite(mpa)) return '—';
  if (mpa >= 100) return `${mpa.toFixed(0)} MPa`;
  if (mpa >= 1) return `${mpa.toFixed(1)} MPa`;
  if (mpa >= 0.1) return `${mpa.toFixed(2)} MPa`;
  return `${(mpa * 1000).toFixed(1)} kPa`;
}

/** Maximum damage across the per-vertex fields and procedural peaks. */
export function computeMaxDamage(fea: FeaResult | null): number {
  if (!fea) return 0;
  let maxDamage = 0;
  for (const field of fea.objects) {
    for (const value of field.damage) {
      if (typeof value === 'number' && Number.isFinite(value) && value > maxDamage) {
        maxDamage = value;
      }
    }
  }
  for (const entry of fea.procedural) {
    if (entry.yield_stress_pa > 0 && entry.peak_stress_pa > 0) {
      const entryPeak = Math.min(1, Math.max(0, entry.peak_stress_pa / entry.yield_stress_pa));
      if (entryPeak > maxDamage) maxDamage = entryPeak;
    }
  }
  return maxDamage;
}

const LEGEND_GRADIENT =
  'linear-gradient(90deg, #0066ff 0%, #00ccff 28%, #00cc66 50%, #ffcc00 72%, #ff2200 100%)';

// Yield-mode legend: none -> plastic zone (whitening) -> tear (cutout).
const YIELD_LEGEND_GRADIENT =
  'linear-gradient(90deg, #5f636b 0%, #e8e8e8 70%, #ff6a3d 92%, #141414 100%)';

/** Human-readable disclosure for known FEA flags; raw code otherwise. */
const FLAG_LABELS: Record<string, string> = {
  FEA_STRUCTURAL_VALIDITY_INCONCLUSIVE:
    'Structural solve is inconclusive — the contour is an approximation',
  FEA_PEAK_STRESS_UNAVAILABLE: 'No stress field available for this run',
  FEA_YIELD_REFERENCE_UNAVAILABLE: 'No yield reference — contour hidden',
  FEA_IMPACT_CENTER_UNAVAILABLE: 'No impact point — hotspot unknown',
  FEA_IMPACT_CENTER_DEFAULTED: 'Hotspot centered on the critical region (no drop)',
  FEA_FALLOFF_DEFAULTED: 'Stress falloff defaulted (no drop compression)',
  FEA_DROP_ESTIMATE_UNAVAILABLE: 'No drop estimate — dent depth zero',
  FEA_TRANSFORM_ASSUMED_IDENTITY: 'Object transform assumed identity',
  FEA_NO_MESHED_OBJECTS: 'No mesh geometry to contour',
  FEA_NON_FINITE_VERTEX: 'Non-finite vertices zeroed in the display field',
};

/**
 * Professional HUD overlay for the FEA render modes: a compact readout panel
 * with the von-Mises legend bar and the true (non-normalized) headline
 * values. Replaces the in-scene 3D badge sprite.
 */
export function FeaHud({
  mode,
  fea,
}: {
  mode: RenderMode;
  fea: FeaResult | null;
}): JSX.Element | null {
  if (mode === 'default' || !fea?.computed) return null;

  const peakMpa = typeof fea.peak?.stress_mpa === 'number' ? fea.peak.stress_mpa : null;
  const yieldMpa =
    typeof fea.yield_stress_pa === 'number' && Number.isFinite(fea.yield_stress_pa)
      ? fea.yield_stress_pa / 1e6
      : null;
  const damage = typeof fea.peak?.damage === 'number' ? fea.peak.damage : null;
  const safetyFactor =
    typeof fea.safety_factor === 'number' && Number.isFinite(fea.safety_factor)
      ? fea.safety_factor
      : null;
  const legendMax = peakMpa !== null ? peakMpa : 0;
  const legendMin = 0;

  // Zone vertex counts over the per-vertex fields (dent >= 0.7, tear > 0.92).
  let dentVertices = 0;
  let tearVertices = 0;
  for (const field of fea.objects) {
    for (const value of field.damage) {
      if (typeof value !== 'number' || !Number.isFinite(value)) continue;
      if (value >= fea.dent_threshold) dentVertices += 1;
      if (value > fea.tear_threshold) tearVertices += 1;
    }
  }

  return (
    <div className="fea-hud" role="status" aria-label="FEA stress analysis overlay">
      <div className="fea-hud__header">
        <span className="fea-hud__title">FEA STRESS ANALYSIS</span>
        <span className={`fea-hud__mode fea-hud__mode--${mode}`}>
          {mode === 'yield' ? 'YIELD SHADER' : 'HEATMAP'}
        </span>
      </div>
      <div className="fea-hud__legend">
        <span className="fea-hud__legend-label">
          {mode === 'yield' ? '0' : formatStress(legendMin)}
        </span>
        <div className="fea-hud__legend-track">
          <div
            className="fea-hud__legend-bar"
            style={{
              background: mode === 'yield' ? YIELD_LEGEND_GRADIENT : LEGEND_GRADIENT,
            }}
            aria-hidden="true"
          />
          {mode === 'fea' ? (
            <div className="fea-hud__legend-marks" aria-hidden="true">
              <span
                className="fea-hud__legend-mark fea-hud__legend-mark--dent"
                style={{ left: `${fea.dent_threshold * 100}%` }}
              >
                <span className="fea-hud__legend-mark-line" />
                <span className="fea-hud__legend-mark-label">dent</span>
              </span>
              <span
                className="fea-hud__legend-mark fea-hud__legend-mark--tear"
                style={{ left: `${fea.tear_threshold * 100}%` }}
              >
                <span className="fea-hud__legend-mark-line" />
                <span className="fea-hud__legend-mark-label">tear</span>
              </span>
            </div>
          ) : null}
        </div>
        <span className="fea-hud__legend-label">
          {mode === 'yield' ? '0.92 tear' : formatStress(legendMax)}
        </span>
      </div>
      <dl className="fea-hud__rows">
        <div className="fea-hud__row">
          <dt>Peak stress</dt>
          <dd>{peakMpa !== null ? formatStress(peakMpa) : '—'}</dd>
        </div>
        <div className="fea-hud__row">
          <dt>Yield stress</dt>
          <dd>{yieldMpa !== null ? formatStress(yieldMpa) : '—'}</dd>
        </div>
        <div className="fea-hud__row">
          <dt>Max damage D</dt>
          <dd>{damage !== null ? damage.toFixed(4) : '—'}</dd>
        </div>
        {safetyFactor !== null ? (
          <div className="fea-hud__row">
            <dt>Safety factor</dt>
            <dd>{safetyFactor.toFixed(2)}</dd>
          </div>
        ) : null}
        {dentVertices > 0 ? (
          <div className="fea-hud__row">
            <dt>Plastic zone vertices</dt>
            <dd>{dentVertices}</dd>
          </div>
        ) : null}
        {tearVertices > 0 ? (
          <div className="fea-hud__row">
            <dt>Tear zone vertices</dt>
            <dd>{tearVertices}</dd>
          </div>
        ) : null}
      </dl>
      {fea.flags.length > 0 ? (
        <ul className="fea-hud__flags">
          {fea.flags.map((flag) => (
            <li key={flag}>{FLAG_LABELS[flag] ?? flag}</li>
          ))}
        </ul>
      ) : null}
      <p className="fea-hud__footnote">
        Contour auto-normalized to the field peak · values shown are true
      </p>
    </div>
  );
}
