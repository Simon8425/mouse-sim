/**
 * Results rail — compact side panel for the latest pipeline result.
 * Layout: header (run id + stale banner) → slim metric strip (Run / Validity
 * / Evidence) → tabs (Overview, Impact, Structural, Qualification, Issues) →
 * panel. Optional result sections render as slim 2-column key-value lists
 * (results-rail__kv); the Mass tab is folded into Overview.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import * as React from 'react';
import type {
  PipelineResult,
  QualificationGate,
  ComponentAssessment,
  ComponentResult,
  ComponentScreeningResult,
  PopulationResult,
  SensitivityEntry,
  SensitivityLevel,
  ShellResult,
} from '../api/contracts';
import { isRecord } from '../api/contracts';
import { useProjectStore } from '../state/projectStore';
import { selectAssumptions, selectHasStaleResult, selectUnsupportedModes } from '../state/selectors';
import {
  dispositionLabel, dispositionTone, gateStatusLabel, lifecycleLabel, modeLabel, severityLabel,
  severityTone, validityConfidenceLabel, validityLabel,
} from '../lib/status';
import {
  formatAcceleration, formatDuration, formatEnergy, formatForce, formatLength, formatMass, formatPressure,
} from '../lib/units';
import { formatNumber, formatSigned, formatVector3 } from '../lib/format';
import { StatusBadge } from './StatusBadge';

/** Identifiers for the tabs rendered by the results rail. */
type TabId = 'overview' | 'impact' | 'structural' | 'qualification' | 'issues' | 'components' | 'population';

/** Badge tone values supported by StatusBadge. */
type Tone = 'ok' | 'error' | 'warn' | 'neutral';

/** A row in the combined issues table. */
interface IssueRow {
  severity: string;
  code: string;
  phase: string;
  message: string;
  affectedIds: string[];
}

/** One key-value row in a results-rail__kv list. */
interface KvRow {
  label: string;
  value: ReactNode;
}

const TABS: readonly TabId[] = [
  'overview',
  'impact',
  'structural',
  'qualification',
  'issues',
  'components',
  'population',
];

const TAB_LABELS: Record<TabId, string> = {
  overview: 'Overview',
  impact: 'Impact',
  structural: 'Structural',
  qualification: 'Qualification',
  issues: 'Issues',
  components: 'Components',
  population: 'Population',
};

const MIN_RAIL_WIDTH = 260;
const MAX_RAIL_WIDTH = 720;
const DEFAULT_RAIL_WIDTH = 420;

/** Shape of the project store state and dispatch, derived from the hook. */
type ProjectState = ReturnType<typeof useProjectStore>['state'];
type ProjectDispatch = ReturnType<typeof useProjectStore>['dispatch'];

/** A titled results-rail section with an h4 heading. */
function RailSection({ title, children }: { title: string; children: ReactNode }): JSX.Element {
  return (
    <section className="results-rail__section">
      <h4>{title}</h4>
      {children}
    </section>
  );
}

/** Empty-state placeholder used when a tab has no data. */
function Empty({ children }: { children: ReactNode }): JSX.Element {
  return <p className="results-rail__empty muted">{children}</p>;
}

