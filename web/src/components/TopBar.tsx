import { useProjectStore } from '../state/projectStore';
import { selectSourceLabel } from '../state/selectors';

export interface TopBarProps {
  onOpenNav: () => void;
  onOpenInspector: () => void;
  onOpenControl: () => void;
  onFit: () => void;
}

export function TopBar(props: TopBarProps) {
  const { state, dispatch } = useProjectStore();
  const sourceReadyLabel = selectSourceLabel(state);

  let sourceLabel: string;
  switch (state.sourceStatus) {
    case 'loading':
      sourceLabel = 'Loading…';
      break;
    case 'ready':
      sourceLabel = sourceReadyLabel;
      break;
    case 'error':
      sourceLabel = 'Source error';
      break;
    default:
      sourceLabel = '—';
      break;
  }

  return (
    <header className="top-bar">
      <div className="top-bar__identity">
        <div className="top-bar__project">
          <span className="top-bar__eyebrow">Project</span>
          <h1 className="top-bar__project-name">mouse_sim / {state.projectName || 'no project'}</h1>
        </div>
        <div className="top-bar__source">
          <span className="top-bar__source-label">Source</span>
          <span className="top-bar__source-value" title={sourceLabel}>
            {sourceLabel}
          </span>
        </div>
      </div>
      <div className="top-bar__actions">
        <button
          type="button"
          className={`btn btn--ghost${state.mode === 'exploration' ? ' is-active' : ''}`}
          onClick={() => {
            dispatch({ type: 'SET_MODE', mode: 'exploration' });
            dispatch({ type: 'SET_TAB', tab: 'overview' });
          }}
        >
          EXPLORATION
        </button>
        <button
          type="button"
          className={`btn btn--primary${state.mode === 'qualification' ? ' is-active' : ''}`}
          onClick={() => {
            dispatch({ type: 'SET_MODE', mode: 'qualification' });
            dispatch({ type: 'SET_TAB', tab: 'qualification' });
            dispatch({ type: 'RUN_STUDY' });
          }}
        >
          RUN QUALIFICATION
        </button>
        <button
          type="button"
          className="btn btn--ghost top-bar__nav-toggle"
          aria-label="Toggle model navigator"
          aria-expanded={state.navOpen}
          onClick={props.onOpenNav}
        >
          MODEL
        </button>
        <button type="button" className="btn btn--ghost" onClick={props.onFit} aria-label="Fit view">
          FIT
        </button>
        <button
          type="button"
          className="btn btn--ghost top-bar__control-toggle"
          aria-label="Control panel"
          aria-expanded={state.controlOpen}
          onClick={props.onOpenControl}
        >
          SETTINGS
        </button>
        <button
          type="button"
          className="btn btn--ghost top-bar__inspector-toggle"
          aria-label="Toggle inspector"
          aria-expanded={state.inspectorOpen}
          onClick={props.onOpenInspector}
        >
          INFO
        </button>
      </div>
    </header>
  );
}
