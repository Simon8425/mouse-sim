import * as THREE from 'three';

export interface PickerOptions {
  onClick: (id: string | null) => void;
  thresholdPx?: number;
}

export function pickObjectId(
  canvas: HTMLCanvasElement,
  camera: THREE.Camera,
  root: THREE.Object3D,
  clientX: number,
  clientY: number,
): string | null {
  const rect = canvas.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return null;

  const x = ((clientX - rect.left) / rect.width) * 2 - 1;
  const y = -((clientY - rect.top) / rect.height) * 2 + 1;

  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(new THREE.Vector2(x, y), camera);

  // Three.js's Raycaster does NOT test `object.visible` (only `layers`), so
  // eye-toggled-off (hidden) parts must be excluded explicitly: clicking
  // through a hidden part must not select it.
  const intersects = raycaster.intersectObjects(root.children, true).filter((hit) => {
    let curr: THREE.Object3D | null = hit.object;
    while (curr) {
      if (!curr.visible) return false;
      curr = curr.parent;
    }
    return true;
  });
  for (const hit of intersects) {
    // THREE r168's Raycaster intersects InstancedMesh and returns the
    // per-instance slot on the hit; resolve it through the mapping the batch
    // builder stores (objectId per instance slot). Instances hidden via a
    // degenerate scale are skipped by InstancedMesh.raycast automatically.
    const instanced = hit.object as THREE.InstancedMesh & {
      isInstancedMesh?: boolean;
      userData?: { instanceObjectIds?: string[] };
    };
    if (instanced.isInstancedMesh && hit.instanceId != null) {
      const ids = instanced.userData?.instanceObjectIds;
      const objectId = ids?.[hit.instanceId];
      if (typeof objectId === 'string') return objectId;
    }
    let curr: THREE.Object3D | null = hit.object;
    while (curr) {
      if (typeof curr.userData?.objectId === 'string') {
        return curr.userData.objectId;
      }
      curr = curr.parent;
    }
  }

  return null;
}

export function createPicker(
  canvas: HTMLCanvasElement,
  camera: THREE.Camera,
  root: THREE.Object3D,
  options: PickerOptions,
): { dispose: () => void } {
  let startX = 0;
  let startY = 0;
  let isDown = false;
  const threshold = options.thresholdPx ?? 5;

  const handlePointerDown = (e: PointerEvent) => {
    if (e.button !== 0) return;
    startX = e.clientX;
    startY = e.clientY;
    isDown = true;
  };

  const handlePointerUp = (e: PointerEvent) => {
    if (!isDown || e.button !== 0) return;
    isDown = false;

    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    const dist = Math.sqrt(dx * dx + dy * dy);

    if (dist <= threshold) {
      const pickedId = pickObjectId(canvas, camera, root, e.clientX, e.clientY);
      options.onClick(pickedId);
    }
  };

  canvas.addEventListener('pointerdown', handlePointerDown);
  canvas.addEventListener('pointerup', handlePointerUp);

  return {
    dispose() {
      canvas.removeEventListener('pointerdown', handlePointerDown);
      canvas.removeEventListener('pointerup', handlePointerUp);
    },
  };
}
