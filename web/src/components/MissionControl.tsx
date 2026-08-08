import * as React from 'react';
import { useProjectStore } from '../state/projectStore';
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
import { useDetectedQuality } from '../scene/SceneViewport';
import type { QualityTier } from '../scene/materialPalette';

export interface MissionControlProps {
  onClose: () => void;
}

const QUALITY_OPTIONS: { tier: QualityTier; label: string }[] = [
  { tier: 'low', label: 'LOW' },
  { tier: 'medium', label: 'MEDIUM' },
  { tier: 'high', label: 'HIGH' },
  { tier: 'ultra', label: 'ULTRA' },
];

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

export function MissionControl({ onClose }: MissionControlProps): React.ReactElement {
  const { state, dispatch } = useProjectStore();
  const panelRef = React.useRef<HTMLDivElement | null>(null);

  const detectedTier = useDetectedQuality();
  const activeTier = state.qualityTier ?? detectedTier;

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
  const hasGeometry = state.project !== null || state.preview !== null;
  const isRunning = state.runStatus === 'running';

  return (
    <div className="mission-control">
      <div className="mission-control__backdrop" aria-hidden="true" onClick={onClose} />
      <div
        className="mission-control__panel"
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
      >
        <header className="mission-control__header">
          <div className="mission-control__title">
            <h2>Settings</h2>
          </div>
          <button
            type="button"
            className="btn btn--ghost"
            aria-label="Close settings panel"
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

          {/* CARD 2: MODEL QUALITY (RENDERING ONLY) */}
          <div className="mc-card">
            <h3 className="mc-card__heading">Model Quality</h3>
            <div className="mc-card__content">
              <div
                className="settings-quality"
                role="radiogroup"
                aria-label="Model quality"
              >
                {QUALITY_OPTIONS.map((option) => (
                  <button
                    key={option.tier}
                    type="button"
                    role="radio"
                    aria-checked={activeTier === option.tier}
                    className={`settings-quality__option${
                      activeTier === option.tier ? ' is-active' : ''
                    }`}
                    onClick={() =>
                      dispatch({ type: 'SET_QUALITY_TIER', tier: option.tier })
                    }
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <p className="mc-subtext muted">
                Affects rendering only (pixel ratio, shadows, anti-aliasing) —
                never physics accuracy.
              </p>
            </div>
            <div className="mc-group">
              <label className="mc-label" htmlFor="mc-default-material">
                Default Material
              </label>
              <select
                id="mc-default-material"
                className="settings-default-material"
                value={state.defaultMaterialKey}
                onChange={(e) =>
                  dispatch({ type: 'SET_DEFAULT_MATERIAL', key: e.target.value })
                }
              >
                {(state.materials && state.materials.length > 0
                  ? state.materials.map((m) => m.key)
                  : ['default']
                ).map((key) => (
                  <option key={key} value={key}>
                    {key}
                  </option>
                ))}
              </select>
              <p className="mc-subtext muted">
                Applied to every component without an explicit material. Never blocks a run.
              </p>
            </div>
          </div>

          {/* CARD 3: WORST-CASE POPULATION ANALYSIS */}
          <div className="mc-card">
            <h3 className="mc-card__heading">Worst-case population analysis</h3>
            <div className="mc-card__content">
              <div className="mc-actions-row">
                <button
                  type="button"
                  className="btn btn--primary btn--sm"
                  aria-label="Run worst-case population analysis"
                  onClick={() => dispatch({ type: 'RUN_POPULATION' })}
                  disabled={!hasGeometry || isRunning}
                  title={
                    isRunning
                      ? 'Analysis in progress'
                      : hasGeometry
                        ? 'Run 10,000-unit worst-case analysis'
                        : 'Upload a model first'
                  }
                >
                  {isRunning ? 'RUNNING — may take a few minutes' : 'RUN 10,000-UNIT WORST-CASE'}
                </button>
              </div>
              <p className="mc-subtext muted">
                Simulates 10,000 manufactured units with tolerance variation, usage from an esports
                profile, and per-component failure analysis. Predicts field failure rates and the
                weakest components. May take a few minutes.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
