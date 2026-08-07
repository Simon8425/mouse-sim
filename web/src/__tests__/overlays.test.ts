import { afterEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import { createOverlayLayer, type OverlaySpec } from '../scene/overlays';

function contactPlaneSpec(): OverlaySpec {
  return {
    loadVector: null,
    fixtures: null,
    stressBadge: null,
    contactPlane: { normal: [0, 0, 1], point: [0, 0, 0] },
    severityMarkers: null,
    selectionAnchor: null,
  };
}

function planeMesh(scene: THREE.Scene): THREE.Mesh {
  const layer = scene.getObjectByName('OverlayLayer');
  const plane = layer?.children.find(
    (child): child is THREE.Mesh => child instanceof THREE.Mesh && child.geometry instanceof THREE.PlaneGeometry,
  );
  if (!plane) throw new Error('Expected contact plane overlay');
  return plane;
}

function labelSprite(scene: THREE.Scene): THREE.Sprite {
  const layer = scene.getObjectByName('OverlayLayer');
  const sprite = layer?.children.find((child): child is THREE.Sprite => child instanceof THREE.Sprite);
  if (!sprite) throw new Error('Expected contact plane label');
  return sprite;
}

describe('overlay radius and resource lifecycle', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('rescales the current overlay when model bounds change', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    const scene = new THREE.Scene();
    const layer = createOverlayLayer(scene, { planeRadius: 1 });
    const spec = contactPlaneSpec();

    layer.apply(spec);
    const initialPlane = planeMesh(scene);
    const initialTexture = (labelSprite(scene).material as THREE.SpriteMaterial).map;
    const textureDispose = initialTexture ? vi.spyOn(initialTexture, 'dispose') : null;

    expect((initialPlane.geometry as THREE.PlaneGeometry).parameters.width).toBe(2);
    expect(labelSprite(scene).scale.x).toBeCloseTo(0.35);

    layer.setPlaneRadius(2);

    const updatedPlane = planeMesh(scene);
    const updatedSprite = labelSprite(scene);
    expect((updatedPlane.geometry as THREE.PlaneGeometry).parameters.width).toBe(4);
    expect(updatedSprite.scale.x).toBeCloseTo(0.7);
    expect((updatedSprite.material as THREE.SpriteMaterial).map).toBe(initialTexture);
    expect(textureDispose?.mock.calls).toHaveLength(0);

    layer.dispose();
    expect(textureDispose?.mock.calls).toHaveLength(1);
  });

  it('does not rebuild overlays for an equivalent spec and radius', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    const scene = new THREE.Scene();
    const layer = createOverlayLayer(scene, { planeRadius: 1 });
    const spec = contactPlaneSpec();

    layer.apply(spec);
    const firstPlane = planeMesh(scene);
    layer.apply({ ...spec, contactPlane: { ...spec.contactPlane! } });

    expect(planeMesh(scene)).toBe(firstPlane);
    layer.dispose();
  });
});
