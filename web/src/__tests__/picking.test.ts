import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { pickObjectId } from '../scene/picking';

function setupScene(): {
  canvas: HTMLCanvasElement;
  camera: THREE.PerspectiveCamera;
  root: THREE.Group;
  visibleMesh: THREE.Mesh;
  hiddenMesh: THREE.Mesh;
} {
  const canvas = document.createElement('canvas');
  canvas.width = 100;
  canvas.height = 100;
  Object.defineProperty(canvas, 'getBoundingClientRect', {
    value: () => ({ left: 0, top: 0, width: 100, height: 100, right: 100, bottom: 100 }),
  });

  const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
  camera.position.set(0, 0, 10);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);

  const root = new THREE.Group();

  const makePart = (id: string, x: number): THREE.Mesh => {
    const outer = new THREE.Group();
    outer.userData.objectId = id;
    const mesh = new THREE.Mesh(
      new THREE.BoxGeometry(1, 1, 1),
      new THREE.MeshBasicMaterial(),
    );
    mesh.position.set(x, 0, 0);
    outer.add(mesh);
    root.add(outer);
    return mesh;
  };

  const visibleMesh = makePart('visible-part', 0);
  const hiddenMesh = makePart('hidden-part', 0); // exactly behind the visible part
  hiddenMesh.parent!.visible = false;

  return { canvas, camera, root, visibleMesh, hiddenMesh };
}

describe('pickObjectId', () => {
  it('picks a visible part at the click point', () => {
    const { canvas, camera, root } = setupScene();
    // Center of the canvas -> ray straight down -z through the visible box.
    const id = pickObjectId(canvas, camera, root, 50, 50);
    expect(id).toBe('visible-part');
  });

  it('skips a hidden part and picks the visible part behind it', () => {
    const { canvas, camera, root, visibleMesh, hiddenMesh } = setupScene();
    // The hidden part is exactly at the ray's first hit; the visible part is
    // directly behind it. The picker must skip the hidden mesh and select
    // the visible one instead of returning the hidden id or null.
    const id = pickObjectId(canvas, camera, root, 50, 50);
    expect(id).toBe('visible-part');
    expect(hiddenMesh.parent!.visible).toBe(false);
    expect(visibleMesh.parent!.visible).toBe(true);
  });
});
