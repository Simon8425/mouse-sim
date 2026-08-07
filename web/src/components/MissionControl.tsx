import * as React from 'react';
import { useProjectStore } from '../state/projectStore';
import { STUDY_PRESETS, type StudyPreset } from '../lib/studies';
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

function formatStudyMethod(method: string): string {
  return method
    .replace('impact · energy_quasi_static_v1', 'Impact Screening (Drop Contact)')
    .replace('load_case · shell_navier_v1', 'Structural Flex (Uniform Pressure)')
    .replace('impact · orientation', 'Drop Test Suite (Multi-Axis)');
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

  const handleStudy = (preset: StudyPreset) => {
    dispatch({ type: 'UPDATE_DRAFT', patch: preset.patch });
    onClose();
  };

  const handleRunStudy = () => {
    dispatch({ type: 'RUN_STUDY' });
  };

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

          {/* CARD 2: STUDY LAUNCHER & QUICK ACTIONS */}
          <div className="mc-card">
            <div className="mc-card__header-row">
              <h3 className="mc-card__heading">Preset Studies & Actions</h3>
              <button type="button" className="btn btn--primary btn--sm" onClick={handleRunStudy}>
                Run study
              </button>
            </div>
            <div className="mc-card__content">
              <div className="mc-studies-grid">
                {STUDY_PRESETS.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    className="mc-study-item"
                    aria-label={`${preset.title} study`}
                    onClick={() => handleStudy(preset)}
                    >
                      <span className="mc-study-item__title">{preset.title}</span>
                      <span className="mc-study-item__method">{formatStudyMethod(preset.method)}</span>
                      <span className="mc-study-item__description">{preset.description}</span>
                    </button>
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
