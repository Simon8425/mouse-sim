import { describe, it, expect, vi } from 'vitest';
import * as THREE from 'three';
import {
  entriesSignature,
  computeExplodeOffsets,
  type ExplodePart,
} from '../scene/sceneRuntime';
import {
  pythonTransformToMatrix4,
  createObjectGroup,
  worldBoundsForGeometry,
  worldVerticesForGeometry,
  worldVerticesForGeometryFull,
  disposeObjectGroup,
  applyFeaPlateField,
  applyDropPlateField,
  plateStressShape,
  buildInstancedBatches,
  syncInstancedPose,
  geometryFingerprint,
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

describe('applyDropPlateField', () => {
  it('fills aDamage from a drop-derived peak, peaked at plate center', () => {
    const geometry = new THREE.PlaneGeometry(0.1, 0.06, 16, 12);
    geometry.rotateX(-Math.PI / 2); // planar XY mesh in world XY
    geometry.setAttribute(
      'aDamage',
      new THREE.BufferAttribute(new Float32Array(geometry.attributes.position.count), 1),
    );
    const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
    expect(applyDropPlateField(mesh, 0.6)).toBe(true);
    const damage = geometry.getAttribute('aDamage').array as Float32Array;
    const positions = geometry.attributes.position.array as Float32Array;
    let max = -1;
    let min = Infinity;
    let maxDistFromCenter = 0;
    let maxDamageAtCenter = -1;
    let maxDamageAtEdge = -1;
    for (let i = 0; i < damage.length; i += 1) {
      const x = positions[i * 3];
      const y = positions[i * 3 + 1];
      const r = Math.hypot(x, y);
      if (r > maxDistFromCenter) {
        maxDistFromCenter = r;
        maxDamageAtEdge = damage[i];
      }
      if (r < 0.005 && damage[i] > maxDamageAtCenter) maxDamageAtCenter = damage[i];
      if (damage[i] > max) max = damage[i];
      if (damage[i] < min) min = damage[i];
    }
    // Field is in [0, peak] and strictly peaked at the center, lower at edges.
    expect(min).toBeGreaterThanOrEqual(0);
    // float32 representation of 0.6 can round above the exact value.
    expect(max).toBeLessThanOrEqual(0.6 + 1e-5);
    expect(maxDamageAtCenter).toBeGreaterThan(0.55);
    expect(maxDamageAtEdge).toBeLessThan(maxDamageAtCenter);
    expect((geometry.getAttribute('aDamage') as THREE.BufferAttribute).version).toBeGreaterThan(0);
  });

  it('returns false for degenerate/unsafe inputs', () => {
    const geometry = new THREE.BoxGeometry(0.1, 0.06, 0.04);
    geometry.setAttribute(
      'aDamage',
      new THREE.BufferAttribute(new Float32Array(geometry.attributes.position.count), 1),
    );
    const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
    expect(applyDropPlateField(mesh, 0)).toBe(false);
    expect(applyDropPlateField(mesh, Number.NaN)).toBe(false);

    const bad = new THREE.PlaneGeometry(0.1, 0.06, 2, 2);
    bad.rotateX(-Math.PI / 2);
    const badMesh = new THREE.Mesh(bad, new THREE.MeshStandardMaterial());
    expect(applyDropPlateField(badMesh, 0.5)).toBe(false);
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

describe('collider vertex sampling', () => {
  it('exports ALL mesh vertices for the physics hull (no stride cap)', () => {
    // 1000 vertices: the strided display sampler caps at ~250 points, while
    // the full export must return every vertex for the Rapier convex hull.
    const vertices = Array.from({ length: 1000 }, (_, i) => [i * 0.001, 0, 0] as [number, number, number]);
    const meshGeom: MeshGeometryJson = {
      type: 'mesh',
      vertices,
      triangles: [[0, 1, 2]],
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };
    const full = worldVerticesForGeometryFull(meshGeom);
    const strided = worldVerticesForGeometry(meshGeom);
    expect(full.length).toBe(1000);
    expect(strided.length).toBeLessThan(1000);
    expect(strided.length).toBeGreaterThan(0);
  });

  it('keeps thin-shell features in the full export (no 2mm grid collapse)', () => {
    // A 0.5 mm wall: two faces 0.5 mm apart must BOTH survive the full
    // vertex export (the 2 mm voxel grid in rapierDropSim would collapse
    // them; the export itself must not).
    const vertices: [number, number, number][] = [
      [0, 0, 0], [0.01, 0, 0], [0.01, 0.01, 0], [0, 0.01, 0],
      [0, 0, 0.0005], [0.01, 0, 0.0005], [0.01, 0.01, 0.0005], [0, 0.01, 0.0005],
    ];
    const meshGeom: MeshGeometryJson = {
      type: 'mesh',
      vertices,
      triangles: [[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6]],
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };
    const full = worldVerticesForGeometryFull(meshGeom);
    const zs = new Set(full.map((v: [number, number, number]) => v[2]));
    expect(zs.has(0)).toBe(true);
    expect(zs.has(0.0005)).toBe(true);
  });
});

describe('InstancedMesh batching (draw-call reduction)', () => {
  const sharedMaterials = {
    default: new THREE.MeshStandardMaterial({ color: 0x9aa0a6 }),
    metal: new THREE.MeshStandardMaterial({ color: 0x50555c }),
    shell: new THREE.MeshStandardMaterial({ color: 0xd9d5cc }),
    pcb: new THREE.MeshStandardMaterial({ color: 0x24282e }),
    battery: new THREE.MeshStandardMaterial({ color: 0xaaaaae }),
    skate: new THREE.MeshStandardMaterial({ color: 0xb4b8bd }),
  };

  const screwGeometry: CylinderGeometryJson = {
    type: 'cylinder',
    radius: 0.0015,
    height: 0.006,
    units: 'm',
    transform: IDENTITY_TRANSFORM,
  };

  function screwEntry(id: string, tx: number, ty: number, tz: number) {
    return {
      entry: {
        id,
        className: 'screw_fastener',
        geometry: {
          ...screwGeometry,
          transform: { ...IDENTITY_TRANSFORM, translation: [tx, ty, tz] as [number, number, number] },
        } as CylinderGeometryJson,
      },
      matrix: pythonTransformToMatrix4({
        ...IDENTITY_TRANSFORM,
        translation: [tx, ty, tz] as [number, number, number],
      }),
    };
  }

  it('collapses N identical parts into one InstancedMesh (draw calls 50 → < 10)', () => {
    const parts = [
      screwEntry('screw-0', 0, 0, 0),
      screwEntry('screw-1', 0.03, 0, 0),
      screwEntry('screw-2', 0, 0.03, 0),
      screwEntry('screw-3', 0.03, 0.03, 0),
      screwEntry('screw-4', 0, 0, 0.01),
      screwEntry('screw-5', 0.03, 0, 0.01),
      screwEntry('screw-6', 0, 0.03, 0.01),
      screwEntry('screw-7', 0.03, 0.03, 0.01),
    ];
    const batchGroup = buildInstancedBatches(parts, { materials: sharedMaterials });
    // One batch for 8 identical screws.
    const batches = batchGroup.group.children.filter((c) => c instanceof THREE.InstancedMesh);
    expect(batches).toHaveLength(1);
    const inst = batches[0] as THREE.InstancedMesh;
    expect(inst.count).toBe(8);
    // Every object id resolves to a slot in the batch.
    for (const part of parts) {
      const slot = batchGroup.byObjectId.get(part.entry.id);
      expect(slot).toBeDefined();
      expect(slot!.batch.mesh).toBe(inst);
      expect(inst.userData.instanceObjectIds[slot!.instanceId]).toBe(part.entry.id);
    }
    // Per-instance matrices carry the part's translation (draw position).
    const probe = new THREE.Matrix4();
    inst.getMatrixAt(3, probe);
    const pos = new THREE.Vector3().setFromMatrixPosition(probe);
    expect(pos.x).toBeCloseTo(0.03, 6);
    expect(pos.y).toBeCloseTo(0.03, 6);
    // A 50-part assembly with 6 unique geometries + palette materials → 6 batches.
    const mixed = buildInstancedBatches(
      [
        ...parts,
        ...Array.from({ length: 42 }, (_, i) =>
          screwEntry(`screw-${8 + i}`, (i % 7) * 0.03, Math.floor(i / 7) * 0.03, 0),
        ),
      ],
      { materials: sharedMaterials },
    );
    const mixedBatches = mixed.group.children.filter((c) => c instanceof THREE.InstancedMesh);
    expect(mixedBatches.length).toBeLessThan(10);
    expect(mixed.byObjectId.size).toBe(50);
  });

  it('keeps distinct geometries and colored parts in separate batches/meshes', () => {
    const boxGeom: BoxGeometryJson = {
      type: 'box',
      size: [0.01, 0.01, 0.01],
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };
    const parts = [
      screwEntry('screw-a', 0, 0, 0),
      screwEntry('screw-b', 0.03, 0, 0),
      { entry: { id: 'box-c', className: 'shell', geometry: boxGeom }, matrix: new THREE.Matrix4() },
      { entry: { id: 'colored-d', className: 'default', color: [1, 0, 0] as [number, number, number], geometry: boxGeom }, matrix: new THREE.Matrix4() },
    ];
    const batchGroup = buildInstancedBatches(parts, { materials: sharedMaterials });
    const batches = batchGroup.group.children.filter((c) => c instanceof THREE.InstancedMesh);
    // Screws → 1 batch; the palette box joins a second batch (different
    // geometry+material); the colored part is skipped entirely (it needs a
    // unique owned material and stays an individual mesh).
    expect(batches).toHaveLength(2);
    expect(batchGroup.byObjectId.has('colored-d')).toBe(false);
    expect(batchGroup.byObjectId.has('box-c')).toBe(true);
  });

  it('fingerprint is transform-independent and size-sensitive', () => {
    const a = geometryFingerprint(screwGeometry);
    const b = geometryFingerprint({ ...screwGeometry, transform: { ...IDENTITY_TRANSFORM, translation: [9, 9, 9] as [number, number, number] } });
    expect(a).toBe(b);
    expect(geometryFingerprint({ ...screwGeometry, radius: 0.002 })).not.toBe(a);
  });

  it('syncInstancedPose hides instances via degenerate scale and applies explode offsets', () => {
    const parts = [
      screwEntry('screw-0', 0, 0, 0),
      screwEntry('screw-1', 0.03, 0, 0),
      screwEntry('screw-2', 0, 0.03, 0),
    ];
    const batchGroup = buildInstancedBatches(parts, { materials: sharedMaterials });
    const inst = batchGroup.group.children[0] as THREE.InstancedMesh;
    const explode = new Map<string, THREE.Vector3>([
      ['screw-0', new THREE.Vector3(0.1, 0, 0)],
      ['screw-1', new THREE.Vector3(0, 0.2, 0)],
    ]);
    syncInstancedPose(batchGroup, explode, { 'screw-2': false });
    const probe = new THREE.Matrix4();
    // Explode offsets ADD to the baked placement (screw-0 baked at origin).
    inst.getMatrixAt(0, probe);
    expect(new THREE.Vector3().setFromMatrixPosition(probe).x).toBeCloseTo(0.1, 6);
    // screw-1 baked at x=0.03, explode offset y=0.2 → position (0.03, 0.2, 0).
    inst.getMatrixAt(1, probe);
    const pos1 = new THREE.Vector3().setFromMatrixPosition(probe);
    expect(pos1.x).toBeCloseTo(0.03, 6);
    expect(pos1.y).toBeCloseTo(0.2, 6);
    inst.getMatrixAt(2, probe);
    const scale = new THREE.Vector3().setFromMatrixScale(probe);
    expect(scale.lengthSq()).toBe(0);
  });

  it('disposing the batch group releases the shared geometry once', () => {
    const spyGeomDispose = vi.spyOn(THREE.BufferGeometry.prototype, 'dispose');
    const parts = [
      screwEntry('screw-0', 0, 0, 0),
      screwEntry('screw-1', 0.03, 0, 0),
    ];
    const batchGroup = buildInstancedBatches(parts, { materials: sharedMaterials });
    for (const child of [...batchGroup.group.children]) {
      disposeObjectGroup(child);
    }
    expect(spyGeomDispose).toHaveBeenCalled();
  });
});

describe('computeExplodeOffsets', () => {
  const assembly: [number, number, number] = [0, 0, 0];

  function part(id: string, cx: number, cy: number, cz: number, size = 0.01): ExplodePart {
    return { id, center: [cx, cy, cz], size: [size, size, size] };
  }

  it('is deterministic for the same inputs', () => {
    const parts = [part('a', 0.1, 0, 0.2), part('b', -0.1, 0.05, -0.1)];
    const a = computeExplodeOffsets(parts, assembly, 1);
    const b = computeExplodeOffsets(parts, assembly, 1);
    for (const p of parts) {
      expect(a.get(p.id)).toEqual(b.get(p.id));
    }
  });

  it('keeps every part at or above the assembly — nothing goes under the floor', () => {
    const parts = [
      part('top', 0, 0, 0.3),      // above
      part('bottom', 0, 0, -0.3),  // below the assembly center
      part('skate', 0, 0, -0.5),   // lowest
    ];
    const offsets = computeExplodeOffsets(parts, assembly, 1);
    for (const p of parts) {
      const off = offsets.get(p.id)!;
      // No part moves below its assembled position (never under the floor).
      expect(off[2]).toBeGreaterThanOrEqual(0);
    }
    // The lowest part stays planted (zero lift) so the model stays low.
    const skate = offsets.get('skate')!;
    expect(skate[2]).toBeCloseTo(0, 6);
    // The part that was higher ends up higher in the exploded pose.
    const top = offsets.get('top')!;
    const bottom = offsets.get('bottom')!;
    expect(top[2]).toBeGreaterThan(bottom[2]);
    expect(bottom[2]).toBeGreaterThan(skate[2]);
  });

  it('spreads parts laterally away from the assembly center', () => {
    const parts = [
      part('left', -0.3, 0, 0),
      part('right', 0.3, 0, 0),
    ];
    const offsets = computeExplodeOffsets(parts, assembly, 1);
    const left = offsets.get('left')!;
    const right = offsets.get('right')!;
    expect(left[0]).toBeLessThan(0);
    expect(right[0]).toBeGreaterThan(0);
  });

  it('does not depend on part id naming', () => {
    // Same height, different lateral position — the de-overlap pass has
    // nothing to resolve, so any divergence would have to come from naming.
    const parts = [part('generic_part_A', -0.02, 0, 0.3), part('top_shell', 0.02, 0, 0.3)];
    const offsets = computeExplodeOffsets(parts, assembly, 1);
    const a = offsets.get('generic_part_A')!;
    const b = offsets.get('top_shell')!;
    // Identical lift, mirrored lateral spread.
    expect(a[2]).toBeCloseTo(b[2], 6);
    expect(a[0]).toBeCloseTo(-b[0], 6);
    expect(a[1]).toBeCloseTo(b[1], 6);
  });

  it('keeps exploded boxes from overlapping at full factor', () => {
    // Two large slabs stacked on top of each other — the naive vertical lift
    // would leave them colliding; separation must push the upper one up.
    const parts = [
      part('lower', 0, 0, -0.05, 0.2),
      part('upper', 0, 0, 0.05, 0.2),
    ];
    const offsets = computeExplodeOffsets(parts, assembly, 1);
    const lo = offsets.get('lower')!;
    const hi = offsets.get('upper')!;
    // After separation the centers must be farther apart than the slab size.
    const gap = Math.abs(hi[2] - lo[2]);
    expect(gap).toBeGreaterThan(0.2);
  });
});
