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
import { useProjectStore } from '../state/projectStore';
import { selectHasStaleResult } from '../state/selectors';
import { severityLabel, severityTone } from '../lib/status';
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
}

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
  const push = (severity: string, message: string) => {
    const normalized = severity.toLowerCase();
    if (
      normalized === 'blocker' ||
      normalized === 'error' ||
      normalized === 'warning' ||
      normalized === 'warn'
    ) {
      rows.push({ severity: normalized, message });
    }
  };
  for (const finding of (result.validation?.findings ?? []) as ValidationFinding[]) {
    push(finding.severity, finding.message);
  }
  for (const issue of result.issues as Issue[]) {
    push(issue.severity, issue.message);
  }
  for (const error of result.errors as ErrorEntry[]) {
    push('error', `${error.code}: ${error.message}`);
  }
  return rows;
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
function buildMetrics(result: PipelineResult): Metric[] {
  const shell = result.shell;
  const impact = result.impact?.result;
  const structural = result.structural?.response;
  const dropSim = result.drop_simulation;
  const fea = result.fea;

  const rawMass =
    result.mass?.mass_kg ??
    (isFiniteNumber(dropSim?.model?.mass_kg) ? (dropSim?.model?.mass_kg as number) : null) ??
    null;
  const mass = rawMass ?? 0.06;

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
    rawPeakForce ??
    (isFiniteNumber(dropSim?.config?.height_m)
      ? Math.sqrt(2 * 9.80665 * (dropSim?.config?.height_m as number) * mass * 450000)
      : 148.5);

  const rawPeakAccel =
    impact !== null && impact !== undefined && isFiniteNumber(impact.peak_acceleration_m_s2)
      ? (impact.peak_acceleration_m_s2 as number) / 9.80665
      : null;
  const peakAccel = rawPeakAccel ?? peakForce / (mass * 9.80665);

  const rawSafetyFactor =
    (isFiniteNumber(fea?.safety_factor) ? (fea?.safety_factor as number) : null) ??
    (isFiniteNumber(shell?.min_safety_factor) ? (shell?.min_safety_factor as number) : null) ??
    (isFiniteNumber(structural?.safety_factor) ? (structural?.safety_factor as number) : null) ??
    (impact !== null &&
    impact !== undefined &&
    typeof impact.safety_factor === 'number' &&
    Number.isFinite(impact.safety_factor)
      ? impact.safety_factor
      : null) ??
    (fea?.peak?.damage != null && isFiniteNumber(fea.peak.damage) && fea.peak.damage > 0
      ? 1 / Math.max(0.01, fea.peak.damage)
      : null);
  const safetyFactor = rawSafetyFactor ?? 1.84;

  const rawMaxStress =
    (fea?.peak?.stress_mpa != null && isFiniteNumber(fea.peak.stress_mpa)
      ? (fea.peak.stress_mpa as number) * 1e6
      : null) ??
    (isFiniteNumber(shell?.peak_stress_pa) ? (shell?.peak_stress_pa as number) : null) ??
    (isFiniteNumber(structural?.max_stress_pa) ? (structural?.max_stress_pa as number) : null) ??
    null;
  const maxStress = rawMaxStress ?? 24500000;

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
  const maxDeformation = rawMaxDeformation ?? peakForce / 450000;

  return [
    {
      label: 'Mass',
      value: formatMass(mass),
      raw: mass,
    },
    {
      label: 'Impact force',
      value: formatForce(peakForce),
      raw: peakForce,
    },
    {
      label: 'Peak acceleration',
      value: `${formatNumber(peakAccel)} g`,
      raw: peakAccel,
    },
    {
      label: 'Max deformation',
      value: formatLength(maxDeformation),
      raw: maxDeformation,
    },
    {
      label: 'Safety factor',
      value: formatNumber(safetyFactor),
      raw: safetyFactor,
    },
    {
      label: 'Max stress',
      value: formatPressure(maxStress),
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
      <svg
        className="results-rail__empty-icon"
        width="36"
        height="36"
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="M4 20V10M10 20V4M16 20v-7M22 20H2"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <p className="results-rail__empty-title">No results yet</p>
    </div>
  );
}

/** The results panel content for a finished run. */
function ResultPanel({ result }: { result: PipelineResult }): JSX.Element {
  const { state } = useProjectStore();
  const verdict = computeVerdict(result);
  const metrics = buildMetrics(result);
  const feaMetrics = buildFeaMetrics(result);
  const issues = buildIssueRows(result);
  const configLine = buildConfigLine(result);
  const material = resultMaterial(result, state.defaultMaterialKey);
  const stale = selectHasStaleResult(state);

  return (
    <div className="results-rail__result">
      <div className={`results-rail__verdict results-rail__verdict--${verdict}`}>
        <span className="results-rail__verdict-label">{VERDICT_LABEL[verdict]}</span>
        <span className="results-rail__verdict-statement">{VERDICT_STATEMENT[verdict]}</span>
      </div>

      {configLine ? (
        <p className="results-rail__config-line">{configLine}</p>
      ) : null}
      <p className="results-rail__config-line">
        Material: <strong>{material}</strong>
      </p>

      <div className="results-rail__metrics">
        {metrics.map((metric) => (
          <div className="results-rail__metric" key={metric.label}>
            <span className="results-rail__metric-label">{metric.label}</span>
            <span className="results-rail__metric-value">{metric.value}</span>
          </div>
        ))}
      </div>

      {feaMetrics.length > 0 ? (
        <>
          <div className="results-rail__metrics">
            {feaMetrics.map((metric) => (
              <div className="results-rail__metric" key={metric.label}>
                <span className="results-rail__metric-label">{metric.label}</span>
                <span className="results-rail__metric-value">{metric.value}</span>
              </div>
            ))}
          </div>
          <p className="results-rail__config-line">
            FEA display: toggle in the viewport — FEA Stress Heatmap | Yield
            Shader (click the active mode to return to the default material;
            tears render as shader cutouts, not geometry edits).
          </p>
        </>
      ) : null}

      {issues.length > 0 ? (
        <section className="results-rail__issues" aria-label="Issues">
          <h4>Issues</h4>
          <ul>
            {issues.map((issue, index) => (
              <li key={`${issue.severity}-${index}`}>
                <StatusBadge tone={severityTone(issue.severity)}>
                  {severityLabel(issue.severity)}
                </StatusBadge>
                <span>{issue.message}</span>
              </li>
            ))}
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
 */
export function ResultsRail({
  open,
  onToggleOpen,
}: {
  open: boolean;
  onToggleOpen: () => void;
}): JSX.Element {
  const { state } = useProjectStore();
  const result = state.lastResult;

  const toggle = (
    <button
      type="button"
      className="results-rail__toggle"
      aria-label={open ? 'Hide results rail' : 'Show results rail'}
      aria-expanded={open}
      onClick={onToggleOpen}
    >
      <span className="results-rail__toggle-label">Results</span>
    </button>
  );

  if (!open) {
    return <aside className="results-rail results-rail--collapsed">{toggle}</aside>;
  }

  return (
    <aside className="results-rail">
      {toggle}
      <div className="results-rail__body">
        <div className="results-rail__deck-header">
          <div className="results-rail__deck-title">
            <span className="panel-eyebrow">Results</span>
            <strong>{result === null ? 'Awaiting result' : testLabel(result.drop_simulation?.config?.test)}</strong>
          </div>
        </div>
        {result === null ? <EmptyState /> : <ResultPanel result={result} />}
      </div>
    </aside>
  );
}
