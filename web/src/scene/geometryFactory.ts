import * as THREE from 'three';
import type {
  Vec3,
  GeometryJson,
  RigidTransformJson,
  CompoundGeometryJson,
  FeaObjectField,
  FeaResult,
} from '../api/contracts';
import { IDENTITY_TRANSFORM } from '../api/contracts';
import { paletteKeyForComponent, type PaletteKey, type QualityTier } from './materialPalette';

export interface ObjectSceneEntry {
  id: string;
  geometry: GeometryJson;
  className?: string | null;
  displayAssetUrl?: string | null;
  color?: [number, number, number] | null;
}

export interface FactoryOptions {
  quality?: QualityTier;
  materials?: Partial<Record<PaletteKey, THREE.Material>>;
  wireframe?: boolean;
}

interface Bounds {
  min: Vec3;
  max: Vec3;
}

const ZERO_BOUNDS: Bounds = { min: [0, 0, 0], max: [0, 0, 0] };

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isRenderableNumber(value: unknown): value is number {
  return isFiniteNumber(value) && Number.isFinite(Math.fround(value));
}

function isFiniteVec3(value: unknown, requireFloat32 = false): value is Vec3 {
  if (!Array.isArray(value) || value.length !== 3) return false;
  const isValid = requireFloat32 ? isRenderableNumber : isFiniteNumber;
  return value.every(isValid);
}

function isFiniteTransform(value: unknown): value is RigidTransformJson {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const candidate = value as Partial<RigidTransformJson>;
  if (candidate.units !== undefined && typeof candidate.units !== 'string') return false;
  if (!Array.isArray(candidate.rotation)) return false;
  if (candidate.rotation.length !== 3 || !isFiniteVec3(candidate.translation)) return false;
  return candidate.rotation.every((row) => isFiniteVec3(row));
}

function hasSafeTransform(value: unknown): boolean {
  return value == null || isFiniteTransform(value);
}

function isSafeMesh(geometry: Record<string, unknown>): boolean {
  if (!Array.isArray(geometry.vertices) || !Array.isArray(geometry.triangles)) return false;
  if (!geometry.vertices.every((vertex) => isFiniteVec3(vertex, true))) return false;

  const vertexCount = geometry.vertices.length;
  return geometry.triangles.every((triangle) => {
    if (!Array.isArray(triangle) || triangle.length !== 3) return false;
    return triangle.every(
      (index) =>
        typeof index === 'number' &&
        Number.isInteger(index) &&
        index >= 0 &&
        index < vertexCount,
    );
  });
}

/**
 * Scene geometry comes from JSON at runtime, so the TypeScript union cannot be
 * treated as validation. Keep this guard stricter than the public contract
 * where Three.js buffers require it, especially for Float32Array values.
 */
function isSafeGeometry(value: unknown): value is GeometryJson {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const geometry = value as Record<string, unknown>;
  if (typeof geometry.type !== 'string' || !hasSafeTransform(geometry.transform)) return false;
  if (geometry.units !== undefined && typeof geometry.units !== 'string') return false;

  switch (geometry.type) {
    case 'box':
      return isFiniteVec3(geometry.size, true) && geometry.size.every((size) => size >= 0);
    case 'sphere':
      return isRenderableNumber(geometry.radius) && geometry.radius > 0;
    case 'cylinder':
      return (
        isRenderableNumber(geometry.radius) &&
        geometry.radius > 0 &&
        isRenderableNumber(geometry.height) &&
        geometry.height > 0
      );
    case 'cone':
      return (
        isRenderableNumber(geometry.base_radius) &&
        geometry.base_radius > 0 &&
        isRenderableNumber(geometry.height) &&
        geometry.height > 0
      );
    case 'frustum':
      return (
        isRenderableNumber(geometry.top_radius) &&
        geometry.top_radius > 0 &&
        isRenderableNumber(geometry.bottom_radius) &&
        geometry.bottom_radius > 0 &&
        isRenderableNumber(geometry.height) &&
        geometry.height > 0
      );
    case 'mesh':
      return isSafeMesh(geometry);
    case 'compound':
      return Array.isArray(geometry.children);
    default:
      return false;
  }
}

