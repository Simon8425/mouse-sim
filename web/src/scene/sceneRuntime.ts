import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { OutlinePass } from 'three/examples/jsm/postprocessing/OutlinePass.js';
import { SMAAPass } from 'three/examples/jsm/postprocessing/SMAAPass.js';

import type { Vec3, DropSimulationResult, DropTrajectorySample } from '../api/contracts';
import {
  MaterialPalette,
  type QualityTier,
  SELECTION_ACCENT,
} from './materialPalette';
import { createSceneCamera, fitCameraToBounds, applyCameraPreset, type CameraPreset } from './camera';
import { createPicker } from './picking';
import { createOverlayLayer, type OverlaySpec } from './overlays';
import {
  createObjectGroup,
  worldBoundsForGeometry,
  disposeObjectGroup,
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
  theme: 'light' | 'dark';
  quality: QualityTier;
  onPick: (id: string | null) => void;
  onDoublePick?: (id: string | null) => void;
  onStats?: (stats: RenderStats) => void;
  onDropEnded?: () => void;
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

/** DISPLAY ONLY — never sent to analysis */
export function explodeOffsetFor(id: string, maxDim: number): Vec3 {
  const seed = hashString(id);
  const rand = mulberry32(seed);

  const u = rand();
  const v = rand();
  const theta = u * 2.0 * Math.PI;
  const phi = Math.acos(2.0 * v - 1.0);
  const r = (0.2 + 0.3 * rand()) * maxDim;

  const x = r * Math.sin(phi) * Math.cos(theta);
  const y = r * Math.sin(phi) * Math.sin(theta);
  const z = r * Math.cos(phi);

  return [x, y, z];
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

export function createSceneRuntime(opts: SceneRuntimeOptions): SceneRuntime {
  const { canvas } = opts;
  let theme = opts.theme;
  let quality = opts.quality;

  const renderer = new THREE.WebGLRenderer({
    canvas,
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
    const maxRatio = quality === 'high' ? 2 : 1.5;
    renderer.setPixelRatio(Math.min(dpr, maxRatio));
  };
  updatePixelRatio();

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(theme === 'dark' ? 0x14161a : 0xf4f5f7);

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
  keyLight.shadow.mapSize.width = quality === 'high' ? 1024 : 512;
  keyLight.shadow.mapSize.height = quality === 'high' ? 1024 : 512;
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

  const syncGridAndGround = () => {
    if (gridHelper) {
      disposeObject3D(gridHelper);
      gridHelper = null;
    }
    if (groundMesh) {
      disposeObject3D(groundMesh);
      groundMesh = null;
    }

    const size = Math.max(maxDimension * 5, 0.2);
    const gridColor = theme === 'dark' ? 0x2e2c25 : 0xd8d5cc;
    gridHelper = new THREE.GridHelper(size, 20, gridColor, gridColor);
    gridHelper.rotation.x = Math.PI / 2;
    gridHelper.userData.owned = true;
    scene.add(gridHelper);

    if (quality !== 'low') {
      const planeGeom = new THREE.PlaneGeometry(size * 2, size * 2);
      const shadowMat = new THREE.ShadowMaterial({ opacity: theme === 'dark' ? 0.3 : 0.15 });
      shadowMat.userData.owned = true;
      groundMesh = new THREE.Mesh(planeGeom, shadowMat);
      groundMesh.receiveShadow = true;
      groundMesh.position.set(
        (boundsUnion.min[0] + boundsUnion.max[0]) / 2,
        (boundsUnion.min[1] + boundsUnion.max[1]) / 2,
        boundsUnion.min[2] - 0.002,
      );
      groundMesh.userData.owned = true;
      scene.add(groundMesh);
    }
  };
  syncGridAndGround();

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

  const applyDropTransform = (): void => {
    if (!currentDropSimulation || currentDropSimulation.trajectory.length === 0) {
      objectsGroup.position.set(0, 0, 0);
      objectsGroup.quaternion.identity();
      return;
    }
    const samples = currentDropSimulation.trajectory;
    const total = samples[samples.length - 1][0];
    const t = Math.min(dropTime, total);
    const resolved = resolveDropSample(t, samples);
    if (!resolved) return;
    const { a, b, alpha } = resolved;
    objectsGroup.position.set(
      a[1] + (b[1] - a[1]) * alpha,
      a[2] + (b[2] - a[2]) * alpha,
      a[3] + (b[3] - a[3]) * alpha,
    );
    objectsGroup.quaternion.slerpQuaternions(
      new THREE.Quaternion(a[4], a[5], a[6], a[7]),
      new THREE.Quaternion(b[4], b[5], b[6], b[7]),
      alpha,
    );
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
        // without mutating individual assembly placements.
        gltf.scene.rotation.x = Math.PI;
        target.add(gltf.scene);
        const meshes: THREE.Mesh[] = [];
        gltf.scene.traverse((object) => {
          const mesh = object as THREE.Mesh;
          if (mesh.isMesh) meshes.push(mesh);
        });
        target.userData.meshObjects = meshes;
        applySelection();
      },
      undefined,
      () => {
        if (disposed || generation !== assetLoadGeneration || outer.parent !== objectsGroup) return;
        // Keep a usable fallback if a cached GLB is unavailable. This path is
        // intentionally only a transport failure fallback, never a STEP
        // parsing fallback.
        const fallback = createObjectGroup(
          { ...entry, displayAssetUrl: null },
          { quality, materials: palette.getAll() },
        );
        target.add(fallback);
        target.userData.meshObjects = fallback.userData.meshObjects ?? [];
        applySelection();
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
        quality,
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
    applyExplode();
    applySelection();
  };

  const applyExplode = () => {
    for (const outer of objectsGroup.children) {
      const id = outer.userData.objectId;
      if (typeof id === 'string' && currentExplodeFactor > 0) {
        const offset = explodeOffsetFor(id, maxDimension);
        outer.position.set(
          offset[0] * currentExplodeFactor,
          offset[1] * currentExplodeFactor,
          offset[2] * currentExplodeFactor,
        );
      } else {
        outer.position.set(0, 0, 0);
      }
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
    applyDropTransform();

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
        fitCameraToBounds(camera, controls, boundsUnion);
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
      if (disposed || factor === currentExplodeFactor) return;
      currentExplodeFactor = factor;
      applyExplode();
    },

    setTheme(newTheme: 'light' | 'dark') {
      if (disposed || newTheme === theme) return;
      theme = newTheme;
      palette.dispose();
      palette = new MaterialPalette(theme);
scene.background = new THREE.Color(theme === 'dark' ? 0x141310 : 0xf7f7f4);
      rebuildObjects();
    },

    setQuality(newQuality: QualityTier) {
      if (disposed || newQuality === quality) return;
      quality = newQuality;
      updatePixelRatio();
      renderer.shadowMap.enabled = quality !== 'low';
      keyLight.castShadow = quality !== 'low';
      keyLight.shadow.mapSize.width = quality === 'high' ? 1024 : 512;
      keyLight.shadow.mapSize.height = quality === 'high' ? 1024 : 512;
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
      applyDropTransform();
      if (simulation) {
        // Frame the whole drop envelope (trajectory AABB ∪ model bounds) so
        // the fall is visible instead of happening off-screen.
        const trajectory = dropTrajectoryBounds(simulation);
        const combined: { min: Vec3; max: Vec3 } = {
          min: [
            Math.min(trajectory.min[0], boundsUnion.min[0]),
            Math.min(trajectory.min[1], boundsUnion.min[1]),
            Math.min(trajectory.min[2], boundsUnion.min[2]),
          ],
          max: [
            Math.max(trajectory.max[0], boundsUnion.max[0]),
            Math.max(trajectory.max[1], boundsUnion.max[1]),
            Math.max(trajectory.max[2], boundsUnion.max[2]),
          ],
        };
        fitCameraToBounds(camera, controls, combined);
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

    fit() {
      if (disposed) return;
      fitCameraToBounds(camera, controls, boundsUnion);
    },

    preset(name: CameraPreset) {
      if (disposed) return;
      const center = [
        (boundsUnion.min[0] + boundsUnion.max[0]) / 2,
        (boundsUnion.min[1] + boundsUnion.max[1]) / 2,
        (boundsUnion.min[2] + boundsUnion.max[2]) / 2,
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

      const oldChildren = [...objectsGroup.children];
      for (const child of oldChildren) {
        disposeObjectGroup(child);
      }
      disposeSceneResources(scene);
      renderer.dispose();
      if (typeof renderer.forceContextLoss === 'function') renderer.forceContextLoss();
    },
  };
}
