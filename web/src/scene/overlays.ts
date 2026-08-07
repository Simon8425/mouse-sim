import * as THREE from 'three';
import type { Vec3 } from '../api/contracts';
import { SELECTION_ACCENT, WARNING_ACCENT, BLOCKER_ACCENT } from './materialPalette';

export interface OverlaySpec {
  loadVector: { origin: Vec3; direction: Vec3 } | null;
  fixtures: { name: string; location: Vec3 }[] | null;
  stressBadge: { location: Vec3 } | null;
  contactPlane: { normal: Vec3; point: Vec3 } | null;
  severityMarkers: { id: string; location: Vec3; severity: string }[] | null;
  selectionAnchor: Vec3 | null;
}

const DEFAULT_PLANE_RADIUS = 0.1;

function safePlaneRadius(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return DEFAULT_PLANE_RADIUS;
  if (!Number.isFinite(value * 2) || !Number.isFinite(Math.fround(value * 2))) {
    return DEFAULT_PLANE_RADIUS;
  }
  return value;
}

function isFiniteVec3(value: unknown): value is Vec3 {
  return Array.isArray(value) && value.length === 3 && value.every((item) => Number.isFinite(item));
}

function normalizedDirection(value: unknown): THREE.Vector3 | null {
  if (!isFiniteVec3(value)) return null;
  const vector = new THREE.Vector3(...value);
  const length = vector.length();
  if (!Number.isFinite(length) || length <= Number.EPSILON) return null;
  return vector.multiplyScalar(1 / length);
}

function overlaySignature(spec: OverlaySpec | null): string {
  try {
    return JSON.stringify(spec) ?? 'null';
  } catch {
    return 'unserializable';
  }
}

function disposeOverlayResources(root: THREE.Object3D): void {
  root.traverse((obj) => {
    const resource = obj as THREE.Mesh & { material?: THREE.Material | THREE.Material[] };
    resource.geometry?.dispose();

    if (resource.material) {
      const materials = Array.isArray(resource.material) ? resource.material : [resource.material];
      for (const material of materials) material.dispose();
    }
  });
}

