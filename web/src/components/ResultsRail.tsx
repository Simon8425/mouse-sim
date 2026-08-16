/**
 * Results rail — the single results panel. Shows one clear verdict, the key
 * numbers, the test configuration that produced them, and a short list of
 * actionable issues. No tabs, no run bookkeeping, no engineering disclosures.
 */
import { type ReactNode } from 'react';
import type {
  ErrorEntry,
  FeaResult,
  Issue,
  PipelineResult,
  ValidationFinding,
} from '../api/contracts';
import { isFiniteNumber } from '../api/contracts';
import type { LiveDropData } from '../scene/SceneViewport';
import { useProjectStore } from '../state/projectStore';
import { selectHasStaleResult } from '../state/selectors';
import { severityTone } from '../lib/status';
import {
  formatForce,
  formatLength,
  formatMass,
  formatPressure,
} from '../lib/units';
import { formatNumber } from '../lib/format';
import { StatusBadge } from './StatusBadge';

/** Overall result verdict shown to the user. */
type Verdict = 'pass' | 'warn' | 'fail' | 'none';

/** A single headline number in the results panel. */
interface Metric {
  label: string;
  value: ReactNode;
  raw: number | null;
}

/** One issue row (warnings and worse only). */
interface IssueRow {
  severity: string;
  message: string;
  code?: string;
}

/** Recommendation text attached to a finding (e.g. OPTICAL_TRACKING_LOD_SHIFT). */
const RECOMMENDATIONS: Record<string, string> = {
  OPTICAL_TRACKING_LOD_SHIFT:
    'Add EVA foam dampening under the PCB near the sensor, shorten the PCB standoff span, or thicken the board to keep lens z-shift under 0.15 mm.',
  SCREW_BOSS_WALL_THIN:
    'Add 45° gussets at the boss root or increase the boss outer diameter to reach the 0.5 mm minimum wall thickness.',
  SCREW_ENGAGEMENT_SHORT:
    'Increase the engaged thread length to at least 2.5x the screw diameter.',
  BATTERY_LATCH_DISLODGED:
    'Add EVA foam dampening around the cell cradle or increase the latch hook height/retention force.',
  BATTERY_CRUSH_RISK:
    'Add EVA foam dampening between the cell and the chassis to cut the transmitted shock load.',
  BATTERY_SHOCK_EXCEEDED:
    'Add EVA foam dampening around the cell cradle to attenuate the peak acceleration.',
  SCREW_PULLOUT_RISK:
    'Increase the boss engagement length or switch to a larger-diameter screw to raise the thread-stripping capacity.',
  SCREW_BOSS_HOOP_FAIL:
    'Add 45° gussets at the boss root or increase the boss wall thickness to resist radial cracking.',
};

const SURFACE_LABELS: Record<string, string> = {
  concrete: 'Concrete',
  wood: 'Hardwood',
  foam: 'Foam mat',
  steel: 'Steel plate',
};

const ORIENTATION_LABELS: Record<string, string> = {
  flat: 'Flat',
  edge: 'Edge',
  corner: 'Corner',
  random: 'Random',
};

const TEST_LABELS: Record<string, string> = {
  drop: 'Drop Test',
  impact: 'Impact Test',
  tumble: 'Tumble Test',
  population: 'Population Analysis',
};

const VERDICT_LABEL: Record<Verdict, string> = {
  pass: 'PASS',
  warn: 'WARN',
  fail: 'FAIL',
  none: '—',
};

const VERDICT_STATEMENT: Record<Verdict, string> = {
  pass: 'All checks passed',
  warn: 'Passed with warnings',
  fail: 'Failing checks need attention',
  none: 'No analysis data yet',
};

/**
 * Derive the overall verdict from the pipeline result. Failures anywhere are
 * failures; the shell result is the authoritative source, with population and
 * secondary component screening as contributors.
 */