function boundsAreFinite(bounds: Bounds): boolean {
  return bounds.min.every(isFiniteNumber) && bounds.max.every(isFiniteNumber);
}

function unionBounds(current: Bounds | null, next: Bounds): Bounds {
  if (!current) return { min: [...next.min], max: [...next.max] };
  return {
    min: [
      Math.min(current.min[0], next.min[0]),
      Math.min(current.min[1], next.min[1]),
      Math.min(current.min[2], next.min[2]),
    ],
    max: [
      Math.max(current.max[0], next.max[0]),
      Math.max(current.max[1], next.max[1]),
      Math.max(current.max[2], next.max[2]),
    ],
  };
}

/**
 * Map a Python rigid transform (world = R · local + t, R row-major 3x3) onto a
 * three.js Matrix4 (column-major storage).
 *
 * Parity: three.js elements are column-major, so the row-major matrix
 * [r00 r01 r02; r10 r11 r12; r20 r21 r22] is stored as
 * [r00, r10, r20, 0, r01, r11, r21, 0, r02, r12, r22, 0, tx, ty, tz, 1].
 */
export function pythonTransformToMatrix4(transform?: RigidTransformJson | null): THREE.Matrix4 {
  const t = isFiniteTransform(transform) ? transform : IDENTITY_TRANSFORM;
  const [r00, r01, r02] = t.rotation[0];
  const [r10, r11, r12] = t.rotation[1];
  const [r20, r21, r22] = t.rotation[2];
  const [tx, ty, tz] = t.translation;
  return new THREE.Matrix4().fromArray([
    r00, r10, r20, 0,
    r01, r11, r21, 0,
    r02, r12, r22, 0,
    tx, ty, tz, 1,
  ]);
}

/**
 * Build a THREE.Group for a scene entry. Primitive transforms are applied to
 * the group itself; compound transforms are applied once to an inner container.
 */
