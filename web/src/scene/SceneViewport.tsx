import * as React from 'react';
import {
  createSceneRuntime,
  detectQualityTier,
  type SceneRuntime,
  type RenderStats,
} from './sceneRuntime';
import type { CameraPreset } from './camera';
import type { QualityTier } from './materialPalette';
import type { OverlaySpec } from './overlays';
import type { ObjectSceneEntry } from './geometryFactory';
import type { DropSimulationResult } from '../api/contracts';

export interface SceneViewportHandle {
  fit: () => void;
  preset: (name: CameraPreset) => void;
  setDropPlayback: (playing: boolean) => void;
}

export interface SceneViewportProps {
  entries: ObjectSceneEntry[];
  visibility: Record<string, boolean>;
  selectedId: string | null;
  explode: number;
  theme: 'light' | 'dark';
  quality: QualityTier;
  overlays: OverlaySpec | null;
  dropSimulation?: DropSimulationResult | null;
  onPick: (id: string | null) => void;
  onDoublePick?: (id: string | null) => void;
  onStats?: (stats: RenderStats) => void;
  onDropEnded?: () => void;
  onWebGLUnsupported?: (reason: string) => void;
}

export function useDetectedQuality(): QualityTier {
  const [tier] = React.useState<QualityTier>(() => detectQualityTier());
  return tier;
}

function releaseWebGLProbe(gl: WebGLRenderingContext | WebGL2RenderingContext): void {
  try {
    gl.getExtension('WEBGL_lose_context')?.loseContext();
  } catch {
    // Some browsers expose the probe extension but reject context loss while
    // the canvas is detached. There is no additional portable disposal API.
  }
}

function webGLFailureReason(error: unknown): string {
  if (error instanceof Error && error.message) {
    return `WebGL initialization failed: ${error.message}`;
  }
  return 'WebGL is not available in this browser';
}

export const SceneViewport = React.forwardRef<
  SceneViewportHandle,
  SceneViewportProps