export function createOverlayLayer(
  scene: THREE.Scene,
  options: { planeRadius: number },
) {
  const container = new THREE.Group();
  container.name = 'OverlayLayer';
  scene.add(container);

  const textureCache = new Map<string, THREE.CanvasTexture>();
  let planeRadius = safePlaneRadius(options.planeRadius);
  let currentSpec: OverlaySpec | null = null;
  let appliedKey = '';
  let disposed = false;

  function createLabelSprite(text: string, radius: number): THREE.Sprite {
    let texture = textureCache.get(text);
    if (!texture) {
      const canvas = document.createElement('canvas');
      canvas.width = 256;
      canvas.height = 64;
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.fillStyle = 'rgba(26, 29, 36, 0.85)';
        ctx.beginPath();
        if (typeof ctx.roundRect === 'function') {
          ctx.roundRect(0, 0, 256, 64, 8);
        } else {
          ctx.rect(0, 0, 256, 64);
        }
        ctx.fill();

        ctx.fillStyle = '#f2f0ea';
        ctx.font = '600 26px Inter, system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(text, 128, 32);
      }
      texture = new THREE.CanvasTexture(canvas);
      texture.colorSpace = THREE.SRGBColorSpace;
      textureCache.set(text, texture);
    }

    const spriteMat = new THREE.SpriteMaterial({ map: texture, depthTest: false });
    spriteMat.userData.owned = true;
    const sprite = new THREE.Sprite(spriteMat);
    sprite.scale.set(radius * 0.35, radius * 0.0875, 1);
    return sprite;
  }

  const apply = (spec: OverlaySpec | null): void => {
      if (disposed) return;

      const nextKey = `${planeRadius}:${overlaySignature(spec)}`;
      if (nextKey === appliedKey) return;

      // Clear existing overlays
      disposeOverlayResources(container);
      container.clear();
      currentSpec = spec;
      appliedKey = nextKey;

      if (!spec) return;

      const r = planeRadius;

      // Load Vector
      if (spec.loadVector && isFiniteVec3(spec.loadVector.origin)) {
        const dir = normalizedDirection(spec.loadVector.direction);
        if (dir) {
          const origin = new THREE.Vector3(...spec.loadVector.origin);
          const arrow = new THREE.ArrowHelper(
            dir,
            origin,
            r * 0.8,
            SELECTION_ACCENT,
            r * 0.2,
            r * 0.1,
          );
          container.add(arrow);

          const sprite = createLabelSprite('load', r);
          sprite.position.copy(origin).add(dir.clone().multiplyScalar(r * 0.9));
          container.add(sprite);
        }
      }

      // Fixtures
      if (spec.fixtures) {
        for (const fixture of spec.fixtures) {
          if (!isFiniteVec3(fixture.location)) continue;
          const geom = new THREE.OctahedronGeometry(r * 0.08);
          const mat = new THREE.MeshBasicMaterial({ color: WARNING_ACCENT });
          mat.userData.owned = true;
          const mesh = new THREE.Mesh(geom, mat);
          mesh.position.set(...fixture.location);
          container.add(mesh);

          const sprite = createLabelSprite(fixture.name, r);
          sprite.position.set(
            fixture.location[0],
            fixture.location[1],
            fixture.location[2] + r * 0.12,
          );
          container.add(sprite);
        }
      }

      // Stress Badge
      if (spec.stressBadge && isFiniteVec3(spec.stressBadge.location)) {
        const loc = spec.stressBadge.location;
        const geom = new THREE.OctahedronGeometry(r * 0.09);
        const mat = new THREE.MeshBasicMaterial({ color: WARNING_ACCENT });
        mat.userData.owned = true;
        const mesh = new THREE.Mesh(geom, mat);
        mesh.position.set(...loc);
        container.add(mesh);

        const sprite = createLabelSprite('stress (filtered)', r);
        sprite.position.set(loc[0], loc[1], loc[2] + r * 0.14);
        container.add(sprite);
      }

      // Contact Plane
      if (spec.contactPlane && isFiniteVec3(spec.contactPlane.point)) {
        const normal = normalizedDirection(spec.contactPlane.normal);
        if (normal) {
          const pt = new THREE.Vector3(...spec.contactPlane.point);

          const geom = new THREE.PlaneGeometry(r * 2, r * 2);
          const mat = new THREE.MeshBasicMaterial({
            color: WARNING_ACCENT,
            transparent: true,
            opacity: 0.12,
            side: THREE.DoubleSide,
            depthWrite: false,
          });
          mat.userData.owned = true;

          const planeMesh = new THREE.Mesh(geom, mat);
          planeMesh.position.copy(pt);
          planeMesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal);
          container.add(planeMesh);

          const sprite = createLabelSprite('contact plane (assumption)', r);
          sprite.position.copy(pt).add(normal.clone().multiplyScalar(r * 0.2));
          container.add(sprite);
        }
      }

      // Severity Markers
      if (spec.severityMarkers) {
        for (const marker of spec.severityMarkers) {
          if (!isFiniteVec3(marker.location)) continue;
          let color = 0x888888;
          if (marker.severity === 'warning') color = WARNING_ACCENT;
          else if (marker.severity === 'error') color = 0xd94f30;
          else if (marker.severity === 'blocker') color = BLOCKER_ACCENT;

          const geom = new THREE.SphereGeometry(r * 0.06, 16, 16);
          const mat = new THREE.MeshBasicMaterial({ color });
          mat.userData.owned = true;

          const mesh = new THREE.Mesh(geom, mat);
          mesh.position.set(...marker.location);
          container.add(mesh);
        }
      }

      // Selection Anchor
      if (spec.selectionAnchor && isFiniteVec3(spec.selectionAnchor)) {
        const geom = new THREE.OctahedronGeometry(r * 0.07);
        const mat = new THREE.MeshBasicMaterial({ color: SELECTION_ACCENT });
        mat.userData.owned = true;
        const mesh = new THREE.Mesh(geom, mat);
        mesh.position.set(...spec.selectionAnchor);
        container.add(mesh);
      }
  };

  const setPlaneRadius = (nextRadius: number): void => {
    if (disposed) return;
    const next = safePlaneRadius(nextRadius);
    if (next === planeRadius) return;
    planeRadius = next;
    // Reapply the last spec so all radius-dependent geometry and labels track
    // the current model bounds without requiring callers to retain the spec.
    appliedKey = '';
    apply(currentSpec);
  };

  return {
    apply,
    setPlaneRadius,
    dispose() {
      if (disposed) return;
      disposed = true;
      disposeOverlayResources(container);
      container.clear();
      container.removeFromParent();
      for (const texture of textureCache.values()) {
        texture.dispose();
      }
      textureCache.clear();
      currentSpec = null;
      appliedKey = '';
    },
  };
}