export function createObjectGroup(entry: ObjectSceneEntry, opts?: FactoryOptions): THREE.Group {
  const group = new THREE.Group();
  group.userData.objectId = entry.id;
  group.userData.className = entry.className ?? null;
  group.userData.meshObjects = [] as THREE.Mesh[];

  // A kernel-backed STEP preview has a native GLB display asset. The scene
  // runtime loads it asynchronously; keep the normalized mesh only for
  // bounds/analysis and avoid rendering two overlapping representations.
  if (entry.displayAssetUrl) return group;

  const quality = opts?.quality ?? 'high';
  const sphereSegments = quality === 'high' ? 32 : quality === 'medium' ? 24 : 16;
  const sphereRings = quality === 'high' ? 16 : quality === 'medium' ? 12 : 8;
  const radialSegments = quality === 'high' ? 32 : quality === 'medium' ? 24 : 16;

  function resolveMaterial(key: PaletteKey): THREE.Material {
    const shared = opts?.materials?.[key];
    if (shared) return shared;
    const mat = new THREE.MeshStandardMaterial({ color: 0x9aa0a6 });
    mat.userData.owned = true;
    return mat;
  }

  /**
   * Attach the zero-filled FEA vertex attributes (aDamage/aDisplacement) to a
   * primitive geometry so the decorated shader compiles with the attribute
   * path and applyFeaPlateField() can fill real values — mirrors the 'mesh'
   * case which attaches them at build time.
   */
  function attachFeaAttributes(geometry: THREE.BufferGeometry): void {
    const vertexCount = geometry.attributes.position.count;
    geometry.setAttribute('aDamage', new THREE.BufferAttribute(new Float32Array(vertexCount), 1));
    geometry.setAttribute(
      'aDisplacement',
      new THREE.BufferAttribute(new Float32Array(vertexCount * 3), 3),
    );
  }

  function buildMesh(geometry: GeometryJson): THREE.Mesh | null {
    if (!isSafeGeometry(geometry) || geometry.type === 'compound') return null;
    if (geometry.type === 'mesh' && (geometry.vertices.length === 0 || geometry.triangles.length === 0)) {
      return null;
    }

    const key = paletteKeyForComponent(entry.className ?? null);
    const material = entry.color
      ? (() => {
          // Kernel-tessellated STEP parts carry their CAD presentation color;
          // own the material so disposal never touches the shared palette.
          // CAD colors are sRGB; the renderer outputs sRGB with ACES tone
          // mapping, so convert once to linear.
          const colored = new THREE.MeshStandardMaterial({
            color: new THREE.Color().setRGB(
              entry.color[0],
              entry.color[1],
              entry.color[2],
              THREE.SRGBColorSpace,
            ),
            roughness: 0.6,
            metalness: 0.1,
          });
          colored.userData.owned = true;
          return colored;
        })()
      : resolveMaterial(key);
    let mesh: THREE.Mesh | null = null;
    let createdGeometry: THREE.BufferGeometry | null = null;
    let wireframeMaterial: THREE.LineBasicMaterial | null = null;

    try {
      switch (geometry.type) {
        case 'box': {
          const size = geometry.size as Vec3;
          createdGeometry = new THREE.BoxGeometry(size[0], size[1], size[2]);
          attachFeaAttributes(createdGeometry);
          mesh = new THREE.Mesh(createdGeometry, material);
          break;
        }
        case 'sphere': {
          createdGeometry = new THREE.SphereGeometry(geometry.radius, sphereSegments, sphereRings);
          attachFeaAttributes(createdGeometry);
          mesh = new THREE.Mesh(createdGeometry, material);
          break;
        }
        case 'cylinder': {
          const geometryCyl = new THREE.CylinderGeometry(
            geometry.radius,
            geometry.radius,
            geometry.height,
            radialSegments,
          );
          geometryCyl.rotateX(Math.PI / 2);
          geometryCyl.translate(0, 0, geometry.height / 2);
          createdGeometry = geometryCyl;
          attachFeaAttributes(createdGeometry);
          mesh = new THREE.Mesh(createdGeometry, material);
          break;
        }
        case 'cone': {
          const geometryCone = new THREE.ConeGeometry(
            geometry.base_radius,
            geometry.height,
            radialSegments,
          );
          geometryCone.rotateX(Math.PI / 2);
          geometryCone.translate(0, 0, geometry.height / 2);
          createdGeometry = geometryCone;
          attachFeaAttributes(createdGeometry);
          mesh = new THREE.Mesh(createdGeometry, material);
          break;
        }
        case 'frustum': {
          const geometryFrustum = new THREE.CylinderGeometry(
            geometry.top_radius,
            geometry.bottom_radius,
            geometry.height,
            radialSegments,
          );
          geometryFrustum.rotateX(Math.PI / 2);
          geometryFrustum.translate(0, 0, geometry.height / 2);
          createdGeometry = geometryFrustum;
          attachFeaAttributes(createdGeometry);
          mesh = new THREE.Mesh(createdGeometry, material);
          break;
        }
        case 'mesh': {
          const vertices = geometry.vertices as Vec3[];
          const positions = new Float32Array(vertices.length * 3);
          for (let i = 0; i < vertices.length; i += 1) {
            positions[i * 3] = vertices[i][0];
            positions[i * 3 + 1] = vertices[i][1];
            positions[i * 3 + 2] = vertices[i][2];
          }
          const bufferGeometry = new THREE.BufferGeometry();
          createdGeometry = bufferGeometry;
          bufferGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
          const triangleCount = geometry.triangles.length;
          const vertexCount = vertices.length;
          const indices =
            vertexCount <= 65535
              ? new Uint16Array(triangleCount * 3)
              : new Uint32Array(triangleCount * 3);
          for (let i = 0; i < triangleCount; i += 1) {
            const tri = geometry.triangles[i];
            indices[i * 3] = tri[0];
            indices[i * 3 + 1] = tri[1];
            indices[i * 3 + 2] = tri[2];
          }
          bufferGeometry.setIndex(new THREE.BufferAttribute(indices, 1));
          bufferGeometry.computeVertexNormals();
          // FEA per-vertex attributes (~16 bytes/vertex): zero-filled damage
          // (itemSize 1) and displacement (itemSize 3). They exist on ALL
          // meshes so the decorated FEA shader always compiles and renders
          // identically until applyFeaObjectField() writes real values.
          bufferGeometry.setAttribute('aDamage', new THREE.BufferAttribute(new Float32Array(vertexCount), 1));
          bufferGeometry.setAttribute(
            'aDisplacement',
            new THREE.BufferAttribute(new Float32Array(vertexCount * 3), 3),
          );
          bufferGeometry.computeBoundingSphere();
          mesh = new THREE.Mesh(bufferGeometry, material);
          if (opts?.wireframe) {
            wireframeMaterial = new THREE.LineBasicMaterial({
              color: 0x000000,
              transparent: true,
              opacity: 0.35,
            });
            wireframeMaterial.userData.owned = true;
            const edges = new THREE.LineSegments(
              new THREE.EdgesGeometry(bufferGeometry, 20),
              wireframeMaterial,
            );
            edges.userData.owned = true;
            mesh.add(edges);
          }
          break;
        }
      }
    } catch {
      createdGeometry?.dispose();
      wireframeMaterial?.dispose();
      if (material.userData.owned) material.dispose();
      return null;
    }

    return mesh;
  }

  if (!isSafeGeometry(entry.geometry)) return group;

  if (geometryIsCompound(entry.geometry)) {
    const container = new THREE.Group();
    entry.geometry.children.forEach((child: GeometryJson, index: number) => {
      const childGroup = createObjectGroup(
        {
          id: `${entry.id}:${index}`,
          geometry: child,
          className: entry.className,
          color: entry.color,
        },
        opts,
      );
      if (childGroup.userData.meshObjects.length > 0) container.add(childGroup);
    });
    container.applyMatrix4(pythonTransformToMatrix4(entry.geometry.transform));
    container.matrixAutoUpdate = false;
    group.add(container);

    const collected: THREE.Mesh[] = [];
    container.traverse((obj) => {
      const candidate = obj as THREE.Mesh;
      if (candidate.isMesh) collected.push(candidate);
    });
    group.userData.meshObjects = collected;
  } else {
    const mesh = buildMesh(entry.geometry);
    if (!mesh) return group;
    group.applyMatrix4(pythonTransformToMatrix4(entry.geometry.transform));
    group.matrixAutoUpdate = false;
    group.add(mesh);
    group.userData.meshObjects = [mesh];
  }

  return group;
}

