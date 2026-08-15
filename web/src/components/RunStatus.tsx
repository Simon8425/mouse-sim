import { useProjectStore } from '../state/projectStore';

export function RunStatus() {
  const { state } = useProjectStore();

  const isRunning = state.runStatus === 'loading' || state.runStatus === 'running';
  const isPop = state.draft?.population !== undefined;

  if (state.runError) {
    return (
      <div className="top-bar__status run-status" aria-live="polite" aria-atomic="true">
        <span className="run-status__error" role="status">
          {state.runError}
        </span>
      </div>
    );
  }

  if (isRunning) {
    return (
      <div className="top-bar__status run-status is-running" aria-live="polite" aria-atomic="true">
        <span className="run-status__spinner" aria-hidden="true" />
        <span className="run-status__text" role="status">
          {isPop ? 'Simulating 10,000 units…' : 'Running analysis…'}
        </span>
      </div>
    );
  }

  return null;
}
