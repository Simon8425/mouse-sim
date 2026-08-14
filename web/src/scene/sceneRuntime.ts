import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { OutlinePass } from 'three/examples/jsm/postprocessing/OutlinePass.js';
import { SMAAPass } from 'three/examples/jsm/postprocessing/SMAAPass.js';

import type { Vec3, DropSimulationResult, DropTrajectorySample, FeaResult, RenderMode } from '../api/contracts';
import {
  MaterialPalette,
  FeaMaterialCache,
  type QualityTier,
  SELECTION_ACCENT,
} from './materialPalette';
import {
  createFeaUniforms,
  updateFeaUniforms,
  feaFieldMaxDamage,
  type FeaUniforms,
  type FeaPlateConfig,
} from './feaStressShader';
import { createSceneCamera, fitCameraToBounds, applyCameraPreset, type CameraPreset } from './camera';
import { createPicker } from './picking';
import { createOverlayLayer, type OverlaySpec } from './overlays';
import {
  applyFeaPlateField,
  createObjectGroup,
  worldBoundsForGeometry,
  disposeObjectGroup,
  applyFeaObjectField,
  objectMeshesFor,
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
  setDropSimulation: (simulation: DropSimulationResult | null) => void;
  setDropPlayback: (playing: boolean) => void;
  restartDropPlayback: () => void;
  getDropTime: () => number;
  setRenderMode: (mode: RenderMode) => void;
  setFeaResult: (fea: FeaResult | null) => void;
  fit: () => void;
  preset: (name: CameraPreset) => void;
  getStats: () => RenderStats;
  dispose: () => void;
}