function geometryIsCompound(geometry: GeometryJson): geometry is CompoundGeometryJson {
  return geometry.type === 'compound';
}

/**
 * Exact world AABB of a geometry. Local bounds follow the Python convention
 * (box ±size/2, sphere ±r, cylinder/cone/frustum x/y ±r with z in [0, height],
 * mesh from vertex extents), then all 8 corners are transformed by
 * rotation + translation and min/max taken.
 *
 * Parity: box size (2,4,6), rotation [[0,-1,0],[1,0,0],[0,0,1]], translation
 * (1,2,3) yields min (−1,1,0), max (3,3,6).
 */
export function worldBoundsForGeometry(geometry: GeometryJson): { min: Vec3; max: Vec3 } {
  return worldBoundsRecursive(geometry, new THREE.Matrix4()) ?? {
    min: [...ZERO_BOUNDS.min],
    max: [...ZERO_BOUNDS.max],
  };
}

function worldBoundsRecursive(geometry: unknown, parent: THREE.Matrix4): Bounds | null {
  if (!isSafeGeometry(geometry)) return null;

  if (geometry.type === 'compound') {
    if (!hasSafeTransform(geometry.transform)) return null;
    const matrix = new THREE.Matrix4().multiplyMatrices(parent, pythonTransformToMatrix4(geometry.transform));
    if (!matrix.elements.every(isFiniteNumber)) return null;

    let bounds: Bounds | null = null;
    for (const child of geometry.children) {
      const childBounds = worldBoundsRecursive(child, matrix);
      if (childBounds && boundsAreFinite(childBounds)) bounds = unionBounds(bounds, childBounds);
    }
    return bounds;
  }

  const local = localBoundsOf(geometry);
  if (!local) return null;
  const matrix = new THREE.Matrix4().multiplyMatrices(parent, pythonTransformToMatrix4(geometry.transform));
  if (!matrix.elements.every(isFiniteNumber)) return null;

  let bounds: Bounds | null = null;
  for (let i = 0; i < 8; i += 1) {
    const x = i & 1 ? local.max[0] : local.min[0];
    const y = i & 2 ? local.max[1] : local.min[1];
    const z = i & 4 ? local.max[2] : local.min[2];
    const p = new THREE.Vector3(x, y, z).applyMatrix4(matrix);
    if (!isFiniteNumber(p.x) || !isFiniteNumber(p.y) || !isFiniteNumber(p.z)) return null;
    const pointBounds: Bounds = { min: [p.x, p.y, p.z], max: [p.x, p.y, p.z] };
    bounds = unionBounds(bounds, pointBounds);
  }
  return bounds;
}

