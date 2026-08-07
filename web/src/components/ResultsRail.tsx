/**
 * Results rail — the side panel that renders the latest pipeline result,
 * organized into tabs (overview, mass, structural, impact, qualification,
 * issues) with guarded access to each optional result section.
 */
import { useState, useRef, useEffect, type ReactNode } from 'react';
import * as React from 'react';
import type { PipelineResult, QualificationGate } from '../api/contracts';
import { useProjectStore } from '../state/projectStore';
import {
  selectUnsupportedModes,
  selectAssumptions,
  selectHasStaleResult,
} from '../state/selectors';
import {
  lifecycleLabel,
  validityLabel,
  dispositionLabel,
  dispositionTone,
  modeLabel,
  gateStatusLabel,
  severityTone,
  severityLabel,
  validityConfidenceLabel,
} from '../lib/status';
import {
  formatMass,
  formatLength,
  formatForce,
  formatPressure,
  formatEnergy,
  formatAcceleration,
  formatDuration,
} from '../lib/units';
import { formatVector3, formatNumber } from '../lib/format';
import { StatusBadge } from './StatusBadge';

/** Identifiers for the tabs rendered by the results rail. */
type TabId = 'overview' | 'mass' | 'structural' | 'impact' | 'qualification' | 'issues';

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

const TABS: readonly TabId[] = ['overview', 'mass', 'structural', 'impact', 'qualification', 'issues'];

const TAB_LABELS: Record<TabId, string> = {
  overview: 'Overview',
  mass: 'Mass',
  structural: 'Structural',
  impact: 'Impact',
  qualification: 'Qualification',
  issues: 'Issues',
};

/** Shape of the project store state, derived from the store hook itself. */
type ProjectState = ReturnType<typeof useProjectStore>['state'];

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

/** One compact metric in the analysis deck. Values are always result-backed. */
function MetricCard({
  label,
  value,
  detail,
  tone = 'neutral',
}: {
  label: string;
  value: ReactNode;
  detail: string;
  tone?: 'brand' | 'telemetry' | 'neutral';
}): JSX.Element {
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
  const massValue =
    result.mass === null
      ? 'Not requested'
      : result.mass.mass_kg === null
        ? 'No value'
        : formatMass(result.mass.mass_kg);
  const structuralValue =
    result.structural === null
      ? 'Not requested'
      : result.structural.response.max_stress_pa === null
        ? 'No value'
        : formatPressure(result.structural.response.max_stress_pa);
  const impactValue =
    result.impact === null
      ? 'Not requested'
      : result.impact.result === null
        ? 'No estimate'
        : formatForce(result.impact.result.peak_force_n);

  return (
    <div className="results-rail__metric-strip" aria-label="Analysis metrics">
      <MetricCard
        label="Run"
        value={lifecycleLabel(result.lifecycle_state)}
        detail={result.mode}
        tone="brand"
      />
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
      <MetricCard label="Mass" value={massValue} detail="aggregate result" />
      <MetricCard label="Structural" value={structuralValue} detail="max stress" />
      <MetricCard label="Impact" value={impactValue} detail="peak force" />
    </div>
  );
}

function canonicalSeverity(severity: string): string {
  return severity.toLowerCase() === 'warn' ? 'warning' : severity.toLowerCase();
}