export function hashString(str: string): number {
  let hash = 2166136261;
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

export function mulberry32(seed: number): () => number {
  return function () {
    let t = (seed += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * World-Class CAD Exploded View Offset System:
 * Computes structured, axis-aligned, non-colliding explosion offset vectors
 * for assembly components (top shell, bottom case, PCB, buttons, scroll wheel).
 *
 * DISPLAY ONLY — never sent to structural analysis or pipeline requests.
 */
export function explodeOffsetFor(
  id: string,
  maxDim: number,
  boundsCenter?: Vec3,
  partCenter?: Vec3,
): Vec3 {
  const lowerId = id.toLowerCase();

  let dirX = 0;
  let dirY = 0;
  let dirZ = 0.3;

  if (
    lowerId.includes('button') ||
    lowerId.includes('clicker') ||
    lowerId.includes('paddle')
  ) {
    // Click buttons MUST float HIGHEST above the top shell
    dirZ = 1.3;
    dirY = 0.25;
  } else if (
    lowerId.includes('top') ||
    lowerId.includes('upper') ||
    lowerId.includes('cover') ||
    lowerId.includes('shell_top')
  ) {
    dirZ = 0.95;
    dirY = -0.05;
  } else if (
    lowerId.includes('wheel') ||
    lowerId.includes('scroll') ||
    lowerId.includes('encoder') ||
    lowerId.includes('c-wheel') ||
    lowerId.includes('c-pq')
  ) {
    if (
      lowerId.includes('c-pq') ||
      lowerId.includes('ring') ||
      lowerId.includes('tire') ||
      lowerId.includes('rubber')
    ) {
      // Outer rubber scroll ring / tire (e.g. C-PQ-2_7): slides off upward and right
      dirZ = 0.85;
      dirY = 0.42;
      dirX = 0.22;
    } else if (lowerId.includes('encoder')) {
      dirZ = 0.55;
      dirY = 0.18;
      dirX = -0.3;
    } else {
      // Inner plastic wheel hub / axle (e.g. C-WHEEL-01FK): shifts lower and left
      dirZ = 0.65;
      dirY = 0.22;
      dirX = -0.18;
    }
  } else if (lowerId.includes('switch') || lowerId.includes('microswitch')) {
    dirZ = 0.55;
    dirY = 0.1;
  } else if (lowerId.includes('battery') || lowerId.includes('cell') || lowerId.includes('accu')) {
    dirZ = 0.45;
    dirX = -0.3;
  } else if (
    lowerId.includes('pcb') ||
    lowerId.includes('board') ||
    lowerId.includes('sensor') ||
    lowerId.includes('mcu')
  ) {
    dirZ = 0.2;
    dirY = -0.05;
  } else if (
    lowerId.includes('bottom') ||
    lowerId.includes('lower') ||
    lowerId.includes('base') ||
    lowerId.includes('chassis')
  ) {
    dirZ = -0.45;
  } else if (lowerId.includes('skate') || lowerId.includes('feet') || lowerId.includes('foot')) {
    dirZ = -0.9;
    dirY = 0.05;
  } else if (boundsCenter && partCenter) {
    const dz = partCenter[2] - boundsCenter[2];
    const dy = partCenter[1] - boundsCenter[1];
    const dx = partCenter[0] - boundsCenter[0];
    dirZ = (dz + maxDim * 0.5) / maxDim;
    if (Math.abs(dx) > maxDim * 0.08) {
      dirX = Math.sign(dx) * 0.6;
    }
    if (Math.abs(dy) > maxDim * 0.08) {
      dirY = Math.sign(dy) * 0.25;
    }
  }

  // Handle side buttons and grips with clear outward lateral separation
  if (lowerId.includes('left')) dirX = -0.85;
  if (lowerId.includes('right')) dirX = 0.85;
  if (lowerId.includes('side') || lowerId.includes('grip')) {
    const side = hashString(id) % 2 === 0 ? 0.85 : -0.85;
    dirX = side;
    dirZ = Math.max(0.3, dirZ);
  }

  // Deterministic subtle jitter for generic/manifold parts to prevent overlaps
  const hash = hashString(id);
  const angle = (hash % 12) * (Math.PI / 6);
  const jitterRadius = 0.12;
  dirX += Math.cos(angle) * jitterRadius;
  dirY += Math.sin(angle) * jitterRadius;

  const radius = maxDim * 0.85;
  return [dirX * radius, dirY * radius, dirZ * radius];
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

  const updatePixelRatio = () => {
    const dpr = window.devicePixelRatio || 1;
    const maxRatio = quality === 'ultra' ? 2.5 : quality === 'high' ? 2 : 1.5;
    renderer.setPixelRatio(Math.min(dpr, maxRatio));
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

  let gridHelper: THREE.GridHelper | null = null;
  let groundMesh: THREE.Mesh | null = null;
  let boundsUnion = { min: [-0.05, -0.05, -0.05] as Vec3, max: [0.05, 0.05, 0.05] as Vec3 };
  let maxDimension = 0.1;
  let floorSize = 2.5;
  const currentSurfaceMaterial = 'concrete';

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

    const size = Math.max(maxDimension * 4, 0.4);
    floorSize = size;
    const gridColor = theme === 'dark' ? 0x2e2c25 : 0xd8d5cc;
    gridHelper = new THREE.GridHelper(size, 64, gridColor, gridColor);
    gridHelper.rotation.x = Math.PI / 2;
    gridHelper.position.set(
      (boundsUnion.min[0] + boundsUnion.max[0]) / 2,
      (boundsUnion.min[1] + boundsUnion.max[1]) / 2,
      -0.0005,
    );
    gridHelper.userData.owned = true;
    scene.add(gridHelper);

    if (quality !== 'low') {
      const thickness = 0.02; // 2 cm solid 3D ground slab
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
  let composer: EffectComposer | null = null;
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
      outlinePass.edgeStrength = 4;
      outlinePass.edgeGlow = 0.15;
      outlinePass.edgeThickness = 1.5;
      outlinePass.visibleEdgeColor.setHex(SELECTION_ACCENT);
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
  };

  // State variables
  let currentEntries: ObjectSceneEntry[] = [];
  let currentVisibility: Record<string, boolean> = {};
  let currentSelectionId: string | null = null;
  let currentDropSimulation: DropSimulationResult | null = null;
  let dropTime = 0;
  let dropPlaying = false;
  let lastFrameTime: number | null = null;

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
    // A zero damage field has nothing to visualize: keep the palette
    // materials so the model can never be blanked by the white base.
    if (feaFieldMaxDamage(currentFeaResult) <= 1e-9) return;
    const fea = currentFeaResult;
    // Continuous plate-layer peak: min(1, shell peak / yield) — the field
    // maximum the contour is normalized against.
    const platePeakDamage =
      typeof fea.peak?.stress_pa === 'number' &&
      Number.isFinite(fea.peak.stress_pa) &&
      fea.peak.stress_pa > 0 &&
      typeof fea.yield_stress_pa === 'number' &&
      Number.isFinite(fea.yield_stress_pa) &&
      fea.yield_stress_pa > 0
        ? Math.min(1, fea.peak.stress_pa / fea.yield_stress_pa)
        : 0;
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
        const decorated = feaMaterialCache.get(material, mesh.geometry, uniforms);
        if (decorated !== material) mesh.material = decorated;
        feaDecorated.push({ material: decorated, uniforms });
      }
    }
  };

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
    const samples = currentDropSimulation.trajectory;
    const total = samples[samples.length - 1][0];
    const t = Math.min(dropTime, total);
    const resolved = resolveDropSample(t, samples);
    if (!resolved) return;
    const { a, b, alpha } = resolved;
    const posX = a[1] + (b[1] - a[1]) * alpha;
    const posY = a[2] + (b[2] - a[2]) * alpha;
    const posZ = a[3] + (b[3] - a[3]) * alpha;
    // 1:1 Faithful representation of backend physics trajectory
    // The backend writes quaternions in (w, x, y, z) order; THREE.Quaternion constructor takes (x, y, z, w).
    const quaternion = new THREE.Quaternion().slerpQuaternions(
      new THREE.Quaternion(a[5], a[6], a[7], a[4]),
      new THREE.Quaternion(b[5], b[6], b[7], b[4]),
      alpha,
    );

    objectsGroup.position.set(posX, posY, posZ);
    objectsGroup.quaternion.copy(quaternion);
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
    assetLoadGeneration += 1;
    const generation = assetLoadGeneration;
    // Clear old objects
    const oldChildren = [...objectsGroup.children];
    for (const child of oldChildren) {
      disposeObjectGroup(child);
    }

    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;

    let hasFiniteBounds = false;

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
      }

      objectsGroup.add(outerGroup);
      loadDisplayAsset(entry, innerGroup, outerGroup, generation);
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

    syncGridAndGround();
    overlayLayer.setPlaneRadius(maxDimension / 2);
    overlayLayer.setContactPlaneRadius(floorSize / 2);
    applyExplode();
    applySelection();
    applyModeDecoration();
  };

  const tempBox = new THREE.Box3();
  const tempCenter = new THREE.Vector3();

  const applyExplode = () => {
    const defaultBaseZ = -boundsUnion.min[2];
    objectsGroup.position.set(0, 0, defaultBaseZ);

    if (currentExplodeFactor <= 0) {
      for (const outer of objectsGroup.children) {
        outer.position.set(0, 0, 0);
      }
      return;
    }

    const children = objectsGroup.children.filter(
      (c) => typeof c.userData?.objectId === 'string',
    );
    if (children.length === 0) return;

    const assemblyCenter: Vec3 = boundsUnion
      ? [
          (boundsUnion.min[0] + boundsUnion.max[0]) / 2,
          (boundsUnion.min[1] + boundsUnion.max[1]) / 2,
          (boundsUnion.min[2] + boundsUnion.max[2]) / 2,
        ]
      : [0, 0, 0];

    // Measure unexploded local Z centroid for spatial rank sorting
    const partList: { outer: THREE.Object3D; id: string; centerZ: number; partCenter: Vec3 }[] = [];
    for (const outer of children) {
      const id = outer.userData.objectId as string;
      tempBox.setFromObject(outer);
      if (!tempBox.isEmpty()) {
        tempBox.getCenter(tempCenter);
        partList.push({
          outer,
          id,
          centerZ: tempCenter.z,
          partCenter: [tempCenter.x, tempCenter.y, tempCenter.z],
        });
      } else {
        partList.push({ outer, id, centerZ: 0, partCenter: [0, 0, 0] });
      }
    }

    // Sort parts from bottom to top along Z height
    partList.sort((a, b) => a.centerZ - b.centerZ);

    const count = partList.length;
    const minZ = partList[0].centerZ;
    const maxZ = partList[count - 1].centerZ;
    const zRange = Math.max(maxZ - minZ, 0.001);

    // 1. Calculate raw base target positions and rank ratios for all parts
    const targets: { outer: THREE.Object3D; id: string; targetPos: Vec3; rankRatio: number }[] = [];

    for (let i = 0; i < count; i++) {
      const { outer, id, centerZ, partCenter } = partList[i];
      const baseOffset = explodeOffsetFor(id, maxDimension, assemblyCenter, partCenter);

      const spatialRankRatio = count > 1 ? (centerZ - minZ) / zRange : 0.5;
      const indexRankRatio = count > 1 ? i / (count - 1) : 0;
      const rankRatio = spatialRankRatio * 0.6 + indexRankRatio * 0.4;

      const rankZ = rankRatio * maxDimension * 0.55;
      const finalZ = baseOffset[2] * 0.35 + rankZ;

      targets.push({
        outer,
        id,
        targetPos: [baseOffset[0], baseOffset[1], finalZ],
        rankRatio,
      });
    }

    // 2. Dynamic Concentric & Overlap Repulsion Pass:
    // Guarantees that concentric or nested parts (such as wheel hub vs rubber ring,
    // or concentric switches/buttons) push away from each other so NO TWO components overlap!
    const minDistance = maxDimension * 0.20;
    for (let i = 0; i < count; i++) {
      for (let j = i + 1; j < count; j++) {
        const a = targets[i];
        const b = targets[j];
        const dx = a.targetPos[0] - b.targetPos[0];
        const dy = a.targetPos[1] - b.targetPos[1];
        const dz = a.targetPos[2] - b.targetPos[2];
        const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);

        if (dist < minDistance) {
          const push = (minDistance - Math.max(dist, 0.001)) * 0.55;
          let nx = dist > 0.001 ? dx / dist : 0.707;
          const ny = dist > 0.001 ? dy / dist : 0.707;
          const nz = dist > 0.001 ? dz / dist : 0.5;

          // If parts are heavily co-centered along X/Y (like scroll wheel rings), force a strong lateral split
          if (Math.abs(dx) < maxDimension * 0.05) {
            nx = (i % 2 === 0 ? 1 : -1) * 0.8;
          }

          a.targetPos[0] += nx * push;
          a.targetPos[1] += ny * push;
          a.targetPos[2] += nz * push;

          b.targetPos[0] -= nx * push;
          b.targetPos[1] -= ny * push;
          b.targetPos[2] -= nz * push;
        }
      }
    }

    // 3. Apply positions with smooth staggered easing
    for (let i = 0; i < count; i++) {
      const { outer, targetPos, rankRatio } = targets[i];
      const staggerDelay = (1.0 - rankRatio) * 0.35;
      const rawPartFactor = Math.min(
        1.0,
        Math.max(0.0, (currentExplodeFactor - staggerDelay) / Math.max(0.001, 1.0 - staggerDelay)),
      );
      const easedFactor = rawPartFactor * rawPartFactor * (3 - 2 * rawPartFactor);

      outer.position.set(
        targetPos[0] * easedFactor,
        targetPos[1] * easedFactor,
        targetPos[2] * easedFactor,
      );
    }
  };

  const applySelection = () => {
    const selectedMeshes: THREE.Mesh[] = [];

    objectsGroup.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (mesh.isMesh && mesh.material) {
        const mat = mesh.material as THREE.MeshStandardMaterial;
        // Restore low quality emissive
        if (originalEmissiveMap.has(mat)) {
          mat.emissive.setHex(originalEmissiveMap.get(mat)!);
        }
      }

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
        selectedMeshes.push(mesh);
        if (quality === 'low' && mesh.material) {
          const mat = mesh.material as THREE.MeshStandardMaterial;
          if (!originalEmissiveMap.has(mat)) {
            originalEmissiveMap.set(mat, mat.emissive.getHex());
          }
          mat.emissive.setHex(0x403d36);
        }
      }
    });

    if (outlinePass) {
      outlinePass.selectedObjects = selectedMeshes;
    }
  };

  // Resize handler
  const resize = () => {
    const parent = canvas.parentElement;
    const width = parent?.clientWidth || canvas.clientWidth || 300;
    const height = parent?.clientHeight || canvas.clientHeight || 150;

    if (width === 0 || height === 0) return;

    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
    composer?.setSize(width, height);
  };

  const resizeObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => resize());
  if (resizeObserver && canvas.parentElement) {
    resizeObserver.observe(canvas.parentElement);
  }
  resize();

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

    // Drive the model along the simulated drop trajectory (real physics
    // data, not a decorative animation), advancing in wall-clock time so
    // the playback rate is real-time on any display refresh rate.
    if (currentDropSimulation && dropPlaying) {
      const now = performance.now();
      if (lastFrameTime === null) lastFrameTime = now;
      const delta = Math.min(0.1, (now - lastFrameTime) / 1000);
      lastFrameTime = now;
      const samples = currentDropSimulation.trajectory;
      const total = samples.length > 0 ? samples[samples.length - 1][0] : 0;
      const previous = dropTime;
      if (total <= 0) {
        dropPlaying = false;
        lastFrameTime = null;
        opts.onDropEnded?.();
      } else {
        dropTime = Math.min(dropTime + delta, total);
        if (dropTime >= total && previous < total) {
          dropPlaying = false;
          lastFrameTime = null;
          opts.onDropEnded?.();
        }
      }
    } else {
      lastFrameTime = null;
    }
    // Smoothly animate exploded view transition (60 FPS spring lerp)
    if (Math.abs(targetExplodeFactor - currentExplodeFactor) > 0.0005) {
      currentExplodeFactor += (targetExplodeFactor - currentExplodeFactor) * 0.12;
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
      const windowS = currentFeaResult?.impact_window_s ?? 0;
      let window01 = 1;
      let active = true;
      if (currentDropSimulation && windowS > 0) {
        const impactTime = currentDropSimulation.impacts?.[0]?.t_s ?? 0;
        window01 = impactWindowProgress(dropTime, impactTime, windowS);
        active = window01 > 0;
      }
      feaFrameOpts.mode = currentRenderMode;
      feaFrameOpts.impactWindow01 = window01;
      feaFrameOpts.impactWindowActive = active;
      // Freeze the yield-mode whitening noise while a drop simulation is
      // loaded but not actively playing (finished or paused): uTime must be
      // constant so the damaged-region speckle pattern (feaHashNoise in
      // feaStressShader.ts) is static instead of shimmering every frame.
      // Freeze the yield-mode whitening noise for the ENTIRE loaded-
      // simulation lifetime (playing, paused, finished): during playback
      // the settled tail displays a frozen pose for seconds, and the
      // time-seeded speckle (feaHashNoise) would shimmer on the damage
      // band every frame — the visible "slightly moving" jitter on a
      // static rest.
      feaFrameOpts.time = currentDropSimulation ? 0 : performance.now() / 1000;
      for (const pair of feaDecorated) {
        updateFeaUniforms(pair.uniforms, feaFrameOpts);
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
        fitCameraToBounds(camera, controls, floorFramingBounds());
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
      palette.dispose();
      palette = new MaterialPalette(theme);
      scene.background = new THREE.Color(theme === 'dark' ? 0x121212 : 0xf7f7f4);
      rebuildObjects();
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

    setOverlays(spec: OverlaySpec | null) {
      if (disposed) return;
      overlayLayer.apply(spec);
    },

    setDropSimulation(simulation: DropSimulationResult | null) {
      if (disposed) return;
      currentDropSimulation = simulation;
      dropTime = 0;
      dropPlaying = simulation !== null;
      lastFrameTime = null;
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
        fitCameraToBounds(camera, controls, floorFramingBounds());
      }
    },

    setDropPlayback(playing: boolean) {
      if (disposed) return;
      if (!playing || currentDropSimulation === null) {
        dropPlaying = false;
        return;
      }
      const samples = currentDropSimulation.trajectory;
      const total = samples.length > 0 ? samples[samples.length - 1][0] : 0;
      // Resume from the exact paused moment; a play press at the end of the
      // playback restarts the whole sequence from the beginning.
      if (dropTime >= total) {
        dropTime = 0;
      }
      dropPlaying = true;
    },

    restartDropPlayback() {
      if (disposed) return;
      dropTime = 0;
      dropPlaying = currentDropSimulation !== null;
      applyDropTransform();
    },

    getDropTime() {
      return dropTime;
    },

    setRenderMode(mode: RenderMode) {
      if (disposed || mode === currentRenderMode) return;
      currentRenderMode = mode;
      feaMaterialCache.clear();
      // Rebuild with the pristine palette (default) so decorations never
      // accumulate; applyModeDecoration re-applies the fea/yield materials.
      rebuildObjects();
    },

    setFeaResult(fea: FeaResult | null) {
      if (disposed) return;
      currentFeaResult = fea;
      feaMaterialCache.clear();
      if (currentRenderMode !== 'default') {
        rebuildObjects();
      }
    },

    fit() {
      if (disposed) return;
      fitCameraToBounds(camera, controls, floorFramingBounds());
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

      const oldChildren = [...objectsGroup.children];
      for (const child of oldChildren) {
        disposeObjectGroup(child);
      }
      disposeSceneResources(scene);
      renderer.dispose();
      // Note: do NOT force context loss here. The context is created and
      // validated by SceneViewport and reused on remounts (React StrictMode
      // double-mounts in dev); losing it makes the second mount's
      // getContext('webgl2') fail and the viewport shows "unavailable".
    },
  };
}
