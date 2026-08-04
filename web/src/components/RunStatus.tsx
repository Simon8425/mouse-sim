import { useProjectStore } from '../state/projectStore';
import { selectHasStaleResult, selectRunStatusLabel } from '../state/selectors';

export function RunStatus() {
  const { state } = useProjectStore();
  const label = selectRunStatusLabel(state);
  const hasStaleResult = selectHasStaleResult(state);
  const isRunning = state.runStatus === 'running';

  return (
    <div className="run-status" aria-live="polite" aria-atomic="true">
      <span className="run-status__label">Run</span>
      <span className="run-status__value">{label.text}</span>
      {hasStaleResult && !isRunning && state.lastResult !== null ? (
        <span className="stale-marker" title="A newer analysis run is pending">STALE RESULT</span>
      ) : null}
      {state.runStatus === 'error' ? (
        <span className="badge badge--error" role="status" title={state.runError ?? 'Run failed'}>
          {state.runError?.slice(0, 100) ?? 'Run failed'}
        </span>
      ) : null}
    </div>
  );
}
