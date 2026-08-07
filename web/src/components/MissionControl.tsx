import * as React from 'react';
import { useProjectStore, type ProjectAction } from '../state/projectStore';
import {
  DROP_ORIENTATIONS,
  DROP_SURFACES,
  DROP_TESTS,
  clampDropConfig,
  persistedConfigForTest,
  persistConfigForTest,
  type DropTestConfigState,
} from '../lib/studies';
import type { DropOrientation, DropSurface } from '../api/contracts';
import {
  selectEvidenceCount,
  selectRunStatusLabel,
  selectSourceLabel,
  selectSolverModelBadge,
  selectUnsupportedModes,
} from '../state/selectors';
import {
  dispositionLabel,
  dispositionTone,
  modeLabel,
  validityConfidenceLabel,
  validityLabel,
} from '../lib/status';
import type { SeverityTone } from '../lib/status';
import { formatForce, formatLength, formatMass } from '../lib/units';
import { formatVector3 } from '../lib/format';
import { StatusBadge } from './StatusBadge';

export interface MissionControlProps {
  onClose: () => void;
  onUpload: () => void;
}

function validityTone(validity: string): SeverityTone {
  switch (validity.toLowerCase()) {
    case 'valid':
      return 'ok';
    case 'invalid':
    case 'failed':
      return 'error';
    case 'approximate':
    case 'inconclusive':
      return 'warn';
    default:
      return 'neutral';
  }
}

function sourceTone(status: string): SeverityTone {
  switch (status) {
    case 'loading':
      return 'info';
    case 'ready':
      return 'ok';
    case 'error':
      return 'error';
    default:
      return 'neutral';
  }
}

function sourceLabel(status: string): string {
  switch (status) {
    case 'loading':
      return 'Loading…';
    case 'ready':
      return 'Ready';
    case 'error':
      return 'Error';
    default:
      return 'Idle';
  }
}

