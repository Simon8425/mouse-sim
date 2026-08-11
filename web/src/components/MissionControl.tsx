import * as React from 'react';
import { useProjectStore } from '../state/projectStore';

export interface MissionControlProps {
  onClose: () => void;
}

export function MissionControl({ onClose }: MissionControlProps): React.ReactElement {
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

  const hasGeometry = state.project !== null || state.preview !== null;
  const isRunning = state.runStatus === 'loading' || state.runStatus === 'running';

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
            Close
          </button>
        </header>

        <div className="mission-control__body mission-control__body--two-cards">
          {/* CARD 1: MATERIALS (rendering quality is pinned to the low tier) */}
          <div className="mc-card">
            <h3 className="mc-card__heading">Materials</h3>
            <div className="mc-card__content">
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
          </div>

          {/* CARD 3: POPULATION ANALYSIS */}
          <div className="mc-card">
            <h3 className="mc-card__heading">Population analysis</h3>
            <div className="mc-card__content">
              <div className="mc-actions-row">
                <button
                  type="button"
                  className="btn btn--primary btn--sm"
                  aria-label="Run Monte Carlo population (10k units)"
                  onClick={() => dispatch({ type: 'RUN_POPULATION' })}
                  disabled={!hasGeometry || isRunning}
                  title={
                    isRunning
                      ? 'A run is in progress'
                      : hasGeometry
                        ? 'Run 10,000-unit Monte Carlo population analysis'
                        : 'Upload a model first'
                  }
                >
                  {isRunning ? 'Running…' : 'Run population (10k units)'}
                </button>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  aria-label="Run deterministic worst-case"
                  onClick={() => dispatch({ type: 'RUN_POPULATION', worst_case: true })}
                  disabled={!hasGeometry || isRunning}
                  title={
                    isRunning
                      ? 'A run is in progress'
                      : hasGeometry
                        ? 'Run deterministic worst-case analysis'
                        : 'Upload a model first'
                  }
                >
                  {isRunning ? 'Running…' : 'Run worst-case'}
                </button>
              </div>
              <p className="mc-subtext muted">
                Monte Carlo simulates 10,000 manufactured units with tolerance variation, usage
                from an esports profile, and per-component failure analysis. Deterministic
                worst-case runs a single unit at the tolerance corner that minimizes safety
                factor. May take a few minutes.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
