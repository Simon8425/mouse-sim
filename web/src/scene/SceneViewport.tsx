import * as React from 'react';
import {
  createSceneRuntime,
  detectQualityTier,
  type SceneRuntime,
  type RenderStats,
  type ShaderPrecision,
} from './sceneRuntime';
import type { LiveBodyState } from './rapierDropSim';
import type { CameraPreset } from './camera';
import type { QualityTier } from './materialPalette';
import type { OverlaySpec } from './overlays';
import type { ObjectSceneEntry } from './geometryFactory';
import type { DropSimulationDrop, DropSimulationResult, FeaResult, RenderMode, PopulationResult } from '../api/contracts';
import { FeaHud } from '../components/FeaHud';
import { PopulationFleetHud } from '../components/PopulationFleetHud';
import type { FleetUnitData } from './populationFleetScene';

import type {
  TelemetryFrame,
  TelemetryEvent,
  TelemetryLogSession,
} from '../api/telemetryDebuggerContracts';

/**
 * Resolve the drop active at a playback time.
 *
 * The counter is monotonic: the drop with the largest ``start_s <= t`` is
 * active even during the inter-drop gaps (no samples), so the readout never
 * jumps ahead to the last drop prematurely.
 */
export function resolveActiveDrop(
  drops: DropSimulationDrop[],
  dropTime: number,
): DropSimulationDrop | null {
  let active: DropSimulationDrop | null = null;
  for (const drop of drops) {
    if (drop.start_s <= dropTime) active = drop;
    else break;
  }
  return active ?? (drops.length > 0 ? drops[0] : null);
}

export interface SceneViewportHandle {
  fit: () => void;
  preset: (name: CameraPreset) => void;
  setDropPlayback: (playing: boolean) => void;
  /** Seek live drop playback to an absolute time (seconds). */
  seekDropTime: (t: number) => void;
  getTelemetryFrames: () => TelemetryFrame[];
  getTelemetryEvents: () => TelemetryEvent[];
  buildTelemetrySession: () => TelemetryLogSession | null;
}

export interface LiveDropData {
  activeDropIndex: number;
  totalDrops: number;
  dropTime: number;
  activeDrop: DropSimulationDrop | null;
  liveFrame: LiveBodyState | null;
  speedMps: number;
  kineticEnergyJ: number;
  isPlaying: boolean;
  status: 'free_fall' | 'impact' | 'settled' | 'idle';
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
  populationResult?: PopulationResult | null;
  renderMode?: RenderMode;
  feaResult?: FeaResult | null;
  insets?: { left?: number; right?: number; top?: number; bottom?: number };
  onPick: (id: string | null) => void;
  onDoublePick?: (id: string | null) => void;
  onStats?: (stats: RenderStats) => void;
  onDropEnded?: () => void;
  onPlaybackStateChange?: (playing: boolean) => void;
  onLiveDropData?: (data: LiveDropData | null) => void;
  onWebGLUnsupported?: (reason: string) => void;
}

export function useDetectedQuality(): QualityTier {
  const [tier] = React.useState<QualityTier>(() => detectQualityTier());
  return tier;
}

/**
 * Memoized playback buttons.  The status text next to them re-renders every
 * 100 ms while playback runs; keeping the buttons on their own memoized
 * subtree (stable props: the play state and stable callbacks) prevents the
 * 100 Hz re-render churn from making the buttons unreliable to interact
 * with under load.
 */
const DropPlaybackButtons = React.memo(function DropPlaybackButtons({
  playing,
  onTogglePlay,
  onRestart,
}: {
  playing: boolean;
  onTogglePlay: () => void;
  onRestart: () => void;
}) {
  return (
    <div className="viewport-toolbar__group" role="group" aria-label="Playback actions">
      <button
        type="button"
        className="btn"
        aria-label={playing ? 'Pause drop simulation' : 'Play drop simulation'}
        onClick={onTogglePlay}
      >
        {playing ? 'Pause' : 'Play'}
      </button>
      <button
        type="button"
        className="btn"
        aria-label="Restart drop simulation"
        onClick={onRestart}
      >
        Restart
      </button>
    </div>
  );
});

