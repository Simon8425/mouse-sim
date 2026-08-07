import * as React from 'react';
import { useProjectStore } from '../state/projectStore';
import type { CameraPreset } from '../scene/camera';
import type { RenderStats } from '../scene/sceneRuntime';
import type { SceneViewportHandle } from '../scene/SceneViewport';

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

  const presetButtons: Array<{ label: string; preset: CameraPreset | 'fit' }> = [
    { label: 'Fit', preset: 'fit' },
    { label: 'Iso', preset: 'iso' },
    { label: 'Top', preset: 'top' },
    { label: 'Front', preset: 'front' },
    { label: 'Right', preset: 'right' },
  ];

  return (
    <div className="viewport-toolbar" role="toolbar" aria-label="Viewport controls">
      <span className="viewport-toolbar__heading">Viewport HUD</span>
      <div className="viewport-toolbar__group" role="group" aria-label="Camera preset">
        <span className="viewport-toolbar__group-label">Camera</span>
        {presetButtons.map(({ label, preset }) => (
          <button
            key={preset}
            type="button"
            className="btn"
            aria-label={preset === 'fit' ? 'Fit view' : `View ${label}`}
            onClick={() => {
              if (preset === 'fit') {
                viewport.current?.fit();
              } else {
                viewport.current?.preset(preset);
              }
            }}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="viewport-toolbar__group" role="group" aria-label="Exploded view">
        <button
          type="button"
          className="btn"
          aria-pressed={state.explode > 0}
          onClick={() =>
            dispatch({ type: 'SET_EXPLODE', factor: state.explode > 0 ? 0 : 1 })
          }
        >
          Exploded
        </button>
        <span
          className="display-only-label"
          title="Display-only offsets; never included in analysis"
        >
          Display only
        </span>
      </div>

      <details className="viewport-toolbar__legend">
        <summary>Overlay legend</summary>
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
      </details>

      <span className="quality-tier">
        Quality: {state.qualityTier ?? 'auto'}
      </span>

      <details className="viewport-toolbar__stats">
        <summary>Telemetry</summary>
        <dl>
          <dt>Draw calls</dt>
          <dd>{stats?.drawCalls ?? '—'}</dd>
          <dt>Triangles</dt>
          <dd>{stats?.triangles ?? '—'}</dd>
          <dt>Geometries</dt>
          <dd>{stats?.geometries ?? '—'}</dd>
          <dt>Textures</dt>
          <dd>{stats?.textures ?? '—'}</dd>
          <dt>Render tier</dt>
          <dd>{stats?.tier ?? state.qualityTier ?? 'auto'}</dd>
        </dl>
      </details>
    </div>
  );
}
