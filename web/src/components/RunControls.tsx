/**
 * Run controls — the primary action bar above the viewport: pick a material,
 * pick a test, and run. The run includes the structural analysis (shell panel
 * sized from the loaded model under a 5 kPa flex load), so the results panel
 * reports stress, safety factor, and deformation alongside the drop.
 */
import * as React from 'react';
import { useProjectStore, type ObjectEntry } from '../state/projectStore';
import { worldBounds } from '../lib/geometryBounds';
import { selectObjectEntries } from '../state/selectors';
import {
  DROP_TESTS,
  type DropTestDefinition,
} from '../lib/studies';

/** Shell-flex load case: uniform 5 kPa pressure on the panel. */
export const SHELL_FLEX_LOAD_CASE = {
  kind: 'pressure',
  magnitude_pa: 5000,
  distribution: 'uniform',
  direction: [0, 0, -1],
} as const;

/** Union bounds extents (meters) across all loaded geometry entries. */
export function shellDims(entries: ObjectEntry[]): { a_m: number; b_m: number; t_m: number } | null {
  let minX = Infinity;
  let minY = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let maxZ = -Infinity;
  for (const entry of entries) {
    const bounds = worldBounds(entry.geometry);
    minX = Math.min(minX, bounds.min[0]);
    minY = Math.min(minY, bounds.min[1]);
    minZ = Math.min(minZ, bounds.min[2]);
    maxX = Math.max(maxX, bounds.max[0]);
    maxY = Math.max(maxY, bounds.max[1]);
    maxZ = Math.max(maxZ, bounds.max[2]);
  }
  if (![minX, minY, minZ, maxX, maxY, maxZ].every(Number.isFinite)) return null;
  const extents = [maxX - minX, maxY - minY, maxZ - minZ]
    .filter((value) => value > 0)
    .sort((a, b) => b - a);
  if (extents.length < 3) return null;
  return { a_m: extents[0], b_m: extents[1], t_m: extents[2] };
}

export interface RunControlsProps {
  onReplaceModel?: () => void;
  uploadOpen?: boolean;
}

export function RunControls({ onReplaceModel, uploadOpen }: RunControlsProps = {}): React.ReactElement {
  const { state, dispatch } = useProjectStore();
  const [testId, setTestId] = React.useState<DropTestDefinition['id']>('drop-test');

  const hasGeometry =
    selectObjectEntries(state).length > 0 ||
    state.project !== null ||
    state.preview !== null ||
    state.tempPreview !== null;
  const isRunning = state.runStatus === 'loading' || state.runStatus === 'running';

  // The leave-test button is coupled to an ACTIVE test: once the test has
  // been left (playbackDismissed) the button must disappear even though the
  // retained result stays visible.
  const testActive =
    state.draft?.drop_simulation != null ||
    (!state.playbackDismissed && state.lastResult?.drop_simulation != null);

  const openConfigCard = () => {
    if (testId === 'population-test') {
      dispatch({ type: 'RUN_POPULATION' });
    } else {
      dispatch({ type: 'SET_CONTROL_OPEN', open: true, mode: 'simulation' });
    }
  };

  const leaveTest = () => {
    dispatch({ type: 'LEAVE_TEST' });
  };

  if (isRunning) {
    return (
      <div className="run-controls run-controls--loading" role="status" aria-label="Loading test">
        <div className="run-controls__loading-indicator">
          <span className="run-controls__spinner" aria-hidden="true" />
          <span className="run-controls__loading-text">Loading test…</span>
        </div>
        <div className="run-controls__divider" aria-hidden="true" />
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          aria-label="Cancel test loading"
          onClick={() => dispatch({ type: 'CANCEL_RUN' })}
        >
          Cancel
        </button>
      </div>
    );
  }

  if (testActive) {
    // During an active test only the leave action and settings are offered here: the
    // playback card (PLAY/PAUSE/status) floats over the viewport bottom.
    return (
      <div className="run-controls" role="group" aria-label="Analysis run parameters">
        <button
          type="button"
          className="btn run-controls__leave-test"
          aria-label="Leave test and return to normal mode"
          onClick={leaveTest}
        >
          Leave test
        </button>
        <div className="run-controls__divider" aria-hidden="true" />
        <button
          type="button"
          className="btn run-controls__settings-btn"
          aria-label="Settings"
          title="Settings & Mission Control"
          onClick={() => dispatch({ type: 'SET_CONTROL_OPEN', open: true, mode: 'settings' })}
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
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
      </div>
    );
  }

  return (
    <div className="run-controls" role="group" aria-label="Analysis run parameters">
      {onReplaceModel ? (
        <>
          <button
            type="button"
            className="btn run-controls__replace"
            onClick={onReplaceModel}
            aria-expanded={uploadOpen}
            title="Replace the loaded model with another file"
          >
            Replace
          </button>
          <div className="run-controls__divider" aria-hidden="true" />
        </>
      ) : null}


      <label className="run-controls__field">
        <select
          value={testId}
          aria-label="Test type"
          onChange={(event) => setTestId(event.target.value as DropTestDefinition['id'])}
        >
          {DROP_TESTS.filter((definition) => definition.id !== 'population-test').map((definition) => (
            <option key={definition.id} value={definition.id}>
              {definition.title}
            </option>
          ))}
        </select>
      </label>

      <button
        type="button"
        className="btn btn--primary"
        aria-label="Configure and run test"
        onClick={openConfigCard}
        disabled={!hasGeometry || isRunning}
        title={
          isRunning
            ? 'A run is in progress'
            : hasGeometry
              ? 'Configure test parameters & run simulation'
              : 'Upload a model first'
        }
      >
        {isRunning ? 'Running…' : 'Run'}
      </button>

      <div className="run-controls__divider" aria-hidden="true" />

      <button
        type="button"
        className="btn run-controls__settings-btn"
        aria-label="Settings"
        title="Settings & Mission Control"
        onClick={() => dispatch({ type: 'SET_CONTROL_OPEN', open: true, mode: 'settings' })}
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
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </button>
    </div>
  );
}