function hasUsableShaderPrecision(
  gl: WebGLRenderingContext | WebGL2RenderingContext,
  precisionType: number,
): boolean {
  const vertex = gl.getShaderPrecisionFormat(gl.VERTEX_SHADER, precisionType);
  const fragment = gl.getShaderPrecisionFormat(gl.FRAGMENT_SHADER, precisionType);
  return (
    vertex != null &&
    fragment != null &&
    typeof vertex.precision === 'number' &&
    typeof fragment.precision === 'number' &&
    vertex.precision > 0 &&
    fragment.precision > 0
  );
}

/**
 * Select a renderer precision without allowing Three.js to probe a null
 * HIGH_FLOAT format.  Three.js does not probe LOW_FLOAT when `lowp` is passed,
 * making it the safe last resort for otherwise usable but degraded contexts.
 */
export function selectShaderPrecision(
  gl: WebGLRenderingContext | WebGL2RenderingContext,
): ShaderPrecision {
  try {
    if (hasUsableShaderPrecision(gl, gl.HIGH_FLOAT)) return 'highp';
    if (hasUsableShaderPrecision(gl, gl.MEDIUM_FLOAT)) return 'mediump';
  } catch {
    // A broken precision query is handled by Three.js's lowp shader path.
  }
  return 'lowp';
}

export function probeShaderPrecision(gl: WebGLRenderingContext | WebGL2RenderingContext): boolean {
  // THREE r168's WebGLCapabilities.maxPrecision reads
  // `gl.getShaderPrecisionFormat(...).precision` for HIGH_FLOAT.  On some
  // software renderers / GPU-blocklisted drivers the context is created but
  // getShaderPrecisionFormat returns null, and the renderer constructor
  // crashes with "Cannot read properties of null (reading 'precision')".
  // Detect that exact failure here so the UI can fall back cleanly (and the
  // runtime can retry with a lower precision request).
  return selectShaderPrecision(gl) === 'highp';
}

/** Verifies a WebGL2 context is actually usable — not just created. */
function isUsableWebGL2(gl: WebGL2RenderingContext): boolean {
  try {
    // THREE's WebGLState reads `gl.getParameter(gl.VERSION).indexOf('WebGL')`
    // — on degraded/broken contexts VERSION can be null and the renderer
    // crashes with "Cannot read properties of null (reading 'indexOf')".
    const version = gl.getParameter(gl.VERSION);
    if (typeof version !== 'string' || version.indexOf('WebGL') === -1) return false;
    // A context that cannot report basic limits is not renderable either.
    const maxTexture = gl.getParameter(gl.MAX_TEXTURE_SIZE);
    const maxVertex = gl.getParameter(gl.MAX_VERTEX_ATTRIBS);
    if (typeof maxTexture !== 'number' || maxTexture <= 0) return false;
    if (typeof maxVertex !== 'number' || maxVertex <= 0) return false;
    return true;
  } catch {
    return false;
  }
}

/**
 * Create a WebGL2 context with the MOST permissive attribute set, then
 * validate it (VERSION string and basic limits).  Passing the
 * validated context to THREE via the `context` option prevents the renderer
 * from re-requesting a context with attributes this browser rejects, and
 * prevents the "precision"/"indexOf"-of-null crashes on degraded drivers.
 * Returns null when no usable WebGL2 context can be created.
 */
