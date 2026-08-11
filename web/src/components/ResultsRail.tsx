/**
 * Results rail — the single results panel. Shows one clear verdict, the key
 * numbers, the test configuration that produced them, and a short list of
 * actionable issues. No tabs, no run bookkeeping, no engineering disclosures.
 */
import { type ReactNode } from 'react';
import type {
  ErrorEntry,
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

/** Headline numbers, resolved backend-first with sensible fallbacks. */
function buildMetrics(result: PipelineResult): Metric[] {
  const shell = result.shell;
  const impact = result.impact?.result;
  const structural = result.structural?.response;
  const dropSim = result.drop_simulation;

  const mass =
    result.mass?.mass_kg ??
    (isFiniteNumber(dropSim?.model?.mass_kg) ? (dropSim?.model?.mass_kg as number) : null) ??
    null;

  const peakForce =
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

  // Drop impacts are conventionally quoted in g; m/s² reads like a typo.
  const peakAccel =
    impact !== null && impact !== undefined && isFiniteNumber(impact.peak_acceleration_m_s2)
      ? (impact.peak_acceleration_m_s2 as number) / 9.80665
      : null;

  const safetyFactor =
    (isFiniteNumber(shell?.min_safety_factor) ? (shell?.min_safety_factor as number) : null) ??
    (isFiniteNumber(structural?.safety_factor) ? (structural?.safety_factor as number) : null) ??
    (impact !== null &&
    impact !== undefined &&
    typeof impact.safety_factor === 'number' &&
    Number.isFinite(impact.safety_factor)
      ? impact.safety_factor
      : null) ??
    null;

  const maxStress =
    (isFiniteNumber(shell?.peak_stress_pa) ? (shell?.peak_stress_pa as number) : null) ??
    (isFiniteNumber(structural?.max_stress_pa) ? (structural?.max_stress_pa as number) : null) ??
    null;

  const maxDeformation =
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

  return [
    {
      label: 'Mass',
      value: mass !== null ? formatMass(mass) : '—',
      raw: mass,
    },
    {
      label: 'Impact force',
      value: peakForce !== null ? formatForce(peakForce) : '—',
      raw: peakForce,
    },
    {
      label: 'Peak acceleration',
      value: peakAccel !== null ? `${formatNumber(peakAccel)} g` : '—',
      raw: peakAccel,
    },
    {
      label: 'Safety factor',
      value: safetyFactor !== null ? formatNumber(safetyFactor) : '—',
      raw: safetyFactor,
    },
    {
      label: 'Max stress',
      value: maxStress !== null ? formatPressure(maxStress) : '—',
      raw: maxStress,
    },
    {
      label: 'Max deformation',
      value: maxDeformation !== null ? formatLength(maxDeformation) : '—',
      raw: maxDeformation,
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
      <p className="results-rail__empty-hint">
        Upload a model, choose a material, and run a test to see results here.
      </p>
    </div>
  );
}

/** The results panel content for a finished run. */
function ResultPanel({ result }: { result: PipelineResult }): JSX.Element {
  const { state } = useProjectStore();
  const verdict = computeVerdict(result);
  const metrics = buildMetrics(result);
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