>(function SceneViewport(props, ref) {
  const canvasRef = React.useRef<HTMLCanvasElement | null>(null);
  const runtimeRef = React.useRef<SceneRuntime | null>(null);
  const propsRef = React.useRef(props);
  propsRef.current = props;

  // WebGL support probe + SceneRuntime initialization
  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const probeCanvas = document.createElement('canvas');
    let gl: WebGLRenderingContext | WebGL2RenderingContext | null = null;
    try {
      const context =
        probeCanvas.getContext('webgl2') ||
        probeCanvas.getContext('webgl') ||
        probeCanvas.getContext('experimental-webgl');
      if (context && typeof (context as WebGLRenderingContext).getExtension === 'function') {
        gl = context as WebGLRenderingContext | WebGL2RenderingContext;
      }
    } catch {
      gl = null;
    }

    if (!gl) {
      propsRef.current.onWebGLUnsupported?.('WebGL is not available in this browser');
      return;
    }

    releaseWebGLProbe(gl);

    let runtime: SceneRuntime;
    try {
      runtime = createSceneRuntime({
        canvas,
        theme: propsRef.current.theme,
        quality: propsRef.current.quality,
        onPick: (id) => propsRef.current.onPick(id),
        onStats: (stats) => propsRef.current.onStats?.(stats),
        onDropEnded: () => dropEndedRef.current(),
      });    } catch (error: unknown) {
      propsRef.current.onWebGLUnsupported?.(webGLFailureReason(error));
      return;
    }

    runtimeRef.current = runtime;

    // Initial sync
    runtime.setObjects(propsRef.current.entries);
    runtime.setVisibility(propsRef.current.visibility);
    runtime.setSelection(propsRef.current.selectedId);
    runtime.setExplode(propsRef.current.explode);
    runtime.setOverlays(propsRef.current.overlays);

    return () => {
      runtime.dispose();
      if (runtimeRef.current === runtime) runtimeRef.current = null;
    };
  }, []);

  // Sync props to runtime
  React.useEffect(() => {
    runtimeRef.current?.setObjects(props.entries);
  }, [props.entries]);

  React.useEffect(() => {
    runtimeRef.current?.setVisibility(props.visibility);
  }, [props.visibility]);

  React.useEffect(() => {
    runtimeRef.current?.setDropSimulation(props.dropSimulation ?? null);
    setDropPlaying(props.dropSimulation !== null && props.dropSimulation !== undefined);
    setDropTime(0);
  }, [props.dropSimulation]);

  const dropEndedRef = React.useRef<() => void>(() => {});
  const handleDropEnded = React.useCallback(() => {
    setDropPlaying(false);
    props.onDropEnded?.();
  }, [props.onDropEnded]);
  dropEndedRef.current = handleDropEnded;

  const [dropPlaying, setDropPlaying] = React.useState(false);
  const [dropTime, setDropTime] = React.useState(0);
  React.useEffect(() => {
    if (!props.dropSimulation) return;
    const interval = window.setInterval(() => {
      setDropTime(runtimeRef.current?.getDropTime() ?? 0);
    }, 100);
    return () => window.clearInterval(interval);
  }, [props.dropSimulation]);

  const dropSimulation = props.dropSimulation;
  const activeDrop =
    dropSimulation && dropSimulation.drops.length > 0
      ? dropSimulation.drops.find(
          (drop) => dropTime >= drop.start_s && dropTime <= drop.end_s,
        ) ?? dropSimulation.drops[dropSimulation.drops.length - 1]
      : null;

  React.useEffect(() => {
    runtimeRef.current?.setSelection(props.selectedId);
  }, [props.selectedId]);

  React.useEffect(() => {
    runtimeRef.current?.setExplode(props.explode);
  }, [props.explode]);

  React.useEffect(() => {
    runtimeRef.current?.setTheme(props.theme);
  }, [props.theme]);

  React.useEffect(() => {
    runtimeRef.current?.setQuality(props.quality);
  }, [props.quality]);

  React.useEffect(() => {
    runtimeRef.current?.setOverlays(props.overlays);
  }, [props.overlays]);

  React.useImperativeHandle(
    ref,
    () => ({
      fit() {
        runtimeRef.current?.fit();
      },
      preset(name: CameraPreset) {
        runtimeRef.current?.preset(name);
      },
      setDropPlayback(playing: boolean) {
        runtimeRef.current?.setDropPlayback(playing);
      },
    }),
    [],
  );

  return (
    <div
      className="scene-viewport"
      role="img"
      aria-label="3D engineering viewport"
    >
      <canvas ref={canvasRef} aria-hidden="true" />
      {dropSimulation ? (
        <div className="drop-sim-controls" role="group" aria-label="Drop simulation playback">
          <button
            type="button"
            className="btn btn--sm"
            aria-label={dropPlaying ? 'Pause drop simulation' : 'Play drop simulation'}
            onClick={() => {
              const next = !dropPlaying;
              setDropPlaying(next);
              runtimeRef.current?.setDropPlayback(next);
            }}
          >
            {dropPlaying ? 'PAUSE' : 'PLAY'}
          </button>
          <button
            type="button"
            className="btn btn--sm"
            aria-label="Restart drop simulation"
            onClick={() => {
              setDropTime(0);
              runtimeRef.current?.restartDropPlayback();
            }}
          >
            RESTART
          </button>
          <span className="drop-sim-controls__status">
            {activeDrop ? `Drop ${activeDrop.index + 1}/${dropSimulation.drops.length}` : 'Simulation'}
            {' · '}
            {dropTime.toFixed(2)}s
            {dropSimulation.peak ? (
              <>
                {' · peak '}
                {dropSimulation.peak.impact_speed_m_s.toFixed(2)} m/s
              </>
            ) : null}
          </span>
        </div>
      ) : null}
    </div>
  );
});
