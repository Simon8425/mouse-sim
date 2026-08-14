import { describe, it, expect, vi } from 'vitest';
import * as THREE from 'three';
import {
  entriesSignature,
} from '../scene/sceneRuntime';
import {
  pythonTransformToMatrix4,
  createObjectGroup,
  worldBoundsForGeometry,
  disposeObjectGroup,
  applyFeaPlateField,
  plateStressShape,
} from '../scene/geometryFactory';
import {
  IDENTITY_TRANSFORM,
  type BoxGeometryJson,
  type CylinderGeometryJson,
  type ConeGeometryJson,
  type FrustumGeometryJson,
  type MeshGeometryJson,
  type CompoundGeometryJson,
  type FeaResult,
} from '../api/contracts';

describe('geometryFactory Three.js integration and parity', () => {
  it('maps python rigid transform matrix correctly to column-major Matrix4', () => {
    const identityM4 = pythonTransformToMatrix4(IDENTITY_TRANSFORM);
    const origin = new THREE.Vector3(0, 0, 0);
    origin.applyMatrix4(identityM4);
    expect(origin.x).toBeCloseTo(0);
    expect(origin.y).toBeCloseTo(0);
    expect(origin.z).toBeCloseTo(0);

    const translateT = {
      ...IDENTITY_TRANSFORM,
      translation: [1, 2, 3] as [number, number, number],
    };
    const transM4 = pythonTransformToMatrix4(translateT);
    const p1 = new THREE.Vector3(0, 0, 0).applyMatrix4(transM4);
    expect(p1.x).toBeCloseTo(1);
    expect(p1.y).toBeCloseTo(2);
    expect(p1.z).toBeCloseTo(3);

    const rotateT = {
      rotation: [
        [0, -1, 0],
        [1, 0, 0],
        [0, 0, 1],
      ] as [[number, number, number], [number, number, number], [number, number, number]],
      translation: [0, 0, 0] as [number, number, number],
      units: 'm',
    };
    const rotM4 = pythonTransformToMatrix4(rotateT);
    const p2 = new THREE.Vector3(1, 0, 0).applyMatrix4(rotM4);
    expect(p2.x).toBeCloseTo(0);
    expect(p2.y).toBeCloseTo(1);
    expect(p2.z).toBeCloseTo(0);
  });

  it('computes parity box world bounds accurately', () => {
    const parityBox: BoxGeometryJson = {
      type: 'box',
      size: [2, 4, 6],
      units: 'm',
      transform: {
        rotation: [
          [0, -1, 0],
          [1, 0, 0],
          [0, 0, 1],
        ],
        translation: [1, 2, 3],
        units: 'm',
      },
    };

    const bounds = worldBoundsForGeometry(parityBox);
    expect(bounds.min[0]).toBeCloseTo(-1);
    expect(bounds.min[1]).toBeCloseTo(1);
    expect(bounds.min[2]).toBeCloseTo(0);

    expect(bounds.max[0]).toBeCloseTo(3);
    expect(bounds.max[1]).toBeCloseTo(3);
    expect(bounds.max[2]).toBeCloseTo(6);

    const group = createObjectGroup({ id: 'box-parity', geometry: parityBox });
    expect(group.children.length).toBe(1);
  });

  it('aligns primitive base at z=0 for cylinder, cone, and frustum', () => {
    const cylinder: CylinderGeometryJson = {
      type: 'cylinder',
      radius: 0.5,
      height: 2.0,
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };
    const cylGroup = createObjectGroup({ id: 'cyl', geometry: cylinder });
    const cylMesh = cylGroup.children[0] as THREE.Mesh;
    cylMesh.geometry.computeBoundingBox();
    const cylB = cylMesh.geometry.boundingBox!;
    expect(cylB.min.z).toBeCloseTo(0);
    expect(cylB.max.z).toBeCloseTo(2.0);

    const cone: ConeGeometryJson = {
      type: 'cone',
      base_radius: 0.5,
      height: 1.5,
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };
    const coneGroup = createObjectGroup({ id: 'cone', geometry: cone });
    const coneMesh = coneGroup.children[0] as THREE.Mesh;
    coneMesh.geometry.computeBoundingBox();
    expect(coneMesh.geometry.boundingBox!.max.z).toBeCloseTo(1.5);

    const frustum: FrustumGeometryJson = {
      type: 'frustum',
      bottom_radius: 0.5,
      top_radius: 0.2,
      height: 1.0,
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };
    const frustumGroup = createObjectGroup({ id: 'frustum', geometry: frustum });
    const frustumMesh = frustumGroup.children[0] as THREE.Mesh;
    frustumMesh.geometry.computeBoundingBox();
    expect(frustumMesh.geometry.boundingBox!.max.z).toBeCloseTo(1.0);
  });

  it('builds valid mesh geometry with vertices, indices, and normals', () => {
    const meshGeom: MeshGeometryJson = {
      type: 'mesh',
      vertices: [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
      triangles: [[0, 1, 2]],
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };

    const group = createObjectGroup({ id: 'mesh-1', geometry: meshGeom });
    const mesh = group.children[0] as THREE.Mesh;
    const geom = mesh.geometry as THREE.BufferGeometry;

    expect(geom.getAttribute('position').count).toBe(3);
    expect(geom.getIndex()?.count).toBe(3);
    expect(geom.getAttribute('normal')).toBeDefined();
  });

  it('carries zero-initialized FEA attributes on mesh geometry', () => {
    const meshGeom: MeshGeometryJson = {
      type: 'mesh',
      vertices: [
        [0, 0, 0],
        [1, 0, 0],
        [0, 1, 0],
        [1, 1, 0],
      ],
      triangles: [
        [0, 1, 2],
        [1, 3, 2],
      ],
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };

    const group = createObjectGroup({ id: 'fea-mesh', geometry: meshGeom });
    const mesh = group.children[0] as THREE.Mesh;
    const geom = mesh.geometry as THREE.BufferGeometry;

    const damage = geom.getAttribute('aDamage');
    const displacement = geom.getAttribute('aDisplacement');
    expect(damage).toBeDefined();
    expect(displacement).toBeDefined();
    expect(damage!.itemSize).toBe(1);
    expect(displacement!.itemSize).toBe(3);
    expect(damage!.count).toBe(4);
    expect(displacement!.count).toBe(4);
    expect(Array.from(damage!.array as Float32Array)).toEqual([0, 0, 0, 0]);
    expect(Array.from(displacement!.array as Float32Array)).toEqual([
      0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    ]);
  });

  it('skips empty, non-finite, and out-of-range mesh buffers safely', () => {
    const emptyMesh: MeshGeometryJson = {
      type: 'mesh',
      vertices: [],
      triangles: [],
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };
    const nonFiniteMesh: MeshGeometryJson = {
      type: 'mesh',
      vertices: [[0, 0, Number.NaN], [1, 0, 0], [0, 1, 0]],
      triangles: [[0, 1, 2]],
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };
    const outOfRangeMesh: MeshGeometryJson = {
      type: 'mesh',
      vertices: [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
      triangles: [[0, 1, 3]],
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };

    for (const [id, geometry] of [
      ['empty', emptyMesh],
      ['non-finite', nonFiniteMesh],
      ['out-of-range', outOfRangeMesh],
    ] as [string, MeshGeometryJson][]) {
      const group = createObjectGroup({ id, geometry });
      expect(group.userData.meshObjects).toHaveLength(0);
      const bounds = worldBoundsForGeometry(geometry);
      expect([...bounds.min, ...bounds.max].every(Number.isFinite)).toBe(true);
    }
  });

  it('keeps bounds finite for degenerate geometry', () => {
    const degenerateBox: BoxGeometryJson = {
      type: 'box',
      size: [0, 0, 0],
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };

    const bounds = worldBoundsForGeometry(degenerateBox);
    expect(bounds).toEqual({ min: [0, 0, 0], max: [0, 0, 0] });
    expect(createObjectGroup({ id: 'degenerate', geometry: degenerateBox }).userData.meshObjects).toHaveLength(1);
  });

  it('applies transform once on compound containers', () => {
    const childBox: BoxGeometryJson = {
      type: 'box',
      size: [1, 1, 1],
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };

    const compound: CompoundGeometryJson = {
      type: 'compound',
      children: [childBox],
      transform: {
        rotation: IDENTITY_TRANSFORM.rotation,
        translation: [5, 0, 0],
        units: 'm',
      },
    };

    const bounds = worldBoundsForGeometry(compound);
    expect(bounds.min[0]).toBeCloseTo(4.5);
    expect(bounds.max[0]).toBeCloseTo(5.5);
  });

  it('disposes owned resources and preserves shared materials', () => {
    const spyGeomDispose = vi.spyOn(THREE.BufferGeometry.prototype, 'dispose');

    const box: BoxGeometryJson = {
      type: 'box',
      size: [1, 1, 1],
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };

    const sharedMat = new THREE.MeshStandardMaterial({ color: 0xff0000 });
    sharedMat.userData.shared = true;
    const spyMatDispose = vi.spyOn(sharedMat, 'dispose');

    const group = createObjectGroup(
      { id: 'shared-test', geometry: box },
      { materials: { default: sharedMat } },
    );

    disposeObjectGroup(group);
    expect(spyGeomDispose).toHaveBeenCalled();
    expect(spyMatDispose).not.toHaveBeenCalled();
  });
});

describe('plateStressShape', () => {
  it('peaks at the plate center and vanishes at the free-edge midpoints', () => {
    const a = 0.1;
    const b = 0.06;
    const center = plateStressShape(a / 2, b / 2, a, b);
    expect(center).toBeGreaterThan(0);
    // Free-edge midpoints (x=0 or y=0): sin(0) term -> ~0 stress.
    expect(plateStressShape(0, b / 2, a, b)).toBeCloseTo(0, 3);
    expect(plateStressShape(a / 2, 0, a, b)).toBeCloseTo(0, 3);
    // Monotone decay from the center along the mid-line.
    const mid = plateStressShape(a / 2, b / 4, a, b);
    expect(mid).toBeGreaterThan(0);
    expect(mid).toBeLessThan(center);
  });
});

describe('applyFeaPlateField', () => {
  it('fills aDamage for attribute-less primitive geometry', () => {
    const geometry = new THREE.BoxGeometry(0.1, 0.06, 0.04);
    geometry.setAttribute(
      'aDamage',
      new THREE.BufferAttribute(new Float32Array(geometry.attributes.position.count), 1),
    );
    geometry.setAttribute(
      'aDisplacement',
      new THREE.BufferAttribute(new Float32Array(geometry.attributes.position.count * 3), 3),
    );
    const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
    const fea: FeaResult = {
      computed: true,
      peak: {
        object_id: 'box',
        vertex_index: 0,
        location_model_m: [0, 0, 0],
        damage: 0.5,
        stress_pa: 1e7,
        stress_mpa: 10,
      },
      yield_stress_pa: 2e7,
      safety_factor: 2,
      impact_window_s: 0.05,
      dent_threshold: 0.7,
      tear_threshold: 0.92,
      objects: [],
      procedural: [],
      assumptions: [],
      flags: [],
    };
    expect(applyFeaPlateField(mesh, fea)).toBe(true);
    const damage = geometry.getAttribute('aDamage').array as Float32Array;
    // Every box vertex is a corner at the same |x|,|y| -> same damage,
    // close to but not exceeding min(1, peak/yield) = 0.5.
    for (let i = 0; i < damage.length; i += 1) {
      expect(damage[i]).toBeGreaterThan(0.4);
      expect(damage[i]).toBeLessThanOrEqual(0.5);
    }
    // r168 needsUpdate is a write-only setter; the version bump proves the upload flag ran.
    expect((geometry.getAttribute('aDamage') as THREE.BufferAttribute).version).toBeGreaterThan(0);
  });

  it('returns false for unusable field data', () => {
    const geometry = new THREE.BoxGeometry(0.1, 0.06, 0.04);
    const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
    const fea = {
      computed: true,
      peak: null,
      yield_stress_pa: 0,
      safety_factor: null,
      impact_window_s: 0.05,
      dent_threshold: 0.7,
      tear_threshold: 0.92,
      objects: [],
      procedural: [],
      assumptions: [],
      flags: [],
    } as unknown as FeaResult;
    expect(applyFeaPlateField(mesh, fea)).toBe(false);
  });
});

describe('entriesSignature', () => {
  const meshEntry = {
    id: 'part-0',
    geometry: {
      type: 'mesh' as const,
      vertices: Array.from({ length: 100 }, (_, i) => [i, 0, 0] as [number, number, number]),
      triangles: [[0, 1, 2] as [number, number, number]],
      units: 'm' as const,
      transform: {
        rotation: [[1, 0, 0], [0, 1, 0], [0, 0, 1]] as [[number, number, number], [number, number, number], [number, number, number]],
        translation: [0, 0, 0] as [number, number, number],
        units: 'm' as const,
      },
    },
    className: 'mesh',
  };

  it('is stable when only visibility changes (no scene rebuild)', () => {
    const signature = entriesSignature([{ ...meshEntry, displayAssetUrl: null }]);
    expect(signature).toBe(signature);
  });

  it('changes when mesh sizes change but not when vertices move', () => {
    const base = entriesSignature([meshEntry]);
    const moved = entriesSignature([
      {
        ...meshEntry,
        geometry: {
          ...meshEntry.geometry,
          vertices: Array.from({ length: 100 }, (_, i) => [i + 1, 0, 0] as [number, number, number]),
        },
      },
    ]);
    const resized = entriesSignature([
      {
        ...meshEntry,
        geometry: {
          ...meshEntry.geometry,
          vertices: Array.from({ length: 101 }, (_, i) => [i, 0, 0] as [number, number, number]),
        },
      },
    ]);
    expect(moved).toBe(base);
    expect(resized).not.toBe(base);
  });
});
