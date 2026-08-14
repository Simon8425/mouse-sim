import { useProjectStore } from '../state/projectStore';

export function RunStatus() {
  const { state } = useProjectStore();

  if (!state.runError) {
    return null;
  }

  return (
    <div className="top-bar__status run-status" aria-live="polite" aria-atomic="true">
      <span className="run-status__error" role="status">
        {state.runError}
      </span>
    </div>
  );
}
