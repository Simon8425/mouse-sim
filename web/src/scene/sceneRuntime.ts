import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { OutlinePass } from 'three/examples/jsm/postprocessing/OutlinePass.js';
import { SMAAPass } from 'three/examples/jsm/postprocessing/SMAAPass.js';

import type { Vec3, DropSimulationResult, DropTrajectorySample, FeaResult, RenderMode, PopulationResult } from '../api/contracts';
import type { TelemetryEvent, TelemetryFrame, TelemetryLogSession } from '../api/telemetryDebuggerContracts';
import { TelemetryCollector, type LiveTelemetryRecord } from '../lib/telemetryCollector';
import { buildTelemetrySession } from '../lib/telemetrySessionBuilder';
import { createPopulationFleet, type PopulationFleetManager, type FleetUnitData } from './populationFleetScene';
import {
  buildRapierDropSim,
  type RapierDropSim,
  type LiveBodyState,
} from './rapierDropSim';
import {
  MaterialPalette,
  FeaMaterialCache,
  type QualityTier,
} from './materialPalette';
import {
  createFeaUniforms,
  updateFeaUniforms,
  feaFieldMaxDamage,
  impactPulseFor,
  type FeaUniforms,
  type FeaPlateConfig,
} from './feaStressShader';
import { createSceneCamera, fitCameraToBounds, applyCameraPreset, type CameraPreset } from './camera';
import { createPicker } from './picking';
import { createOverlayLayer, type OverlaySpec } from './overlays';
import {
  applyFeaPlateField,
  buildInstancedBatches,
  createObjectGroup,
  worldBoundsForGeometry,
  disposeObjectGroup,
  applyFeaObjectField,
  objectMeshesFor,
  syncInstancedPose,
  type InstancedBatchGroup,
  type ObjectSceneEntry,
} from './geometryFactory';
import { disposeObject3D, disposeSceneResources } from './disposal';

export interface RenderStats {
  drawCalls: number;
  triangles: number;
  geometries: number;
  textures: number;
  tier: QualityTier;
}

export interface SceneRuntimeOptions {
  canvas: HTMLCanvasElement;
  /** Pre-created, validated WebGL2 context to hand to THREE. */
  context?: WebGL2RenderingContext | null;
  precision?: ShaderPrecision;
  theme: 'light' | 'dark';
  quality: QualityTier;
  onPick: (id: string | null) => void;
  onDoublePick?: (id: string | null) => void;
  onStats?: (stats: RenderStats) => void;
  onDropEnded?: () => void;
  onFleetUnitPick?: (unit: FleetUnitData | null) => void;
}

export type ShaderPrecision = 'highp' | 'mediump' | 'lowp';

/**
 * Vertical correction that lifts a rendered model whose bounds have sunk
 * below the display floor (used by drop playback alignment). Returns the
 * amount to raise the model, or 0 when it already sits above the floor or
 * the inputs are not finite.
 */
export function floorCorrectionForModel(minZ: number, floorZ: number): number {
  if (!Number.isFinite(minZ) || !Number.isFinite(floorZ)) return 0;
  return minZ < floorZ ? floorZ - minZ : 0;
}

/**
 * Lowest z of the model's AABB corners after applying the given orientation.
 * The rotated AABB of the original AABB contains the rotated model, so this
 * is a conservative lower bound of the model's true lowest point — it never
 * under-estimates how low the model can reach, which is what the drop
 * playback floor clamp requires. Falls back to the unrotated minimum z when
 * the quaternion or bounds are not finite.
 */
export function rotatedBoundsMinZ(
  min: Vec3,
  max: Vec3,
  quaternion: THREE.Quaternion,
): number {
  const scratch = new THREE.Vector3();
  let lowest = Infinity;
  for (let corner = 0; corner < 8; corner += 1) {
    scratch.set(
      (corner & 1) === 0 ? min[0] : max[0],
      (corner & 2) === 0 ? min[1] : max[1],
      (corner & 4) === 0 ? min[2] : max[2],
    );
    scratch.applyQuaternion(quaternion);
    if (scratch.z < lowest) lowest = scratch.z;
  }
  return Number.isFinite(lowest) ? lowest : min[2];
}

/**
 * EXACT lowest z of the model's actual vertices after applying the given
 * orientation.  Unlike the conservative rotated-AABB bound (which over-lifts
 * the model so it floats above the floor on tilted rest poses), this returns
 * the true lowest surface point so the playback clamp can rest the model
 * flush on the ground.  Falls back to +Infinity (no clamp) when the vertices
 * are empty or the quaternion is not finite.
 */
export function exactModelLowestZ(vertices: Vec3[], quaternion: THREE.Quaternion): number {
  if (!Number.isFinite(quaternion.x + quaternion.y + quaternion.z + quaternion.w)) {
    return Infinity;
  }
  const scratch = new THREE.Vector3();
  let lowest = Infinity;
  for (const vertex of vertices) {
    scratch.set(vertex[0], vertex[1], vertex[2]).applyQuaternion(quaternion);
    if (scratch.z < lowest) lowest = scratch.z;
  }
  return Number.isFinite(lowest) ? lowest : Infinity;
}

export interface SceneRuntime {
  setObjects: (entries: ObjectSceneEntry[]) => void;
  setVisibility: (visibility: Record<string, boolean>) => void;
  setSelection: (id: string | null) => void;
  setExplode: (factor: number) => void;
  setTheme: (theme: 'light' | 'dark') => void;
  setQuality: (quality: QualityTier) => void;
  setOverlays: (overlays: OverlaySpec | null) => void;
  clearOverlays: () => void;
  setFloorMaterial: (surfaceKey: string | null | undefined) => void;
  setDropSimulation: (simulation: DropSimulationResult | null) => void;
  setDropPlayback: (playing: boolean) => void;
  setPlaybackSpeed: (speed: number) => void;
  restartDropPlayback: () => void;
  getDropTime: () => number;
  getLiveDropIndex: () => number;
  /** Live physics frame (Rapier body state) when active, else null. */
  getLiveFrame: () => LiveBodyState | null;
  /** Seek live drop playback to an absolute time. */
  seekDropTime: (target: number) => void;
  /** Recorded telemetry frames (bounded ring). */
  getTelemetryFrames: () => TelemetryFrame[];
  /** Recorded telemetry events. */
  getTelemetryEvents: () => TelemetryEvent[];
  clearTelemetry: () => void;
  /** Full debugger session (specs + frames) or null when idle. */
  buildTelemetrySession: () => TelemetryLogSession | null;
  setRenderMode: (mode: RenderMode) => void;
  setFeaResult: (fea: FeaResult | null) => void;
  setPopulationFleet: (population: PopulationResult | null, fleetMode: boolean) => void;
  setFleetPlayback: (playing: boolean) => void;
  restartFleetPlayback: () => void;
  getFleetTime: () => number;
  pickFleetUnit: (raycaster: THREE.Raycaster) => FleetUnitData | null;
  highlightFleetUnit: (unitId: number | null) => void;
  fit: () => void;
  preset: (name: CameraPreset) => void;
  setInsets: (insets: { left?: number; right?: number; top?: number; bottom?: number }) => void;
  getStats: () => RenderStats;
  dispose: () => void;
}

/**
 * Exploded-view offset computation.
 *
 * The model is "unstacked" vertically: every part lifts off the assembly and
 * rises above the floor, with its height driven by its vertical position in
 * the assembly (parts that were already higher end up higher in the explode),
 * plus a modest outward lateral spread away from the assembly center. Nothing
 * ever moves below the assembly — the exploded view can never go under the
 * floor. Deterministic, id-independent, and purely display-only.
 */
export interface ExplodePart {
  id: string;
  /** Part AABB center (must share the frame of assemblyCenter). */
  center: Vec3;
  /** Part AABB size, used for the vertical stacking and lateral spread. */
  size: Vec3;
}

const EXPLODE_LIFT_BASE = 0;
const EXPLODE_LIFT_RANGE = 1.1;
const EXPLODE_LATERAL = 0.3;

/**
 * Compute the full-explode offset for every part. Targets are cached once per
 * rebuild (they do not depend on the current explode factor), so the per-frame
 * apply step only scales them by an eased factor.
 */