function localBoundsOf(geometry: GeometryJson): Bounds | null {
  switch (geometry.type) {
    case 'box': {
      const size = geometry.size as Vec3;
      return {
        min: [-size[0] / 2, -size[1] / 2, -size[2] / 2],
        max: [size[0] / 2, size[1] / 2, size[2] / 2],
      };
    }
    case 'sphere': {
      const r = geometry.radius;
      return { min: [-r, -r, -r], max: [r, r, r] };
    }
    case 'cylinder': {
      const r = geometry.radius;
      return { min: [-r, -r, 0], max: [r, r, geometry.height] };
    }
    case 'cone': {
      const r = geometry.base_radius;
      return { min: [-r, -r, 0], max: [r, r, geometry.height] };
    }
    case 'frustum': {
      const r = Math.max(geometry.top_radius, geometry.bottom_radius);
      return { min: [-r, -r, 0], max: [r, r, geometry.height] };
    }
    case 'mesh': {
      const vertices = geometry.vertices as Vec3[];
      if (vertices.length === 0 || geometry.triangles.length === 0) return null;
      let min: Vec3 = [...vertices[0]];
      let max: Vec3 = [...vertices[0]];
      for (const v of vertices.slice(1)) {
        min = [Math.min(min[0], v[0]), Math.min(min[1], v[1]), Math.min(min[2], v[2])];
        max = [Math.max(max[0], v[0]), Math.max(max[1], v[1]), Math.max(max[2], v[2])];
      }
      return { min, max };
    }
    case 'compound':
      return null;
    default: {
      return null;
    }
  }
}

/**
 * Exact world-frame vertices of a geometry (meshes: every vertex transformed
 * by the full parent/child chain; analytic primitives and unknown shapes:
 * the 8 transformed AABB corners, which are conservative for curved shapes
 * like spheres).  Used by the drop playback floor clamp to rest the model
 * flush on the ground instead of floating on a conservative AABB bound.
 */
export function worldVerticesForGeometry(geometry: GeometryJson): Vec3[] {
  const collected: Vec3[] = [];
  collectWorldVertices(geometry, new THREE.Matrix4(), collected);
  return collected;
}

/**
 * ALL world-frame vertices of a geometry (no stride sampling), for building
 * physics colliders (e.g. Rapier's convexHull).  The display-stride version
 * (worldVerticesForGeometry) caps at ~250 points per mesh which is fine for
 * rendering bounds but too coarse for a contact hull.
 */
