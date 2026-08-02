import { useProjectStore } from '../state/projectStore';
import { selectHasStaleResult, selectRunStatusLabel } from '../state/selectors';

export function RunStatus() {
  const { state } = useProjectStore();
  const label = selectRunStatusLabel(state);
  const hasStaleResult = selectHasStaleResult(state);
  return (
    <div className="run-status" aria-live="polite" aria-atomic="true">
      <span className="status-live">{label.text}</span>
      {hasStaleResult && state.lastResult !== null ? (
        <span className="stale-marker" title="A newer analysis run is pending">STALE RESULT</span>
      ) : null}
      {state.runStatus === 'error' ? (
        <span className="badge badge--error" role="status">{state.runError?.slice(0, 160) ?? 'Run failed'}</span>
      ) : null}
    </div>
  );
}