export function createUsableWebGL2Context(canvas: HTMLCanvasElement): WebGL2RenderingContext | null {
  const attributes: WebGLContextAttributes = {
    alpha: false,
    depth: true,
    stencil: false,
    antialias: false,
    premultipliedAlpha: true,
    preserveDrawingBuffer: false,
    powerPreference: 'default',
    failIfMajorPerformanceCaveat: false,
  };
  let gl: WebGL2RenderingContext | null = null;
  try {
    gl = canvas.getContext('webgl2', attributes) as WebGL2RenderingContext | null;
  } catch {
    gl = null;
  }
  if (gl === null) return null;
  if (!isUsableWebGL2(gl)) {
    try {
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    } catch {
      // no-op
    }
    return null;
  }
  return gl;
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

    // Create and validate a WebGL2 context with permissive attributes, then
    // hand it to THREE.  On broken/software drivers this avoids both the
    // "precision"-of-null and "indexOf"-of-null renderer crashes.
    const gl = createUsableWebGL2Context(canvas);
    if (gl === null) {
      propsRef.current.onWebGLUnsupported?.(
        'WebGL 2.0 is not available in this browser',
      );
      return;
    }
    const precision = selectShaderPrecision(gl);

    let runtime: SceneRuntime;
    try {
      runtime = createSceneRuntime({
        canvas,
        context: gl,
        precision,
        theme: propsRef.current.theme,
        quality: propsRef.current.quality,
        onPick: (id) => propsRef.current.onPick(id),
        onStats: (stats) => propsRef.current.onStats?.(stats),
        onDropEnded: () => dropEndedRef.current(),
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
    // Keep the floor's material in sync with the configured drop-test surface
    // (floor look follows the test surface selection).
    const surfaceKey =
      (props.dropSimulation as DropSimulationResult | null)?.config?.surface ??
      (props.overlays ? null : 'concrete');
    runtimeRef.current?.setFloorMaterial(surfaceKey);
  }, [props.dropSimulation, props.overlays]);

  const prevDropSimRef = React.useRef<DropSimulationResult | null | undefined>(undefined);
  React.useEffect(() => {
    if (props.dropSimulation === prevDropSimRef.current) return;
    prevDropSimRef.current = props.dropSimulation;
    runtimeRef.current?.setDropSimulation(props.dropSimulation ?? null);
    setDropPlaying(props.dropSimulation !== null && props.dropSimulation !== undefined);
    setDropTime(0);
  }, [props.dropSimulation]);

  const dropEndedRef = React.useRef<() => void>(() => {});
  const { onDropEnded: onDropEndedProp } = props;
  const handleDropEnded = React.useCallback(() => {
    setDropPlaying(false);
    onDropEndedProp?.();
  }, [onDropEndedProp]);
  dropEndedRef.current = handleDropEnded;

  const [dropPlaying, setDropPlaying] = React.useState(false);
  const [dropTime, setDropTime] = React.useState(0);
  const [liveDropIndex, setLiveDropIndex] = React.useState(0);
  const [playbackSpeed, setPlaybackSpeed] = React.useState(1.0);
  const [liveFrame, setLiveFrame] = React.useState<LiveBodyState | null>(null);
  const [liveDropData, setLiveDropData] = React.useState<LiveDropData | null>(null);

  const { onLiveDropData, dropSimulation } = props;

  // The status interval only needs to run while playback is active: the
  // runtime's drop clock is frozen whenever the simulation is paused or
  // finished, so polling it every 100 ms would otherwise keep a timer (and
  // a setState call per tick) alive for the whole lifetime of a displayed
  // drop result.
  React.useEffect(() => {
    props.onPlaybackStateChange?.(dropPlaying);
  }, [dropPlaying, props]);

  React.useEffect(() => {
    if (!props.dropSimulation) return;
    const interval = window.setInterval(() => {
      setDropTime(runtimeRef.current?.getDropTime() ?? 0);
      setLiveDropIndex(runtimeRef.current?.getLiveDropIndex() ?? 0);
      setLiveFrame(runtimeRef.current?.getLiveFrame() ?? null);
    }, 50);
    return () => window.clearInterval(interval);
  }, [props.dropSimulation]);

  const activeDrop = dropSimulation
    ? (dropSimulation.drops[liveDropIndex] ?? resolveActiveDrop(dropSimulation.drops, dropTime))
    : null;

  // Broadcast live frame / telemetry to listeners (e.g. ResultsRail) and
  // mirror it locally so the FEA HUD can show the live impact readout.
  React.useEffect(() => {
    if (!dropSimulation) {
      setLiveDropData(null);
      onLiveDropData?.(null);
      return;
    }
    const mass = dropSimulation.model?.mass_kg ?? 0.06;
    let speedMps = 0;
    if (liveFrame?.linvel) {
      const [vx, vy, vz] = liveFrame.linvel;
      speedMps = Math.sqrt(vx * vx + vy * vy + vz * vz);
    } else if (activeDrop) {
      speedMps = activeDrop.peak_impact_speed_m_s;
    }
    const ke = 0.5 * mass * speedMps * speedMps;
    let status: LiveDropData['status'] = 'idle';
    if (dropPlaying) {
      if (speedMps < 0.05 && liveFrame?.position && liveFrame.position[2] < 0.02) {
        status = 'settled';
      } else if (liveFrame?.position && liveFrame.position[2] < 0.06 && speedMps > 0.4) {
        status = 'impact';
      } else {
        status = 'free_fall';
      }
    }
    const data: LiveDropData = {
      activeDropIndex: activeDrop?.index ?? 0,
      totalDrops: dropSimulation.drops.length,
      dropTime,
      activeDrop,
      liveFrame,
      speedMps,
      kineticEnergyJ: ke,
      isPlaying: dropPlaying,
      status,
    };
    setLiveDropData(data);
    onLiveDropData?.(data);
  }, [dropSimulation, dropPlaying, dropTime, liveFrame, activeDrop, onLiveDropData]);

  const handleTogglePlay = React.useCallback(() => {
    const next = !dropPlaying;
    setDropPlaying(next);
    runtimeRef.current?.setDropPlayback(next);
  }, [dropPlaying]);

  const handleRestart = React.useCallback(() => {
    // The runtime restarts and resumes playing; keep the React play state in
    // sync so the control bar shows PAUSE after a restart.
    setDropPlaying(true);
    setDropTime(0);
    runtimeRef.current?.restartDropPlayback();
  }, []);

  const handleSpeedChange = React.useCallback((speed: number) => {
    setPlaybackSpeed(speed);
    runtimeRef.current?.setPlaybackSpeed(speed);
  }, []);

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

  React.useEffect(() => {
    runtimeRef.current?.setRenderMode(props.renderMode ?? 'default');
  }, [props.renderMode]);

  // Population Fleet 3D State
  const [fleetPlaying, setFleetPlaying] = React.useState(true);
  const [selectedFleetUnit, setSelectedFleetUnit] = React.useState<FleetUnitData | null>(null);

  React.useEffect(() => {
    if (props.populationResult) {
      setFleetPlaying(true);
      setSelectedFleetUnit(null);
    }
  }, [props.populationResult]);

  React.useEffect(() => {
    runtimeRef.current?.setPopulationFleet(props.populationResult ?? null, true);
  }, [props.populationResult]);

  React.useEffect(() => {
    runtimeRef.current?.setFeaResult(props.feaResult ?? null);
  }, [props.feaResult]);

  React.useEffect(() => {
    if (props.insets) {
      runtimeRef.current?.setInsets(props.insets);
    }
  }, [props.insets]);

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
      seekDropTime(t: number) {
        runtimeRef.current?.seekDropTime(t);
      },
      getTelemetryFrames() {
        return runtimeRef.current?.getTelemetryFrames() ?? [];
      },
      getTelemetryEvents() {
        return runtimeRef.current?.getTelemetryEvents() ?? [];
      },
      buildTelemetrySession() {
        return runtimeRef.current?.buildTelemetrySession() ?? null;
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
      <FeaHud
        mode={props.renderMode ?? 'default'}
        fea={props.feaResult ?? null}
        liveDropData={liveDropData}
      />
      {props.populationResult ? (
        <PopulationFleetHud
          population={props.populationResult}
          playing={fleetPlaying}
          onTogglePlay={() => {
            const next = !fleetPlaying;
            setFleetPlaying(next);
            runtimeRef.current?.setFleetPlayback(next);
          }}
          selectedUnit={selectedFleetUnit}
          onCloseSelectedUnit={() => {
            setSelectedFleetUnit(null);
            runtimeRef.current?.highlightFleetUnit(null);
          }}
        />
      ) : null}

      {dropSimulation && !props.populationResult ? (
        <div className="drop-sim-controls viewport-toolbar" role="toolbar" aria-label="Drop simulation playback">
          <DropPlaybackButtons
            playing={dropPlaying}
            onTogglePlay={handleTogglePlay}
            onRestart={handleRestart}
          />
          <div className="viewport-toolbar__divider" aria-hidden="true" />
          <div className="viewport-toolbar__group" role="group" aria-label="Playback speed">
            {[1, 5, 20].map((spd) => (
              <button
                key={spd}
                type="button"
                className={`btn${playbackSpeed === spd ? ' is-active' : ''}`}
                onClick={() => handleSpeedChange(spd)}
                aria-label={`Set speed to ${spd}x`}
              >
                {spd}x
              </button>
            ))}
          </div>
          <div className="viewport-toolbar__divider" aria-hidden="true" />
          <div className="viewport-toolbar__group" role="group" aria-label="Simulation time">
            <span className="drop-sim-controls__status">
              {activeDrop ? `Drop ${activeDrop.index + 1}/${dropSimulation.drops.length}` : 'Simulation'}
              {' · '}
              {dropTime.toFixed(2)}s
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
});
