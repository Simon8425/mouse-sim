import * as THREE from 'three';
import type { Vec3, GeometryJson, RigidTransformJson, CompoundGeometryJson } from '../api/contracts';
import { IDENTITY_TRANSFORM } from '../api/contracts';
import { paletteKeyForComponent, type PaletteKey } from './materialPalette';

export interface ObjectSceneEntry {
  id: string;
  geometry: GeometryJson;
  className?: string | null;
}

export interface FactoryOptions {
  quality?: 'high' | 'medium' | 'low';
  materials?: Partial<Record<PaletteKey, THREE.Material>>;
  wireframe?: boolean;
}

interface Bounds {
  min: Vec3;
  max: Vec3;
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
  const t = transform ?? IDENTITY_TRANSFORM;
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

  function buildMesh(geometry: GeometryJson): THREE.Mesh {
    const key = paletteKeyForComponent(entry.className ?? null);
    const material = resolveMaterial(key);
    let mesh: THREE.Mesh;

    switch (geometry.type) {
      case 'box': {
        const size = geometry.size as Vec3;
        mesh = new THREE.Mesh(new THREE.BoxGeometry(size[0], size[1], size[2]), material);
        break;
      }
      case 'sphere': {
        mesh = new THREE.Mesh(
          new THREE.SphereGeometry(geometry.radius, sphereSegments, sphereRings),
          material,
        );
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
        mesh = new THREE.Mesh(geometryCyl, material);
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
        mesh = new THREE.Mesh(geometryCone, material);
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
        mesh = new THREE.Mesh(geometryFrustum, material);
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
        bufferGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        const indexFlat: number[] = [];
        for (const t of geometry.triangles) {
          indexFlat.push(t[0], t[1], t[2]);
        }
        const vertexCount = vertices.length;
        if (vertexCount <= 65535) {
          bufferGeometry.setIndex(new THREE.BufferAttribute(new Uint16Array(indexFlat), 1));
        } else {
          bufferGeometry.setIndex(new THREE.BufferAttribute(new Uint32Array(indexFlat), 1));
        }
        bufferGeometry.computeVertexNormals();
        bufferGeometry.computeBoundingSphere();
        mesh = new THREE.Mesh(bufferGeometry, material);
        if (opts?.wireframe) {
          const edges = new THREE.LineSegments(
            new THREE.EdgesGeometry(bufferGeometry, 20),
            new THREE.LineBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.35 }),
          );
          edges.userData.owned = true;
          mesh.add(edges);
        }
        break;
      }
      default: {
        throw new Error(`Unsupported geometry type: ${(geometry as GeometryJson).type}`);
      }
    }

    return mesh;
  }

  if (geometryIsCompound(entry.geometry)) {
    const container = new THREE.Group();
    entry.geometry.children.forEach((child: GeometryJson, index: number) => {
      const childGroup = createObjectGroup(
        { id: `${entry.id}:${index}`, geometry: child, className: entry.className },
        opts,
      );
      container.add(childGroup);
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
  return worldBoundsRecursive(geometry, new THREE.Matrix4());
}

function worldBoundsRecursive(geometry: GeometryJson, parent: THREE.Matrix4): Bounds {
  if (geometry.type === 'compound') {
    const matrix = new THREE.Matrix4().multiplyMatrices(parent, pythonTransformToMatrix4(geometry.transform));
    let min: Vec3 = [Infinity, Infinity, Infinity];
    let max: Vec3 = [-Infinity, -Infinity, -Infinity];
    for (const child of geometry.children) {
      const childBounds = worldBoundsRecursive(child, matrix);
      min = [Math.min(min[0], childBounds.min[0]), Math.min(min[1], childBounds.min[1]), Math.min(min[2], childBounds.min[2])];
      max = [Math.max(max[0], childBounds.max[0]), Math.max(max[1], childBounds.max[1]), Math.max(max[2], childBounds.max[2])];
    }
    return { min, max };
  }

  const local = localBoundsOf(geometry);
  const matrix = new THREE.Matrix4().multiplyMatrices(parent, pythonTransformToMatrix4(geometry.transform));
  let min: Vec3 = [Infinity, Infinity, Infinity];
  let max: Vec3 = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < 8; i += 1) {
    const x = i & 1 ? local.max[0] : local.min[0];
    const y = i & 2 ? local.max[1] : local.min[1];
    const z = i & 4 ? local.max[2] : local.min[2];
    const p = new THREE.Vector3(x, y, z).applyMatrix4(matrix);
    min = [Math.min(min[0], p.x), Math.min(min[1], p.y), Math.min(min[2], p.z)];
    max = [Math.max(max[0], p.x), Math.max(max[1], p.y), Math.max(max[2], p.z)];
  }
  return { min, max };
}

function localBoundsOf(geometry: GeometryJson): Bounds {
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
      let min: Vec3 = [Infinity, Infinity, Infinity];
      let max: Vec3 = [-Infinity, -Infinity, -Infinity];
      for (const v of vertices) {
        min = [Math.min(min[0], v[0]), Math.min(min[1], v[1]), Math.min(min[2], v[2])];
        max = [Math.max(max[0], v[0]), Math.max(max[1], v[1]), Math.max(max[2], v[2])];
      }
      return { min, max };
    }
    case 'compound':
      return { min: [0, 0, 0], max: [0, 0, 0] };
    default: {
      throw new Error(`Unsupported geometry type: ${(geometry as GeometryJson).type}`);
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