function Chips({ items }: { items: string[] }): React.ReactElement {
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

export function MissionControl({ onClose, onUpload }: MissionControlProps): React.ReactElement {
  const { state, dispatch } = useProjectStore();
  const panelRef = React.useRef<HTMLDivElement | null>(null);

  React.useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const health = state.health;
  const result = state.lastResult;
  const runStatus = selectRunStatusLabel(state);
  const unsupported = selectUnsupportedModes(state);
  const disposition = result?.qualification?.evidence_disposition ?? null;
  const modelBadge = selectSolverModelBadge(state);
  const evidence = selectEvidenceCount(state);
  const sourceReadyLabel = selectSourceLabel(state);

  return (
    <div className="mission-control">
      <div className="mission-control__backdrop" aria-hidden="true" onClick={onClose} />
      <div
        className="mission-control__panel"
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Mission control"
      >
        <header className="mission-control__header">
          <div className="mission-control__title">
            <h2>Mission Control</h2>
          </div>
          <button
            type="button"
            className="btn btn--ghost"
            aria-label="Close control panel"
            onClick={onClose}
          >
            CLOSE
          </button>
        </header>

        <div className="mission-control__body mission-control__body--two-cards">
          {/* CARD 1: ENGINE HEALTH & WORKSPACE STATUS */}
          <div className="mc-card">
            <h3 className="mc-card__heading">Engine health</h3>
            <div className="mc-card__content">
              {health ? (
                <div className="mc-group">
                  <div className="mc-subtext">
                    v{health.engine_version} · api {health.api_version} · cache <span>{health.cache_active ? 'on' : 'off'}</span>
                  </div>
                  <div className="mc-row" style={{ marginTop: '4px' }}>
                    <Chips items={health.supported_formats} />
                    <Chips items={health.solver_capabilities} />
                  </div>
                </div>
              ) : (
                <div className="mc-subtext">{state.healthError ?? 'Engine status unavailable.'}</div>
              )}

              <div className="mc-group">
                <span className="mc-label">Workspace Mode & Source</span>
                <div className="mc-row">
                  <StatusBadge tone="neutral">{modeLabel(state.mode)}</StatusBadge>
                  <StatusBadge tone={sourceTone(state.sourceStatus)}>
                    {sourceLabel(state.sourceStatus)}
                  </StatusBadge>
                  <span className="mc-subtext">{sourceReadyLabel}</span>
                  <span className="mc-subtext">{runStatus.text}</span>
                </div>
              </div>

              {result ? (
                <div className="mc-group">
                  <span className="mc-label">Fidelity & Results</span>
                  <div className="mc-row">
                    <StatusBadge tone={validityTone(result.validity.state)}>
                      {validityLabel(result.validity.state)}
                    </StatusBadge>
                    <span className="mc-subtext">
                      {validityConfidenceLabel(result.validity.confidence)} confidence
                    </span>
                    {disposition ? (
                      <StatusBadge tone={dispositionTone(disposition)}>
                        {dispositionLabel(disposition)}
                      </StatusBadge>
                    ) : null}
                  </div>
                  {modelBadge ? (
                    <div className="mc-subtext" style={{ marginTop: '2px' }}>
                      Model: <code>{modelBadge}</code>
                    </div>
                  ) : null}
                  {unsupported.length > 0 ? (
                    <div className="mc-row" style={{ marginTop: '4px' }}>
                      <span className="mc-label" style={{ fontSize: '9px' }}>Not simulated:</span>
                      <Chips items={unsupported} />
                    </div>
                  ) : null}
                  <div className="mc-row" style={{ marginTop: '6px', flexWrap: 'wrap', gap: '10px' }}>
                    {result.mass ? (
                      <span className="mc-subtext">
                        Mass: <span>{formatMass(result.mass.mass_kg)}</span> (CoM {formatVector3(result.mass.center_of_mass_m)})
                      </span>
                    ) : null}
                    {result.structural?.response ? (
                      <span className="mc-subtext">
                        Disp: <span>{formatLength(result.structural.response.max_displacement_m)}</span>
                      </span>
                    ) : null}
                    {result.impact?.result ? (
                      <span className="mc-subtext">
                        Peak: <span>{formatForce(result.impact.result.peak_force_n)}</span>
                      </span>
                    ) : null}
                    <span className="mc-subtext">
                      Evidence: <span>{evidence}</span> {evidence === 1 ? 'gate' : 'gates'} evaluated
                    </span>
                  </div>
                </div>
              ) : null}
            </div>
          </div>

          {/* CARD 2: DROP SIMULATION TESTS */}
          <div className="mc-card">
            <div className="mc-card__header-row">
              <h3 className="mc-card__heading">Durability Tests</h3>
              <span className="mc-subtext">3D rigid-body drop simulation</span>
            </div>
            <div className="mc-card__content">
              <div className="mc-tests-grid">
                {DROP_TESTS.map((test) => (
                  <DropTestCard key={test.id} test={test} dispatch={dispatch} onClose={onClose} />
                ))}
              </div>

              <div className="mc-actions-row">
                <button type="button" className="btn btn--sm" onClick={onUpload}>
                  Upload geometry
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

interface DropTestCardProps {
  test: (typeof DROP_TESTS)[number];
  dispatch: React.Dispatch<ProjectAction>;
  onClose: () => void;
}

function DropTestCard({ test, dispatch, onClose }: DropTestCardProps): React.ReactElement {
  const { state } = useProjectStore();
  const [config, setConfig] = React.useState<DropTestConfigState>(() =>
    persistedConfigForTest(test),
  );
  const hasGeometry = state.project !== null || state.preview !== null;

  const update = (patch: Partial<DropTestConfigState>) => {
    const next = clampDropConfig({ ...config, ...patch });
    setConfig(next);
    persistConfigForTest(test, next);
  };

  const run = () => {
    const finalConfig = clampDropConfig(config);
    dispatch({
      type: 'RUN_DROP_TEST',
      test: test.test,
      config: {
        height_m: finalConfig.height_m,
        surface: finalConfig.surface,
        drop_count: finalConfig.drop_count,
        orientation: finalConfig.orientation,
        spin_rps: finalConfig.spin_rps,
        mass_kg: finalConfig.mass_kg,
      },
    });
    // Close the panel so the 3D drop animation is visible immediately.
    onClose();
  };

  return (
    <div className="mc-test-item">
      <div className="mc-test-item__header">
        <span className="mc-test-item__title">{test.title}</span>
        <span className="mc-test-item__badge">{test.test.toUpperCase()}</span>
      </div>
      <p className="mc-test-item__description">{test.description}</p>
      <div className="mc-test-item__controls">
        <label className="mc-field">
          <span>Drop height (m)</span>
          <input
            type="number"
            min={0.02}
            max={3}
            step={0.05}
            value={config.height_m}
            aria-label={`${test.title} height`}
            onChange={(e) => update({ height_m: Number(e.target.value) })}
          />
        </label>
        <label className="mc-field">
          <span>Drops</span>
          <input
            type="number"
            min={1}
            max={20}
            step={1}
            value={config.drop_count}
            aria-label={`${test.title} drop count`}
            onChange={(e) => update({ drop_count: Number(e.target.value) })}
          />
        </label>
        <label className="mc-field">
          <span>Surface</span>
          <select
            value={config.surface}
            aria-label={`${test.title} surface`}
            onChange={(e) => update({ surface: e.target.value as DropSurface })}
          >
            {DROP_SURFACES.map((surface) => (
              <option key={surface.value} value={surface.value}>
                {surface.label}
              </option>
            ))}
          </select>
        </label>
        <label className="mc-field">
          <span>Orientation</span>
          <select
            value={config.orientation}
            aria-label={`${test.title} orientation`}
            onChange={(e) => update({ orientation: e.target.value as DropOrientation })}
          >
            {DROP_ORIENTATIONS.map((orientation) => (
              <option key={orientation.value} value={orientation.value}>
                {orientation.label}
              </option>
            ))}
          </select>
        </label>
        {test.test === 'tumble' ? (
          <label className="mc-field">
            <span>Spin (rev/s)</span>
            <input
              type="number"
              min={0}
              max={20}
              step={0.5}
              value={config.spin_rps}
              aria-label={`${test.title} spin`}
              onChange={(e) => update({ spin_rps: Number(e.target.value) })}
            />
          </label>
        ) : null}
        <label className="mc-field">
          <span>Mass (kg, optional)</span>
          <input
            type="number"
            min={0.01}
            max={10}
            step={0.01}
            placeholder="auto"
            value={config.mass_kg ?? ''}
            aria-label={`${test.title} mass`}
            onChange={(e) =>
              update({ mass_kg: e.target.value === '' ? null : Number(e.target.value) })
            }
          />
        </label>
      </div>
      <button
        type="button"
        className="btn btn--primary btn--sm"
        onClick={run}
        disabled={!hasGeometry}
        title={hasGeometry ? `Run ${test.title}` : 'Upload a model first'}
      >
        Run {test.title}
      </button>
    </div>
  );
}