/** Shared severity filter for the issue deck and its affected-object signals. */
function SeverityFilter({
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
  let cleaned = text.replace(/^UNSUPPORTED_/i, '');
  cleaned = cleaned.replace(/_/g, ' ').toLowerCase();
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

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

function UnifiedUnsupportedTable({ items }: { items: string[] }): JSX.Element {
  if (items.length === 0) {
    return <p className="muted">None</p>;
  }
  return (
    <table className="dense-table">
      <thead>
        <tr>
          <th style={{ width: '40%' }}>Failure Mode</th>
          <th style={{ width: '60%' }}>Status</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item}>
            <td>
              <code>{formatHumanLabel(item)}</code>
            </td>
            <td>
              <StatusBadge tone="neutral">Disclosed (Not Simulated)</StatusBadge>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function UnifiedAssumptionsTable({ items }: { items: string[] }): JSX.Element {
  if (items.length === 0) {
    return <p className="muted">None</p>;
  }
  return (
    <table className="dense-table">
      <thead>
        <tr>
          <th style={{ width: '35%' }}>Item</th>
          <th style={{ width: '65%' }}>Assumption / Formula</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item, index) => {
          const parts = item.split(': ');
          const title = parts.length > 1 ? parts[0] : `Assumption ${index + 1}`;
          const detail = parts.length > 1 ? parts.slice(1).join(': ') : item;
          return (
            <tr key={index}>
              <th scope="row">{title}</th>
              <td>{detail}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function UnifiedReactionsTable({ reactions }: { reactions: Record<string, number> }): JSX.Element {
  const entries = Object.entries(reactions);
  if (entries.length === 0) {
    return <p className="muted">None</p>;
  }
  return (
    <table className="dense-table">
      <thead>
        <tr>
          <th style={{ width: '40%' }}>Fixture / Reaction</th>
          <th style={{ width: '60%' }}>Force</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([fixture, force]) => (
          <tr key={fixture}>
            <td>
              <code>{fixture}</code>
            </td>
            <td className="num">{formatForce(force)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function UnifiedFlagsTable({ flags }: { flags: string[] }): JSX.Element {
  if (flags.length === 0) {
    return <p className="muted">None</p>;
  }
  return (
    <table className="dense-table">
      <thead>
        <tr>
          <th style={{ width: '40%' }}>Flag Key</th>
          <th style={{ width: '60%' }}>Status</th>
        </tr>
      </thead>
      <tbody>
        {flags.map((flag) => (
          <tr key={flag}>
            <td>
              <code>{flag}</code>
            </td>
            <td>
              <StatusBadge tone="warn">Active Assumption</StatusBadge>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Mono chip list, or a muted dash when the list is empty. */
function Chips({ items }: { items: string[] }): JSX.Element {
  if (items.length === 0) {
    return <span className="muted">—</span>;
  }
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

/**
 * Tone for a lifecycle state: completed reads as ok, everything else as error.
 * @param lifecycleState - Raw lifecycle state from the pipeline result.
 * @returns The badge tone for that state.
 */
function lifecycleTone(lifecycleState: string): Tone {
  return lifecycleState === 'completed' ? 'ok' : 'error';
}

/**
 * Tone for a validity state: valid → ok, invalid → error, otherwise warn.
 * @param validityState - Raw validity state string.
 * @returns The badge tone for that state.
 */
function validityTone(validityState: string): Tone {
  if (validityState === 'valid') return 'ok';
  if (validityState === 'invalid') return 'error';
  return 'warn';
}

/**
 * Tone for a qualification gate: blockers are errors, unevaluable gates are
 * neutral, passed gates are ok, and any other gate is warn.
 * @param gate - The evaluated fields of a qualification gate.
 * @returns The badge tone for that gate.
 */
function gateTone(gate: { passed: boolean; evaluable: boolean; blocker: boolean }): Tone {
  if (gate.blocker) return 'error';
  if (!gate.evaluable) return 'neutral';
  return gate.passed ? 'ok' : 'warn';
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

  const [railWidth, setRailWidth] = useState<number>(420);
  const [isDragging, setIsDragging] = useState<boolean>(false);
  const dragStartXRef = useRef<number>(0);
  const startWidthRef = useRef<number>(420);

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
      const newWidth = Math.min(Math.max(startWidthRef.current + deltaX, 260), 720);
      setRailWidth(newWidth);
    };
    const handleMouseUp = () => {
      setIsDragging(false);
    };
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging]);

  const handleToggleExpand = () => {
    setRailWidth((prev) => (prev > 300 ? 260 : 420));
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
    <aside className="results-rail" style={{ width: `${railWidth}px` }}>
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
            <div className="results-rail__deck-header">
              <div className="results-rail__deck-title">
                <span className="panel-eyebrow">Analysis deck</span>
                <strong>Awaiting result</strong>
              </div>
              <span className="results-rail__deck-id">NO RUN</span>
            </div>
            <div className="results-rail__empty-container">
              <p className="results-rail__empty muted">
                No result yet — run an analysis to populate the deck.
              </p>
            </div>
          </>
        ) : (
          <>
            <div className="results-rail__deck-header">
              <div className="results-rail__deck-title">
                <span className="panel-eyebrow">Analysis deck</span>
                <strong>{state.mode === 'qualification' ? 'Qualification review' : 'Exploration study'}</strong>
              </div>
              <span className="results-rail__deck-id">RUN {result.run_id.slice(0, 12)}</span>
            </div>
            <MetricStrip result={result} />
            {selectHasStaleResult(state) && (
              <div className="stale-banner">Result is stale — rerun to refresh.</div>
            )}
            {selectUnsupportedModes(state).length > 0 ? (
              <details className="results-rail__disclosure">
                <summary>Unsupported modes disclosed ({selectUnsupportedModes(state).length})</summary>
                <ul>
                  {selectUnsupportedModes(state).map((mode) => <li key={mode}>{mode}</li>)}
                </ul>
              </details>
            ) : null}
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
            <SeverityFilter
              result={result}
              value={state.severityFilter}
              onChange={(severity) => dispatch({ type: 'SET_SEVERITY_FILTER', severity })}
            />
            <div className="results-rail__panel" role="tabpanel">
              {renderTab(activeTab, result, state)}
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
 * @returns The rendered panel content.
 */
function renderTab(tab: TabId, result: PipelineResult, state: ProjectState): JSX.Element {
  switch (tab) {
    case 'overview':
      return renderOverview(result, state);
    case 'mass':
      return renderMass(result);
    case 'structural':
      return renderStructural(result);
    case 'impact':
      return renderImpact(result);
    case 'qualification':
      return renderQualification(result);
    case 'issues':
      return renderIssues(result, state);
  }
}

/**
 * Tone for mass completeness status.
 * @param status - Mass status string.
 * @returns Badge tone matching status.
 */
function massStatusTone(status: string): Tone {
  if (status === 'complete' || status === 'valid') {
    return 'ok';
  }
  if (status === 'partial' || status === 'mixed') {
    return 'warn';
  }
  return 'neutral';
}

/**
 * Overview tab: executive summary, mode, validity, unsupported modes, assumptions, errors, and issues.
 * @param result - Latest pipeline result.
 * @param state - Global project state.
 * @returns The overview panel content.
 */
function renderOverview(result: PipelineResult, state: ProjectState): JSX.Element {
  const disposition = result.qualification?.evidence_disposition ?? 'exploration_only';
  const runId =
    result.run_id.length > 18 ? `${result.run_id.slice(0, 12)}…${result.run_id.slice(-6)}` : result.run_id;
  const unsupported = selectUnsupportedModes(state);
  const assumptions = selectAssumptions(state);
  const errors = result.errors;
  const issues = result.issues;

  return (
    <>
      <RailSection title="Summary">
        <table className="dense-table">
          <thead>
            <tr>
              <th style={{ width: '30%' }}>Metric</th>
              <th style={{ width: '30%' }}>Value / Status</th>
              <th style={{ width: '40%' }}>Description</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Mode</th>
              <td className="num-mid">{modeLabel(result.mode)}</td>
              <td className="muted">Pipeline evaluation mode</td>
            </tr>
            <tr>
              <th scope="row">Lifecycle</th>
              <td>
                <StatusBadge tone={lifecycleTone(result.lifecycle_state)}>
                  {lifecycleLabel(result.lifecycle_state)}
                </StatusBadge>
              </td>
              <td className="muted">Run execution state</td>
            </tr>
            <tr>
              <th scope="row">Run ID</th>
              <td className="num-mid">
                <code>{runId}</code>
              </td>
              <td className="muted">Execution run identifier</td>
            </tr>
            <tr>
              <th scope="row">Validity</th>
              <td>
                <StatusBadge tone={validityTone(result.validity.state)}>
                  {validityLabel(result.validity.state)}
                </StatusBadge>
              </td>
              <td className="muted">Overall physics validity</td>
            </tr>
            <tr>
              <th scope="row">Confidence</th>
              <td className="num-mid">{validityConfidenceLabel(result.validity.confidence)}</td>
              <td className="muted">Confidence metric</td>
            </tr>
            <tr>
              <th scope="row">Evidence disposition</th>
              <td>
                <StatusBadge tone={dispositionTone(disposition)}>{dispositionLabel(disposition)}</StatusBadge>
              </td>
              <td className="muted">Evidence fidelity level</td>
            </tr>
            <tr>
              <th scope="row">Engine version</th>
              <td className="num-mid">{result.engine_version}</td>
              <td className="muted">Pipeline solver engine</td>
            </tr>
          </tbody>
        </table>
      </RailSection>

      <DisclosureCard title="Unsupported failure modes" count={unsupported.length}>
        <UnifiedUnsupportedTable items={unsupported} />
      </DisclosureCard>

      <DisclosureCard title="Solver assumptions" count={assumptions.length}>
        <UnifiedAssumptionsTable items={assumptions} />
      </DisclosureCard>

      {errors.length > 0 && (
        <DisclosureCard title="Errors" count={errors.length} defaultOpen={true}>
          <ul>
            {errors.map((error, index) => (
              <li key={`${error.code}-${index}`}>
                <code>{error.code}</code> — {error.message}
              </li>
            ))}
          </ul>
        </DisclosureCard>
      )}

      {issues.length > 0 && (
        <DisclosureCard title="Issues" count={issues.length} defaultOpen={false}>
          <ul>
            {issues.map((issue, index) => (
              <li key={`${issue.code}-${index}`}>
                <StatusBadge tone={severityTone(issue.severity)}>{severityLabel(issue.severity)}</StatusBadge>{' '}
                <code>{issue.category}</code> — {issue.message}
              </li>
            ))}
          </ul>
        </DisclosureCard>
      )}
    </>
  );
}

/**
 * Mass tab: aggregate mass summary and the per-object breakdown table.
 * @param result - Latest pipeline result.
 * @returns The mass panel content.
 */
function renderMass(result: PipelineResult): JSX.Element {
  if (result.mass === null) {
    return <Empty>No mass analysis was requested.</Empty>;
  }
  const mass = result.mass;
  return (
    <>
      <RailSection title="Mass Summary">
        <table className="dense-table">
          <thead>
            <tr>
              <th style={{ width: '30%' }}>Metric</th>
              <th style={{ width: '30%' }}>Value</th>
              <th style={{ width: '40%' }}>Description</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Mass</th>
              <td className="num-mid">{mass.mass_kg !== null ? formatMass(mass.mass_kg) : '—'}</td>
              <td className="muted">Total mouse assembly weight</td>
            </tr>
            <tr>
              <th scope="row">Status</th>
              <td className="num-mid">
                <StatusBadge tone={massStatusTone(mass.mass_status)}>{mass.mass_status}</StatusBadge>
              </td>
              <td className="muted">Mass computation fidelity</td>
            </tr>
            <tr>
              <th scope="row">Center of mass</th>
              <td className="num-mid">{formatVector3(mass.center_of_mass_m)}</td>
              <td className="muted">Center of gravity coordinates</td>
            </tr>
            <tr>
              <th scope="row">Uncertainty</th>
              <td className="num-mid">{mass.uncertainty_kg !== null ? formatMass(mass.uncertainty_kg) : '—'}</td>
              <td className="muted">Estimated mass tolerance</td>
            </tr>
            <tr>
              <th scope="row">Completeness</th>
              <td className="num-mid">{`${Math.round(mass.completeness * 100)}%`}</td>
              <td className="muted">Geometry completeness ratio</td>
            </tr>
          </tbody>
        </table>
      </RailSection>

      <RailSection title="Objects">
        <table className="dense-table">
          <thead>
            <tr>
              <th style={{ width: '22%' }}>Object</th>
              <th style={{ width: '18%' }} className="num-mid">Mass</th>
              <th style={{ width: '18%' }} className="num-mid">Volume</th>
              <th style={{ width: '14%' }}>Status</th>
              <th style={{ width: '14%' }}>Source</th>
              <th style={{ width: '14%' }}>Review</th>
            </tr>
          </thead>
          <tbody>
            {mass.objects.map((obj) => (
              <tr key={obj.object_id}>
                <td>
                  <code>{obj.object_id}</code>
                </td>
                <td className="num-mid">{obj.mass_kg !== null ? formatMass(obj.mass_kg) : '—'}</td>
                <td className="num-mid">{obj.volume_m3 !== null ? `${formatNumber(obj.volume_m3)} m³` : '—'}</td>
                <td>{obj.mass_status}</td>
                <td>{obj.source_status}</td>
                <td>{obj.review_status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </RailSection>
    </>
  );
}

/**
 * Structural tab: solver response, displacements, stresses, reactions, flags, assumptions, unsupported modes, and preflight checks.
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
  const preflight = result.structural.preflight ?? [];

  return (
    <>
      <RailSection title="Structural Response">
        <table className="dense-table">
          <thead>
            <tr>
              <th style={{ width: '30%' }}>Metric</th>
              <th style={{ width: '30%' }}>Value</th>
              <th style={{ width: '40%' }}>Description</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Method</th>
              <td className="num-mid"><code>{response.method_id}</code></td>
              <td className="muted">Structural FEA method</td>
            </tr>
            <tr>
              <th scope="row">Validity</th>
              <td>
                <StatusBadge tone={validityTone(response.validity)}>{validityLabel(response.validity)}</StatusBadge>
              </td>
              <td className="muted">FEA convergence status</td>
            </tr>
            <tr>
              <th scope="row">Max displacement</th>
              <td className="num-mid">{response.max_displacement_m !== null ? formatLength(response.max_displacement_m) : '—'}</td>
              <td className="muted">Shell deflection under hand load</td>
            </tr>
            <tr>
              <th scope="row">Displacement location</th>
              <td className="num-mid">{formatVector3(response.max_displacement_location)}</td>
              <td className="muted">Peak displacement location</td>
            </tr>
            <tr>
              <th scope="row">Max stress</th>
              <td className="num-mid">{response.max_stress_pa !== null ? formatPressure(response.max_stress_pa) : '—'}</td>
              <td className="muted">Peak von Mises stress</td>
            </tr>
            <tr>
              <th scope="row">Filtered stress</th>
              <td className="num-mid">{response.max_stress_filtered_pa !== null ? formatPressure(response.max_stress_filtered_pa) : '—'}</td>
              <td className="muted">Singularity-filtered stress</td>
            </tr>
            <tr>
              <th scope="row">Filtered location</th>
              <td className="num-mid">{formatVector3(response.filtered_location)}</td>
              <td className="muted">Filtered peak stress location</td>
            </tr>
            <tr>
              <th scope="row">Safety factor</th>
              <td className="num-mid">
                {response.safety_factor !== null ? formatNumber(response.safety_factor) : (response.safety_factor_status ?? '—')}
              </td>
              <td className="muted">Structural safety factor</td>
            </tr>
            <tr>
              <th scope="row">Force residual</th>
              <td className="num-mid">{response.force_residual_n !== null ? formatForce(response.force_residual_n) : '—'}</td>
              <td className="muted">Equilibrium force residual</td>
            </tr>
            <tr>
              <th scope="row">Moment residual</th>
              <td className="num-mid">
                {response.moment_residual_n_m !== null ? `${formatNumber(response.moment_residual_n_m)} N·m` : '—'}
              </td>
              <td className="muted">Equilibrium moment residual</td>
            </tr>
          </tbody>
        </table>
      </RailSection>
      <DisclosureCard title="Reactions" count={Object.keys(response.reactions).length}>
        <UnifiedReactionsTable reactions={response.reactions} />
      </DisclosureCard>
      <DisclosureCard title="Flags" count={response.flags.length}>
        <UnifiedFlagsTable flags={response.flags} />
      </DisclosureCard>
      <DisclosureCard title="Solver assumptions" count={response.assumptions.length}>
        <UnifiedAssumptionsTable items={response.assumptions} />
      </DisclosureCard>
      <DisclosureCard title="Unsupported failure modes" count={response.unsupported_failure_modes.length}>
        <UnifiedUnsupportedTable items={response.unsupported_failure_modes} />
      </DisclosureCard>
      {preflight.length > 0 && (
        <DisclosureCard title="Preflight checks" count={preflight.length}>
          <table className="dense-table">
            <thead>
              <tr>
                <th style={{ width: '30%' }}>Code</th>
                <th style={{ width: '30%' }}>Severity</th>
                <th style={{ width: '40%' }}>Message</th>
              </tr>
            </thead>
            <tbody>
              {preflight.map((item, index) => (
                <tr key={`${item.code}-${index}`}>
                  <td>
                    <code>{item.code}</code>
                  </td>
                  <td>
                    <StatusBadge tone={severityTone(item.severity)}>{severityLabel(item.severity)}</StatusBadge>
                  </td>
                  <td className="muted">{item.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </DisclosureCard>
      )}
    </>
  );
}

/**
 * Impact tab: contact estimate summary, assumptions, and unsupported modes.
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
          <p className="muted" style={{ fontSize: '11px' }}>
            3D rigid-body simulation · {simulation.config.test} test ·{' '}
            {simulation.config.height_m.toFixed(2)} m · {simulation.config.surface} ·{' '}
            {simulation.config.drop_count} drop(s) · {simulation.config.orientation} orientation
            {simulation.model.support_model === 'mesh_extreme_points'
              ? ` · ${simulation.model.support_point_count} support points`
              : ''}
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
            <p className="muted" style={{ fontSize: '11px' }}>
              Worst impact: {simulation.peak.impact_speed_m_s.toFixed(2)} m/s at{' '}
              {simulation.peak.t_s.toFixed(2)} s
              {simulation.peak_force_estimate_n
                ? ` · estimated peak force ${formatForce(simulation.peak_force_estimate_n)}`
                : ''}
            </p>
          ) : null}
        </RailSection>
      ) : null}
      {estimate ? (
        <RailSection title="Impact Estimate">
          <table className="dense-table">
            <thead>
              <tr>
                <th style={{ width: '30%' }}>Metric</th>
                <th style={{ width: '30%' }}>Value</th>
                <th style={{ width: '40%' }}>Description</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row">Closing velocity</th>
                <td className="num-mid">{formatNumber(estimate.closing_velocity_m_s)} m/s</td>
                <td className="muted">Free fall impact velocity</td>
              </tr>
              <tr>
                <th scope="row">Impact energy</th>
                <td className="num-mid">{formatEnergy(estimate.impact_energy_j)}</td>
                <td className="muted">Quasi-static impact kinetic energy</td>
              </tr>
              <tr>
                <th scope="row">Effective mass</th>
                <td className="num-mid">{formatMass(estimate.effective_mass_kg)}</td>
                <td className="muted">Effective falling assembly mass</td>
              </tr>
              <tr>
                <th scope="row">Impulse</th>
                <td className="num-mid">{formatNumber(estimate.impulse_n_s)} N·s</td>
                <td className="muted">Total impact momentum change</td>
              </tr>
              <tr>
                <th scope="row">Peak force</th>
                <td className="num-mid">{formatForce(estimate.peak_force_n)}</td>
                <td className="muted">Peak contact force</td>
              </tr>
              <tr>
                <th scope="row">Peak acceleration</th>
                <td className="num-mid">{formatNumber(estimate.peak_acceleration_m_s2)} m/s²</td>
                <td className="muted">Peak deceleration at impact</td>
              </tr>
              <tr>
                <th scope="row">Contact duration</th>
                <td className="num-mid">{formatNumber(estimate.contact_duration_s)} s</td>
                <td className="muted">Impulse contact duration</td>
              </tr>
              <tr>
                <th scope="row">Contact compression</th>
                <td className="num-mid">{formatLength(estimate.contact_compression_m)}</td>
                <td className="muted">Peak bumper deformation</td>
              </tr>
              <tr>
                <th scope="row">Validity</th>
                <td>
                  <StatusBadge tone={validityTone(estimate.validity)}>{validityLabel(estimate.validity)}</StatusBadge>
                </td>
                <td className="muted">Impact solver validity state</td>
              </tr>
              <tr>
                <th scope="row">Qualification blocked</th>
                <td className="num-mid">{estimate.qualification_blocked ? 'yes' : 'no'}</td>
                <td className="muted">Impact qualification gate blocker</td>
              </tr>
              <tr>
                <th scope="row">Safety factor</th>
                <td className="num-mid">
                  {typeof estimate.safety_factor === 'number' ? formatNumber(estimate.safety_factor) : estimate.safety_factor}
                </td>
                <td className="muted">Impact safety factor limit</td>
              </tr>
            </tbody>
          </table>
        </RailSection>
      ) : null}
      {estimate && estimate.flags.includes('CONTACT_PATCH_ASSUMPTION') ? (
        <p className="results-rail__note muted">Energy-based quasi-static estimate; local contact stress not resolved.</p>
      ) : null}
      {estimate ? (
        <DisclosureCard title="Solver assumptions" count={estimate.assumptions.length}>
          <UnifiedAssumptionsTable items={estimate.assumptions} />
        </DisclosureCard>
      ) : null}
      {estimate ? (
        <DisclosureCard title="Unsupported failure modes" count={estimate.unsupported_failure_modes.length}>
          <UnifiedUnsupportedTable items={estimate.unsupported_failure_modes} />
        </DisclosureCard>
      ) : null}
    </>
  );
}

function renderGateTable(gates: QualificationGate[]): JSX.Element {
  if (gates.length === 0) {
    return <p className="muted" style={{ padding: '4px 0' }}>No gate checks assigned to this study.</p>;
  }
  return (
    <table className="dense-table">
      <thead>
        <tr>
          <th style={{ width: '22%' }}>Key</th>
          <th style={{ width: '20%', textAlign: 'center' }}>Status</th>
          <th style={{ width: '58%' }}>Gate & Explanation</th>
        </tr>
      </thead>
      <tbody>
        {gates.map((gate) => (
          <tr key={gate.key}>
            <td>
              <code>{gate.key}</code>
            </td>
            <td style={{ textAlign: 'center' }}>
              <StatusBadge tone={gateTone(gate)}>
                {gateStatusLabel({ passed: gate.passed, evaluable: gate.evaluable, blocker: gate.blocker })}
              </StatusBadge>
            </td>
            <td>
              <strong style={{ color: 'var(--text-primary)' }}>{gate.label}</strong>
              {gate.explanation ? (
                <span className="muted" style={{ marginLeft: '6px' }}>— {gate.explanation}</span>
              ) : null}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * Qualification tab: divided into 3 study sections (Slam Impact, Downforce, Drop Suite)
 * with values aligned in the middle column and explanations on the right side.
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
        <table className="dense-table">
          <thead>
            <tr>
              <th style={{ width: '25%' }}>Metric</th>
              <th style={{ width: '25%' }}>Status / Value</th>
              <th style={{ width: '50%' }}>Description</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Overall Status</th>
              <td>
                <StatusBadge tone={qualification.qualified ? 'ok' : 'error'}>
                  {qualification.qualified ? 'qualified' : 'not qualified'}
                </StatusBadge>
              </td>
              <td className="muted">Overall gate pass criteria</td>
            </tr>
            <tr>
              <th scope="row">Evidence Disposition</th>
              <td>
                <StatusBadge tone={dispositionTone(qualification.evidence_disposition)}>
                  {dispositionLabel(qualification.evidence_disposition)}
                </StatusBadge>
              </td>
              <td className="muted">Fidelity evidence level</td>
            </tr>
            <tr>
              <th scope="row">Blocking Keys</th>
              <td>
                <Chips items={qualification.blocking_keys} />
              </td>
              <td className="muted">Critical gate blockers</td>
            </tr>
          </tbody>
        </table>
      </RailSection>

      {/* STUDY 1: SLAM IMPACT */}
      <div className="qualification-study-card">
        <div className="qualification-study-card__header">
          <div className="qualification-study-card__title">
            <span className="qualification-study-card__badge">STUDY 01</span>
            <span>Slam Impact (Drop Contact)</span>
          </div>
        </div>
        <div className="qualification-study-card__subhead">Preview Metrics</div>
        <table className="dense-table" style={{ marginBottom: '12px' }}>
          <thead>
            <tr>
              <th style={{ width: '25%' }}>Metric</th>
              <th style={{ width: '25%' }}>Value</th>
              <th style={{ width: '50%' }}>Description</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Peak Acceleration</th>
              <td className="num-mid">{impact ? formatAcceleration(impact.peak_acceleration_m_s2) : '—'}</td>
              <td className="muted">Drop deceleration peak</td>
            </tr>
            <tr>
              <th scope="row">Peak Force</th>
              <td className="num-mid">{impact ? formatForce(impact.peak_force_n) : '—'}</td>
              <td className="muted">Contact impact load</td>
            </tr>
            <tr>
              <th scope="row">Impact Energy</th>
              <td className="num-mid">{impact ? formatEnergy(impact.impact_energy_j) : '—'}</td>
              <td className="muted">Impact kinetic energy</td>
            </tr>
            <tr>
              <th scope="row">Contact Duration</th>
              <td className="num-mid">{impact ? formatDuration(impact.contact_duration_s) : '—'}</td>
              <td className="muted">Impact duration window</td>
            </tr>
          </tbody>
        </table>
        <div className="qualification-study-card__subhead">Gate Checks</div>
        {renderGateTable(slamGates)}
      </div>

      {/* STUDY 2: DOWNFORCE */}
      <div className="qualification-study-card">
        <div className="qualification-study-card__header">
          <div className="qualification-study-card__title">
            <span className="qualification-study-card__badge">STUDY 02</span>
            <span>Downforce (Hand Load Flex)</span>
          </div>
        </div>
        <div className="qualification-study-card__subhead">Preview Metrics</div>
        <table className="dense-table" style={{ marginBottom: '12px' }}>
          <thead>
            <tr>
              <th style={{ width: '25%' }}>Metric</th>
              <th style={{ width: '25%' }}>Value</th>
              <th style={{ width: '50%' }}>Description</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Max Displacement</th>
              <td className="num-mid">{structural ? formatLength(structural.max_displacement_m) : '—'}</td>
              <td className="muted">Shell deflection under hand load</td>
            </tr>
            <tr>
              <th scope="row">Max Stress</th>
              <td className="num-mid">{structural ? formatPressure(structural.max_stress_pa) : '—'}</td>
              <td className="muted">Peak von Mises stress</td>
            </tr>
            <tr>
              <th scope="row">Safety Factor</th>
              <td className="num-mid">
                {structural?.safety_factor !== null && structural?.safety_factor !== undefined
                  ? formatNumber(structural.safety_factor)
                  : '—'}
              </td>
              <td className="muted">Structural safety margin</td>
            </tr>
            <tr>
              <th scope="row">Response Validity</th>
              <td className="num-mid">{structural ? structural.validity : '—'}</td>
              <td className="muted">FEA solver convergence state</td>
            </tr>
          </tbody>
        </table>
        <div className="qualification-study-card__subhead">Gate Checks</div>
        {renderGateTable(downforceGates)}
      </div>

      {/* STUDY 3: DROP SUITE */}
      <div className="qualification-study-card">
        <div className="qualification-study-card__header">
          <div className="qualification-study-card__title">
            <span className="qualification-study-card__badge">STUDY 03</span>
            <span>Drop Suite (Multi-Axis & Budget Sweep)</span>
          </div>
        </div>
        <div className="qualification-study-card__subhead">Preview Metrics</div>
        <table className="dense-table" style={{ marginBottom: '12px' }}>
          <thead>
            <tr>
              <th style={{ width: '25%' }}>Metric</th>
              <th style={{ width: '25%' }}>Value</th>
              <th style={{ width: '50%' }}>Description</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Total Mass</th>
              <td className="num-mid">{result.mass ? formatMass(result.mass.mass_kg) : '—'}</td>
              <td className="muted">Total mouse assembly weight</td>
            </tr>
            <tr>
              <th scope="row">Center of Mass</th>
              <td className="num-mid">{result.mass ? formatVector3(result.mass.center_of_mass_m) : '—'}</td>
              <td className="muted">Center of gravity coordinates</td>
            </tr>
            <tr>
              <th scope="row">Completeness</th>
              <td className="num-mid">{result.mass ? `${Math.round(result.mass.completeness * 100)}%` : '—'}</td>
              <td className="muted">Geometry mass completeness</td>
            </tr>
          </tbody>
        </table>
        <div className="qualification-study-card__subhead">Gate Checks</div>
        {renderGateTable(remainingGates)}
      </div>
    </>
  );
}

/**
 * Issues tab: combined table of validation findings, pipeline issues, and
 * pipeline errors.
 * @param result - Latest pipeline result.
 * @returns The issues panel content.
 */
function IssuesTable({ rows }: { rows: IssueRow[] }): JSX.Element {
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);

  if (rows.length === 0) {
    return <Empty>No issues or validation findings reported.</Empty>;
  }

  return (
    <table className="dense-table dense-table--interactive">
      <thead>
        <tr>
          <th style={{ width: '24px' }}></th>
          <th style={{ width: '90px' }}>Severity</th>
          <th style={{ width: '210px' }}>Code</th>
          <th style={{ width: '100px' }}>Phase</th>
          <th>Message</th>
          <th style={{ width: '140px' }}>Affected</th>
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
                onMouseEnter={() => setExpandedIndex(index)}
                tabIndex={0}
                role="button"
                aria-expanded={isExpanded}
                title="Move cursor over or click line to expand details"
              >
                <td className="expand-chevron">{isExpanded ? '▼' : '▶'}</td>
                <td>
                  <StatusBadge tone={severityTone(row.severity)}>
                    {severityLabel(row.severity)}
                  </StatusBadge>
                </td>
                <td>
                  <code>{row.code}</code>
                </td>
                <td>{row.phase}</td>
                <td title={row.message}>{row.message}</td>
                <td>
                  <Chips items={row.affectedIds} />
                </td>
              </tr>
              {isExpanded ? (
                <tr className="table-row--details">
                  <td colSpan={6}>
                    <div className="issue-detail-card">
                      <h5>Diagnostic Details: {row.code}</h5>
                      <p><strong>Message:</strong> {row.message}</p>
                      <p><strong>Phase:</strong> {row.phase} &bull; <strong>Severity:</strong> {row.severity}</p>
                      {row.affectedIds.length > 0 ? (
                        <p><strong>Affected Components:</strong> {row.affectedIds.join(', ')}</p>
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

function renderIssues(result: PipelineResult, state: ProjectState): JSX.Element {
  const allRows = buildIssueRows(result);
  const rows = state.severityFilter
    ? allRows.filter((row) => canonicalSeverity(row.severity) === state.severityFilter)
    : allRows;

  return (
    <RailSection title="Issues">
      {state.severityFilter && allRows.length > 0 ? (
        <p className="results-rail__note muted">
          Showing {rows.length} of {allRows.length} findings at {state.severityFilter} severity.
        </p>
      ) : null}
      <IssuesTable rows={rows} />
    </RailSection>
  );
}