export function computeExplodeOffsets(
  parts: ExplodePart[],
  assemblyCenter: Vec3,
  maxDim: number,
): Map<string, Vec3> {
  const targets = new Map<string, Vec3>();
  if (parts.length === 0) return targets;

  // Vertical rank: 0 = lowest part, 1 = highest part, by center Z.
  const sorted = [...parts].sort((a, b) => a.center[2] - b.center[2]);
  const zMin = sorted[0].center[2];
  const zMax = sorted[sorted.length - 1].center[2];
  const zSpan = Math.max(zMax - zMin, 1e-9);
  const rankByPart = new Map<string, number>();
  for (const part of parts) {
    rankByPart.set(part.id, (part.center[2] - zMin) / zSpan);
  }

  // Distance from assembly center on the X/Y plane (0 = innermost).
  let maxLateral = 0;
  for (const part of parts) {
    const dx = part.center[0] - assemblyCenter[0];
    const dy = part.center[1] - assemblyCenter[1];
    maxLateral = Math.max(maxLateral, Math.sqrt(dx * dx + dy * dy));
  }
  const lateralSpan = Math.max(maxLateral, 1e-9);

  for (const part of parts) {
    const rank01 = rankByPart.get(part.id) ?? 0.5;
    // All parts rise; higher parts rise more. The base lift keeps even the
    // lowest parts well above the assembly (never under the floor).
    const lift = maxDim * (EXPLODE_LIFT_BASE + EXPLODE_LIFT_RANGE * rank01);

    // Modest outward lateral spread, away from the assembly center.
    const dx = part.center[0] - assemblyCenter[0];
    const dy = part.center[1] - assemblyCenter[1];
    const lateralDist = Math.sqrt(dx * dx + dy * dy);
    const lateralDirX = lateralDist > 1e-9 ? dx / lateralDist : 0;
    const lateralDirY = lateralDist > 1e-9 ? dy / lateralDist : 0;
    const lateral = maxDim * EXPLODE_LATERAL * (0.4 + 0.6 * (lateralDist / lateralSpan));

    targets.set(part.id, [lateralDirX * lateral, lateralDirY * lateral, lift]);
  }

  // Vertical de-overlap (single-pass stack resolution): sort parts by their
  // exploded center Z and walk top-down. Whenever two parts overlap, push the
  // higher one up just enough to clear the lower one — the stack compresses
  // upward deterministically and can never move anything below the assembly.
  // This handles nested/concentric parts (wheel hub inside the ring, buttons
  // on the shell) without oscillating pairwise pushes.
  const order = [...parts].sort((a, b) => {
    const za = a.center[2] + (targets.get(a.id)?.[2] ?? 0);
    const zb = b.center[2] + (targets.get(b.id)?.[2] ?? 0);
    return za - zb;
  });
  const minGap = maxDim * 0.03;
  const resolved = new Map<string, { center: Vec3; size: Vec3 }>();
  for (const part of order) {
    const ta = targets.get(part.id);
    if (!ta) continue;
    const ca = [
      part.center[0] + ta[0],
      part.center[1] + ta[1],
      part.center[2] + ta[2],
    ];
    // Find the highest already-resolved part this one still overlaps.
    let minLift = 0;
    for (const other of resolved.values()) {
      const overlapX = Math.abs(ca[0] - other.center[0]) < (part.size[0] + other.size[0]) / 2 + minGap;
      const overlapY = Math.abs(ca[1] - other.center[1]) < (part.size[1] + other.size[1]) / 2 + minGap;
      if (!overlapX || !overlapY) continue;
      const required = (part.size[2] + other.size[2]) / 2 + minGap - (ca[2] - other.center[2]);
      if (required > 0) minLift = Math.max(minLift, required);
    }
    if (minLift > 0) {
      ta[2] += minLift;
      ca[2] += minLift;
    }
    resolved.set(part.id, { center: [ca[0], ca[1], ca[2]], size: part.size });
  }

  return targets;
}

export function detectQualityTier(): QualityTier {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return 'medium';
  const userAgent = navigator.userAgent || '';
  const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(userAgent);
  const cores = navigator.hardwareConcurrency || 4;
  const dpr = window.devicePixelRatio || 1;

  if (isMobile || cores <= 4) return 'low';
  if (cores <= 8 || dpr > 2) return 'medium';
  return 'high';
}

export function entriesSignature(entries: ObjectSceneEntry[]): string {
  try {
    return JSON.stringify(
      entries.map((entry) => [
        entry.id,
        entry.className ?? null,
        entry.displayAssetUrl ?? null,
        entry.color ?? null,
        // Large per-part meshes are analysed/bounded from their geometry; the
        // signature only needs identity and sizes, not the vertex payload.
        entry.displayAssetUrl
          ? [entry.geometry.type, entry.geometry.type === 'mesh' ? entry.geometry.triangles.length : null]
          : entry.geometry.type === 'mesh'
            ? [entry.geometry.type, entry.geometry.vertices.length, entry.geometry.triangles.length]
            : entry.geometry,
      ]),
    );
  } catch {
    // Circular data is malformed input; retain a conservative ID-based
    // fallback rather than allowing a stats-driven render to throw.
    return entries.map((entry) => String(entry.id)).join('|');
  }
}

function idsSignature(entries: ObjectSceneEntry[]): string {
  try {
    return JSON.stringify(entries.map((entry) => entry.id));
  } catch {
    return entries.map((entry) => String(entry.id)).join('|');
  }
}

function finiteBounds(bounds: { min: Vec3; max: Vec3 }): boolean {
  return bounds.min.every((value) => Number.isFinite(value)) && bounds.max.every((value) => Number.isFinite(value));
}

// Trajectory samples are written at a fixed 60 Hz by the simulator.
export const TRAJECTORY_HZ = 60;

/**
 * Resolve the bracketing trajectory samples for a playback time.
 *
 * Trajectory samples exist only during each drop's active sim time; the
 * ~0.35 s inter-drop gaps contain no samples.  During a gap the pose holds
 * the previous drop's final (rest) sample, and the model teleports up at
 * the next drop's first sample — matching the physics reset.
 */
export function resolveDropSample(
  t: number,
  samples: DropTrajectorySample[],
): { a: DropTrajectorySample; b: DropTrajectorySample; alpha: number } | null {
  if (samples.length === 0) return null;
  if (t <= samples[0][0]) return { a: samples[0], b: samples[0], alpha: 0 };
  let low = 0;
  let high = samples.length - 1;
  while (low < high) {
    const mid = (low + high + 1) >> 1;
    if (samples[mid][0] <= t) low = mid;
    else high = mid - 1;
  }
  const a = samples[low];
  if (low === samples.length - 1) return { a, b: a, alpha: 0 };
  const b = samples[low + 1];
  if (b[0] - a[0] > 2 / TRAJECTORY_HZ) {
    return { a, b: a, alpha: 0 };
  }
  const span = Math.max(1e-9, b[0] - a[0]);
  const alpha = Math.min(1, Math.max(0, (t - a[0]) / span));
  return { a, b, alpha };
}

/**
 * Progress of the 300 ms FEA dent window: 0 before the impact time, 1 once
 * a full window has elapsed. Returns 0 when the window duration is missing
 * or non-positive, and clamps out-of-range inputs. Pure and allocation-free.
 */
export function impactWindowProgress(dropTime: number, impactTime: number, windowS: number): number {
  if (!Number.isFinite(windowS) || windowS <= 0) return 0;
  if (!Number.isFinite(dropTime) || !Number.isFinite(impactTime)) return 0;
  const t = (dropTime - impactTime) / windowS;
  if (t <= 0) return 0;
  if (t >= 1) return 1;
  return t;
}

