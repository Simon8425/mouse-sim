import * as React from 'react';
import { useProjectStore } from '../state/projectStore';
import type { RenderStats } from '../scene/sceneRuntime';
import type { SceneViewportHandle } from '../scene/SceneViewport';
import type { RenderMode } from '../api/contracts';
import { PhysicsLogDebugger } from './PhysicsLogDebugger';

export interface ViewportToolbarProps {
  viewport: React.RefObject<SceneViewportHandle | null>;
  stats: RenderStats | null;
}

/**
 * Toolbar overlaying the 3D viewport: camera presets, exploded view toggle,
 * overlay legend, quality tier and renderer diagnostics.
 */
export function ViewportToolbar({ viewport, stats }: ViewportToolbarProps) {
  const { state, dispatch } = useProjectStore();
  const [openDropdown, setOpenDropdown] = React.useState<'legend' | 'stats' | null>(null);

  // The FEA preview is a NORMAL-MODE feature: during an active test only
  // Leave Test and the playback card are shown. Once the test is left the
  // results stay visible (LEAVE_TEST resets renderMode so the model returns
  // to normal materials) and this switch lets the user enable the heatmap /
  // yield shader for inspection.
  const testModeActive =
    !state.playbackDismissed &&
    (state.lastResult?.drop_simulation != null ||
      state.lastResult?.population != null ||
      state.draft?.population != null) &&
    !state.stale;
  const feaAvailable = state.lastResult?.fea?.computed === true && !state.stale;
  const showFeaSwitch = feaAvailable && !testModeActive;

  const renderModeButtons: Array<{ mode: RenderMode; label: string }> = [
    { mode: 'fea', label: 'FEA Stress Heatmap' },
    { mode: 'yield', label: 'Yield Shader' },
  ];

  return (
    <div className="viewport-toolbar" role="toolbar" aria-label="Viewport controls">
      {!testModeActive ? (
        <div className="viewport-toolbar__group" role="group" aria-label="Exploded view">
          <button
            type="button"
            className="btn"
            aria-pressed={state.explode > 0}
            title="Display-only offsets; never included in analysis"
            onClick={() =>
              dispatch({ type: 'SET_EXPLODE', factor: state.explode > 0 ? 0 : 1 })
            }
          >
            Exploded
          </button>
        </div>
      ) : null}

      {showFeaSwitch ? (
        <>
          {!testModeActive ? <div className="viewport-toolbar__divider" aria-hidden="true" /> : null}
          <div className="viewport-toolbar__group" role="group" aria-label="Render mode">
            {renderModeButtons.map(({ mode, label }) => {
              const active = state.renderMode === mode;
              return (
                <button
                  key={mode}
                  type="button"
                  className="btn"
                  aria-pressed={active}
                  title={
                    active
                      ? 'Return to default material'
                      : `Render the model in ${label} mode`
                  }
                  onClick={() =>
                    dispatch({ type: 'SET_RENDER_MODE', mode: active ? 'default' : mode })
                  }
                >
                  {label}
                </button>
              );
            })}
          </div>
        </>
      ) : null}

      <div className="viewport-toolbar__divider" aria-hidden="true" />

      <div className={`viewport-toolbar__log${state.debuggerOpen ? ' is-open' : ''}`} role="group" aria-label="Telemetry debugger">
        <button
          type="button"
          className="btn"
          aria-pressed={state.debuggerOpen}
          title="Open the physics & telemetry log"
          onClick={() => dispatch({ type: 'SET_DEBUGGER_OPEN', open: !state.debuggerOpen })}
        >
          Log
        </button>
        {state.debuggerOpen ? (
          <PhysicsLogDebugger viewportRef={viewport} />
        ) : null}
      </div>

      <div className="viewport-toolbar__divider" aria-hidden="true" />

      <div className="viewport-toolbar__group" role="group" aria-label="Overlay and diagnostics">
        <div className={`viewport-toolbar__legend${openDropdown === 'legend' ? ' is-open' : ''}`}>
          <button
            type="button"
            className="btn viewport-toolbar__disclosure"
            aria-expanded={openDropdown === 'legend'}
            title="Overlay legend"
            onClick={() => setOpenDropdown(openDropdown === 'legend' ? null : 'legend')}
          >
            Legend
          </button>
          <ul>
            <li>
              <span className="legend-swatch legend-swatch--selection" aria-hidden="true" />
              Selection / load vector
            </li>
            <li>
              <span className="legend-swatch legend-swatch--warn" aria-hidden="true" />
              Warnings / fixtures / filtered stress badge
            </li>
            <li>
              <span className="legend-swatch legend-swatch--blocker" aria-hidden="true" />
              Blockers
            </li>
            <li>
              <span className="legend-swatch legend-swatch--plane" aria-hidden="true" />
              Contact plane — assumption (display aid)
            </li>
            <li>
              <span className="legend-swatch legend-swatch--neutral" aria-hidden="true" />
              Severity marker
            </li>
          </ul>
        </div>

        <div className={`viewport-toolbar__stats${openDropdown === 'stats' ? ' is-open' : ''}`}>
          <button
            type="button"
            className="btn viewport-toolbar__disclosure"
            aria-expanded={openDropdown === 'stats'}
            onClick={() => setOpenDropdown(openDropdown === 'stats' ? null : 'stats')}
          >
            Telemetry
          </button>
          <dl>
            <dt>Draw calls</dt>
            <dd>{stats?.drawCalls ?? '—'}</dd>
            <dt>Triangles</dt>
            <dd>{stats?.triangles ?? '—'}</dd>
            <dt>Geometries</dt>
            <dd>{stats?.geometries ?? '—'}</dd>
            <dt>Textures</dt>
            <dd>{stats?.textures ?? '—'}</dd>
          </dl>
        </div>
      </div>
    </div>
  );
}
