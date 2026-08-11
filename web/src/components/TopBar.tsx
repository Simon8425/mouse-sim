import * as React from 'react';
import { useProjectStore } from '../state/projectStore';
import { selectSourceLabel } from '../state/selectors';
import { RunStatus } from './RunStatus';

export interface TopBarProps {
  onOpenNav: () => void;
  onOpenInspector: () => void;
  onOpenControl: () => void;
}

export function TopBar(props: TopBarProps): React.ReactElement {
  const { state } = useProjectStore();
  const sourceReadyLabel = selectSourceLabel(state);

  let sourceLabel: string;
  if (state.sourceStatus === 'loading' || state.previewStatus === 'working') {
    sourceLabel = 'Loading…';
  } else if (state.sourceStatus === 'error' || state.previewStatus === 'error') {
    sourceLabel = state.previewError || 'Source error';
  } else if (state.preview !== null || state.sourceStatus === 'ready') {
    sourceLabel = sourceReadyLabel;
  } else {
    sourceLabel = 'None';
  }

  return (
    <header className="top-bar">
      <div className="top-bar__brand">
        <svg className="top-bar__logo-icon" width="14" height="14" viewBox="0 0 16 16" fill="none">
          <rect width="16" height="16" fill="var(--text-primary)" />
          <path d="M3 13L13 3" stroke="var(--on-primary)" strokeWidth="2" />
        </svg>
        <h1 className="top-bar__app-name">Mouse Sim</h1>
      </div>

      <div className="top-bar__meta-item">
        <span className="top-bar__meta-label">Model</span>
        <span className="top-bar__meta-value" title={sourceLabel}>
          {sourceLabel}
        </span>
      </div>

      <div className="top-bar__spacer" />

      <RunStatus />

      <div className="top-bar__actions">
        <button
          type="button"
          className={`btn${state.navOpen ? ' is-active' : ''}`}
          aria-label="Toggle model navigator"
          aria-expanded={state.navOpen}
          onClick={props.onOpenNav}
        >
          Model
        </button>
        <button
          type="button"
          className={`btn${state.controlOpen ? ' is-active' : ''}`}
          aria-label="Control panel"
          aria-expanded={state.controlOpen}
          onClick={props.onOpenControl}
        >
          Settings
        </button>
        <button
          type="button"
          className={`btn${state.inspectorOpen ? ' is-active' : ''}`}
          aria-label="Toggle inspector"
          aria-expanded={state.inspectorOpen}
          onClick={props.onOpenInspector}
        >
          Info
        </button>
      </div>
    </header>
  );
}
