import * as THREE from 'three';

export function disposeObject3D(
  root: THREE.Object3D,
  opts?: { isShared?: (mat: THREE.Material) => boolean },
): void {
  const isShared = opts?.isShared ?? ((mat: THREE.Material) => Boolean(mat.userData?.shared));

  root.traverse((obj) => {
    // Dispose BufferGeometry
    const mesh = obj as THREE.Mesh;
    if (mesh.geometry) {
      mesh.geometry.dispose();
    }

    // Dispose Material(s)
    if (mesh.material) {
      const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      for (const mat of materials) {
        if (!isShared(mat)) {
          // Dispose textures attached to material
          const matRecord = mat as unknown as Record<string, unknown>;
          const textureMapKeys = [
            'map',
            'normalMap',
            'roughnessMap',
            'metalnessMap',
            'emissiveMap',
            'alphaMap',
            'aoMap',
            'bumpMap',
            'displacementMap',
            'envMap',
          ];

          for (const key of textureMapKeys) {
            const texture = matRecord[key];
            if (texture && typeof (texture as THREE.Texture).dispose === 'function') {
              (texture as THREE.Texture).dispose();
            }
          }

          mat.dispose();
        }
      }
    }
  });

  root.removeFromParent();
}

export function disposeSceneResources(scene: THREE.Scene): void {
  const children = [...scene.children];
  for (const child of children) {
    disposeObject3D(child);
  }
}
