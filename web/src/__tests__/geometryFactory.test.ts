import { describe, it, expect, vi } from 'vitest';
import * as THREE from 'three';
import {
  pythonTransformToMatrix4,
  createObjectGroup,
  worldBoundsForGeometry,
  disposeObjectGroup,
} from '../scene/geometryFactory';
import {
  IDENTITY_TRANSFORM,
  type BoxGeometryJson,
  type CylinderGeometryJson,
  type ConeGeometryJson,
  type FrustumGeometryJson,
  type MeshGeometryJson,
  type CompoundGeometryJson,
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
