/**
 * Results rail — the side panel that renders the latest pipeline result,
 * organized into tabs (overview, mass, structural, impact, qualification,
 * issues) with guarded access to each optional result section.
 */
import { useState, useRef, useEffect, type ReactNode } from 'react';
import * as React from 'react';
import type { PipelineResult } from '../api/contracts';
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
import { formatVector3, formatNumber, formatPercent } from '../lib/format';
import { StatusBadge } from './StatusBadge';

/** Identifiers for the tabs rendered by the results rail. */
type TabId = 'overview' | 'mass' | 'structural' | 'impact' | 'qualification' | 'issues';

/** Badge tone values supported by StatusBadge. */
type Tone = 'ok' | 'error' | 'warn' | 'neutral';

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

/** Bullet list of strings, or a muted "None" when the list is empty. */
function StringList({ items }: { items: string[] }): JSX.Element {
  if (items.length === 0) {
    return <p className="muted">None</p>;
  }
  return (
    <ul>
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
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
 * @returns The results rail element.
 */
export function ResultsRail(): JSX.Element {
  const { state, dispatch } = useProjectStore();
  const result = state.lastResult;
  const activeTab: TabId = (TABS as readonly string[]).includes(state.resultsTab)
    ? (state.resultsTab as TabId)
    : 'overview';

  const [railHeight, setRailHeight] = useState<number>(240);
  const [isDragging, setIsDragging] = useState<boolean>(false);
  const dragStartYRef = useRef<number>(0);
  const startHeightRef = useRef<number>(240);

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
    dragStartYRef.current = e.clientY;
    startHeightRef.current = railHeight;
  };

  useEffect(() => {
    if (!isDragging) return;
    const handleMouseMove = (e: MouseEvent) => {
      const deltaY = dragStartYRef.current - e.clientY;
      const newHeight = Math.min(Math.max(startHeightRef.current + deltaY, 140), 650);
      setRailHeight(newHeight);
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
    setRailHeight((prev) => (prev > 320 ? 240 : 480));
  };

  if (result === null) {
    return (
      <aside className="results-rail" style={{ height: `${railHeight}px` }}>
        <div
          className="results-rail__resize-handle"
          onMouseDown={handleMouseDown}
          onDoubleClick={handleToggleExpand}
          title="Drag up/down or double-click to resize/expand results panel"
        >
          <span className="resize-handle-bar" />
        </div>
        <div className="results-rail__empty-container" style={{ padding: '16px', textAlign: 'center' }}>
          <p className="results-rail__empty muted">
            No analysis result yet. Upload geometry to calculate mass &amp; structural analysis.
          </p>
        </div>
      </aside>
    );
  }

  return (
    <aside className="results-rail" style={{ height: `${railHeight}px` }}>
      <div
        className="results-rail__resize-handle"
        onMouseDown={handleMouseDown}
        onDoubleClick={handleToggleExpand}
        title="Drag up/down or double-click to resize/expand results panel"
      >
        <span className="resize-handle-bar" />
      </div>
      {selectHasStaleResult(state) && (
        <div className="stale-banner">
          This result is stale — it no longer matches the current model inputs. Rerun the analysis to
          refresh it.
        </div>
      )}
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
        {renderTab(activeTab, result, state)}
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
      return renderIssues(result);
  }
}

/**
 * Overview tab: run metadata, validity, and cross-cutting findings.
 * @param result - Latest pipeline result.
 * @param state - Project store state.
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
          <tbody>
            <tr>
              <th scope="row">Mode</th>
              <td>{modeLabel(result.mode)}</td>
            </tr>
            <tr>
              <th scope="row">Lifecycle</th>
              <td>
                <StatusBadge tone={lifecycleTone(result.lifecycle_state)}>
                  {lifecycleLabel(result.lifecycle_state)}
                </StatusBadge>
              </td>
            </tr>
            <tr>
              <th scope="row">Run ID</th>
              <td>
                <code>{runId}</code>
              </td>
            </tr>
            <tr>
              <th scope="row">Validity</th>
              <td>
                <StatusBadge tone={validityTone(result.validity.state)}>
                  {validityLabel(result.validity.state)}
                </StatusBadge>
              </td>
            </tr>
            <tr>
              <th scope="row">Confidence</th>
              <td>{validityConfidenceLabel(result.validity.confidence)}</td>
            </tr>
            <tr>
              <th scope="row">Evidence disposition</th>
              <td>
                <StatusBadge tone={dispositionTone(disposition)}>{dispositionLabel(disposition)}</StatusBadge>
              </td>
            </tr>
            <tr>
              <th scope="row">Engine version</th>
              <td>{result.engine_version}</td>
            </tr>
          </tbody>
        </table>
      </RailSection>
      <RailSection title="Unsupported failure modes">
        <StringList items={unsupported} />
      </RailSection>
      <RailSection title="Assumptions">
        <StringList items={assumptions} />
      </RailSection>
      <RailSection title="Errors">
        {errors.length === 0 ? (
          <p className="muted">None</p>
        ) : (
          <ul>
            {errors.map((error, index) => (
              <li key={`${error.code}-${index}`}>
                <code>{error.code}</code> — {error.message}
              </li>
            ))}
          </ul>
        )}
      </RailSection>
      <RailSection title="Issues">
        {issues.length === 0 ? (
          <p className="muted">None</p>
        ) : (
          <ul>
            {issues.map((issue, index) => (
              <li key={`${issue.code}-${index}`}>
                <StatusBadge tone={severityTone(issue.severity)}>{severityLabel(issue.severity)}</StatusBadge>{' '}
                <code>{issue.category}</code> — {issue.message}
              </li>
            ))}
          </ul>
        )}
      </RailSection>
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
      <RailSection title="Mass summary">
        <table className="dense-table">
          <tbody>
            <tr>
              <th scope="row">Mass</th>
              <td className="num">{mass.mass_kg !== null ? formatMass(mass.mass_kg) : '—'}</td>
            </tr>
            <tr>
              <th scope="row">Status</th>
              <td>{mass.mass_status}</td>
            </tr>
            <tr>
              <th scope="row">Center of mass</th>
              <td>{formatVector3(mass.center_of_mass_m)}</td>
            </tr>
            <tr>
              <th scope="row">Uncertainty</th>
              <td className="num">{mass.uncertainty_kg !== null ? formatMass(mass.uncertainty_kg) : '—'}</td>
            </tr>
            <tr>
              <th scope="row">Completeness</th>
              <td className="num">{formatPercent(mass.completeness)}</td>
            </tr>
          </tbody>
        </table>
      </RailSection>
      <RailSection title="Objects">
        <table className="dense-table">
          <thead>
            <tr>
              <th>Object</th>
              <th>Mass</th>
              <th>Volume</th>
              <th>Status</th>
              <th>Source</th>
              <th>Review</th>
            </tr>
          </thead>
          <tbody>
            {mass.objects.map((object) => (
              <tr key={object.object_id}>
                <th scope="row">
                  <code>{object.object_id}</code>
                </th>
                <td className="num">{object.mass_kg !== null ? formatMass(object.mass_kg) : '—'}</td>
                <td className="num">
                  {object.volume_m3 !== null ? `${formatNumber(object.volume_m3)} m³` : '—'}
                </td>
                <td>{object.mass_status}</td>
                <td>{object.source_status}</td>
                <td>{object.review_status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </RailSection>
    </>
  );
}

/**
 * Structural tab: response summary, reactions, flags, assumptions, and
 * preflight checks.
 * @param result - Latest pipeline result.
 * @returns The structural panel content.
 */
function renderStructural(result: PipelineResult): JSX.Element {
  if (result.structural === null) {
    return <Empty>No structural analysis was requested.</Empty>;
  }
  const { response, preflight } = result.structural;
  return (
    <>
      <RailSection title="Structural response">
        <table className="dense-table">
          <tbody>
            <tr>
              <th scope="row">Method</th>
              <td>{response.method_id}</td>
            </tr>
            <tr>
              <th scope="row">Validity</th>
              <td>
                <StatusBadge tone={validityTone(response.validity)}>{validityLabel(response.validity)}</StatusBadge>
              </td>
            </tr>
            <tr>
              <th scope="row">Max displacement</th>
              <td className="num">
                {response.max_displacement_m !== null ? formatLength(response.max_displacement_m) : '—'}
              </td>
            </tr>
            <tr>
              <th scope="row">Displacement location</th>
              <td>{formatVector3(response.max_displacement_location)}</td>
            </tr>
            <tr>
              <th scope="row">Max stress</th>
              <td className="num">{formatPressure(response.max_stress_pa)}</td>
            </tr>
            <tr>
              <th scope="row">Filtered stress</th>
              <td className="num">{formatPressure(response.max_stress_filtered_pa)}</td>
            </tr>
            <tr>
              <th scope="row">Filtered location</th>
              <td>{formatVector3(response.filtered_location)}</td>
            </tr>
            <tr>
              <th scope="row">Safety factor</th>
              <td className="num">
                {response.safety_factor !== null ? formatNumber(response.safety_factor) : (response.safety_factor_status ?? '—')}
              </td>
            </tr>
            <tr>
              <th scope="row">Force residual</th>
              <td className="num">{response.force_residual_n !== null ? formatForce(response.force_residual_n) : '—'}</td>
            </tr>
            <tr>
              <th scope="row">Moment residual</th>
              <td className="num">
                {response.moment_residual_n_m !== null ? `${formatNumber(response.moment_residual_n_m)} N·m` : '—'}
              </td>
            </tr>
          </tbody>
        </table>
      </RailSection>
      <RailSection title="Reactions">
        {Object.keys(response.reactions).length === 0 ? (
          <p className="muted">None</p>
        ) : (
          <ul>
            {Object.entries(response.reactions).map(([fixture, force]) => (
              <li key={fixture}>
                <code>{fixture}</code>: {formatNumber(force)} N
              </li>
            ))}
          </ul>
        )}
      </RailSection>
      <RailSection title="Flags">
        <StringList items={response.flags} />
      </RailSection>
      <RailSection title="Assumptions">
        <StringList items={response.assumptions} />
      </RailSection>
      <RailSection title="Unsupported modes">
        <StringList items={response.unsupported_failure_modes} />
      </RailSection>
      <RailSection title="Preflight">
        <table className="dense-table">
          <thead>
            <tr>
              <th>Code</th>
              <th>Severity</th>
              <th>Message</th>
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
                <td>{item.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </RailSection>
    </>
  );
}

/**
 * Impact tab: contact estimate summary, assumptions, and unsupported modes.
 * @param result - Latest pipeline result.
 * @returns The impact panel content.
 */
function renderImpact(result: PipelineResult): JSX.Element {
  if (result.impact === null) {
    return <Empty>No impact estimate was requested.</Empty>;
  }
  const estimate = result.impact.result;
  if (estimate === null) {
    return <Empty>{result.impact.reason ?? 'No impact estimate was produced.'}</Empty>;
  }
  return (
    <>
      <RailSection title="Impact estimate">
        <table className="dense-table">
          <tbody>
            <tr>
              <th scope="row">Closing velocity</th>
              <td className="num">{formatNumber(estimate.closing_velocity_m_s)} m/s</td>
            </tr>
            <tr>
              <th scope="row">Impact energy</th>
              <td className="num">{formatEnergy(estimate.impact_energy_j)}</td>
            </tr>
            <tr>
              <th scope="row">Effective mass</th>
              <td className="num">{formatMass(estimate.effective_mass_kg)}</td>
            </tr>
            <tr>
              <th scope="row">Impulse</th>
              <td className="num">{formatNumber(estimate.impulse_n_s)} N·s</td>
            </tr>
            <tr>
              <th scope="row">Peak force</th>
              <td className="num">{formatForce(estimate.peak_force_n)}</td>
            </tr>
            <tr>
              <th scope="row">Peak acceleration</th>
              <td className="num">{formatAcceleration(estimate.peak_acceleration_m_s2)}</td>
            </tr>
            <tr>
              <th scope="row">Contact duration</th>
              <td className="num">{formatDuration(estimate.contact_duration_s)}</td>
            </tr>
            <tr>
              <th scope="row">Contact compression</th>
              <td className="num">{formatLength(estimate.contact_compression_m)}</td>
            </tr>
            <tr>
              <th scope="row">Validity</th>
              <td>
                <StatusBadge tone={validityTone(estimate.validity)}>{validityLabel(estimate.validity)}</StatusBadge>
              </td>
            </tr>
            <tr>
              <th scope="row">Qualification blocked</th>
              <td>{estimate.qualification_blocked ? 'yes' : 'no'}</td>
            </tr>
            <tr>
              <th scope="row">Safety factor</th>
              <td className="num">
                {typeof estimate.safety_factor === 'number' ? formatNumber(estimate.safety_factor) : estimate.safety_factor}
              </td>
            </tr>
          </tbody>
        </table>
      </RailSection>
      {estimate.flags.includes('CONTACT_PATCH_ASSUMPTION') && (
        <p className="results-rail__note muted">Energy-based quasi-static estimate; local contact stress not resolved.</p>
      )}
      <RailSection title="Assumptions">
        <StringList items={estimate.assumptions} />
      </RailSection>
      <RailSection title="Unsupported modes">
        <StringList items={estimate.unsupported_failure_modes} />
      </RailSection>
    </>
  );
}

/**
 * Qualification tab: gate-by-gate outcome and evidence disposition.
 * @param result - Latest pipeline result.
 * @returns The qualification panel content.
 */
function renderQualification(result: PipelineResult): JSX.Element {
  if (result.qualification === null) {
    return <Empty>No qualification assessment was performed.</Empty>;
  }
  const { qualification } = result;
  return (
    <>
      <RailSection title="Qualification">
        <table className="dense-table">
          <tbody>
            <tr>
              <th scope="row">Qualified</th>
              <td>
                <StatusBadge tone={qualification.qualified ? 'ok' : 'error'}>
                  {qualification.qualified ? 'qualified' : 'not qualified'}
                </StatusBadge>
              </td>
            </tr>
            <tr>
              <th scope="row">Evidence disposition</th>
              <td>
                <StatusBadge tone={dispositionTone(qualification.evidence_disposition)}>
                  {dispositionLabel(qualification.evidence_disposition)}
                </StatusBadge>
              </td>
            </tr>
            <tr>
              <th scope="row">Blocking keys</th>
              <td>
                <Chips items={qualification.blocking_keys} />
              </td>
            </tr>
            <tr>
              <th scope="row">Summary</th>
              <td>{qualification.summary}</td>
            </tr>
          </tbody>
        </table>
      </RailSection>
      <RailSection title="Gates">
        <table className="dense-table">
          <thead>
            <tr>
              <th>Key</th>
              <th>Gate</th>
              <th>Status</th>
              <th>Explanation</th>
            </tr>
          </thead>
          <tbody>
            {qualification.gates.map((gate) => (
              <tr key={gate.key}>
                <td>
                  <code>{gate.key}</code>
                </td>
                <td>{gate.label}</td>
                <td>
                  <StatusBadge tone={gateTone(gate)}>
                    {gateStatusLabel({ passed: gate.passed, evaluable: gate.evaluable, blocker: gate.blocker })}
                  </StatusBadge>
                </td>
                <td>{gate.explanation}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </RailSection>
    </>
  );
}

/** A row in the combined issues table. */
interface IssueRow {
  severity: string;
  code: string;
  phase: string;
  message: string;
  affectedIds: string[];
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

function renderIssues(result: PipelineResult): JSX.Element {
  const findings = result.validation?.findings ?? [];
  const issues = result.issues;
  const errors = result.errors;

  const rows: IssueRow[] = [
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

  return (
    <RailSection title="Issues">
      <IssuesTable rows={rows} />
    </RailSection>
  );
}
