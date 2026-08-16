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

  it('clears and disposes every overlay mesh, sprite, and material', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    const scene = new THREE.Scene();
    const layer = createOverlayLayer(scene, { planeRadius: 1 });
    const spec: OverlaySpec = {
      loadVector: { origin: [0, 0, 0], direction: [0, 0, -1] },
      fixtures: [{ name: 'fixture', location: [0.1, 0.1, 0] }],
      stressBadge: { location: [0, 0, 0.2], label: 'stress (filtered)', color: 0xd94f30 },
      contactPlane: { normal: [0, 0, 1], point: [0, 0, 0] },
      severityMarkers: [{ id: 'a', location: [0, 0, 0.4], severity: 'warning' }],
      selectionAnchor: [0, 0, 0.5],
    };

    layer.apply(spec);
    const layerGroup = scene.getObjectByName('OverlayLayer');
    expect(layerGroup?.children.length).toBeGreaterThan(0);
    const disposeSpies: ReturnType<typeof vi.spyOn>[] = [];
    layerGroup?.traverse((obj) => {
      const resource = obj as THREE.Mesh & { material?: THREE.Material | THREE.Material[] };
      if (resource.geometry) disposeSpies.push(vi.spyOn(resource.geometry, 'dispose'));
      if (resource.material) {
        const materials = Array.isArray(resource.material)
          ? resource.material
          : [resource.material];
        for (const material of materials) disposeSpies.push(vi.spyOn(material, 'dispose'));
      }
    });
    expect(disposeSpies.length).toBeGreaterThan(0);

    layer.clear();

    expect(layerGroup?.children.length).toBe(0);
    for (const spy of disposeSpies) expect(spy).toHaveBeenCalled();
  });

  it('re-applies a new spec after clear() and keeps the layer attached', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    const scene = new THREE.Scene();
    const layer = createOverlayLayer(scene, { planeRadius: 1 });

    layer.apply(contactPlaneSpec());
    const layerGroup = scene.getObjectByName('OverlayLayer');
    expect(layerGroup?.children.length).toBeGreaterThan(0);

    layer.clear();
    expect(layerGroup?.children.length).toBe(0);

    layer.apply(contactPlaneSpec());
    expect(layerGroup?.children.length).toBeGreaterThan(0);
    expect(scene.getObjectByName('OverlayLayer')).toBe(layerGroup);
    layer.dispose();
  });

  it('removes and disposes all overlays and label textures on dispose()', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    const scene = new THREE.Scene();
    const layer = createOverlayLayer(scene, { planeRadius: 1 });
    const spec: OverlaySpec = {
      loadVector: { origin: [0, 0, 0], direction: [0, 0, -1] },
      fixtures: null,
      stressBadge: { location: [0, 0, 0.2] },
      contactPlane: null,
      severityMarkers: null,
      selectionAnchor: null,
    };

    layer.apply(spec);
    const layerGroup = scene.getObjectByName('OverlayLayer');
    expect(layerGroup).not.toBeNull();
    const loadSprite = layerGroup?.children.find(
      (child): child is THREE.Sprite => child instanceof THREE.Sprite,
    );
    const loadSpriteMat = loadSprite?.material as THREE.SpriteMaterial | undefined;
    const loadTexture = loadSpriteMat?.map;
    const textureDispose = loadTexture ? vi.spyOn(loadTexture, 'dispose') : null;

    layer.dispose();

    expect(scene.getObjectByName('OverlayLayer')).toBeUndefined();
    expect(textureDispose?.mock.calls).toHaveLength(1);
  });
});