export function worldVerticesForGeometryFull(geometry: GeometryJson): Vec3[] {
  const collected: Vec3[] = [];
  collectWorldVerticesFull(geometry, new THREE.Matrix4(), collected);
  return collected;
}

function collectWorldVerticesFull(geometry: unknown, parent: THREE.Matrix4, out: Vec3[]): void {
  if (!isSafeGeometry(geometry)) return;
  if (geometry.type === 'compound') {
    if (!hasSafeTransform(geometry.transform)) return;
    const matrix = new THREE.Matrix4().multiplyMatrices(parent, pythonTransformToMatrix4(geometry.transform));
    if (!matrix.elements.every(isFiniteNumber)) return;
    for (const child of geometry.children) {
      collectWorldVerticesFull(child, matrix, out);
    }
    return;
  }
  const matrix = new THREE.Matrix4().multiplyMatrices(parent, pythonTransformToMatrix4(geometry.transform));
  if (!matrix.elements.every(isFiniteNumber)) return;
  if (geometry.type === 'mesh') {
    const vertices = geometry.vertices as Vec3[];
    if (vertices.length === 0) return;
    const point = new THREE.Vector3();
    for (const vertex of vertices) {
      point.set(vertex[0], vertex[1], vertex[2]).applyMatrix4(matrix);
      if (isFiniteNumber(point.x) && isFiniteNumber(point.y) && isFiniteNumber(point.z)) {
        out.push([point.x, point.y, point.z]);
      }
    }
    return;
  }
  // Primitives: same 8-corner AABB conservative proxy as the strided version.
  const local = localBoundsOf(geometry);
  if (!local) return;
  for (let i = 0; i < 8; i += 1) {
    const x = i & 1 ? local.max[0] : local.min[0];
    const y = i & 2 ? local.max[1] : local.min[1];
    const z = i & 4 ? local.max[2] : local.min[2];
    const p = new THREE.Vector3(x, y, z).applyMatrix4(matrix);
    if (isFiniteNumber(p.x) && isFiniteNumber(p.y) && isFiniteNumber(p.z)) {
      out.push([p.x, p.y, p.z]);
    }
  }
}

function collectWorldVertices(geometry: unknown, parent: THREE.Matrix4, out: Vec3[]): void {
  if (!isSafeGeometry(geometry)) return;
  if (geometry.type === 'compound') {
    if (!hasSafeTransform(geometry.transform)) return;
    const matrix = new THREE.Matrix4().multiplyMatrices(parent, pythonTransformToMatrix4(geometry.transform));
    if (!matrix.elements.every(isFiniteNumber)) return;
    for (const child of geometry.children) {
      collectWorldVertices(child, matrix, out);
    }
    return;
  }
  const matrix = new THREE.Matrix4().multiplyMatrices(parent, pythonTransformToMatrix4(geometry.transform));
  if (!matrix.elements.every(isFiniteNumber)) return;
  if (geometry.type === 'mesh') {
    const vertices = geometry.vertices as Vec3[];
    if (vertices.length === 0) return;
    const point = new THREE.Vector3();
    const stride = Math.max(1, Math.floor(vertices.length / 250));
    for (let i = 0; i < vertices.length; i += stride) {
      const vertex = vertices[i];
      point.set(vertex[0], vertex[1], vertex[2]).applyMatrix4(matrix);
      if (isFiniteNumber(point.x) && isFiniteNumber(point.y) && isFiniteNumber(point.z)) {
        out.push([point.x, point.y, point.z]);
      }
    }
    return;
  }
  const local = localBoundsOf(geometry);
  if (!local) return;
  for (let i = 0; i < 8; i += 1) {
    const x = i & 1 ? local.max[0] : local.min[0];
    const y = i & 2 ? local.max[1] : local.min[1];
    const z = i & 4 ? local.max[2] : local.min[2];
    const p = new THREE.Vector3(x, y, z).applyMatrix4(matrix);
    if (isFiniteNumber(p.x) && isFiniteNumber(p.y) && isFiniteNumber(p.z)) {
      out.push([p.x, p.y, p.z]);
    }
  }
}

