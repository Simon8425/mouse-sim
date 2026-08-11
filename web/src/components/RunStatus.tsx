import { useProjectStore } from '../state/projectStore';
import { selectRunStatusLabel } from '../state/selectors';

export function RunStatus() {
  const { state, dispatch } = useProjectStore();
  const label = selectRunStatusLabel(state);
  const isBusy = state.runStatus === 'loading' || state.runStatus === 'running';
  const modeText =
    state.mode === 'qualification'
      ? 'Qualification'
      : state.mode === 'validation'
        ? 'Validation'
        : 'Analysis';

  return (
    <div className="top-bar__status run-status" aria-live="polite" aria-atomic="true">
      <span className="top-bar__status-label">{modeText}</span>
      <span className="top-bar__status-value">{label.text}</span>
      {isBusy ? (
        <>
          <div
            className="run-status__progress"
            role="progressbar"
            aria-label="Run progress"
            aria-valuetext={label.text}
          >
            <span aria-hidden="true" />
          </div>
          <button
            type="button"
            className="btn btn--ghost btn--sm run-status__cancel"
            aria-label="Cancel running analysis"
            onClick={() => dispatch({ type: 'CANCEL_RUN' })}
          >
            Cancel
          </button>
        </>
      ) : null}
      {state.runError ? (
        <span className="run-status__error" role="status">
          {state.runError}
        </span>
      ) : null}
    </div>
  );
}
