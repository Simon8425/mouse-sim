import * as THREE from 'three';
import type { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import type { Vec3 } from '../api/contracts';

export type CameraPreset = 'iso' | 'top' | 'front' | 'right';

export function createSceneCamera(): THREE.PerspectiveCamera {
  const camera = new THREE.PerspectiveCamera(42, 1, 0.0005, 100);
  camera.up.set(0, 0, 1);
  return camera;
}

export function fitCameraToBounds(
  camera: THREE.PerspectiveCamera,
  controls: OrbitControls,
  bounds: { min: Vec3; max: Vec3 },
  elevation = 0.4,
): void {
  const cx = (bounds.min[0] + bounds.max[0]) / 2;
  const cy = (bounds.min[1] + bounds.max[1]) / 2;
  const cz = (bounds.min[2] + bounds.max[2]) / 2;

  const dx = bounds.max[0] - bounds.min[0];
  const dy = bounds.max[1] - bounds.min[1];
  const dz = bounds.max[2] - bounds.min[2];

  const radius = Math.max(Math.sqrt(dx * dx + dy * dy + dz * dz) / 2, 1e-4);
  const fovRad = (camera.fov * Math.PI) / 180;
  // Outzoomed framing: comfortable distance so the entire model and surroundings are visible.
  const dist = (radius / Math.tan(fovRad / 2)) * 0.65 + radius * 0.1;

  camera.position.set(
    cx + dist * 0.7071,
    cy - dist * 0.7071,
    cz + dist * elevation,
  );

  camera.near = Math.max(radius / 1000, 0.0005);
  camera.far = Math.max(dist * 10 + radius * 4, 10);
  camera.up.set(0, 0, 1);

  controls.target.set(cx, cy, cz);
  camera.updateProjectionMatrix();
  controls.update();
}

export function applyCameraPreset(
  camera: THREE.PerspectiveCamera,
  controls: OrbitControls,
  preset: CameraPreset,
  center: Vec3,
  radius: number,
): void {
  const d = radius * 1.1 + 1e-4;
  const [cx, cy, cz] = center;

  switch (preset) {
    case 'iso':
      camera.position.set(cx + d * 0.5773, cy - d * 0.5773, cz + d * 0.5773);
      camera.up.set(0, 0, 1);
      break;
    case 'top':
      camera.position.set(cx, cy, cz + d);
      camera.up.set(0, 1, 0);
      break;
    case 'front':
      camera.position.set(cx, cy - d, cz);
      camera.up.set(0, 0, 1);
      break;
    case 'right':
      camera.position.set(cx + d, cy, cz);
      camera.up.set(0, 0, 1);
      break;
  }

  controls.target.set(cx, cy, cz);
  camera.updateProjectionMatrix();
  controls.update();
}
