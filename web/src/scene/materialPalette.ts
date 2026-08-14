import * as THREE from 'three';
import { decorateForFea, type FeaUniforms } from './feaStressShader';

export type QualityTier = 'ultra' | 'high' | 'medium' | 'low';
export type PaletteKey = 'shell' | 'pcb' | 'battery' | 'metal' | 'skate' | 'default';

export const SELECTION_ACCENT = 0xffffff;
export const WARNING_ACCENT = 0x8a6d3b;
export const BLOCKER_ACCENT = 0xb3402e;

export function paletteKeyForComponent(className: string | null): PaletteKey {
  if (!className) return 'default';
  const cls = className.toLowerCase();
  if (cls.includes('shell') || cls.includes('envelope')) return 'shell';
  if (cls.includes('pcb') || cls.includes('board')) return 'pcb';
  if (cls.includes('battery') || cls.includes('lipo')) return 'battery';
  if (
    cls.includes('wheel') ||
    cls.includes('screw') ||
    cls.includes('fastener') ||
    cls.includes('bearing')
  ) {
    return 'metal';
  }
  if (cls.includes('skate') || cls.includes('glide')) return 'skate';
  return 'default';
}

const PALETTE_CONFIGS: Record<
  PaletteKey,
  { lightColor: number; darkColor: number; roughness: number; metalness: number }
> = {
  shell: { lightColor: 0xf2f0ea, darkColor: 0xd9d5cc, roughness: 0.7, metalness: 0.08 },
  pcb: { lightColor: 0x2e333b, darkColor: 0x24282e, roughness: 0.55, metalness: 0.08 },
  battery: { lightColor: 0xb9bdc2, darkColor: 0xaaaaae, roughness: 0.6, metalness: 0.08 },
  metal: { lightColor: 0x5a6068, darkColor: 0x50555c, roughness: 0.45, metalness: 0.55 },
  skate: { lightColor: 0xc8ccd1, darkColor: 0xb4b8bd, roughness: 0.65, metalness: 0.08 },
  default: { lightColor: 0x9aa0a6, darkColor: 0x8b9095, roughness: 0.65, metalness: 0.08 },
};

export class MaterialPalette {
  private readonly materials = new Map<PaletteKey, THREE.MeshStandardMaterial>();

  constructor(private readonly theme: 'light' | 'dark' = 'light') {
    this.init();
  }

  private init() {
    const keys: PaletteKey[] = ['shell', 'pcb', 'battery', 'metal', 'skate', 'default'];
    for (const key of keys) {
      const cfg = PALETTE_CONFIGS[key];
      const rawHex = this.theme === 'dark' ? cfg.darkColor : cfg.lightColor;
      const color = new THREE.Color(rawHex);
      if (this.theme === 'dark') {
        color.multiplyScalar(0.92);
      }
      const mat = new THREE.MeshStandardMaterial({
        color,
        roughness: cfg.roughness,
        metalness: cfg.metalness,
      });
      mat.userData.shared = true;
      this.materials.set(key, mat);
    }
  }

  get(key: PaletteKey): THREE.MeshStandardMaterial {
    return this.materials.get(key) ?? this.materials.get('default')!;
  }

  getAll(): Record<PaletteKey, THREE.MeshStandardMaterial> {
    const result: Partial<Record<PaletteKey, THREE.MeshStandardMaterial>> = {};
    for (const [k, v] of this.materials.entries()) {
      result[k] = v;
    }
    return result as Record<PaletteKey, THREE.MeshStandardMaterial>;
  }

  dispose() {
    for (const mat of this.materials.values()) {
      mat.dispose();
    }
    this.materials.clear();
  }
}

/**
 * Per-geometry cache of FEA-decorated material clones.
 *
 * Decoration via onBeforeCompile must NEVER be applied to a SHARED palette
 * material: damage data is per-vertex but the hook (and its defines) is
 * material-level, so decorating the shared instance would leak state across
 * every object that uses it. Instead each decoration owns a CLONE
 * (`userData.owned = true`, `userData.feaSource = base`), so the shared
 * palette stays pristine and the existing owned-resource disposal convention
 * (disposeObjectGroup) releases the clone correctly.
 *
 * The clone is cached per (geometry, base) pair. `clear()` forgets the cache
 * (the runtime agent calls it on mode switches back to default); it does NOT
 * dispose clones — disposal stays with the scene's owned-resource path.
 */
export class FeaMaterialCache {
  private entries = new WeakMap<
    THREE.BufferGeometry,
    { base: THREE.MeshStandardMaterial; clone: THREE.MeshStandardMaterial }
  >();

  get(
    base: THREE.MeshStandardMaterial,
    geometry: THREE.BufferGeometry,
    uniforms?: FeaUniforms,
  ): THREE.MeshStandardMaterial {
    // Attribute-less geometry (analytic primitives) is still decorated: the
    // shader evaluates the same Gaussian procedurally from the uniforms,
    // which keeps heatmaps consistent across the whole assembly.
    const existing = this.entries.get(geometry);
    if (existing && existing.base === base) return existing.clone;

    const clone = base.clone();
    clone.userData.owned = true;
    clone.userData.feaSource = base;
    decorateForFea(clone, geometry, uniforms);
    this.entries.set(geometry, { base, clone });
    return clone;
  }

  clear(): void {
    this.entries = new WeakMap();
  }
}

const globalFeaMaterialCache = new FeaMaterialCache();

/**
 * Convenience wrapper over a module-level FeaMaterialCache: returns the same
 * material instance when the geometry is not FEA-capable, else the decorated
 * owned clone. Cached per geometry so repeated calls are cheap.
 */
export function feaDecoratedMaterial(
  base: THREE.MeshStandardMaterial,
  geometry: THREE.BufferGeometry,
  uniforms?: FeaUniforms,
): THREE.MeshStandardMaterial {
  return globalFeaMaterialCache.get(base, geometry, uniforms);
}
