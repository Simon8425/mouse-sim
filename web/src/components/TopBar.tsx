import type { WebHealth } from '../api/contracts';
import { useProjectStore } from '../state/projectStore';
import { selectSourceLabel } from '../state/selectors';
import { RunStatus } from './RunStatus';
import { StatusBadge } from './StatusBadge';

/**
 * Props for the {@link TopBar} component.
 */
export interface TopBarProps {
  /** Opens the model navigator drawer. */
  onOpenNav: () => void;
  /** Opens the inspector drawer. */
  onOpenInspector: () => void;
  /** Fits the graph viewport to its contents. */
  onFit: () => void;
}

/**
 * Builds the health tooltip/label text from the engine health payload.
 */
function healthTexts(health: WebHealth): { title: string; label: string } {
  const cache = health.cache_active ? 'on' : 'off';
  const title = `engine ${health.engine_version} · api ${health.api_version} · cache ${cache} · formats ${health.supported_formats.join(', ')}`;
  const label = `engine ${health.engine_version} · api ${health.api_version} · cache ${cache}`;
  return { title, label };
}

/**
 * Top application bar: project identity, source status, mode switch,
 * run status, and view actions.
 */
export function TopBar(props: TopBarProps) {
  const { state, dispatch } = useProjectStore();
  const mode = state.mode;
  const sourceReadyLabel = selectSourceLabel(state);

  let sourceTone: 'info' | 'ok' | 'error' | 'neutral';
  let sourceLabel: string;
  switch (state.sourceStatus) {
    case 'loading':
      sourceTone = 'info';
      sourceLabel = 'Loading…';
      break;
    case 'ready':
      sourceTone = 'ok';
      sourceLabel = sourceReadyLabel;
      break;
    case 'error':
      sourceTone = 'error';
      sourceLabel = 'Source error';
      break;
    default:
      sourceTone = 'neutral';
      sourceLabel = '—';
      break;
  }

  const health =
    state.health !== null ? healthTexts(state.health) : null;
  const healthTitle = health?.title ?? state.healthError ?? 'engine status unavailable';
  const healthLabel = health?.label ?? state.healthError ?? 'engine status unavailable';

  return (
    <header className="top-bar">
      <div className="top-bar__group">
        <button
          type="button"
          className="btn btn--ghost top-bar__nav-toggle"
          aria-label="Toggle model navigator"
          aria-expanded={state.navOpen}
          onClick={props.onOpenNav}
        >
          ☰
        </button>
        <h1 className="top-bar__title">mouse_sim — {state.projectName || 'no project'}</h1>
        <StatusBadge tone={sourceTone} title={sourceLabel}>
          {sourceLabel}
        </StatusBadge>
        <span className="health-dot" title={healthTitle} aria-label={healthLabel} />
      </div>
      <div className="top-bar__group">
        <div className="mode-switch" role="group" aria-label="Analysis mode">
          <button
            type="button"
            className={
              mode === 'exploration'
                ? 'mode-switch__option mode-switch__option--active'
                : 'mode-switch__option'
            }
            aria-pressed={mode === 'exploration'}
            onClick={() => {
              dispatch({ type: 'SET_MODE', mode: 'exploration' });
              dispatch({ type: 'SET_TAB', tab: 'overview' });
            }}
          >
            Exploration
          </button>
          <button
            type="button"
            className={
              mode === 'qualification'
                ? 'mode-switch__option mode-switch__option--active'
                : 'mode-switch__option'
            }
            aria-pressed={mode === 'qualification'}
            onClick={() => {
              dispatch({ type: 'SET_MODE', mode: 'qualification' });
              dispatch({ type: 'SET_TAB', tab: 'qualification' });
            }}
          >
            Qualification
          </button>
        </div>
        <RunStatus />
        <button type="button" className="btn btn--ghost" onClick={props.onFit} aria-label="Fit view">
          Fit
        </button>
        <button
          type="button"
          className="btn btn--ghost"
          aria-pressed={state.theme === 'dark'}
          onClick={() =>
            dispatch({ type: 'SET_THEME', theme: state.theme === 'dark' ? 'light' : 'dark' })
          }
          aria-label="Toggle theme"
        >
          {state.theme === 'dark' ? 'Light' : 'Dark'}
        </button>
        <button
          type="button"
          className="btn btn--ghost top-bar__inspector-toggle"
          aria-label="Toggle inspector"
          aria-expanded={state.inspectorOpen}
          onClick={props.onOpenInspector}
        >
          ⓘ
        </button>
      </div>
    </header>
  );
}