/**
 * Dispose every owned resource under the group (geometries unconditionally,
 * materials only when marked owned) and detach the group from its parent.
 */
export function disposeObjectGroup(group: THREE.Object3D): void {
  group.traverse((obj) => {
    const anyObj = obj as unknown as { geometry?: THREE.BufferGeometry };
    if (anyObj.geometry) anyObj.geometry.dispose();
    const mesh = obj as unknown as { material?: THREE.Material | THREE.Material[] };
    if (mesh.material) {
      const list = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      for (const m of list) {
        if ((m as unknown as { userData?: { owned?: boolean } }).userData?.owned) {
          m.dispose();
        }
      }
    }
  });
  group.removeFromParent();
}

/**
 * Write a backend FeaObjectField into the mesh's `aDamage`/`aDisplacement`
 * attributes (set once per result, not per frame; `needsUpdate` is flagged
 * unconditionally). Returns false when the field's vertex_count does not match
 * the geometry, or when the attributes are missing — the caller then falls
 * back to the procedural path.
 */
export function applyFeaObjectField(mesh: THREE.Mesh, field: FeaObjectField): boolean {
  const geometry = mesh.geometry;
  const damage = geometry.getAttribute('aDamage');
  const displacement = geometry.getAttribute('aDisplacement');
  if (
    !(damage instanceof THREE.BufferAttribute) ||
    !(displacement instanceof THREE.BufferAttribute)
  ) {
    return false;
  }
  if (field.vertex_count !== damage.count || field.vertex_count !== displacement.count) {
    return false;
  }
  const damageArray = damage.array as Float32Array;
  const displacementArray = displacement.array as Float32Array;
  for (let i = 0; i < field.vertex_count; i += 1) {
    damageArray[i] = field.damage[i] ?? 0;
    const offset = i * 3;
    const d = field.displacement[i];
    displacementArray[offset] = d ? d[0] : 0;
    displacementArray[offset + 1] = d ? d[1] : 0;
    displacementArray[offset + 2] = d ? d[2] : 0;
  }
  damage.needsUpdate = true;
  displacement.needsUpdate = true;
  return true;
}

/**
 * Collect every `isMesh` descendant of a scene object group, in traversal
 * order — the mesh list the runtime agent walks to apply FEA fields.
 */
/**
 * Normalized von Mises bending-stress shape of a simply-supported rectangular
 * plate under uniform pressure (Navier series, odd terms 1..15), mirrored
 * 1:1 from the backend's `mouse_sim/fea.py` plate display field. The shape is
 * dimensionless: the isotropic plate stiffness (D, p, 6/t^2) cancels in the
 * normalization, leaving only the panel dimensions a/b and Poisson's ratio.
 * (x, y) are PANEL coordinates in [0, a] x [0, b]; the maximum sits at the
 * panel center (a/2, b/2).
 */
export function plateStressShape(x: number, y: number, a: number, b: number): number {
  const NAVIER_TERMS = [1, 3, 5, 7, 9, 11, 13, 15];
  // Isotropic Poisson ratio used by the moment combination (D12 = nu*D,
  // D66 = D*(1-nu)/2). The backend uses the resolved material's value; the
  // shape difference over polymer materials is a few percent.
  const NU = 0.35;
  let mxx = 0;
  let myy = 0;
  let mxy = 0;
  for (const m of NAVIER_TERMS) {
    const alpha = (Math.PI * m) / a;
    const alpha2 = alpha * alpha;
    const sinx = Math.sin(alpha * x);
    const cosx = Math.cos(alpha * x);
    const mOverA = m / a;
    for (const n of NAVIER_TERMS) {
      const beta = (Math.PI * n) / b;
      const beta2 = beta * beta;
      const nOverB = n / b;
      const den = mOverA ** 4 + 2 * (mOverA ** 2) * (nOverB ** 2) + nOverB ** 4;
      const coeff = 1 / (m * n * den);
      const s = sinx * Math.sin(beta * y);
      mxx += coeff * alpha2 * s;
      myy += coeff * beta2 * s;
      mxy += coeff * alpha * beta * cosx * Math.cos(beta * y);
    }
  }
  const mx = -(mxx + NU * myy);
  const my = -(NU * mxx + myy);
  // 2*D66 = D*(1-nu) for the isotropic plate (D66 = D*(1-nu)/2).
  const txy = (1 - NU) * mxy;
  return Math.sqrt(Math.max(0, mx * mx + my * my - mx * my + 3 * txy * txy));
}