/** Slim 2-column key-value list used across all tabs. */
function KvList({ rows }: { rows: KvRow[] }): JSX.Element {
  return (
    <table className="results-rail__kv">
      <tbody>
        {rows.map((row) => (
          <tr key={row.label}>
            <th scope="row">{row.label}</th>
            <td>{row.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}/** One compact metric in the analysis strip. */
function MetricCard({ label, value, detail, tone = 'neutral' }: { label: string; value: ReactNode; detail: string; tone?: 'brand' | 'telemetry' | 'neutral' }): JSX.Element {
  return (
    <div className={`metric-card metric-card--${tone}`}>
      <span className="metric-card__label">{label}</span>
      <span className="metric-card__value">{value}</span>
      <span className="metric-card__detail">{detail}</span>
    </div>
  );
}

/** Horizontal summary of run state and returned engineering evidence. */
function MetricStrip({ result }: { result: PipelineResult }): JSX.Element {
  const disposition = result.qualification?.evidence_disposition ?? 'exploration_only';
  return (
    <div className="results-rail__metric-strip" aria-label="Analysis metrics">
      <MetricCard label="Run" value={modeLabel(result.mode)} detail={lifecycleLabel(result.lifecycle_state)} tone="brand" />
      <MetricCard
        label="Validity"
        value={<StatusBadge tone={validityTone(result.validity.state)}>{validityLabel(result.validity.state)}</StatusBadge>}
        detail={validityConfidenceLabel(result.validity.confidence)}
        tone="telemetry"
      />
      <MetricCard
        label="Evidence"
        value={<StatusBadge tone={dispositionTone(disposition)}>{dispositionLabel(disposition)}</StatusBadge>}
        detail={result.qualification ? 'qualification gate' : 'no qualification result'}
      />
    </div>
  );
}

function canonicalSeverity(severity: string): string {
  return severity.toLowerCase() === 'warn' ? 'warning' : severity.toLowerCase();
}

/** Shared severity filter for the issues tab. */function SeverityFilter({
  result,
  value,
  onChange,
}: {
  result: PipelineResult;
  value: string | null;
  onChange: (severity: string | null) => void;
}): JSX.Element | null {
  const rows = buildIssueRows(result);
  const counts = new Map<string, number>();
  for (const row of rows) {
    const severity = canonicalSeverity(row.severity);
    counts.set(severity, (counts.get(severity) ?? 0) + 1);
  }
  if (rows.length === 0) return null;

  const options = [
    { key: null, label: 'All', count: rows.length },
    ...(['blocker', 'error', 'warning', 'info'] as const)
      .filter((severity) => (counts.get(severity) ?? 0) > 0)
      .map((severity) => ({ key: severity, label: severity, count: counts.get(severity) ?? 0 })),
  ];

  return (
    <div className="results-rail__filter-bar" role="group" aria-label="Filter results by severity">
      <span className="results-rail__filter-label">Severity</span>
      {options.map((option) => (
        <button
          key={option.key ?? 'all'}
          type="button"
          className={`results-rail__filter${value === option.key ? ' is-active' : ''}`}
          aria-pressed={value === option.key}
          onClick={() => onChange(option.key)}
        >
          {option.label} {option.count}
        </button>
      ))}
    </div>
  );
}

function formatHumanLabel(text: string): string {
  if (!text) return text;
  const cleaned = text.replace(/^UNSUPPORTED_/i, '').replace(/_/g, ' ').toLowerCase();
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

/** Collapsible disclosure bar (▶ / ▼ chevron) for secondary solver data. */
function DisclosureCard({
  title,
  count,
  children,
  defaultOpen = false,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
  defaultOpen?: boolean;
}): JSX.Element {
  return (
    <details className="disclosure-card" open={defaultOpen}>
      <summary className="disclosure-card__summary">
        <span className="disclosure-card__chevron">▶</span>
        <span className="disclosure-card__title">
          {title} {count !== undefined ? `(${count})` : ''}
        </span>
      </summary>
      <div className="disclosure-card__content">{children}</div>
    </details>
  );
}

/** Assumption / formula lines, split on the ': ' separator. */
function AssumptionsList({ items }: { items: string[] }): JSX.Element {
  if (items.length === 0) return <p className="muted">None</p>;
  return (
    <ul className="disclosure-card__list">
      {items.map((item, index) => {
        const parts = item.split(': ');
        const title = parts.length > 1 ? parts[0] : `Assumption ${index + 1}`;
        const detail = parts.length > 1 ? parts.slice(1).join(': ') : item;
        return (
          <li key={index}>
            <strong>{title}</strong>
            {detail ? `: ${detail}` : ''}
          </li>
        );
      })}
    </ul>
  );
}

/** Badged mono list for disclosed items (unsupported modes / solver flags). */
function DisclosedList({
  items,
  label = (item: string) => item,
  badge,
}: {
  items: string[];
  label?: (item: string) => string;
  badge: ReactNode;
}): JSX.Element {
  if (items.length === 0) return <p className="muted">None</p>;
  return (
    <ul className="disclosure-card__list">
      {items.map((item) => (
        <li key={item}>
          <code>{label(item)}</code> {badge}
        </li>
      ))}
    </ul>
  );
}

/** Fixture → force reaction lines. */
function ReactionsList({ reactions }: { reactions: Record<string, number> }): JSX.Element {
  const entries = Object.entries(reactions);
  if (entries.length === 0) return <p className="muted">None</p>;
  return (
    <ul className="disclosure-card__list">
      {entries.map(([fixture, force]) => (
        <li key={fixture}>
          <code>{fixture}</code> — {formatForce(force)}
        </li>
      ))}
    </ul>
  );
}

/** Mono chip list, or a muted dash when the list is empty. */
function Chips({ items }: { items: string[] }): JSX.Element {
  if (items.length === 0) return <span className="muted">—</span>;
  return (
    <span className="chips">
      {items.map((item) => (
        <code key={item} className="chip">
          {item}
        </code>
      ))}
    </span>
  );
}

function lifecycleTone(lifecycleState: string): Tone {
  return lifecycleState === 'completed' ? 'ok' : 'error';
}

function validityTone(validityState: string): Tone {
  if (validityState === 'valid') return 'ok';
  if (validityState === 'invalid') return 'error';
  return 'warn';
}

function gateTone(gate: { passed: boolean; evaluable: boolean; blocker: boolean }): Tone {
  if (gate.blocker) return 'error';
  if (!gate.evaluable) return 'neutral';
  return gate.passed ? 'ok' : 'warn';
}

/** Rail header: eyebrow + run title. */
function RailHeader({ runId }: { runId: string | null }): JSX.Element {
  return (
    <div className="results-rail__deck-header">
      <div className="results-rail__deck-title">
        <span className="panel-eyebrow">Results</span>
        <strong>{runId === null ? 'Awaiting result' : `RUN ${runId.slice(0, 12)}`}</strong>
      </div>
    </div>
  );
}

/**
 * Side rail showing the latest pipeline result, organized into tabs.
 * Collapses to a slim vertical strip on the right edge; the strip toggles
 * the expanded panel, whose width is adjustable via the left-edge handle.
 * @param open - Whether the panel is expanded (false = collapsed strip).
 * @param onToggleOpen - Callback toggling the expanded state.
 * @returns The results rail element.
 */
export function ResultsRail({
  open,
  onToggleOpen,
}: {
  open: boolean;
  onToggleOpen: () => void;
}): JSX.Element {
  const { state, dispatch } = useProjectStore();
  const result = state.lastResult;
  const activeTab: TabId = (TABS as readonly string[]).includes(state.resultsTab)
    ? (state.resultsTab as TabId)
    : 'overview';

  const [railWidth, setRailWidth] = useState<number>(DEFAULT_RAIL_WIDTH);
  const [isDragging, setIsDragging] = useState<boolean>(false);
  const railRef = useRef<HTMLElement | null>(null);
  const dragStartXRef = useRef<number>(0);
  const startWidthRef = useRef<number>(DEFAULT_RAIL_WIDTH);

  useEffect(() => {
    if (railRef.current) railRef.current.style.width = `${railWidth}px`;
  }, [railWidth]);

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
    dragStartXRef.current = e.clientX;
    startWidthRef.current = railWidth;
  };

  useEffect(() => {
    if (!isDragging) return;
    const handleMouseMove = (e: MouseEvent) => {
      const deltaX = dragStartXRef.current - e.clientX;
      const newWidth = Math.min(Math.max(startWidthRef.current + deltaX, MIN_RAIL_WIDTH), MAX_RAIL_WIDTH);
      setRailWidth(newWidth);
    };
    const handleMouseUp = () => setIsDragging(false);
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging]);

  const handleToggleExpand = () => {
    setRailWidth((prev) => (prev > 300 ? MIN_RAIL_WIDTH : DEFAULT_RAIL_WIDTH));
  };

  const toggle = (
    <button
      type="button"
      className="results-rail__toggle"
      aria-label={open ? 'Hide results rail' : 'Show results rail'}
      aria-expanded={open}
      onClick={onToggleOpen}
    >
      <span className="results-rail__toggle-label">RESULTS</span>
    </button>
  );

  if (!open) {
    return <aside className="results-rail results-rail--collapsed">{toggle}</aside>;
  }

  return (
    <aside ref={railRef} className="results-rail">
      {toggle}
      <div className="results-rail__body">
        <div
          className="results-rail__resize-handle"
          onMouseDown={handleMouseDown}
          onDoubleClick={handleToggleExpand}
          title="Drag left/right or double-click to resize results panel"
        >
          <span className="resize-handle-bar" />
        </div>
        {result === null ? (
          <>
            <RailHeader runId={null} />
            <p className="results-rail__empty muted">No result yet — run an analysis to populate the rail.</p>
          </>
        ) : (
          <>
            <RailHeader runId={result.run_id} />
            {selectHasStaleResult(state) ? (
              <div className="stale-banner">Result is stale — rerun to refresh.</div>
            ) : null}
            <MetricStrip result={result} />
            <div className="results-rail__tabs" role="tablist" aria-label="Results">
              {TABS.map((tab) => (
                <button
                  key={tab}
                  type="button"
                  role="tab"
                  aria-selected={tab === activeTab}
                  className={`results-rail__tab${tab === activeTab ? ' is-active' : ''}`}
                  onClick={() => dispatch({ type: 'SET_TAB', tab })}
                >
                  {TAB_LABELS[tab]}
                </button>
              ))}
            </div>
            <div className="results-rail__panel" role="tabpanel">
              {renderTab(activeTab, result, state, dispatch)}
            </div>
          </>
        )}
      </div>
    </aside>
  );
}

/**
 * Dispatch rendering to the active tab's panel function.
 * @param tab - Active tab identifier.
 * @param result - Latest pipeline result (non-null here).
 * @param state - Project store state, needed by state-based selectors.
 * @param dispatch - Project store dispatch, needed by the issues filter.
 * @returns The rendered panel content.
 */
function renderTab(
  tab: TabId,
  result: PipelineResult,
  state: ProjectState,
  dispatch: ProjectDispatch,
): JSX.Element {
  switch (tab) {
    case 'overview':
      return renderOverview(result, state);
    case 'impact':
      return renderImpact(result);
    case 'structural':
      return renderStructural(result);
    case 'qualification':
      return renderQualification(result);
    case 'issues':
      return renderIssues(result, state, dispatch);
    case 'components':
      return renderComponents(result);
    case 'population':
      return renderPopulation(result);
  }
}

function shellTone(status: string): Tone {
  switch (status.toLowerCase()) {
    case 'pass':
      return 'ok';
    case 'warn':
      return 'warn';
    case 'fail':
      return 'error';
    default:
      return 'neutral';
  }
}

function classificationTone(classification: string): Tone {
  switch (classification.toLowerCase()) {
    case 'safe':
      return 'ok';
    case 'marginal':
    case 'unsupported':
      return 'warn';
    case 'failed':
    case 'invalid_input':
      return 'error';
    case 'insufficient_evidence':
    default:
      return 'neutral';
  }
}

/** Capitalize the first letter of a raw backend label, keeping the rest as-is. */
function capitalizeLabel(label: string): string {
  if (!label) return label;
  return label.charAt(0).toUpperCase() + label.slice(1);
}

/** Critical-region coordinates rounded to mm-scale precision, or '—'. */
function formatCriticalRegion(region: number[] | null | undefined): string {
  if (!Array.isArray(region) || region.length < 3) return '—';
  if (!region.every((coord) => typeof coord === 'number' && Number.isFinite(coord))) return '—';
  return `(${region.map((coord) => formatNumber(coord)).join(', ')})`;
}

/**
 * Shell Validation — the authoritative engineering result. Rendered first in
 * the Overview tab whenever the backend returned a shell block.
 * @param shell - The shell FEA result.
 * @param sampleCount - Population sample count, used when the shell verdict
 *   came from a Monte Carlo sample rather than a single deterministic run.
 * @returns The shell validation panel content.
 */
function renderShellValidation(shell: ShellResult, sampleCount?: number): JSX.Element {
  const classification = shell.classification;
  const statKind = shell.statistical_confidence?.kind;
  const statValue =
    statKind === 'single_run'
      ? 'Single deterministic run'
      : statKind
        ? `Sampling (${sampleCount !== undefined ? sampleCount.toLocaleString('en-US') : '—'}, 95% Wilson CI)`
        : '—';
  const stability = shell.critical_region_stability;
  const rows: KvRow[] = [
    {
      label: 'Peak stress',
      value: shell.peak_stress_pa !== null && shell.peak_stress_pa !== undefined ? formatPressure(shell.peak_stress_pa) : '—',
    },
    {
      label: 'Max deformation',
      value:
        shell.max_displacement_m !== null && shell.max_displacement_m !== undefined
          ? formatLength(shell.max_displacement_m)
          : '—',
    },
    {
      label: 'Minimum safety factor',
      value:
        shell.min_safety_factor !== null && shell.min_safety_factor !== undefined
          ? formatNumber(shell.min_safety_factor)
          : '—',
    },
    { label: 'Critical region', value: formatCriticalRegion(shell.critical_region) },
    { label: 'Failure mode', value: shell.failure_mode || '—' },
    {
      label: 'Physical-model confidence',
      value: shell.physical_model_confidence ? capitalizeLabel(shell.physical_model_confidence) : '—',
    },
    { label: 'Statistical confidence', value: statValue },
  ];
  if (shell.loading) {
    const loading = shell.loading;
    if (loading.drop_peak_speed_m_s !== null && loading.drop_peak_speed_m_s !== undefined) {
      rows.push({
        label: 'Drop peak speed',
        value: `${formatNumber(loading.drop_peak_speed_m_s)} m/s`,
      });
    }
    if (loading.drop_peak_energy_j !== null && loading.drop_peak_energy_j !== undefined) {
      rows.push({ label: 'Drop peak energy', value: formatEnergy(loading.drop_peak_energy_j) });
    }
    if (loading.drop_peak_force_n !== null && loading.drop_peak_force_n !== undefined) {
      rows.push({ label: 'Drop peak force', value: formatForce(loading.drop_peak_force_n) });
    }
  }
  const assumptions = shell.assumptions ?? [];
  const limitations = shell.limitations ?? [];
  return (
    <RailSection title="Shell Validation">
      <div className="results-rail__shell-status">
        <StatusBadge tone={shellTone(shell.status ?? 'not_evaluated')}>
          {shell.status ?? 'not_evaluated'}
        </StatusBadge>
        {classification ? (
          <StatusBadge tone={classificationTone(classification)}>{formatHumanLabel(classification)}</StatusBadge>
        ) : null}
      </div>
      {stability && stability.stable === false && stability.statement ? (
        <p className="results-rail__stability-warning">{stability.statement}</p>
      ) : null}
      <KvList rows={rows} />
      <DisclosureCard title="Screening assumptions" count={assumptions.length + limitations.length}>
        {assumptions.length > 0 ? <AssumptionsList items={assumptions} /> : null}
        {limitations.length > 0 ? (
          <div className="disclosure-card__limitations">
            <span className="results-rail__subhead">Limitations</span>
            <AssumptionsList items={limitations} />
          </div>
        ) : null}
        {assumptions.length === 0 && limitations.length === 0 ? <p className="muted">None</p> : null}
      </DisclosureCard>
    </RailSection>
  );
}

/**
 * Overview tab: shell validation (authoritative) first, then the run summary
 * kv, mass mini-section, and disclosures for assumptions, unsupported modes,
 * and errors. When no shell block was returned the overview falls back to the
 * existing run summary — the Structural tab keeps its own section.
 * @param result - Latest pipeline result.
 * @param state - Global project state.
 * @returns The overview panel content.
 */
function renderOverview(result: PipelineResult, state: ProjectState): JSX.Element {
  const disposition = result.qualification?.evidence_disposition ?? 'exploration_only';
  const runId =
    result.run_id.length > 18 ? `${result.run_id.slice(0, 12)}…${result.run_id.slice(-6)}` : result.run_id;
  const assumptions = selectAssumptions(state);
  const unsupported = selectUnsupportedModes(state);
  const errors = result.errors;

  return (
    <>
      {result.shell ? renderShellValidation(result.shell, result.population?.sample_count) : null}
      <RailSection title="Summary">
        <KvList
          rows={[
            { label: 'Mode', value: modeLabel(result.mode) },
            { label: 'Lifecycle', value: <StatusBadge tone={lifecycleTone(result.lifecycle_state)}>{lifecycleLabel(result.lifecycle_state)}</StatusBadge> },
            { label: 'Run ID', value: <code>{runId}</code> },
            { label: 'Validity', value: <StatusBadge tone={validityTone(result.validity.state)}>{validityLabel(result.validity.state)}</StatusBadge> },
            { label: 'Confidence', value: validityConfidenceLabel(result.validity.confidence) },
            { label: 'Evidence disposition', value: <StatusBadge tone={dispositionTone(disposition)}>{dispositionLabel(disposition)}</StatusBadge> },
            { label: 'Engine version', value: result.engine_version },
          ]}
        />
      </RailSection>
      {result.mass ? (
        <RailSection title="Mass">
          <KvList
            rows={[
              { label: 'Mass', value: result.mass.mass_kg !== null ? formatMass(result.mass.mass_kg) : '—' },
              { label: 'Center of mass', value: formatVector3(result.mass.center_of_mass_m) },
              { label: 'Completeness', value: `${Math.round(result.mass.completeness * 100)}%` },
            ]}
          />
        </RailSection>
      ) : null}
      {assumptions.length > 0 ? (
        <DisclosureCard title="Solver assumptions" count={assumptions.length}>
          <AssumptionsList items={assumptions} />
        </DisclosureCard>
      ) : null}
      {unsupported.length > 0 ? (
        <DisclosureCard title="Unsupported failure modes" count={unsupported.length}>
          <DisclosedList items={unsupported} label={formatHumanLabel} badge={<StatusBadge tone="neutral">Disclosed (Not Simulated)</StatusBadge>} />
        </DisclosureCard>
      ) : null}
      {errors.length > 0 ? (
        <DisclosureCard title="Errors" count={errors.length} defaultOpen>
          <ul>
            {errors.map((error, index) => (
              <li key={`${error.code}-${index}`}>
                <code>{error.code}</code> — {error.message}
              </li>
            ))}
          </ul>
        </DisclosureCard>
      ) : null}
    </>
  );
}

/**
 * Impact tab: drop simulation table when present, plus the quasi-static
 * impact estimate kv and its assumptions disclosure.
 * @param result - Latest pipeline result.
 * @returns The impact panel content.
 */
function renderImpact(result: PipelineResult): JSX.Element {
  const simulation = result.drop_simulation;
  const estimate = result.impact?.result ?? null;
  if (estimate === null && simulation === null) {
    return <Empty>No impact estimate was requested.</Empty>;
  }
  return (
    <>
      {simulation ? (
        <RailSection title="Drop Simulation">
          <p className="results-rail__config-line">
            3D rigid-body simulation · {simulation.config.test} test ·{' '}
            {simulation.config.height_m.toFixed(2)} m · {simulation.config.surface} ·{' '}
            {simulation.config.drop_count} drop(s) · {simulation.config.orientation} orientation
          </p>
          <table className="dense-table">
            <thead>
              <tr>
                <th>Drop</th>
                <th>Settled</th>
                <th>Impacts</th>
                <th>Peak speed</th>
                <th>Peak energy</th>
              </tr>
            </thead>
            <tbody>
              {simulation.drops.map((drop) => (
                <tr key={drop.index}>
                  <td>#{drop.index + 1}</td>
                  <td className="num-mid">{drop.settled_s.toFixed(2)} s</td>
                  <td className="num-mid">{drop.impact_count}</td>
                  <td className="num-mid">{drop.peak_impact_speed_m_s.toFixed(2)} m/s</td>
                  <td className="num-mid">{formatEnergy(drop.peak_kinetic_energy_j)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {simulation.peak ? (
            <p className="results-rail__worst-impact">
              Worst impact: {simulation.peak.impact_speed_m_s.toFixed(2)} m/s at {simulation.peak.t_s.toFixed(2)} s
              {simulation.peak_force_estimate_n
                ? ` · estimated peak force ${formatForce(simulation.peak_force_estimate_n)}`
                : ''}
            </p>
          ) : null}
        </RailSection>
      ) : null}
      {estimate ? (
        <RailSection title="Impact Estimate">
          <KvList
            rows={[
              { label: 'Closing velocity', value: `${formatNumber(estimate.closing_velocity_m_s)} m/s` },
              { label: 'Impact energy', value: formatEnergy(estimate.impact_energy_j) },
              { label: 'Effective mass', value: formatMass(estimate.effective_mass_kg) },
              { label: 'Impulse', value: `${formatNumber(estimate.impulse_n_s)} N·s` },
              { label: 'Peak force', value: formatForce(estimate.peak_force_n) },
              { label: 'Peak acceleration', value: `${formatNumber(estimate.peak_acceleration_m_s2)} m/s²` },
              { label: 'Contact duration', value: `${formatNumber(estimate.contact_duration_s)} s` },
              { label: 'Contact compression', value: formatLength(estimate.contact_compression_m) },
              { label: 'Validity', value: <StatusBadge tone={validityTone(estimate.validity)}>{validityLabel(estimate.validity)}</StatusBadge> },
              { label: 'Safety factor', value: typeof estimate.safety_factor === 'number' ? formatNumber(estimate.safety_factor) : estimate.safety_factor },
            ]}
          />
        </RailSection>
      ) : null}
      {estimate && estimate.assumptions.length > 0 ? (
        <DisclosureCard title="Solver assumptions" count={estimate.assumptions.length}>
          <AssumptionsList items={estimate.assumptions} />
        </DisclosureCard>
      ) : null}
    </>
  );
}

/**
 * Structural tab: solver response kv, plus disclosures for reactions, flags,
 * and assumptions.
 * @param result - Latest pipeline result.
 * @returns The structural panel content.
 */
function renderStructural(result: PipelineResult): JSX.Element {
  if (result.structural === null) {
    return <Empty>No structural evaluation was requested.</Empty>;
  }
  const response = result.structural.response;
  if (response === null) {
    return <Empty>No structural response was produced.</Empty>;
  }
  const maxDisplacement =
    response.max_displacement_m !== null
      ? response.max_displacement_location !== null
        ? `${formatLength(response.max_displacement_m)} at ${formatVector3(response.max_displacement_location)}`
        : formatLength(response.max_displacement_m)
      : '—';
  const residuals =
    response.force_residual_n !== null || response.moment_residual_n_m !== null
      ? `${response.force_residual_n !== null ? formatForce(response.force_residual_n) : '—'} / ${
          response.moment_residual_n_m !== null ? `${formatNumber(response.moment_residual_n_m)} N·m` : '—'
        }`
      : '—';

  return (
    <>
      <RailSection title="Structural Response">
        <KvList
          rows={[
            { label: 'Method', value: <code>{response.method_id}</code> },
            { label: 'Validity', value: <StatusBadge tone={validityTone(response.validity)}>{validityLabel(response.validity)}</StatusBadge> },
            { label: 'Max displacement', value: maxDisplacement },
            { label: 'Max stress', value: response.max_stress_pa !== null ? formatPressure(response.max_stress_pa) : '—' },
            { label: 'Filtered stress', value: response.max_stress_filtered_pa !== null ? formatPressure(response.max_stress_filtered_pa) : '—' },
            { label: 'Safety factor', value: response.safety_factor !== null ? formatNumber(response.safety_factor) : response.safety_factor_status ?? '—' },
            { label: 'Residuals', value: residuals },
          ]}
        />
      </RailSection>
      {Object.keys(response.reactions).length > 0 ? (
        <DisclosureCard title="Reactions" count={Object.keys(response.reactions).length}>
          <ReactionsList reactions={response.reactions} />
        </DisclosureCard>
      ) : null}
      {response.flags.length > 0 ? (
        <DisclosureCard title="Flags" count={response.flags.length}>
          <DisclosedList items={response.flags} badge={<StatusBadge tone="warn">Active Assumption</StatusBadge>} />
        </DisclosureCard>
      ) : null}
      {response.assumptions.length > 0 ? (
        <DisclosureCard title="Solver assumptions" count={response.assumptions.length}>
          <AssumptionsList items={response.assumptions} />
        </DisclosureCard>
      ) : null}
    </>
  );
}

/** Qualification gate checks table: Key | Status | Gate & Explanation. */
function renderGateTable(gates: QualificationGate[]): JSX.Element {
  if (gates.length === 0) {
    return <p className="muted">No gate checks assigned to this study.</p>;
  }
  return (
    <table className="dense-table">
      <thead>
        <tr>
          <th className="results-rail__gate-key">Key</th>
          <th className="results-rail__gate-status">Status</th>
          <th>Gate &amp; Explanation</th>
        </tr>
      </thead>
      <tbody>
        {gates.map((gate) => (
          <tr key={gate.key}>
            <td><code>{gate.key}</code></td>
            <td className="results-rail__gate-status-cell">
              <StatusBadge tone={gateTone(gate)}>
                {gateStatusLabel({ passed: gate.passed, evaluable: gate.evaluable, blocker: gate.blocker })}
              </StatusBadge>
            </td>
            <td>
              <strong className="gate-label">{gate.label}</strong>
              {gate.explanation ? <span className="gate-explanation muted">— {gate.explanation}</span> : null}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** One qualification study card: preview metric kv + gate checks. */
function StudyCard({
  badge,
  title,
  metrics,
  gates,
}: {
  badge: string;
  title: string;
  metrics: KvRow[];
  gates: QualificationGate[];
}): JSX.Element {
  return (
    <div className="qualification-study-card">
      <div className="qualification-study-card__header">
        <div className="qualification-study-card__title">
          <span className="qualification-study-card__badge">{badge}</span>
          <span>{title}</span>
        </div>
      </div>
      <div className="qualification-study-card__subhead">Preview Metrics</div>
      <KvList rows={metrics} />
      <div className="qualification-study-card__subhead">Gate Checks</div>
      {renderGateTable(gates)}
    </div>
  );
}

/**
 * Qualification tab: overall status kv, then the three study cards
 * (Slam Impact, Downforce, Drop Suite) with their gate tables.
 * @param result - Latest pipeline result.
 * @returns The qualification panel content.
 */
function renderQualification(result: PipelineResult): JSX.Element {
  if (result.qualification === null) {
    return <Empty>No qualification assessment was performed.</Empty>;
  }
  const { qualification } = result;
  const gates = qualification.gates ?? [];

  const impactKeys = ['IMPACT', 'ACCEL', 'FORCE', 'SHOCK', 'SLAM', 'CRUSH', 'DROP_CONTACT', 'ENERGY'];
  const downforceKeys = ['DISPLACEMENT', 'STRESS', 'FLEX', 'LOAD', 'DEFORMATION', 'STRUCTURAL', 'SAFETY'];

  const slamGates = gates.filter((g) => impactKeys.some((k) => g.key.toUpperCase().includes(k)));
  const downforceGates = gates.filter((g) => downforceKeys.some((k) => g.key.toUpperCase().includes(k)));
  const remainingGates = gates.filter((g) => !slamGates.includes(g) && !downforceGates.includes(g));

  const impact = result.impact?.result;
  const structural = result.structural?.response;

  return (
    <>
      <RailSection title="Qualification Overview">
        <KvList
          rows={[
            {
              label: 'Overall Status',
              value: (
                <StatusBadge tone={qualification.qualified ? 'ok' : 'error'}>
                  {qualification.qualified ? 'qualified' : 'not qualified'}
                </StatusBadge>
              ),
            },
            {
              label: 'Evidence disposition',
              value: (
                <StatusBadge tone={dispositionTone(qualification.evidence_disposition)}>
                  {dispositionLabel(qualification.evidence_disposition)}
                </StatusBadge>
              ),
            },
            { label: 'Blocking keys', value: <Chips items={qualification.blocking_keys} /> },
          ]}
        />
      </RailSection>
      <StudyCard
        badge="STUDY 01"
        title="Slam Impact (Drop Contact)"
        metrics={[
          { label: 'Peak Acceleration', value: impact ? formatAcceleration(impact.peak_acceleration_m_s2) : '—' },
          { label: 'Peak Force', value: impact ? formatForce(impact.peak_force_n) : '—' },
          { label: 'Impact Energy', value: impact ? formatEnergy(impact.impact_energy_j) : '—' },
          { label: 'Contact Duration', value: impact ? formatDuration(impact.contact_duration_s) : '—' },
        ]}
        gates={slamGates}
      />
      <StudyCard
        badge="STUDY 02"
        title="Downforce (Hand Load Flex)"
        metrics={[
          { label: 'Max Displacement', value: structural ? formatLength(structural.max_displacement_m) : '—' },
          { label: 'Max Stress', value: structural ? formatPressure(structural.max_stress_pa) : '—' },
          {
            label: 'Safety Factor',
            value:
              structural?.safety_factor !== null && structural?.safety_factor !== undefined
                ? formatNumber(structural.safety_factor)
                : '—',
          },
          { label: 'Response Validity', value: structural ? structural.validity : '—' },
        ]}
        gates={downforceGates}
      />
      <StudyCard
        badge="STUDY 03"
        title="Drop Suite (Multi-Axis & Budget Sweep)"
        metrics={[
          { label: 'Total Mass', value: result.mass ? formatMass(result.mass.mass_kg) : '—' },
          { label: 'Center of Mass', value: result.mass ? formatVector3(result.mass.center_of_mass_m) : '—' },
          { label: 'Completeness', value: result.mass ? `${Math.round(result.mass.completeness * 100)}%` : '—' },
        ]}
        gates={remainingGates}
      />
    </>
  );
}

/**
 * Issues tab: combined table of validation findings, pipeline issues, and
 * pipeline errors. Rows expand on click only.
 * @param rows - Combined issue rows.
 * @returns The issues table element.
 */
function IssuesTable({ rows }: { rows: IssueRow[] }): JSX.Element {
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);

  if (rows.length === 0) {
    return <Empty>No issues or validation findings reported.</Empty>;
  }

  return (
    <table className="dense-table dense-table--interactive dense-table--issues">
      <thead>
        <tr>
          <th />
          <th>Severity</th>
          <th>Code</th>
          <th>Phase</th>
          <th>Message</th>
          <th>Affected</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => {
          const isExpanded = expandedIndex === index;
          return (
            <React.Fragment key={`${row.code}-${index}`}>
              <tr
                className={`table-row--expandable${isExpanded ? ' is-expanded' : ''}`}
                onClick={() => setExpandedIndex(isExpanded ? null : index)}
                tabIndex={0}
                role="button"
                aria-expanded={isExpanded}
                title="Click to expand details"
              >
                <td className="expand-chevron">{isExpanded ? '▼' : '▶'}</td>
                <td><StatusBadge tone={severityTone(row.severity)}>{severityLabel(row.severity)}</StatusBadge></td>
                <td><code>{row.code}</code></td>
                <td>{row.phase}</td>
                <td title={row.message}>{row.message}</td>
                <td><Chips items={row.affectedIds} /></td>
              </tr>
              {isExpanded ? (
                <tr className="table-row--details">
                  <td colSpan={6}>
                    <div className="issue-detail-card">
                      <p><strong>Message:</strong> {row.message}</p>
                      <p><strong>Phase:</strong> {row.phase}</p>
                      {row.affectedIds.length > 0 ? (
                        <p><strong>Affected components:</strong> {row.affectedIds.join(', ')}</p>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ) : null}
            </React.Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function buildIssueRows(result: PipelineResult): IssueRow[] {
  const findings = result.validation?.findings ?? [];
  const issues = result.issues;
  const errors = result.errors;

  return [
    ...findings.map((finding) => ({
      severity: finding.severity,
      code: finding.code,
      phase: finding.phase,
      message: finding.message,
      affectedIds: finding.affected_ids,
    })),
    ...issues.map((issue) => ({
      severity: issue.severity,
      code: issue.code,
      phase: issue.category,
      message: issue.message,
      affectedIds: [] as string[],
    })),
    ...errors.map((error) => ({
      severity: 'error',
      code: error.code,
      phase: 'error',
      message: error.message,
      affectedIds: [] as string[],
    })),
  ];
}

/**
 * Issues tab: severity filter bar + the combined issues table.
 * @param result - Latest pipeline result.
 * @param state - Global project state.
 * @param dispatch - Project store dispatch.
 * @returns The issues panel content.
 */
function renderIssues(result: PipelineResult, state: ProjectState, dispatch: ProjectDispatch): JSX.Element {
  const allRows = buildIssueRows(result);
  const rows = state.severityFilter
    ? allRows.filter((row) => canonicalSeverity(row.severity) === state.severityFilter)
    : allRows;

  return (
    <RailSection title="Issues">
      <SeverityFilter
        result={result}
        value={state.severityFilter}
        onChange={(severity) => dispatch({ type: 'SET_SEVERITY_FILTER', severity })}
      />
      <IssuesTable rows={rows} />
    </RailSection>
  );
}

/** Key metrics highlighted per component type, in display order. */
const COMPONENT_METRICS: Record<string, string[]> = {
  pcb: ['max_deflection_m', 'flex_stress_pa', 'thermal_damage'],
  battery: ['transmitted_force_n', 'shock_g', 'crush_margin'],
  switch: ['usage_damage', 'stalk_damage'],
  encoder: ['usage_damage'],
  screw: ['margin', 'preload_fraction'],
  clip: ['derated_retention_force_n', 'creep_modulus_factor'],
  mount: ['compression_stress_pa', 'buckling_margin'],
  adhesive: ['utilization'],
};

function componentTone(status: string): Tone {
  switch (status.toLowerCase()) {
    case 'pass':
      return 'ok';
    case 'warn':
      return 'warn';
    case 'fail':
      return 'error';
    default:
      return 'neutral';
  }
}

function formatMetricValue(key: string, value: number | string | null | undefined): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (!Number.isFinite(value)) return '—';
  if (key.endsWith('_pa') || key.includes('stress')) return formatPressure(value);
  if (key.endsWith('_m')) return formatLength(value);
  if (key.endsWith('_n') || key.includes('force')) return formatForce(value);
  if (key === 'thermal_damage' || key === 'preload_fraction' || key === 'utilization') {
    return `${(value * 100).toFixed(1)}%`;
  }
  return formatNumber(value);
}

/** Read a component metric, honoring the battery crush_margin key rename. */
function readComponentMetric(
  metrics: Record<string, number | string | null>,
  key: string,
): number | string | null | undefined {
  if (key === 'crush_margin') return metrics.crush_margin ?? metrics.margin_crush;
  return metrics[key];
}

/** One component row: status badge, id + type, and the type's key metrics. */
function ComponentRow({ component }: { component: ComponentAssessment }): JSX.Element {
  const id = component.component_id ?? 'unknown';
  const type = component.type ?? 'unknown';
  const metrics = component.metrics ?? {};
  const keys = COMPONENT_METRICS[type] ?? Object.keys(metrics).slice(0, 3);
  const findings = component.findings ?? [];
  const usageRatio = component.usage_ratio;
  return (
    <div className="results-rail__component">
      <div className="results-rail__component-head">
        <StatusBadge tone={componentTone(component.status ?? 'not_evaluated')}>
          {component.status ?? 'not_evaluated'}
        </StatusBadge>
        <code>{id}</code>
        <span className="muted">{type}</span>
        {component.validity ? (
          <span className="results-rail__screening-label muted">{component.validity}</span>
        ) : null}
        {usageRatio !== null && usageRatio !== undefined && Number.isFinite(usageRatio) ? (
          <code className="chip results-rail__usage-chip">{formatRate(usageRatio)}</code>
        ) : null}
      </div>
      {keys.length > 0 ? (
        <div className="results-rail__component-metrics">
          {keys.map((key) => (
            <span key={key}>
              <span className="muted">{key}:</span> {formatMetricValue(key, readComponentMetric(metrics, key))}
            </span>
          ))}
        </div>
      ) : null}
      {findings.length > 0 ? (
        <div className="results-rail__component-findings">
          {findings.map((finding, index) => (
            <code
              key={`${finding.code}-${index}`}
              className="results-rail__finding"
              title={finding.message}
            >
              {finding.code}
            </code>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const SCREENING_NOTE_DEFAULT =
  'simplified models, low-medium confidence; component verdicts do not affect the shell result';

/** Components tab: secondary screening banner plus a compact per-component list. */
function renderComponents(result: PipelineResult): JSX.Element {
  const screening: ComponentScreeningResult | null | undefined = result.component_screening;
  const components: ComponentResult | null | undefined = screening ?? result.components;
  if (components === null || components === undefined) {
    return <Empty>No component analysis was requested.</Empty>;
  }
  const rows = components.components ?? [];
  const summary = components.summary;
  const failCount =
    summary?.fail_count ??
    rows.filter((row) => (row.status ?? '').toLowerCase() === 'fail').length;
  const warnCount =
    summary?.warn_count ??
    rows.filter((row) => (row.status ?? '').toLowerCase() === 'warn').length;
  const weakest =
    summary?.weakest ??
    rows.find((row) => (row.status ?? '').toLowerCase() === 'fail') ??
    null;
  const note = screening?.note ?? SCREENING_NOTE_DEFAULT;
  const confidence = screening?.confidence ?? 'low-medium';
  const bannerText = screening
    ? `SECONDARY COMPONENT SCREENING — ${note} (${confidence} confidence)`
    : `SECONDARY COMPONENT SCREENING — ${SCREENING_NOTE_DEFAULT}`;
  return (
    <>
      <p className="results-rail__screening-note">{bannerText}</p>
      <RailSection title="Component Assessment">
        <p className="results-rail__config-line">
          {rows.length} component(s), {failCount} failed, {warnCount} warnings — weakest:{' '}
          {weakest
            ? `${weakest.component_id ?? 'unknown'} (${weakest.status ?? 'not_evaluated'})`
            : 'none'}
        </p>
        {rows.length === 0 ? (
          <p className="muted">No components were reported.</p>
        ) : (
          rows.map((component, index) => (
            <ComponentRow key={`${component.component_id ?? 'component'}-${index}`} component={component} />
          ))
        )}
      </RailSection>
    </>
  );
}

/**
 * Rate formatter: 1 decimal for rates >= 0.1%, 2 decimals (significant)
 * for smaller rates, e.g. 1/10000 → 0.01%.
 */
function formatRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined || !Number.isFinite(rate)) return '—';
  const percent = rate * 100;
  return `${percent.toFixed(percent >= 0.1 ? 1 : 2)}%`;
}

/** Failure-rate bar list, fill width scaled against the worst rate. */
function FailureRateList({ rates }: { rates: NonNullable<PopulationResult['component_failure_rates']> }): JSX.Element {
  const maxRate = Math.max(...rates.map((r) => r.rate ?? 0), 0);
  return (
    <div className="results-rail__failrates">
      {rates.map((rate, index) => {
        const width = maxRate > 0 ? Math.max(2, ((rate.rate ?? 0) / maxRate) * 100) : 0;
        return (
          <div className="results-rail__failrate" key={`${rate.component_id ?? 'rate'}-${index}`}>
            <span className="results-rail__failrate-label">
              <code>{rate.component_id ?? 'unknown'}</code> <span className="muted">{rate.type ?? ''}</span>
            </span>
            <span className="results-rail__failrate-track">
              <span className="results-rail__failrate-fill" style={{ width: `${width}%` }} />
            </span>
            <span className="results-rail__failrate-value">{formatRate(rate.rate)}</span>
            <span className="results-rail__failrate-rank">#{rate.rank ?? '—'}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Population survival curve rendered as a small SVG polyline. */
function SurvivalCurve({ survival }: { survival: NonNullable<PopulationResult['survival']> }): JSX.Element | null {
  const points = survival
    .filter(
      (point) =>
        Number.isFinite(point.usage_fraction) &&
        Number.isFinite(point.survival_rate),
    )
    .map((point) => `${(point.usage_fraction as number) * 100},${40 - (point.survival_rate as number) * 40}`)
    .join(' ');
  if (points === '') return null;
  return (
    <svg
      className="results-rail__survival"
      viewBox="0 0 100 40"
      preserveAspectRatio="none"
      role="img"
      aria-label="Survival curve across usage"
    >
      <polyline points={points} />
    </svg>
  );
}

/** Guarded reads for the loose-typed population shell block. */
function readPopulationShell(shell: NonNullable<PopulationResult['shell']>): {
  failures: number | null;
  failure_rate: number | null;
  wilson_ci: { low: number | null; high: number | null } | null;
  sensitivity: SensitivityEntry[];
  assumptions: string[];
} {
  const toFinite = (value: unknown): number | null =>
    typeof value === 'number' && Number.isFinite(value) ? value : null;
  const ci = isRecord(shell.wilson_ci) ? shell.wilson_ci : null;
  return {
    failures: toFinite(shell.failures),
    failure_rate: toFinite(shell.failure_rate),
    wilson_ci:
      ci === null
        ? null
        : { low: toFinite(ci.low), high: toFinite(ci.high) },
    sensitivity: Array.isArray(shell.sensitivity)
      ? shell.sensitivity.filter(
          (entry): entry is SensitivityEntry => isRecord(entry) && typeof entry.parameter === 'string',
        )
      : [],
    assumptions: Array.isArray(shell.assumptions)
      ? shell.assumptions.filter((item): item is string => typeof item === 'string')
      : [],
  };
}

/** Guarded reads for a deterministic worst-case shell block. */
function readWorstCaseShell(shell: unknown): {
  safety_factor: number | null;
  peak_stress_pa: number | null;
  max_displacement_m: number | null;
  verdict: string | null;
} {
  const toFinite = (value: unknown): number | null =>
    typeof value === 'number' && Number.isFinite(value) ? value : null;
  const rec = isRecord(shell) ? shell : null;
  return {
    safety_factor: toFinite(rec?.safety_factor),
    peak_stress_pa: toFinite(rec?.peak_stress_pa),
    max_displacement_m: toFinite(rec?.max_displacement_m),
    verdict: rec && typeof rec.verdict === 'string' ? rec.verdict : null,
  };
}

/** Guarded reads for the deterministic worst-case drop block. */
function readWorstCaseDrop(drop: unknown): {
  drop_height_m: number | null;
  surface: string | null;
  orientation: string | null;
  peak_impact_speed_m_s: number | null;
  impact_energy_j: number | null;
  peak_acceleration_g: number | null;
} {
  const toFinite = (value: unknown): number | null =>
    typeof value === 'number' && Number.isFinite(value) ? value : null;
  const rec = isRecord(drop) ? drop : null;
  return {
    drop_height_m: toFinite(rec?.drop_height_m),
    surface: rec && typeof rec.surface === 'string' ? rec.surface : null,
    orientation: rec && typeof rec.orientation === 'string' ? rec.orientation : null,
    peak_impact_speed_m_s: toFinite(rec?.peak_impact_speed_m_s),
    impact_energy_j: toFinite(rec?.impact_energy_j),
    peak_acceleration_g: toFinite(rec?.peak_acceleration_g),
  };
}

/** Guarded reads for worst-case component rows. */
function readWorstCaseComponents(components: unknown): ComponentAssessment[] {
  if (!Array.isArray(components)) return [];
  return components.filter(
    (entry): entry is ComponentAssessment =>
      isRecord(entry) && (typeof entry.component_id === 'string' || typeof entry.status === 'string'),
  );
}

function worstCaseVerdictTone(verdict: string): Tone {
  switch (verdict.toLowerCase()) {
    case 'pass':
      return 'ok';
    case 'warn':
      return 'warn';
    case 'fail':
      return 'error';
    default:
      return 'neutral';
  }
}

/** Sensitivity strength chip (HIGH / MEDIUM / LOW / NOT_OBSERVED). */
function SensitivityLevelChip({ level }: { level?: SensitivityLevel | string }): JSX.Element | null {
  if (!level) return null;
  const normalized = level.toUpperCase();
  const tone: Tone =
    normalized === 'HIGH' || normalized === 'MEDIUM'
      ? normalized === 'HIGH'
        ? 'error'
        : 'warn'
      : 'neutral';
  return (
    <code className={`chip results-rail__sensitivity-level results-rail__sensitivity-level--${tone}`}>
      {normalized}
    </code>
  );
}

/** Deterministic worst-case population block (single corner run, no Monte Carlo tail). */
function renderWorstCasePopulation(population: PopulationResult): JSX.Element {
  const verdict = population.verdict ?? 'not_evaluated';
  const shell = readWorstCaseShell(population.shell);
  const drop = readWorstCaseDrop(population.drop);
  const components = readWorstCaseComponents(population.components);
  const assumptions = population.assumptions ?? [];
  return (
    <>
      <p className="results-rail__screening-note results-rail__worst-case-note">
        DETERMINISTIC WORST CASE — worst-case corner, not a Monte Carlo tail
      </p>
      <RailSection title="Deterministic Worst Case">
        <div className="results-rail__shell-status">
          <StatusBadge tone={worstCaseVerdictTone(verdict)}>{verdict}</StatusBadge>
        </div>
        <KvList
          rows={[
            {
              label: 'Safety factor',
              value: shell.safety_factor !== null ? formatNumber(shell.safety_factor) : '—',
            },
            {
              label: 'Peak stress',
              value: shell.peak_stress_pa !== null ? formatPressure(shell.peak_stress_pa) : '—',
            },
            {
              label: 'Max deformation',
              value: shell.max_displacement_m !== null ? formatLength(shell.max_displacement_m) : '—',
            },
            { label: 'Drop height', value: drop.drop_height_m !== null ? formatLength(drop.drop_height_m) : '—' },
            { label: 'Surface', value: drop.surface ?? '—' },
            { label: 'Orientation', value: drop.orientation ?? '—' },
            {
              label: 'Peak acceleration',
              value: drop.peak_acceleration_g !== null ? `${formatNumber(drop.peak_acceleration_g)} g` : '—',
            },
          ]}
        />
      </RailSection>
      {components.length > 0 ? (
        <RailSection title="Worst-case components">
          <ul className="results-rail__worst-case-components">
            {components.map((component, index) => {
              const usageRatio = component.usage_ratio;
              return (
                <li key={`${component.component_id ?? 'component'}-${index}`}>
                  <StatusBadge tone={componentTone(component.status ?? 'not_evaluated')}>
                    {component.status ?? 'not_evaluated'}
                  </StatusBadge>{' '}
                  <code>{component.component_id ?? 'unknown'}</code>{' '}
                  <span className="muted">{component.type ?? ''}</span>
                  {usageRatio !== null && usageRatio !== undefined && Number.isFinite(usageRatio) ? (
                    <code className="chip results-rail__usage-chip">{formatRate(usageRatio)}</code>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </RailSection>
      ) : null}
      {assumptions.length > 0 ? (
        <DisclosureCard title="Worst-case assumptions" count={assumptions.length}>
          <AssumptionsList items={assumptions} />
        </DisclosureCard>
      ) : null}
    </>
  );
}

/** Population tab: shell robustness first, then the secondary component screening. */
function renderPopulation(result: PipelineResult): JSX.Element {
  const population: PopulationResult | null | undefined = result.population;
  if (population === null || population === undefined) {
    return (
      <>
        <p className="results-rail__screening-note">
          SECONDARY COMPONENT SCREENING — {SCREENING_NOTE_DEFAULT}
        </p>
        <Empty>No population analysis was requested — run the worst-case analysis from Settings.</Empty>
      </>
    );
  }
  if (population.mode === 'deterministic_worst_case') {
    return renderWorstCasePopulation(population);
  }
  const sampleCount = population.sample_count ?? 0;
  const failed = population.units_failed ?? 0;
  const ci = population.wilson_ci;
  const header = [
    `${sampleCount.toLocaleString('en-US')} virtual units`,
    population.profile,
    `${population.lifespan_days ?? '—'} days`,
  ].join(' · ');
  const outcome = `${failed.toLocaleString('en-US')} failed (${formatRate(population.failure_rate)}${ci ? `, 95% Wilson CI ${formatRate(ci.low)}–${formatRate(ci.high)}` : ''})`;
  const weakest = population.weakest_components ?? [];
  const sensitivity = population.sensitivity ?? [];
  const survival = population.survival ?? [];
  const shell = population.shell !== null && population.shell !== undefined ? readPopulationShell(population.shell) : null;
  const shellSensitivity = (shell?.sensitivity ?? []).slice().sort(
    (a, b) => Math.abs(b.correlation ?? 0) - Math.abs(a.correlation ?? 0),
  );

  return (
    <>
      {shell ? (
        <RailSection title="Shell robustness across manufacturing variation">
          <p className="results-rail__config-line">
            {shell.failures !== null ? shell.failures.toLocaleString('en-US') : '—'} /{' '}
            {sampleCount.toLocaleString('en-US')} units below safety factor 1 (
            {formatRate(shell.failure_rate)}
            {shell.wilson_ci
              ? `, 95% Wilson CI ${formatRate(shell.wilson_ci.low)}–${formatRate(shell.wilson_ci.high)}`
              : ''}
            )
          </p>
          {shellSensitivity.length > 0 ? (
            <div className="results-rail__sensitivity">
              <div className="results-rail__subhead">
                Sensitivity (correlation with shell failure — correlation, not causation)
              </div>
              <ul>
                {shellSensitivity.slice(0, 5).map((entry, index) => (
                  <li key={`${entry.parameter ?? 'shell-sensitivity'}-${index}`}>
                    <code>{entry.parameter ?? 'parameter'}</code>:{' '}
                    <span className="results-rail__sensitivity-value">
                      {formatSigned(entry.correlation ?? null)}
                    </span>{' '}
                    <SensitivityLevelChip level={entry.level} />
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </RailSection>
      ) : null}
      <RailSection title="Population Analysis">
        <p className="results-rail__config-line">
          {header} — {outcome}
        </p>
        {population.component_failure_rates && population.component_failure_rates.length > 0 ? (
          <>
            <div className="results-rail__subhead">Secondary component screening</div>
            <FailureRateList rates={population.component_failure_rates} />
          </>
        ) : null}
        {weakest.length > 0 ? (
          <div className="results-rail__weakest">
            <div className="results-rail__subhead">Weakest components</div>
            <ul>
              {weakest.map((component, index) => (
                <li key={`${component.component_id ?? 'weakest'}-${index}`}>
                  <code>{component.component_id ?? 'unknown'}</code>{' '}
                  <span className="muted">{component.type ?? ''}</span> — {formatRate(component.rate)} · rank{' '}
                  {component.rank ?? '—'}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {survival.length > 0 ? (
          <div className="results-rail__survival-block">
            <div className="results-rail__subhead">Survival vs usage</div>
            <SurvivalCurve survival={survival} />
          </div>
        ) : null}
        {sensitivity.length > 0 ? (
          <div className="results-rail__sensitivity">
            <div className="results-rail__subhead">Sensitivity (top 5)</div>
            <ul>
              {sensitivity.slice(0, 5).map((entry, index) => (
                <li key={`${entry.parameter ?? 'sensitivity'}-${index}`}>
                  <code>{entry.parameter ?? 'parameter'}</code>:{' '}
                  <span className="results-rail__sensitivity-value">
                    {formatSigned(entry.correlation ?? null)}
                  </span>{' '}
                  <SensitivityLevelChip level={entry.level} />
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </RailSection>
    </>
  );
}