export function createSceneRuntime(opts: SceneRuntimeOptions): SceneRuntime {
  const { canvas } = opts;
  let theme = opts.theme;
  let quality = opts.quality;

  const renderer = new THREE.WebGLRenderer({
    canvas,
    context: opts.context ?? undefined,
    antialias: quality !== 'low',
    powerPreference: 'high-performance',
  });
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = quality !== 'low';
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  let composer: EffectComposer | null = null;

  const updatePixelRatio = () => {
    const dpr = window.devicePixelRatio || 1;
    const maxRatio = quality === 'ultra' ? 2.5 : quality === 'high' ? 2 : 1.5;
    const ratio = Math.min(dpr, maxRatio);
    renderer.setPixelRatio(ratio);
    // The composer must render at the SAME pixel ratio as the canvas or the
    // post passes (RenderPass/OutlinePass/SMAAPass) render at 1x while the
    // canvas draws at 2-3x on Retina — a massive fill-rate waste and a
    // half-resolution image. EffectComposer.setPixelRatio re-sizes its
    // render targets and calls setSize on every pass.
    composer?.setPixelRatio(ratio);
  };
  updatePixelRatio();

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(theme === 'dark' ? 0x121212 : 0xf4f5f7);

  const camera = createSceneCamera();
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 0.0005;
  controls.maxDistance = 20;
  controls.screenSpacePanning = true;

  let onContextRestored: (() => void) | null = null;
  const handleContextLost = (event: Event) => {
    event.preventDefault();
    if (animationFrameId !== null) {
      cancelAnimationFrame(animationFrameId);
      animationFrameId = null;
    }
  };

  const handleContextRestored = () => {
    if (!disposed) {
      if (onContextRestored) {
        try {
          onContextRestored();
        } catch (err) {
          console.warn('[SceneRuntime] Context restore error:', err);
        }
      }
      if (animationFrameId === null) {
        lastFrameTime = performance.now();
        animationFrameId = requestAnimationFrame(renderLoop);
      }
    }
  };

  canvas.addEventListener('webglcontextlost', handleContextLost, false);
  canvas.addEventListener('webglcontextrestored', handleContextRestored, false);

  // Lighting setup
  const keyLight = new THREE.DirectionalLight(0xffffff, 2.4);
  keyLight.position.set(0.5, -0.8, 1);
  keyLight.castShadow = quality !== 'low';
  keyLight.shadow.mapSize.width = quality === 'ultra' ? 2048 : quality === 'high' ? 1024 : 512;
  keyLight.shadow.mapSize.height = quality === 'ultra' ? 2048 : quality === 'high' ? 1024 : 512;
  keyLight.userData.owned = true;

  const fillLight = new THREE.DirectionalLight(0xffffff, 1.1);
  fillLight.position.set(-0.6, 0.4, 0.3);
  fillLight.userData.owned = true;

  const rimLight = new THREE.DirectionalLight(0xffffff, 0.9);
  rimLight.position.set(0.2, 0.6, -0.5);
  rimLight.userData.owned = true;

  const ambientLight = new THREE.AmbientLight(0xffffff, 0.35);
  ambientLight.userData.owned = true;

  scene.add(keyLight, fillLight, rimLight, ambientLight);

  // Palette & Objects root group
  let palette = new MaterialPalette(theme);
  const objectsGroup = new THREE.Group();
  objectsGroup.name = 'ObjectsGroup';
  scene.add(objectsGroup);

  const selectionHighlightGroup = new THREE.Group();
  selectionHighlightGroup.name = 'SelectionHighlightGroup';
  scene.add(selectionHighlightGroup);

  let gridHelper: THREE.GridHelper | null = null;
  let groundMesh: THREE.Mesh | null = null;
  let boundsUnion = { min: [-0.05, -0.05, -0.05] as Vec3, max: [0.05, 0.05, 0.05] as Vec3 };
  let maxDimension = 0.1;
  let floorSize = 2.5;
  let currentSurfaceMaterial = 'concrete';
  let floorSurfaceKey: string | null | undefined = 'concrete';

  function getSurfaceMaterialProps(surfaceKey: string | null | undefined, currentTheme: 'light' | 'dark') {
    const key = (surfaceKey || 'concrete').toLowerCase();
    if (key.includes('concrete')) {
      return { color: currentTheme === 'dark' ? 0x3f444c : 0x8f96a0, roughness: 0.85, metalness: 0.05 };
    }
    if (key.includes('steel') || key.includes('metal')) {
      return { color: currentTheme === 'dark' ? 0x333b48 : 0x64748b, roughness: 0.35, metalness: 0.85 };
    }
    if (key.includes('wood') || key.includes('timber') || key.includes('oak')) {
      return { color: currentTheme === 'dark' ? 0x5c3818 : 0x925522, roughness: 0.7, metalness: 0.02 };
    }
    if (key.includes('tile') || key.includes('ceramic')) {
      return { color: currentTheme === 'dark' ? 0x272e38 : 0x475569, roughness: 0.25, metalness: 0.1 };
    }
    if (key.includes('asphalt')) {
      return { color: currentTheme === 'dark' ? 0x1e2228 : 0x334155, roughness: 0.95, metalness: 0.02 };
    }
    if (key.includes('foam') || key.includes('rubber')) {
      return { color: currentTheme === 'dark' ? 0x1e1b2e : 0x3730a3, roughness: 0.9, metalness: 0.0 };
    }
    return { color: currentTheme === 'dark' ? 0x22252a : 0x64748b, roughness: 0.6, metalness: 0.1 };
  }

  const syncGridAndGround = () => {
    if (gridHelper) {
      disposeObject3D(gridHelper);
      gridHelper = null;
    }
    if (groundMesh) {
      disposeObject3D(groundMesh);
      groundMesh = null;
    }

    const size = Math.max(maxDimension * 20, 3.5);
    floorSize = size;
    const gridColor = theme === 'dark' ? 0x2e2c25 : 0xd8d5cc;
    gridHelper = new THREE.GridHelper(size, 80, gridColor, gridColor);
    gridHelper.rotation.x = Math.PI / 2;
    gridHelper.position.set(
      (boundsUnion.min[0] + boundsUnion.max[0]) / 2,
      (boundsUnion.min[1] + boundsUnion.max[1]) / 2,
      -0.0005,
    );
    gridHelper.userData.owned = true;
    scene.add(gridHelper);

    if (quality !== 'low') {
      const thickness = 0.04; // 4 cm solid 3D ground slab
      const slabGeom = new THREE.BoxGeometry(size * 2, size * 2, thickness);
      const props = getSurfaceMaterialProps(currentSurfaceMaterial, theme);
      const groundMat = new THREE.MeshStandardMaterial({
        color: new THREE.Color(props.color),
        roughness: props.roughness,
        metalness: props.metalness,
      });
      groundMat.userData.owned = true;
      groundMesh = new THREE.Mesh(slabGeom, groundMat);
      groundMesh.receiveShadow = true;
      // Position top face of slab at z = 0
      groundMesh.position.set(
        (boundsUnion.min[0] + boundsUnion.max[0]) / 2,
        (boundsUnion.min[1] + boundsUnion.max[1]) / 2,
        -thickness / 2,
      );
      groundMesh.userData.owned = true;
      scene.add(groundMesh);
    }
  };
  syncGridAndGround();

  // Model ∪ floor framing bounds: keeps the floor's outer edge (with a
  // generous margin) and the ground level inside the camera frustum, even
  // when a drop envelope sits entirely above the floor plane.
  const floorFramingBounds = (): { min: Vec3; max: Vec3 } => {
    let minX = boundsUnion.min[0];
    let minY = boundsUnion.min[1];
    let maxX = boundsUnion.max[0];
    let maxY = boundsUnion.max[1];
    let maxZ = boundsUnion.max[2] - boundsUnion.min[2];

    if (currentExplodeFactor > 0) {
      tempBox.makeEmpty();
      for (const child of objectsGroup.children) {
        tempBox.expandByObject(child);
      }
      if (!tempBox.isEmpty()) {
        minX = Math.min(minX, tempBox.min.x);
        minY = Math.min(minY, tempBox.min.y);
        maxX = Math.max(maxX, tempBox.max.x);
        maxY = Math.max(maxY, tempBox.max.y);
        maxZ = Math.max(maxZ, tempBox.max.z);
      }
    }

    const margin = Math.max(maxDimension * 0.25, 0.03);
    return {
      min: [minX - margin, minY - margin, -0.001],
      max: [maxX + margin, maxY + margin, Math.max(maxZ + margin, 0.05)],
    };
  };

  // Post processing
  let outlinePass: OutlinePass | null = null;
  const originalEmissiveMap = new WeakMap<THREE.MeshStandardMaterial, number>();

  const initComposer = () => {
    if (composer) {
      composer.dispose();
      composer = null;
      outlinePass = null;
    }

    if (quality !== 'low') {
      const parent = canvas.parentElement;
      const width = parent?.clientWidth || canvas.clientWidth || 300;
      const height = parent?.clientHeight || canvas.clientHeight || 150;

      composer = new EffectComposer(renderer);
      const renderPass = new RenderPass(scene, camera);
      composer.addPass(renderPass);

      outlinePass = new OutlinePass(
        new THREE.Vector2(width, height),
        scene,
        camera,
      );
      outlinePass.edgeStrength = 6.0;
      outlinePass.edgeGlow = 0.35;
      outlinePass.edgeThickness = 2.2;
      outlinePass.visibleEdgeColor.setHex(0xffffff);
      outlinePass.hiddenEdgeColor.setHex(0x0ea5e9);
      composer.addPass(outlinePass);

      const smaaPass = new SMAAPass(width, height);
      composer.addPass(smaaPass);
    }
  };
  initComposer();

  const applyVisibility = () => {
    for (const outer of objectsGroup.children) {
      const id = outer.userData.objectId;
      if (typeof id === 'string') {
        outer.visible = currentVisibility[id] ?? true;
      }
    }
    syncInstancedPose(instancedBatches, explodeByObjectId, currentVisibility);
  };

  // State variables
  let currentEntries: ObjectSceneEntry[] = [];
  let currentVisibility: Record<string, boolean> = {};
  let currentSelectionId: string | null = null;
  let currentDropSimulation: DropSimulationResult | null = null;
  /** Instanced batches for geometrically identical parts (see buildInstancedBatches). */
  let instancedBatches: InstancedBatchGroup | null = null;
  /** Per-object explode translation (objectId → offset), consumed by syncInstancedPose. */
  const explodeByObjectId = new Map<string, THREE.Vector3>();
  /**
   * Full-explode target offset per object, computed once per rebuild from
   * part AABBs (see computeExplodeOffsets). Factor-independent — the per-frame
   * apply step only scales these by an eased factor.
   */
  const explodeTargets = new Map<string, Vec3>();
  /** Per-part stagger (0..1) so parts bloom outward in distance order. */
  const explodeStagger = new Map<string, number>();
  /** Scratch matrix for composing explode translations onto instanced shells. */
  const scratchExplodeMatrix = new THREE.Matrix4();
  let dropTime = 0;
  let dropPlaying = false;
  let playbackSpeed = 1.0;
  let lastFrameTime: number | null = null;
  // Live Rapier simulation (null = fall back to sample playback).
  let rapierSim: RapierDropSim | null = null;
  let rapierBuildGeneration = 0;
  let rapierCurrentDrop = -1;
  let liveDropIndex = 0;
  let liveDropRestTime = 0;
  let lastLiveState: LiveBodyState | null = null;
  let populationFleet: PopulationFleetManager | null = null;
  let isFleetMode = false;
  let fleetTime = 0;
  // Single loop-duration constant: the render loop wraps fleetTime at this
  // value AND setFleetPlayback resets at it, so pressing Play after a loop
  // can never jump the timeline backward (the old 3.2 vs 3.0 mismatch).
  const FLEET_LOOP_S = 3.2;
  let fleetPlaying = false;

  // Telemetry debugger: live collector fed by the Rapier tap.
  let telemetryCollector: TelemetryCollector | null = null;
  let telemetryUnsub: (() => void) | null = null;

  // FEA material mode state: the current render mode, the per-vertex damage
  // field from the last result, the decorated-material cache, and the set of
  // decorated {material, uniforms} pairs updated every frame.
  let currentRenderMode: RenderMode = 'default';
  let currentFeaResult: FeaResult | null = null;
  const feaMaterialCache = new FeaMaterialCache();
  const feaDecorated: { material: THREE.Material; uniforms: FeaUniforms }[] = [];
  const feaFrameOpts = {
    mode: 'default' as RenderMode,
    impactWindow01: 1,
    impactWindowActive: true,
    time: 0,
    impactPulse: 0,
    dropTime: 0,
    impactTime: 0,
  };

  /**
   * First impact time of the drop simulation (seconds), or 0 when the
   * simulation carries no impact record. Mirrors the render-loop impact-time
   * fallback used by the dent window.
   */
  const feaImpactTimeFor = (simulation: DropSimulationResult | null): number => {
    if (!simulation) return 0;
    const impacts = simulation.impacts;
    if (impacts && impacts.length > 0 && typeof impacts[0]?.t_s === 'number') {
      return impacts[0].t_s;
    }
    const first = simulation.drops[0];
    if (first && typeof first.end_s === 'number' && first.end_s > 0) {
      return first.end_s * 0.38;
    }
    return 0;
  };

  /**
   * Bounding-box plate config for the object's first mesh (local frame):
   * x/y extents and center, a = max extent, b = min extent — the same
   * bbox->panel mapping the backend and applyFeaPlateField use.
   */
  const plateConfigForObject = (
    outer: THREE.Object3D,
    peakDamage: number,
  ): FeaPlateConfig | null => {
    const meshes = objectMeshesFor(outer);
    const position = meshes[0]?.geometry.getAttribute('position');
    if (!(position instanceof THREE.BufferAttribute)) return null;
    const values = position.array as Float32Array;
    const count = position.count;
    let xmin = Infinity;
    let xmax = -Infinity;
    let ymin = Infinity;
    let ymax = -Infinity;
    for (let i = 0; i < count; i += 1) {
      const x = values[i * 3];
      const y = values[i * 3 + 1];
      if (x < xmin) xmin = x;
      if (x > xmax) xmax = x;
      if (y < ymin) ymin = y;
      if (y > ymax) ymax = y;
    }
    const xExtent = xmax - xmin;
    const yExtent = ymax - ymin;
    if (!(xExtent > 0) || !(yExtent > 0)) return null;
    return {
      a: Math.max(xExtent, yExtent),
      b: Math.min(xExtent, yExtent),
      cx: (xmin + xmax) / 2,
      cy: (ymin + ymax) / 2,
      xExtent,
      yExtent,
      peakDamage,
    };
  };

  /**
   * Decorate every mesh with the FEA yield/heatmap material when the current
   * mode is fea/yield and a computed fea result exists; no-op in default
   * mode. Called after every rebuild so objects created by setObjects /
   * setTheme / setQuality stay in sync with the active mode.
   */
  const applyModeDecoration = (): void => {
    feaDecorated.length = 0;
    if (currentRenderMode === 'default' || !currentFeaResult?.computed) return;
    const fea = currentFeaResult;
    // Continuous plate-layer peak: min(1, shell peak / yield) — the field
    // maximum the contour is normalized against.
    const rawPeak =
      typeof fea.peak?.stress_pa === 'number' &&
      Number.isFinite(fea.peak.stress_pa) &&
      fea.peak.stress_pa > 0 &&
      typeof fea.yield_stress_pa === 'number' &&
      Number.isFinite(fea.yield_stress_pa) &&
      fea.yield_stress_pa > 0
        ? Math.min(1, fea.peak.stress_pa / fea.yield_stress_pa)
        : feaFieldMaxDamage(fea);
    const platePeakDamage = rawPeak > 0 ? rawPeak : 0.45;
    for (const outer of objectsGroup.children) {
      const objectId = outer.userData.objectId;
      if (typeof objectId !== 'string') continue;
      const field = fea.objects.find((o) => o.object_id === objectId) ?? null;
      const procedural = fea.procedural.find((p) => p.object_id === objectId) ?? null;
      // Per-object procedural uniforms: createFeaUniforms bakes procedural[0],
      // so feed it the object's own entry when one exists.
      // The continuous plate layer is baked from the object's first mesh
      // bounding box (local frame), mirroring the backend bbox->panel map.
      const plate = plateConfigForObject(outer, platePeakDamage);
      const uniforms = createFeaUniforms(
        procedural ? { ...fea, procedural: [procedural] } : fea,
        plate,
      );
      // Dent safety: cap the visual dent depth to 2% of the model so a
      // pathological backend compression can never push the mesh off-screen.
      const rawCompression = uniforms.uMaxCompression.value as number;
      uniforms.uMaxCompression.value =
        Number.isFinite(rawCompression) && rawCompression > 0
          ? Math.min(rawCompression, maxDimension * 0.02)
          : 0;
      for (const mesh of objectMeshesFor(outer)) {
        // Backend per-vertex field wins; primitives (and meshes whose field
        // did not apply) get the frontend plate-field fill so the heatmap
        // always covers the whole model.
        if (field) {
          if (!applyFeaObjectField(mesh, field)) applyFeaPlateField(mesh, fea);
        } else {
          applyFeaPlateField(mesh, fea);
        }
        const material = mesh.material as THREE.MeshStandardMaterial;
        if (!mesh.userData.baseMaterial) {
          mesh.userData.baseMaterial = material;
        }
        const decorated = feaMaterialCache.get(material, mesh.geometry, uniforms);
        if (decorated !== material) mesh.material = decorated;
        feaDecorated.push({ material: decorated, uniforms });
      }
    }
  };

  /**
   * Feed one live physics frame into the telemetry collector (no-op when the
   * debugger is not collecting).
   */
  const pushLiveTelemetry = (delta: number): void => {
    if (!telemetryCollector || !lastLiveState) return;
    const state = lastLiveState;
    const record: LiveTelemetryRecord = {
      t_s: dropTime,
      position_m: [state.position[0], state.position[1], state.position[2]],
      quaternion_xyzw: [state.quaternion[0], state.quaternion[1], state.quaternion[2], state.quaternion[3]],
      velocity_m_s: [state.linvel[0], state.linvel[1], state.linvel[2]],
      angular_velocity_rad_s: [state.angvel[0], state.angvel[1], state.angvel[2]],
      in_contact_window: telemetryCollectorContactWindow(),
      settled: liveDropRestTime > 0 || (liveDropIndex >= (currentDropSimulation?.drops.length ?? 0)),
    };
    telemetryCollector.push(record);
    void delta;
  };

  /**
   * Live Rapier playback scheduler: tracks body motion in 3D, allows natural
   * bouncing, rotational damping, and rolling until the model comes to a complete
   * rest on the floor. Once resting, waits the pause_between_drops setting (0.6s)
   * before starting the next drop.
   */
  const stepRapierPlayback = (delta: number): void => {
    if (!currentDropSimulation || !rapierSim) return;
    const drops = currentDropSimulation.drops;
    if (drops.length === 0) return;

    if (liveDropIndex >= drops.length) {
      lastLiveState = rapierSim.getState();
      return;
    }

    const active = drops[liveDropIndex];
    const model = currentDropSimulation.model;

    // Reset at the start of a new drop
    if (rapierCurrentDrop !== liveDropIndex) {
      rapierSim.reset(active, model);
      rapierCurrentDrop = liveDropIndex;
      liveDropRestTime = 0;
      lastLiveState = rapierSim.getState();
      return;
    }

    // Step the physics simulation
    lastLiveState = rapierSim.step(delta);
    pushLiveTelemetry(delta);

    // Follow the body axis & velocities: check if the model has stopped moving
    const resting = rapierSim.isResting();
    if (resting) {
      liveDropRestTime += delta;
      // Wait the pause between drops setting (0.6s) before advancing to the next drop
      if (liveDropRestTime >= 0.6) {
        if (liveDropIndex + 1 < drops.length) {
          liveDropIndex += 1;
          liveDropRestTime = 0;
          rapierSim.reset(drops[liveDropIndex], model);
          rapierCurrentDrop = liveDropIndex;
          lastLiveState = rapierSim.getState();
        } else {
          // All drops finished
          liveDropIndex = drops.length;
          dropPlaying = false;
          lastFrameTime = null;
          opts.onDropEnded?.();
        }
      }
    } else {
      liveDropRestTime = 0;
    }
  };

  /**
   * True while the live body is inside the FEA impact window (used to flag
   * contact in the telemetry stream). Mirrors the render-loop window logic.
   */
  const telemetryCollectorContactWindow = (): boolean => {
    if (lastLiveState && lastLiveState.position[2] < 0.05) return true;
    if (!currentDropSimulation) return false;
    for (const impact of currentDropSimulation.impacts ?? []) {
      const t = impact.t_s;
      if (Math.abs(dropTime - t) < 0.15) return true;
    }
    const windowS = currentFeaResult?.impact_window_s ?? 0.3;
    const impactTime = currentDropSimulation?.impacts?.[0]?.t_s ?? (currentDropSimulation?.drops[0]?.end_s ?? 1) * 0.38;
    return impactWindowProgress(dropTime, impactTime, windowS > 0 ? windowS : 0.3) > 0 && impactWindowProgress(dropTime, impactTime, windowS > 0 ? windowS : 0.3) < 1;
  };

  const scratchQuatA = new THREE.Quaternion();
  const scratchQuatB = new THREE.Quaternion();
  const scratchQuatOut = new THREE.Quaternion();

  const applyDropTransform = (): void => {
    if (!currentDropSimulation || currentDropSimulation.trajectory.length === 0) {
      // No simulation: position the model so its analytical bottom sits exactly
      // at the physics-world floor (z = 0). The objectsGroup origin is at the
      // model's mesh-frame origin, which is boundsUnion.min[2] below z = 0, so
      // lifting by -boundsUnion.min[2] brings the bottom flush with the floor.
      objectsGroup.position.set(0, 0, -boundsUnion.min[2]);
      objectsGroup.quaternion.identity();
      return;
    }
    // Live Rapier path: the pose is the physics body's transform.
    if (lastLiveState) {
      objectsGroup.position.set(
        lastLiveState.position[0],
        lastLiveState.position[1],
        lastLiveState.position[2],
      );
      objectsGroup.quaternion.set(
        lastLiveState.quaternion[0],
        lastLiveState.quaternion[1],
        lastLiveState.quaternion[2],
        lastLiveState.quaternion[3],
      );
      return;
    }
    const samples = currentDropSimulation.trajectory;
    const total = samples[samples.length - 1][0];
    const t = Math.min(dropTime, total);
    const resolved = resolveDropSample(t, samples);
    if (!resolved) return;
    const { a, b, alpha } = resolved;
    const posX = a[1] + (b[1] - a[1]) * alpha;
    const posY = a[2] + (b[2] - a[2]) * alpha;
    const posZ = a[3] + (b[3] - a[3]) * alpha;
    // 1:1 Faithful representation of backend physics trajectory with zero GC allocations
    // The backend writes quaternions in (w, x, y, z) order; THREE.Quaternion constructor takes (x, y, z, w).
    scratchQuatA.set(a[5], a[6], a[7], a[4]);
    scratchQuatB.set(b[5], b[6], b[7], b[4]);
    scratchQuatOut.slerpQuaternions(scratchQuatA, scratchQuatB, alpha);

    objectsGroup.position.set(posX, posY, posZ);
    objectsGroup.quaternion.copy(scratchQuatOut);
  };

  const dropTrajectoryBounds = (simulation: DropSimulationResult): { min: Vec3; max: Vec3 } => {
    const min = [Infinity, Infinity, Infinity] as Vec3;
    const max = [-Infinity, -Infinity, -Infinity] as Vec3;
    for (const sample of simulation.trajectory) {
      for (let axis = 0; axis < 3; axis += 1) {
        const value = sample[axis + 1];
        if (value < min[axis]) min[axis] = value;
        if (value > max[axis]) max[axis] = value;
      }
    }
    return { min, max };
  };


  let currentExplodeFactor = 0;
  let targetExplodeFactor = 0;
  let currentIdSignature = '';
  let currentEntriesSignature: string | null = null;
  let disposed = false;
  let assetLoadGeneration = 0;
  const gltfLoader = new GLTFLoader();

  const overlayLayer = createOverlayLayer(scene, { planeRadius: maxDimension / 2 });

  const markAssetResourcesOwned = (root: THREE.Object3D): void => {
    root.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh || !mesh.material) return;
      const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      for (const material of materials) material.userData.owned = true;
    });
  };

  const loadDisplayAsset = (
    entry: ObjectSceneEntry,
    target: THREE.Group,
    outer: THREE.Group,
    generation: number,
  ): void => {
    if (!entry.displayAssetUrl) return;
    gltfLoader.load(
      entry.displayAssetUrl,
      (gltf) => {
        if (disposed || generation !== assetLoadGeneration || outer.parent !== objectsGroup) {
          disposeObject3D(gltf.scene);
          return;
        }
        markAssetResourcesOwned(gltf.scene);
        // The OCCT glTF converter preserves the STEP Z-up frame. The source
        // assembly is authored from its underside, while this scene presents
        // the engineering top view by default; flip the complete asset once,
        // without mutating individual assembly placements. The analysis-mesh
        // bounds bake this flip into the vertices (x, -y, -z), so the display
        // transform must be pivot-correct: when the GLB root carries its own
        // translation, the baked and displayed frames still agree only if the
        // translation is flipped along the same axes.
        gltf.scene.rotation.x = Math.PI;
        gltf.scene.position.set(gltf.scene.position.x, -gltf.scene.position.y, -gltf.scene.position.z);
        target.add(gltf.scene);
        const meshes: THREE.Mesh[] = [];
        gltf.scene.traverse((object) => {
          const mesh = object as THREE.Mesh;
          if (mesh.isMesh) meshes.push(mesh);
        });
        target.userData.meshObjects = meshes;
        applySelection();
        applyModeDecoration();
      },
      undefined,
      () => {
        if (disposed || generation !== assetLoadGeneration || outer.parent !== objectsGroup) return;
        // Keep a usable fallback if a cached GLB is unavailable. This path is
        // intentionally only a transport failure fallback, never a STEP
        // parsing fallback.
        const fallback = createObjectGroup(
          { ...entry, displayAssetUrl: null },
          { quality: quality === 'ultra' ? 'high' : quality, materials: palette.getAll() },
        );
        target.add(fallback);
        target.userData.meshObjects = fallback.userData.meshObjects ?? [];
        applySelection();
        applyModeDecoration();
      },
    );
  };

  const rebuildObjects = () => {
    // Keep the ground slab material in sync with the configured test surface
    // (floor look follows the drop-test surface selection).
    currentSurfaceMaterial = floorSurfaceKey ?? 'concrete';
    const generation = assetLoadGeneration;
    // Clear old objects.  FEA-decorated clones are cached per old geometry:
    // drop the cache first so the stale clones are released with their
    // geometries instead of accumulating GPU programs on every rebuild.
    feaMaterialCache.clear();
    const oldChildren = [...objectsGroup.children];
    for (const child of oldChildren) {
      disposeObjectGroup(child);
    }
    if (instancedBatches) {
      for (const child of [...instancedBatches.group.children]) {
        disposeObjectGroup(child);
      }
      instancedBatches = null;
    }
    explodeByObjectId.clear();
    explodeTargets.clear();
    explodeStagger.clear();

    // Instancing is mutually exclusive with FEA decoration: per-vertex
    // attributes (aDamage/aDisplacement) are per-geometry, not per-instance,
    // so batching parts that receive per-object FEA fields would show the
    // first object's field on every copy. Batches are rebuilt when the mode
    // or result crosses the FEA boundary.
    const allowInstancing = currentRenderMode === 'default' && !currentFeaResult?.computed;
    const instancedParts: { entry: ObjectSceneEntry; matrix: THREE.Matrix4 }[] = [];
    const batchMatrix = new THREE.Matrix4();

    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;

    let hasFiniteBounds = false;

    // Part AABBs (analytical, from entry geometry — no scene traversal) feed
    // both the assembly bounds and the explode targets.
    const explodeParts: ExplodePart[] = [];

    for (const entry of currentEntries) {
      const outerGroup = new THREE.Group();
      outerGroup.userData.objectId = entry.id;
      outerGroup.matrixAutoUpdate = true;

      const innerGroup = createObjectGroup(entry, {
        quality: quality === 'ultra' ? 'high' : quality,
        materials: palette.getAll(),
      });
      outerGroup.add(innerGroup);

      const bounds = worldBoundsForGeometry(entry.geometry);
      if (finiteBounds(bounds)) {
        hasFiniteBounds = true;
        minX = Math.min(minX, bounds.min[0]);
        minY = Math.min(minY, bounds.min[1]);
        minZ = Math.min(minZ, bounds.min[2]);
        maxX = Math.max(maxX, bounds.max[0]);
        maxY = Math.max(maxY, bounds.max[1]);
        maxZ = Math.max(maxZ, bounds.max[2]);
        explodeParts.push({
          id: entry.id,
          center: [
            (bounds.min[0] + bounds.max[0]) / 2,
            (bounds.min[1] + bounds.max[1]) / 2,
            (bounds.min[2] + bounds.max[2]) / 2,
          ],
          size: [
            Math.max(bounds.max[0] - bounds.min[0], 1e-6),
            Math.max(bounds.max[1] - bounds.min[1], 1e-6),
            Math.max(bounds.max[2] - bounds.min[2], 1e-6),
          ],
        });
      }

      // Batch the part instead of rendering it as its own mesh when it is
      // eligible: no GLB asset, no CAD color, no compound, geometry renders,
      // and FEA is inactive. The inner group's baked transform becomes the
      // per-instance matrix; the outer group stays as the identity transform
      // shell so explode/selection bookkeeping is unchanged.
      if (
        allowInstancing &&
        !entry.displayAssetUrl &&
        !entry.color &&
        entry.geometry.type !== 'compound'
      ) {
        batchMatrix.copy(innerGroup.matrix);
        instancedParts.push({ entry, matrix: batchMatrix });
        continue;
      }

      objectsGroup.add(outerGroup);
      loadDisplayAsset(entry, innerGroup, outerGroup, generation);
    }

    if (instancedParts.length > 0) {
      instancedBatches = buildInstancedBatches(instancedParts, {
        quality: quality === 'ultra' ? 'high' : quality,
        materials: palette.getAll(),
      });
      objectsGroup.add(instancedBatches.group);
      // Instanced parts keep an invisible outer shell so applyExplode's
      // bounds and the picker's id walk keep working.
      for (const { entry, matrix } of instancedParts) {
        const shell = new THREE.Group();
        shell.userData.objectId = entry.id;
        shell.userData.instanced = true;
        shell.matrixAutoUpdate = false;
        shell.matrix.copy(matrix);
        // Keep the baked transform around so applyExplode can compose the
        // explode translation on top without losing the part's placement.
        shell.userData.bakedMatrix = matrix.clone();
        shell.visible = true;
        objectsGroup.add(shell);
      }
    }

    applyVisibility();

    if (hasFiniteBounds) {
      boundsUnion = { min: [minX, minY, minZ], max: [maxX, maxY, maxZ] };
      const dx = maxX - minX;
      const dy = maxY - minY;
      const dz = maxZ - minZ;
      const dimension = Math.max(dx, dy, dz, 0.05);
      maxDimension = Number.isFinite(dimension) ? dimension : 0.1;
    } else {
      boundsUnion = { min: [-0.05, -0.05, -0.05], max: [0.05, 0.05, 0.05] };
      maxDimension = 0.1;
    }

    // Build the full-explode targets once from the analytical AABBs. The
    // targets are factor-independent; the per-frame apply step scales them by
    // an eased factor.
    if (explodeParts.length > 0) {
      const assemblyCenter: Vec3 = [
        (boundsUnion.min[0] + boundsUnion.max[0]) / 2,
        (boundsUnion.min[1] + boundsUnion.max[1]) / 2,
        (boundsUnion.min[2] + boundsUnion.max[2]) / 2,
      ];
      const computed = computeExplodeOffsets(explodeParts, assemblyCenter, maxDimension);
      // All parts rise together with a single smooth ease — no per-part
      // stagger, so the explode reads as one coherent unstack instead of a
      // chaotic wave.
      for (const part of explodeParts) {
        const target = computed.get(part.id);
        if (target) explodeTargets.set(part.id, target);
        explodeStagger.set(part.id, 0);
      }
    }

    syncGridAndGround();
    overlayLayer.setPlaneRadius(maxDimension / 2);
    overlayLayer.setContactPlaneRadius(floorSize / 2);
    applyExplode();
    applySelection();
    applyModeDecoration();
  };

  const tempBox = new THREE.Box3();

  const applyExplode = () => {
    const defaultBaseZ = -boundsUnion.min[2];
    objectsGroup.position.set(0, 0, defaultBaseZ);

    if (currentExplodeFactor <= 0) {
      for (const outer of objectsGroup.children) {
        if (typeof outer.userData?.objectId !== 'string') continue;
        if (outer.userData.instanced) {
          const baked = outer.userData.bakedMatrix as THREE.Matrix4 | undefined;
          if (baked) outer.matrix.copy(baked);
        } else {
          outer.position.set(0, 0, 0);
        }
      }
      explodeByObjectId.clear();
      syncInstancedPose(instancedBatches, explodeByObjectId, currentVisibility);
      return;
    }

    explodeByObjectId.clear();
    for (const outer of objectsGroup.children) {
      const id = outer.userData?.objectId as string | undefined;
      if (typeof id !== 'string') continue;
      const target = explodeTargets.get(id);
      if (!target) continue;

      // Per-part stagger so parts bloom outward in distance order (inner parts
      // leave first, outer parts follow).
      const stagger = explodeStagger.get(id) ?? 0;
      const local01 =
        stagger > 0.999 ? 1 : Math.min(1, Math.max(0, (currentExplodeFactor - stagger) / (1 - stagger)));
      const eased = local01 * local01 * (3 - 2 * local01);

      const tx = target[0] * eased;
      const ty = target[1] * eased;
      const tz = target[2] * eased;

      if (outer.userData.instanced) {
        // The shell carries the baked part transform in `matrix`
        // (matrixAutoUpdate=false), so compose the explode translation on top
        // of the baked matrix — position alone is ignored when
        // matrixAutoUpdate is false. syncInstancedPose adds the map value to
        // the instance's baked position, so the map stores the offset only.
        const baked = outer.userData.bakedMatrix as THREE.Matrix4 | undefined;
        if (baked) {
          const composed = scratchExplodeMatrix.copy(baked);
          composed.elements[12] += tx;
          composed.elements[13] += ty;
          composed.elements[14] += tz;
          outer.matrix.copy(composed);
        }
        explodeByObjectId.set(id, new THREE.Vector3(tx, ty, tz));
      } else {
        outer.position.set(tx, ty, tz);
      }
    }
    syncInstancedPose(instancedBatches, explodeByObjectId, currentVisibility);
  };

  const selectedMeshesCache: THREE.Mesh[] = [];

  const applySelection = () => {
    selectedMeshesCache.length = 0;

    // Clear previous selection highlight overlay
    while (selectionHighlightGroup.children.length > 0) {
      const child = selectionHighlightGroup.children[0];
      selectionHighlightGroup.remove(child);
      const childMesh = child as THREE.Mesh;
      if (childMesh.geometry) childMesh.geometry.dispose();
      if (childMesh.material) {
        if (Array.isArray(childMesh.material)) {
          childMesh.material.forEach((m) => m.dispose());
        } else {
          childMesh.material.dispose();
        }
      }
    }

    objectsGroup.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (mesh.isMesh && mesh.material) {
        const mat = mesh.material as THREE.MeshStandardMaterial;
        // Restore low quality emissive
        if (originalEmissiveMap.has(mat)) {
          mat.emissive.setHex(originalEmissiveMap.get(mat)!);
        }
      }
      // Instanced batches must never join the highlight/outline path: the
      // whole batch would be outlined. Their per-part shells carry the id.
      if (obj.userData?.instanced) return;

      let curr: THREE.Object3D | null = obj;
      let objId: string | null = null;
      while (curr) {
        if (typeof curr.userData?.objectId === 'string') {
          objId = curr.userData.objectId;
          break;
        }
        curr = curr.parent;
      }

      if (objId && objId === currentSelectionId && mesh.isMesh) {
        selectedMeshesCache.push(mesh);
        if (quality === 'low' && mesh.material) {
          const mat = mesh.material as THREE.MeshStandardMaterial;
          if (!originalEmissiveMap.has(mat)) {
            originalEmissiveMap.set(mat, mat.emissive.getHex());
          }
          mat.emissive.setHex(0x38bdf8);
        }
      }
    });

    if (currentSelectionId && selectedMeshesCache.length > 0) {
      for (const mesh of selectedMeshesCache) {
        if (!mesh.geometry) continue;

        // 1. Crisp CAD Edge Silhouette (renders on top of everything with depthTest: false)
        try {
          const edgesGeom = new THREE.EdgesGeometry(mesh.geometry, 28);
          const edgesMat = new THREE.LineBasicMaterial({
            color: 0xffffff,
            transparent: true,
            opacity: 0.95,
            depthTest: false,
            depthWrite: false,
          });
          const lineSegments = new THREE.LineSegments(edgesGeom, edgesMat);
          lineSegments.renderOrder = 999;
          mesh.getWorldPosition(lineSegments.position);
          mesh.getWorldQuaternion(lineSegments.quaternion);
          mesh.getWorldScale(lineSegments.scale);
          selectionHighlightGroup.add(lineSegments);
        } catch {
          // ignore degenerate geometry
        }

        // 2. Subtle X-Ray Ghost Silhouette (glows through occluding shells)
        try {
          const xrayMat = new THREE.MeshBasicMaterial({
            color: 0x38bdf8,
            transparent: true,
            opacity: 0.25,
            depthTest: false,
            depthWrite: false,
            side: THREE.DoubleSide,
          });
          const xrayMesh = new THREE.Mesh(mesh.geometry, xrayMat);
          xrayMesh.renderOrder = 998;
          mesh.getWorldPosition(xrayMesh.position);
          mesh.getWorldQuaternion(xrayMesh.quaternion);
          mesh.getWorldScale(xrayMesh.scale);
          selectionHighlightGroup.add(xrayMesh);
        } catch {
          // ignore
        }
      }
    }

    if (outlinePass) {
      outlinePass.selectedObjects = selectedMeshesCache;
    }
  };

  let currentInsets = { left: 0, right: 0, top: 0, bottom: 0 };

  const applyCameraViewOffset = (width: number, height: number) => {
    const offsetX = ((currentInsets.left || 0) - (currentInsets.right || 0)) / 2;
    const offsetY = ((currentInsets.top || 0) - (currentInsets.bottom || 0)) / 2;

    if (Math.abs(offsetX) > 0.5 || Math.abs(offsetY) > 0.5) {
      camera.setViewOffset(width, height, -offsetX, -offsetY, width, height);
    } else if (camera.view && camera.view.enabled) {
      camera.clearViewOffset();
    }
  };

  // Resize handler
  const resize = () => {
    const parent = canvas.parentElement;
    const width = parent?.clientWidth || canvas.clientWidth || 300;
    const height = parent?.clientHeight || canvas.clientHeight || 150;

    if (width === 0 || height === 0) return;

    camera.aspect = width / height;
    applyCameraViewOffset(width, height);
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
    composer?.setSize(width, height);
  };

  const resizeObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => resize());
  if (resizeObserver && canvas.parentElement) {
    resizeObserver.observe(canvas.parentElement);
  }
  resize();
  onContextRestored = () => {
    updatePixelRatio();
    rebuildObjects();
    resize();
  };

  // Picker
  const picker = createPicker(canvas, camera, objectsGroup, {
    onClick: opts.onPick,
  });

  // Render loop
  let animationFrameId: number | null = null;
  let frameCount = 0;

  const renderLoop = () => {
    if (disposed) return;
    animationFrameId = requestAnimationFrame(renderLoop);
    controls.update();

    // Drive the model along the simulated drop trajectory.  When the live
    // Rapier simulation is active, the pose comes from real rigid-body
    // physics stepped per frame; otherwise it falls back to the backend
    // sample playback (lerp/slerp).  Advancing in wall-clock time keeps the
    // playback rate real-time on any display refresh rate.
    if (populationFleet && isFleetMode) {
      const now = performance.now();
      if (lastFrameTime === null) lastFrameTime = now;
      const delta = Math.min(0.1, (now - lastFrameTime) / 1000);
      lastFrameTime = now;
      if (fleetPlaying) {
        fleetTime += delta;
        if (fleetTime >= FLEET_LOOP_S) {
          fleetTime = 0;
        }
      }
      populationFleet.update(fleetTime);
    } else if (currentDropSimulation && dropPlaying) {
      const now = performance.now();
      if (lastFrameTime === null) lastFrameTime = now;
      const rawDelta = Math.min(0.1, (now - lastFrameTime) / 1000);
      const delta = rawDelta * playbackSpeed;
      lastFrameTime = now;
      dropTime += delta;
      if (rapierSim) {
        stepRapierPlayback(delta);
      } else {
        const samples = currentDropSimulation.trajectory;
        const total = samples.length > 0 ? samples[samples.length - 1][0] : 0;
        if (dropTime >= total) {
          dropPlaying = false;
          lastFrameTime = null;
          opts.onDropEnded?.();
        }
      }
    } else {
      lastFrameTime = null;
    }
    // Smoothly animate the exploded-view transition. The ease is driven by
    // wall-clock delta so the rate is identical at any refresh rate (a fixed
    // per-frame factor would explode twice as fast on 120 Hz displays).
    if (Math.abs(targetExplodeFactor - currentExplodeFactor) > 0.0005) {
      const now = performance.now();
      const delta = lastFrameTime === null ? 0.016 : Math.min(0.1, (now - lastFrameTime) / 1000);
      const k = 1 - Math.exp(-10 * delta);
      currentExplodeFactor += (targetExplodeFactor - currentExplodeFactor) * k;
      applyExplode();
    } else if (currentExplodeFactor !== targetExplodeFactor) {
      currentExplodeFactor = targetExplodeFactor;
      applyExplode();
    }

    applyDropTransform();

    // FEA dent window: the localized plastic displacement ramps in over the
    // 300 ms impact window starting at the first impact, then stays fully
    // deployed (persistent dent). Before the first impact the window is 0
    // (no dent); without a drop simulation the dent is static.
    if (currentRenderMode !== 'default' && feaDecorated.length > 0) {
      const windowS = currentFeaResult?.impact_window_s ?? 0.3;
      let window01 = 1;
      let active = false;
      let impactPulse = 0;
      let impactTime = 0;
      if (currentDropSimulation) {
        const total = currentDropSimulation.trajectory[currentDropSimulation.trajectory.length - 1]?.[0] ?? 1.5;
        if (dropPlaying || dropTime < total) {
          active = true;
          const impactTimeCandidates = currentDropSimulation.impacts?.[0]?.t_s;
          impactTime =
            typeof impactTimeCandidates === 'number'
              ? impactTimeCandidates
              : feaImpactTimeFor(currentDropSimulation);
          window01 = impactWindowProgress(dropTime, impactTime, windowS > 0 ? windowS : 0.3);
          impactPulse = impactPulseFor(dropTime, impactTime, windowS > 0 ? windowS : 0.3);
        }
      }
      feaFrameOpts.mode = currentRenderMode;
      feaFrameOpts.impactWindow01 = window01;
      feaFrameOpts.impactWindowActive = active;
      feaFrameOpts.impactPulse = impactPulse;
      feaFrameOpts.dropTime = dropTime;
      feaFrameOpts.impactTime = impactTime;
      feaFrameOpts.time = currentDropSimulation && !dropPlaying ? 0 : performance.now() / 1000;
      for (const pair of feaDecorated) {
        updateFeaUniforms(pair.uniforms, feaFrameOpts);
      }
    }

    // Keep selection highlight overlay transforms in sync during camera motion, explode, and drop
    if (currentSelectionId && selectionHighlightGroup.children.length > 0 && selectedMeshesCache.length > 0) {
      let childIdx = 0;
      for (const mesh of selectedMeshesCache) {
        if (childIdx < selectionHighlightGroup.children.length) {
          const lineSeg = selectionHighlightGroup.children[childIdx];
          mesh.getWorldPosition(lineSeg.position);
          mesh.getWorldQuaternion(lineSeg.quaternion);
          mesh.getWorldScale(lineSeg.scale);
        }
        if (childIdx + 1 < selectionHighlightGroup.children.length) {
          const xrayMesh = selectionHighlightGroup.children[childIdx + 1];
          mesh.getWorldPosition(xrayMesh.position);
          mesh.getWorldQuaternion(xrayMesh.quaternion);
          mesh.getWorldScale(xrayMesh.scale);
        }
        childIdx += 2;
      }
    }

    if (composer) {
      composer.render();
    } else {
      renderer.render(scene, camera);
    }

    frameCount++;
    if (frameCount % 30 === 0 && opts.onStats) {
      opts.onStats({
        drawCalls: renderer.info.render.calls,
        triangles: renderer.info.render.triangles,
        geometries: renderer.info.memory.geometries,
        textures: renderer.info.memory.textures,
        tier: quality,
      });
    }
  };
  animationFrameId = requestAnimationFrame(renderLoop);

  return {
    setObjects(entries: ObjectSceneEntry[]) {
      if (disposed) return;
      currentEntries = entries;
      const signature = entriesSignature(entries);
      if (currentEntriesSignature === signature) {
        applyVisibility();
        return;
      }

      const idSignature = idsSignature(entries);
      const first = currentEntriesSignature === null;
      currentEntriesSignature = signature;
      rebuildObjects();
      applyVisibility();

      if (first || idSignature !== currentIdSignature) {
        currentIdSignature = idSignature;
        // Lower camera elevation on model load so the model sits closer to
        // the bottom of the frame.
        fitCameraToBounds(camera, controls, floorFramingBounds(), 0.28);
      }
    },

    setVisibility(visibility: Record<string, boolean>) {
      if (disposed) return;
      currentVisibility = visibility;
      applyVisibility();
    },

    setSelection(id: string | null) {
      if (disposed) return;
      currentSelectionId = id;
      applySelection();
    },

    setExplode(factor: number) {
      // The exploded view is mutually exclusive with the drop playback
      // (the trajectory owns objectsGroup): ignore explode requests while a
      // simulation is loaded so the static rest pose can never be fought
      // by the explode spring.
      if (disposed || currentDropSimulation) return;
      targetExplodeFactor = Math.min(1, Math.max(0, factor));
    },
    setTheme(newTheme: 'light' | 'dark') {
      if (disposed || newTheme === theme) return;
      theme = newTheme;
      const oldPalette = palette;
      palette = new MaterialPalette(theme);
      scene.background = new THREE.Color(theme === 'dark' ? 0x121212 : 0xf7f7f4);
      rebuildObjects();
      oldPalette.dispose();
    },

    setQuality(newQuality: QualityTier) {
      if (disposed || newQuality === quality) return;
      quality = newQuality;
      updatePixelRatio();
      renderer.shadowMap.enabled = quality !== 'low';
      keyLight.castShadow = quality !== 'low';
      keyLight.shadow.mapSize.width = quality === 'ultra' ? 2048 : quality === 'high' ? 1024 : 512;
      keyLight.shadow.mapSize.height = quality === 'ultra' ? 2048 : quality === 'high' ? 1024 : 512;
      initComposer();
      rebuildObjects();
    },

    setFloorMaterial(surfaceKey: string | null | undefined) {
      if (disposed) return;
      const next = (surfaceKey || 'concrete').toLowerCase();
      if (next === currentSurfaceMaterial) return;
      floorSurfaceKey = next;
      currentSurfaceMaterial = next;
      if (groundMesh) {
        // Dispose the previous slab material — setFloorMaterial is called on
        // every surface change and used to leak one GPU program per swap.
        const previous = groundMesh.material;
        const props = getSurfaceMaterialProps(next, theme);
        groundMesh.material = new THREE.MeshStandardMaterial({
          color: new THREE.Color(props.color),
          roughness: props.roughness,
          metalness: props.metalness,
        });
        (groundMesh.material as THREE.MeshStandardMaterial).userData.owned = true;
        const previousMat = Array.isArray(previous) ? previous[0] : previous;
        if (previousMat && (previousMat as unknown as { userData?: { owned?: boolean } }).userData?.owned) {
          previousMat.dispose();
        }
      }
    },

    setOverlays(spec: OverlaySpec | null) {
      if (disposed) return;
      overlayLayer.apply(spec);
    },

    clearOverlays() {
      if (disposed) return;
      overlayLayer.clear();
    },

    setDropSimulation(simulation: DropSimulationResult | null) {
      if (disposed) return;
      // Rebuild the telemetry collector for the new simulation (or tear down).
      if (telemetryUnsub) {
        telemetryUnsub();
        telemetryUnsub = null;
      }
      if (simulation) {
        telemetryCollector = new TelemetryCollector({
          result: simulation,
          fea: currentFeaResult,
          capacity: 8192,
          floorZ: 0,
        });
      } else {
        telemetryCollector = null;
      }
      currentDropSimulation = simulation;
      dropTime = 0;
      liveDropIndex = 0;
      liveDropRestTime = 0;
      // Do not start playing until Rapier simulation is loaded and initialized at t=0
      dropPlaying = false;
      lastFrameTime = null;
      lastLiveState = null;
      rapierCurrentDrop = -1;
      // Tear down any previous live sim; build the new one asynchronously
      // (Rapier WASM init is async). Generation guards against a newer
      // simulation arriving while init is in flight.
      rapierBuildGeneration += 1;
      const generation = rapierBuildGeneration;
      if (rapierSim) {
        rapierSim.dispose();
        rapierSim = null;
      }
      if (simulation) {
        buildRapierDropSim(simulation.model, currentEntries, simulation.drops)
          .then((built) => {
            if (disposed || generation !== rapierBuildGeneration) {
              built?.dispose();
              return;
            }
            rapierSim = built;
            liveDropIndex = 0;
            liveDropRestTime = 0;
            if (built) {
              telemetryUnsub = built.onTelemetry(() => {
                // The collector consumes interpolated live state; per-frame
                // callback is wired but the collector is driven by
                // pushLiveTelemetry so this stays a no-op placeholder.
              });
              console.info('[Drop Physics] Live Rapier.js 3D rigid-body simulation active.');
              // Pose the model at the first drop's release immediately.
              const first = simulation.drops[0];
              if (first) {
                built.reset(first, simulation.model);
                rapierCurrentDrop = 0;
                lastLiveState = built.getState();
                applyDropTransform();
              }
            } else {
              console.warn('[Drop Physics] Rapier build unavailable, using telemetry sample playback.');
              rapierCurrentDrop = -1;
            }
            lastFrameTime = performance.now();
            dropPlaying = true;
          })
          .catch((err) => {
            console.warn('[Drop Physics] Rapier initialization error, using telemetry sample playback:', err);
            lastFrameTime = performance.now();
            dropPlaying = true;
          });
      }
      if (simulation) {
        // Guarantee model is assembled (unexploded) during drop simulation physics
        currentExplodeFactor = 0;
        targetExplodeFactor = 0;
        applyExplode();
      }
      applyDropTransform();
      if (simulation) {
        // Frame the drop envelope centered on the model and impact area
        // so the model remains centered in the viewport throughout fall and impact.
        const trajectory = dropTrajectoryBounds(simulation);
        const modelBounds = floorFramingBounds();
        const cx = (modelBounds.min[0] + modelBounds.max[0]) / 2;
        const cy = (modelBounds.min[1] + modelBounds.max[1]) / 2;
        const maxH = Math.max(trajectory.max[2], modelBounds.max[2]);
        const dx = (modelBounds.max[0] - modelBounds.min[0]) * 1.4;
        const dy = (modelBounds.max[1] - modelBounds.min[1]) * 1.4;
        const dz = maxH;
        const span = Math.max(dx, dy, dz);
        fitCameraToBounds(camera, controls, {
          min: [cx - span / 2, cy - span / 2, 0],
          max: [cx + span / 2, cy + span / 2, span],
        });
      } else {
        // Reset camera back to standard CAD model framing when leaving test mode
        fitCameraToBounds(camera, controls, floorFramingBounds(), 0.28);
      }
    },

    setDropPlayback(playing: boolean) {
      if (disposed) return;
      if (!playing || currentDropSimulation === null) {
        dropPlaying = false;
        lastFrameTime = null;
        return;
      }
      if (liveDropIndex >= currentDropSimulation.drops.length) {
        liveDropIndex = 0;
        liveDropRestTime = 0;
        dropTime = 0;
        if (rapierSim && currentDropSimulation.drops[0]) {
          rapierSim.reset(currentDropSimulation.drops[0], currentDropSimulation.model);
          rapierCurrentDrop = 0;
          lastLiveState = rapierSim.getState();
        }
      }
      lastFrameTime = performance.now();
      dropPlaying = true;
    },

    restartDropPlayback() {
      if (disposed) return;
      liveDropIndex = 0;
      liveDropRestTime = 0;
      dropTime = 0;
      if (rapierSim && currentDropSimulation?.drops[0]) {
        rapierSim.reset(currentDropSimulation.drops[0], currentDropSimulation.model);
        rapierCurrentDrop = 0;
        lastLiveState = rapierSim.getState();
      } else {
        rapierCurrentDrop = -1;
      }
      lastFrameTime = performance.now();
      dropPlaying = currentDropSimulation !== null;
      applyDropTransform();
    },

    setPlaybackSpeed(speed: number) {
      if (disposed) return;
      playbackSpeed = Math.max(0.1, Math.min(100, speed));
    },

    /**
     * Seek the live drop playback to an absolute time within the current
     * simulation. The Rapier body cannot be rewound, so we reset to the
     * first drop and fast-forward with bounded substeps, then pose the model
     * at the target drop state.
     */
    seekDropTime(target: number) {
      if (disposed || !currentDropSimulation) return;
      if (!(target > 0) || !rapierSim) {
        if (!(target > 0) && currentDropSimulation && rapierSim) {
          // Rewind to the first drop.
          rapierSim.reset(currentDropSimulation.drops[0], currentDropSimulation.model);
          rapierCurrentDrop = 0;
          dropTime = 0;
          liveDropIndex = 0;
          liveDropRestTime = 0;
          lastLiveState = rapierSim.getState();
          applyDropTransform();
          lastFrameTime = performance.now();
        }
        return;
      }
      const sim = currentDropSimulation;
      dropTime = target;
      liveDropIndex = 0;
      liveDropRestTime = 0;
      rapierCurrentDrop = -1;
      let remaining = target;
      let guard = 0;
      while (liveDropIndex < sim.drops.length && guard < 512) {
        const drop = sim.drops[liveDropIndex];
        rapierSim.reset(drop, sim.model);
        rapierCurrentDrop = liveDropIndex;
        const span = Math.max(0.0001, (drop.end_s ?? 1) - (drop.start_s ?? 0));
        const toConsume = Math.min(remaining, span);
        // Fixed 1/240 s substeps with the 0.1 s per-call clamp; loop until
        // consumed (bounded by MAX_SUBSTEPS * 512 guard).
        let consumed = 0;
        while (consumed < toConsume && guard < 4096) {
          const dt = Math.min(0.1, toConsume - consumed);
          rapierSim.step(dt);
          consumed += dt;
          guard += 1;
        }
        remaining -= toConsume;
        liveDropIndex += 1;
        if (remaining <= 0.0001) break;
      }
      lastLiveState = rapierSim.getState();
      applyDropTransform();
      lastFrameTime = performance.now();
    },

    getDropTime() {
      return dropTime;
    },

    getLiveDropIndex() {
      return liveDropIndex;
    },

    getLiveFrame() {
      return lastLiveState;
    },

    getTelemetryFrames() {
      return telemetryCollector ? telemetryCollector.frames() : [];
    },

    getTelemetryEvents() {
      return telemetryCollector ? telemetryCollector.eventsList : [];
    },

    clearTelemetry() {
      telemetryCollector?.clear();
    },

    buildTelemetrySession() {
      if (!telemetryCollector || !currentDropSimulation) return null;
      return buildTelemetrySession(currentDropSimulation, telemetryCollector.frames());
    },

    setRenderMode(mode: RenderMode) {
      if (disposed || mode === currentRenderMode) return;
      currentRenderMode = mode;
      if (mode === 'default') {
        for (const pair of feaDecorated) {
          pair.uniforms.uFeaMode.value = -1;
        }
        for (const outer of objectsGroup.children) {
          for (const mesh of objectMeshesFor(outer)) {
            if (mesh.userData.baseMaterial) {
              mesh.material = mesh.userData.baseMaterial;
            }
          }
        }
        feaDecorated.length = 0;
        // Leaving FEA mode re-enables instancing: rebuild so the batches
        // replace the per-part meshes.
        if (currentFeaResult?.computed) {
          rebuildObjects();
        }
      } else {
        if (feaDecorated.length > 0) {
          const targetVal = mode === 'yield' ? 1 : 0;
          for (const pair of feaDecorated) {
            pair.uniforms.uFeaMode.value = targetVal;
          }
        } else {
          // Entering FEA mode disables instancing: rebuild first so every
          // part exists as an individual mesh to decorate.
          if (instancedBatches) {
            rebuildObjects();
            return;
          }
          applyModeDecoration();
        }
      }
    },

    setFeaResult(fea: FeaResult | null) {
      if (disposed) return;
      currentFeaResult = fea;
      feaMaterialCache.clear();
      if (currentRenderMode !== 'default') {
        // A new FEA result may flip the batching boundary (a null result
        // re-enables instancing; a computed result disables it). Rebuild so
        // the mesh/batch layout matches the decoration pass.
        rebuildObjects();
      }
    },

    setPopulationFleet(population: PopulationResult | null, fleetMode: boolean) {
      if (disposed) return;
      isFleetMode = fleetMode && population !== null;
      if (populationFleet) {
        populationFleet.dispose();
        populationFleet = null;
      }
      if (population && isFleetMode && objectsGroup.children.length > 0) {
        const defaultBaseZ = -boundsUnion.min[2];
        populationFleet = createPopulationFleet(scene, objectsGroup, population, 6, defaultBaseZ);
        objectsGroup.visible = false;
        fleetTime = 0;
        fleetPlaying = true;
        const fleetBox = populationFleet.getBounds();
        const center = new THREE.Vector3();
        fleetBox.getCenter(center);
        const size = new THREE.Vector3();
        fleetBox.getSize(size);
        const maxFleetDim = Math.max(size.x, size.y, size.z, 0.4);
        fitCameraToBounds(camera, controls, {
          min: [center.x - maxFleetDim * 0.7, center.y - maxFleetDim * 0.7, 0],
          max: [center.x + maxFleetDim * 0.7, center.y + maxFleetDim * 0.7, maxFleetDim * 0.6],
        });
      } else {
        objectsGroup.visible = true;
        if (!currentDropSimulation) {
          fitCameraToBounds(camera, controls, floorFramingBounds(), 0.28);
        }
      }
    },

    setFleetPlayback(playing: boolean) {
      if (disposed) return;
      fleetPlaying = playing;
      if (playing && fleetTime >= FLEET_LOOP_S) {
        fleetTime = 0;
      }
    },

    restartFleetPlayback() {
      if (disposed) return;
      fleetTime = 0;
      fleetPlaying = true;
      if (populationFleet) {
        populationFleet.update(0);
      }
    },

    getFleetTime() {
      return fleetTime;
    },

    pickFleetUnit(raycaster: THREE.Raycaster) {
      if (!populationFleet || !isFleetMode) return null;
      return populationFleet.pickUnit(raycaster);
    },

    highlightFleetUnit(unitId: number | null) {
      if (!populationFleet || !isFleetMode) return;
      populationFleet.highlightUnit(unitId);
    },

    fit() {
      if (disposed) return;
      fitCameraToBounds(camera, controls, floorFramingBounds(), 0.28);
    },

    preset(name: CameraPreset) {
      if (disposed) return;
      // Model is displayed with bottom at z=0; centroid is at half the model height.
      const modelHeight = boundsUnion.max[2] - boundsUnion.min[2];
      const center = [
        (boundsUnion.min[0] + boundsUnion.max[0]) / 2,
        (boundsUnion.min[1] + boundsUnion.max[1]) / 2,
        modelHeight / 2,
      ] as Vec3;
      const radius = Math.max(maxDimension / 2, 0.05);
      applyCameraPreset(camera, controls, name, center, radius);
    },

    setInsets(insets: { left?: number; right?: number; top?: number; bottom?: number }) {
      if (disposed) return;
      currentInsets = {
        left: insets.left ?? 0,
        right: insets.right ?? 0,
        top: insets.top ?? 0,
        bottom: insets.bottom ?? 0,
      };
      resize();
    },

    getStats() {
      return {
        drawCalls: renderer.info.render.calls,
        triangles: renderer.info.render.triangles,
        geometries: renderer.info.memory.geometries,
        textures: renderer.info.memory.textures,
        tier: quality,
      };
    },

    dispose() {
      if (disposed) return;
      disposed = true;
      assetLoadGeneration += 1;
      if (animationFrameId !== null) cancelAnimationFrame(animationFrameId);
      resizeObserver?.disconnect();
      picker.dispose();
      controls.dispose();
      composer?.dispose();
      overlayLayer.dispose();
      palette.dispose();
      feaMaterialCache.clear();
      feaDecorated.length = 0;
      rapierBuildGeneration += 1;
      if (populationFleet) {
        populationFleet.dispose();
        populationFleet = null;
      }
      if (rapierSim) {
        rapierSim.dispose();
        rapierSim = null;
      }
      if (telemetryUnsub) {
        telemetryUnsub();
        telemetryUnsub = null;
      }
      telemetryCollector = null;
      lastLiveState = null;

      const oldChildren = [...objectsGroup.children];
      for (const child of oldChildren) {
        disposeObjectGroup(child);
      }
      if (instancedBatches) {
        for (const child of [...instancedBatches.group.children]) {
          disposeObjectGroup(child);
        }
        instancedBatches = null;
      }
      explodeByObjectId.clear();
      disposeSceneResources(scene);
      renderer.dispose();
      canvas.removeEventListener('webglcontextlost', handleContextLost);
      canvas.removeEventListener('webglcontextrestored', handleContextRestored);
      // Note: do NOT force context loss here. The context is created and
      // validated by SceneViewport and reused on remounts (React StrictMode
      // double-mounts in dev); losing it makes the second mount's
      // getContext('webgl2') fail and the viewport shows "unavailable".
    },
  };
}
