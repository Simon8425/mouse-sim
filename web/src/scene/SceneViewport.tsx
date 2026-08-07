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

export interface SceneViewportHandle {
  fit: () => void;
  preset: (name: CameraPreset) => void;
}

export interface SceneViewportProps {
  entries: ObjectSceneEntry[];
  visibility: Record<string, boolean>;
  selectedId: string | null;
  explode: number;
  theme: 'light' | 'dark';
  quality: QualityTier;
  overlays: OverlaySpec | null;
  onPick: (id: string | null) => void;
  onDoublePick?: (id: string | null) => void;
  onStats?: (stats: RenderStats) => void;
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
      });
    } catch (error: unknown) {
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
    </div>
  );
});