/**
 * Fill `aDamage` for geometry WITHOUT a backend per-vertex field (analytic
 * primitives): evaluate the plate display field on the mesh's own local
 * vertices, mapping the bounding box onto the panel domain exactly like the
 * backend (`x_panel = a/2 + (x - cx) * a/x_extent`), with a = max extent,
 * b = min extent. The damage is min(1, sigma_peak/yield * raw/rawCenter) so
 * the contour peak equals the shell peak stress. The dent displacement stays
 * zero here — the shader's procedural dent complement covers primitives.
 * Returns false when the geometry/field data is unusable.
 */
export function applyFeaPlateField(mesh: THREE.Mesh, fea: FeaResult): boolean {
  const geometry = mesh.geometry;
  const damageAttr = geometry.getAttribute('aDamage');
  const positionAttr = geometry.getAttribute('position');
  if (
    !(damageAttr instanceof THREE.BufferAttribute) ||
    !(positionAttr instanceof THREE.BufferAttribute)
  ) {
    return false;
  }
  const peakPa = fea.peak?.stress_pa;
  const yieldPa = fea.yield_stress_pa;
  if (
    typeof peakPa !== 'number' ||
    !Number.isFinite(peakPa) ||
    peakPa <= 0 ||
    typeof yieldPa !== 'number' ||
    !Number.isFinite(yieldPa) ||
    yieldPa <= 0
  ) {
    return false;
  }
  const positions = positionAttr.array as Float32Array;
  const count = positionAttr.count;
  let xmin = Infinity;
  let xmax = -Infinity;
  let ymin = Infinity;
  let ymax = -Infinity;
  for (let i = 0; i < count; i += 1) {
    const x = positions[i * 3];
    const y = positions[i * 3 + 1];
    if (x < xmin) xmin = x;
    if (x > xmax) xmax = x;
    if (y < ymin) ymin = y;
    if (y > ymax) ymax = y;
  }
  const xExtent = xmax - xmin;
  const yExtent = ymax - ymin;
  if (!(xExtent > 0) || !(yExtent > 0)) return false;
  const a = Math.max(xExtent, yExtent);
  const b = Math.min(xExtent, yExtent);
  const cx = (xmin + xmax) / 2;
  const cy = (ymin + ymax) / 2;
  const centerRaw = plateStressShape(a / 2, b / 2, a, b);
  if (!(centerRaw > 0) || !Number.isFinite(centerRaw)) return false;
  const peakDamage = peakPa / yieldPa;
  const damageArray = damageAttr.array as Float32Array;
  for (let i = 0; i < count; i += 1) {
    const xPanel = a / 2 + (positions[i * 3] - cx) * (a / xExtent);
    const yPanel = b / 2 + (positions[i * 3 + 1] - cy) * (b / yExtent);
    const raw = plateStressShape(xPanel, yPanel, a, b);
    const damage =
      raw > 0 && Number.isFinite(raw) ? Math.min(1, peakDamage * (raw / centerRaw)) : 0;
    damageArray[i] = damage;
  }
  damageAttr.needsUpdate = true;
  return true;
}

export function objectMeshesFor(group: THREE.Object3D): THREE.Mesh[] {
  const meshes: THREE.Mesh[] = [];
  group.traverse((obj) => {
    if ((obj as THREE.Mesh).isMesh) meshes.push(obj as THREE.Mesh);
  });
  return meshes;
}