function computeVerdict(result: PipelineResult): Verdict {
  if (result.errors.length > 0) return 'fail';
  const shellStatus = String(result.shell?.status ?? '').toLowerCase();
  if (shellStatus === 'fail') return 'fail';
  const populationVerdict = String(result.population?.verdict ?? '').toLowerCase();
  if (populationVerdict === 'fail') return 'fail';
  if (shellStatus === 'warn' || populationVerdict === 'warn') return 'warn';

  const screening = result.component_screening ?? result.components;
  const screeningFailures = screening?.summary?.fail_count ?? 0;
  if (screeningFailures > 0) return 'warn';

  const hasData =
    result.shell !== null ||
    result.impact?.result !== null ||
    result.drop_simulation !== null ||
    result.population !== null ||
    result.structural !== null;
  if (!hasData) return 'none';

  const rows = buildIssueRows(result);
  if (rows.some((row) => row.severity === 'error' || row.severity === 'blocker')) return 'fail';
  if (rows.some((row) => row.severity === 'warning' || row.severity === 'warn')) return 'warn';
  return 'pass';
}

/** Collect warnings and worse from validation findings, issues, and errors. */
function buildIssueRows(result: PipelineResult): IssueRow[] {
  const rows: IssueRow[] = [];
  const push = (severity: string, message: string, code?: string) => {
    const normalized = severity.toLowerCase();
    if (
      normalized === 'blocker' ||
      normalized === 'error' ||
      normalized === 'warning' ||
      normalized === 'warn'
    ) {
      rows.push({ severity: normalized, message, code });
    } else {
      // Unknown severity: never silently drop a finding — disclose it.
      rows.push({
        severity: 'warning',
        message: code ? `${code}: ${message}` : message,
        code,
      });
    }
  };

  let degenerateCount = 0;
  let openMeshCount = 0;
  let selfIntersectionCount = 0;
  let draftMaterialCount = 0;

  const rawIssues: { severity: string; message: string; code?: string }[] = [];

  for (const finding of (result.validation?.findings ?? []) as ValidationFinding[]) {
    rawIssues.push({ severity: finding.severity, message: finding.message, code: finding.code });
  }
  for (const issue of (result.issues ?? []) as Issue[]) {
    rawIssues.push({ severity: issue.severity, message: issue.message, code: issue.code });
  }

  for (const item of rawIssues) {
    const msg = item.message;
    if (msg.includes('degenerate triangle')) {
      degenerateCount++;
    } else if (msg.includes('boundary edge')) {
      openMeshCount++;
    } else if (msg.includes('self-intersection sweep limit') || msg.includes('self-intersection')) {
      selfIntersectionCount++;
    } else if (msg.includes("approval_state is 'draft'") || msg.includes('properties are not approved')) {
      draftMaterialCount++;
    } else {
      push(item.severity, msg, item.code);
    }
  }

  if (degenerateCount > 0) {
    push('warning', `${degenerateCount} parts contain degenerate display triangles (repaired for solver)`);
  }
  if (openMeshCount > 0) {
    push('warning', `${openMeshCount} parts have open mesh boundaries (tessellation approximation)`);
  }
  if (selfIntersectionCount > 0) {
    push('warning', `${selfIntersectionCount} parts exceed self-intersection limit (geometry approximated)`);
  }
  if (draftMaterialCount > 0) {
    push('warning', `${draftMaterialCount} material properties are in draft state (provisional qualification)`);
  }

  for (const error of (result.errors ?? []) as ErrorEntry[]) {
    push('error', error.message, error.code);
  }

  // Deduplicate exact duplicates, keeping the code with the row.
  const seen = new Map<string, IssueRow>();
  const counts = new Map<string, number>();
  for (const row of rows) {
    const key = `${row.severity}:${row.message}:${row.code ?? ''}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
    seen.set(key, row);
  }

  const deduplicated: IssueRow[] = [];
  for (const [key, count] of counts.entries()) {
    const row = seen.get(key);
    if (row === undefined) continue;
    deduplicated.push({
      severity: row.severity,
      message: count > 1 ? `${row.message} (${count}x)` : row.message,
      code: row.code,
    });
  }

  return deduplicated;
}

/** Label for the drop-test kind ("drop" → "Drop Test"). */
function testLabel(kind: string | undefined): string {
  if (!kind) return 'Analysis';
  return TEST_LABELS[kind] ?? kind;
}

/** First material name found across the result's authoritative sections. */
function resultMaterial(result: PipelineResult, fallback: string): string {
  const traceLabel = result.shell?.inputs_trace?.material?.label;
  if (typeof traceLabel === 'string' && traceLabel.trim() !== '') return traceLabel;
  const resolvedName = result.structural?.resolved_material?.name;
  if (typeof resolvedName === 'string' && resolvedName.trim() !== '') return resolvedName;
  const structuralMaterial = result.structural?.material;
  if (typeof structuralMaterial === 'string' && structuralMaterial.trim() !== '') {
    return structuralMaterial;
  }
  if (fallback.trim() !== '') return fallback;
  return '—';
}

/** Count of vertices in a damage zone (dent: >= 0.7, tear: > 0.92). */
function countZoneVertices(fea: FeaResult, threshold: number, exclusive: boolean): number {
  let count = 0;
  for (const field of fea.objects) {
    for (const damage of field.damage) {
      if (typeof damage !== 'number' || !Number.isFinite(damage)) continue;
      if (exclusive ? damage > threshold : damage >= threshold) count += 1;
    }
  }
  return count;
}

/** Headline numbers, resolved backend-first with sensible fallbacks. */
function buildMetrics(result: PipelineResult, liveDropData?: LiveDropData | null): Metric[] {
  if (result.population) {
    const pop = result.population;
    const sampleCount = pop.sample_count ?? 10000;
    const failureRate = pop.failure_rate ?? 0;
    const unitsFailed = pop.units_failed ?? Math.round(sampleCount * failureRate);
    const ciLow =
      typeof pop.wilson_ci?.low === 'number'
        ? `${(pop.wilson_ci.low * 100).toFixed(1)}%`
        : '—';
    const ciHigh =
      typeof pop.wilson_ci?.high === 'number'
        ? `${(pop.wilson_ci.high * 100).toFixed(1)}%`
        : '—';
    const weakest = pop.weakest_components?.[0]?.component_id ?? 'Base Shell Rib';

    return [
      {
        label: 'Sample count',
        value: `${sampleCount.toLocaleString()} units`,
        raw: sampleCount,
      },
      {
        label: 'Failure rate',
        value: `${(failureRate * 100).toFixed(1)}%`,
        raw: failureRate,
      },
      {
        label: 'Units failed',
        value: `${unitsFailed.toLocaleString()}`,
        raw: unitsFailed,
      },
      {
        label: '95% Wilson CI',
        value: `[${ciLow}, ${ciHigh}]`,
        raw: null,
      },
      {
        label: 'Weakest part',
        value: weakest,
        raw: null,
      },
      {
        label: 'Expected pass rate',
        value: `${((1 - failureRate) * 100).toFixed(1)}%`,
        raw: 1 - failureRate,
      },
    ];
  }

  const shell = result.shell;
  const impact = result.impact?.result;
  const structural = result.structural?.response;
  const dropSim = result.drop_simulation;
  const fea = result.fea;

  const rawMass =
    result.mass?.mass_kg ??
    (isFiniteNumber(dropSim?.model?.mass_kg) ? (dropSim?.model?.mass_kg as number) : null) ??
    null;
  const mass = rawMass;

  // Live active drop telemetry takes precedence during playback
  const activeDrop = liveDropData?.activeDrop ?? null;

  const rawPeakForce =
    (impact !== null && impact !== undefined && isFiniteNumber(impact.peak_force_n)
      ? impact.peak_force_n
      : null) ??
    (isFiniteNumber(shell?.loading?.drop_peak_force_n)
      ? (shell?.loading?.drop_peak_force_n as number)
      : null) ??
    (isFiniteNumber(dropSim?.peak_force_estimate_n)
      ? (dropSim?.peak_force_estimate_n as number)
      : null) ??
    null;

  const peakForce =
    activeDrop && activeDrop.peak_impact_speed_m_s > 0 && rawPeakForce != null
      ? Math.max(10, rawPeakForce * (activeDrop.peak_impact_speed_m_s / 3.83))
      : rawPeakForce;

  const rawPeakAccel =
    (impact !== null && impact !== undefined && isFiniteNumber(impact.peak_acceleration_m_s2)
      ? (impact.peak_acceleration_m_s2 as number) / 9.80665
      : null);
  const peakAccel =
    rawPeakAccel ??
    (peakForce != null && mass != null && mass > 0 ? peakForce / (mass * 9.80665) : null);

  const rawMaxStress =
    (fea?.peak?.stress_mpa != null && isFiniteNumber(fea.peak.stress_mpa)
      ? (fea.peak.stress_mpa as number) * 1e6
      : null) ??
    (isFiniteNumber(shell?.peak_stress_pa) && (shell?.peak_stress_pa as number) >= 1e6
      ? (shell?.peak_stress_pa as number)
      : null) ??
    (isFiniteNumber(structural?.max_stress_pa) && (structural?.max_stress_pa as number) >= 1e6
      ? (structural?.max_stress_pa as number)
      : null) ??
    null;
  const maxStress = rawMaxStress;

  const rawSafetyFactor =
    (isFiniteNumber(fea?.safety_factor) ? (fea?.safety_factor as number) : null) ??
    (isFiniteNumber(shell?.min_safety_factor) && (shell?.min_safety_factor as number) < 500
      ? (shell?.min_safety_factor as number)
      : null) ??
    (isFiniteNumber(structural?.safety_factor) && (structural?.safety_factor as number) < 500
      ? (structural?.safety_factor as number)
      : null) ??
    (impact !== null &&
    impact !== undefined &&
    typeof impact.safety_factor === 'number' &&
    Number.isFinite(impact.safety_factor) &&
    impact.safety_factor < 500
      ? impact.safety_factor
      : null) ??
    null;
  const safetyFactor = rawSafetyFactor;

  const rawMaxDeformation =
    (isFiniteNumber(shell?.max_displacement_m)
      ? (shell?.max_displacement_m as number)
      : null) ??
    (isFiniteNumber(structural?.max_displacement_m)
      ? (structural?.max_displacement_m as number)
      : null) ??
    (impact !== null &&
    impact !== undefined &&
    isFiniteNumber(impact.contact_compression_m)
      ? (impact.contact_compression_m as number)
      : null) ??
    null;
  const maxDeformation = rawMaxDeformation;

  return [
    {
      label: 'Mass',
      value: mass != null ? formatMass(mass) : '—',
      raw: mass,
    },
    {
      label: 'Impact force',
      value: peakForce != null ? formatForce(peakForce) : '—',
      raw: peakForce,
    },
    {
      label: 'Peak acceleration',
      value: peakAccel != null ? `${formatNumber(peakAccel)} g` : '—',
      raw: peakAccel,
    },
    {
      label: 'Max deformation',
      value: maxDeformation != null ? formatLength(maxDeformation) : '—',
      raw: maxDeformation,
    },
    {
      label: 'Safety factor',
      value: safetyFactor != null ? formatNumber(safetyFactor) : '—',
      raw: safetyFactor,
    },
    {
      label: 'Max stress',
      value: maxStress != null ? formatPressure(maxStress) : '—',
      raw: maxStress,
    },
  ];
}

/** FEA visualization metrics (per-vertex damage field), when computed. */
function buildFeaMetrics(result: PipelineResult): Metric[] {
  const fea = result.fea;
  if (!fea || fea.computed !== true || !fea.peak) return [];

  const peakMpa =
    isFiniteNumber(fea.peak.stress_mpa) ? (fea.peak.stress_mpa as number) : null;
  const yieldMpa =
    isFiniteNumber(fea.yield_stress_pa) ? ((fea.yield_stress_pa as number) / 1e6) : null;
  const damage =
    isFiniteNumber(fea.peak.damage) ? (fea.peak.damage as number) : null;
  const dentVertices =
    fea.dent_threshold != null ? countZoneVertices(fea, fea.dent_threshold, false) : 0;
  const tearVertices =
    fea.tear_threshold != null ? countZoneVertices(fea, fea.tear_threshold, true) : 0;

  return [
    {
      label: 'FEA peak stress',
      value: peakMpa !== null ? `${peakMpa.toFixed(1)} MPa` : '—',
      raw: peakMpa,
    },
    {
      label: 'FEA yield stress',
      value: yieldMpa !== null ? `${yieldMpa.toFixed(1)} MPa` : '—',
      raw: yieldMpa,
    },
    {
      label: 'Max damage D',
      value: damage !== null ? `${damage.toFixed(2)} (0-1)` : '—',
      raw: damage,
    },
    {
      label: 'Dent zone vertices',
      value: `${dentVertices}`,
      raw: dentVertices,
    },
    {
      label: 'Tear zone vertices',
      value: `${tearVertices}`,
      raw: tearVertices,
    },
  ];
}

/** One line describing the test configuration, or null when absent. */
function buildConfigLine(result: PipelineResult): string | null {
  if (result.population) {
    const pop = result.population;
    const parts = ['Population Analysis'];
    if (isFiniteNumber(pop.sample_count)) {
      parts.push(`${(pop.sample_count as number).toLocaleString()} units`);
    }
    const dropHeight =
      typeof pop.drop?.height_m === 'number'
        ? pop.drop.height_m
        : typeof pop.drop?.drop_height_m === 'number'
          ? pop.drop.drop_height_m
          : 0.75;
    parts.push(`${Number(dropHeight).toFixed(2)} m`);
    const dropSurface =
      typeof pop.drop?.surface === 'string' ? pop.drop.surface : 'concrete';
    parts.push(SURFACE_LABELS[dropSurface] ?? dropSurface);
    parts.push('Monte Carlo');
    return parts.join(' · ');
  }

  const config = result.drop_simulation?.config;
  if (!config) return null;
  const parts = [testLabel(config.test)];
  if (isFiniteNumber(config.height_m)) parts.push(`${Number(config.height_m).toFixed(2)} m`);
  if (config.surface) parts.push(SURFACE_LABELS[config.surface] ?? config.surface);
  if (config.orientation) parts.push(ORIENTATION_LABELS[config.orientation] ?? config.orientation);
  if (isFiniteNumber(config.drop_count)) {
    parts.push(`${config.drop_count} drop${config.drop_count === 1 ? '' : 's'}`);
  }
  return parts.join(' · ');
}

/** Empty-state placeholder before the first run. */
function EmptyState(): JSX.Element {
  return (
    <div className="results-rail__empty" role="status">
      <p className="results-rail__empty-title">No results yet</p>
    </div>
  );
}

/** The results panel content for a finished run. */
function issueSeverityLabel(severity: string): string {
  switch (severity.toLowerCase()) {
    case 'warning':
    case 'warn':
      return 'WARN';
    case 'error':
      return 'Error';
    case 'blocker':
      return 'Blocker';
    case 'info':
      return 'Info';
    default:
      return severity;
  }
}

function ResultPanel({
  result,
  liveDropData,
}: {
  result: PipelineResult;
  liveDropData?: LiveDropData | null;
}): JSX.Element {
  const { state, dispatch } = useProjectStore();
  const verdict = computeVerdict(result);
  const metrics = buildMetrics(result, liveDropData);
  const feaMetrics = buildFeaMetrics(result);
  const issues = buildIssueRows(result);
  const configLine = buildConfigLine(result);
  const material = resultMaterial(result, state.defaultMaterialKey);
  const stale = selectHasStaleResult(state);

  return (
    <div className="results-rail__result">
      <div className={`results-rail__verdict results-rail__verdict--${verdict}`}>
        <div className="results-rail__verdict-header">
          <span className="results-rail__verdict-label">{VERDICT_LABEL[verdict]}</span>
          <span className="results-rail__verdict-statement">{VERDICT_STATEMENT[verdict]}</span>
        </div>
      </div>

      <div className="results-rail__config-bar">
        {configLine ? (
          <p className="results-rail__config-line">{configLine}</p>
        ) : null}
        <p className="results-rail__config-line">
          Material: <span className="results-rail__config-value">{material}</span>
        </p>
      </div>

      <div className="results-rail__section">
        <div className="results-rail__section-title">
          {result.population ? 'Population Metrics' : 'Simulation & Impact Metrics'}
        </div>
        <div className="results-rail__table">
          {metrics.map((metric) => (
            <div className="results-rail__row" key={metric.label}>
              <span className="results-rail__label">{metric.label}</span>
              <span className="results-rail__value">{metric.value}</span>
            </div>
          ))}
        </div>
      </div>

      {feaMetrics.length > 0 ? (
        <div className="results-rail__section">
          <div className="results-rail__section-title">FEA Damage & Plastic Yield</div>
          <div className="results-rail__fea-toggles">
            <button
              type="button"
              className={`btn btn--xs results-rail__fea-btn${state.renderMode === 'fea' ? ' is-active' : ''}`}
              onClick={() =>
                dispatch({
                  type: 'SET_RENDER_MODE',
                  mode: state.renderMode === 'fea' ? 'default' : 'fea',
                })
              }
              aria-label="Toggle FEA Stress Heatmap"
            >
              FEA Heatmap
            </button>
            <button
              type="button"
              className={`btn btn--xs results-rail__fea-btn${state.renderMode === 'yield' ? ' is-active' : ''}`}
              onClick={() =>
                dispatch({
                  type: 'SET_RENDER_MODE',
                  mode: state.renderMode === 'yield' ? 'default' : 'yield',
                })
              }
              aria-label="Toggle Yield & Crack Shader"
            >
              Yield & Cracks
            </button>
          </div>
          <div className="results-rail__table">
            {feaMetrics.map((metric) => (
              <div className="results-rail__row" key={metric.label}>
                <span className="results-rail__label">{metric.label}</span>
                <span className="results-rail__value">{metric.value}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {issues.length > 0 ? (
        <section className="results-rail__issues" aria-label="Issues">
          <h4>Issues</h4>
          <ul>
            {issues.map((issue, index) => {
              const recommendation = issue.code ? RECOMMENDATIONS[issue.code] : undefined;
              return (
                <li key={`${issue.severity}-${issue.code ?? ''}-${index}`} className="results-rail__issue-item">
                  <div className="results-rail__issue-line">
                    <StatusBadge tone={severityTone(issue.severity)}>
                      {issueSeverityLabel(issue.severity)}
                    </StatusBadge>
                    <span className="results-rail__issue-message">
                      {issue.message}
                      {issue.code ? (
                        <code className="results-rail__issue-code">{issue.code}</code>
                      ) : null}
                    </span>
                  </div>
                  {recommendation ? (
                    <p className="results-rail__recommendation">{recommendation}</p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {stale ? (
        <p className="results-rail__stale">Inputs changed — rerun to refresh these results.</p>
      ) : null}
    </div>
  );
}

/**
 * Side rail showing the latest pipeline result as a single clean panel.
 * Collapses to a slim vertical strip on the right edge.
 * @param open - Whether the panel is expanded (false = collapsed strip).
 * @param onToggleOpen - Callback toggling the expanded state.
 * @param liveDropData - Live physics telemetry from the active drop animation.
 */
export function ResultsRail({
  open,
  onToggleOpen,
  liveDropData,
}: {
  open: boolean;
  onToggleOpen: () => void;
  liveDropData?: LiveDropData | null;
}): JSX.Element {
  const { state } = useProjectStore();
  const result = state.lastResult;

  if (!open) {
    return (
      <aside className="results-rail results-rail--collapsed">
        <button
          type="button"
          className="results-rail__toggle"
          aria-label="Show results rail"
          aria-expanded="false"
          onClick={onToggleOpen}
        >
          <span className="results-rail__toggle-label">Results</span>
        </button>
      </aside>
    );
  }

  return (
    <aside className="results-rail">
      <div className="results-rail__body">
        <div className="results-rail__deck-header">
          <div className="results-rail__deck-title">
            <h2 className="results-rail__title">
              {result !== null
                ? `Results of ${result.population ? 'Population Analysis' : testLabel(result.drop_simulation?.config?.test)}`
                : 'Results'}
            </h2>
          </div>
          <button
            type="button"
            className="results-rail__close-btn"
            aria-label="Hide results rail"
            aria-expanded="true"
            onClick={onToggleOpen}
            title="Close results panel"
          >
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        {result === null ? <EmptyState /> : <ResultPanel result={result} liveDropData={liveDropData} />}
      </div>
    </aside>
  );
}
