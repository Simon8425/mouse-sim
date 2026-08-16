import { describe, it, expect } from 'vitest';
import * as THREE from 'three';
import {
  feaGradientColor,
  damageForDistance,
  dentFactorFor,
  dentDepthFactorFor,
  createFeaUniforms,
  updateFeaUniforms,
  decorateForFea,
  feaFieldMaxDamage,
  impactPulseFor,
  yieldNormFor,
  yieldMaskFor,
  crackIntensityFor,
} from '../scene/feaStressShader';
import { applyFeaObjectField } from '../scene/geometryFactory';
import type { FeaObjectField, FeaResult } from '../api/contracts';

describe('feaGradientColor (CPU / GPU-shared ramp)', () => {
  it('maps 0 to blue, 0.45 to green, 0.9 to red', () => {
    const color = new THREE.Color();
    feaGradientColor(0, color);
    expect(color.r).toBeCloseTo(0);
    expect(color.g).toBeCloseTo(0);
    expect(color.b).toBeCloseTo(1);

    feaGradientColor(0.45, color);
    expect(color.g).toBeCloseTo(1);
    expect(color.r).toBeCloseTo(0);
    expect(color.b).toBeCloseTo(0);

    feaGradientColor(0.9, color);
    expect(color.r).toBeCloseTo(1);
    expect(color.g).toBeCloseTo(0);
    expect(color.b).toBeCloseTo(0);
  });

  it('passes through the eased cyan and yellow stops (smoothstep segments)', () => {
    const color = new THREE.Color();
    // d=0.126 is the blue/cyan smoothstep midpoint (s=0.5); d=0.252 the cyan stop (t=0.28).
    feaGradientColor(0.126, color);
    expect(color.r).toBeCloseTo(0);
    expect(color.g).toBeCloseTo(0.5);
    expect(color.b).toBeCloseTo(1);

    feaGradientColor(0.252, color);
    expect(color.g).toBeCloseTo(1);
    expect(color.r).toBeCloseTo(0);
    expect(color.b).toBeCloseTo(1);

    // d=0.549 is the green/yellow smoothstep midpoint; d=0.648 the yellow stop (t=0.72).
    feaGradientColor(0.549, color);
    expect(color.r).toBeCloseTo(0.5);
    expect(color.g).toBeCloseTo(1);
    expect(color.b).toBeCloseTo(0);

    feaGradientColor(0.648, color);
    expect(color.r).toBeCloseTo(1);
    expect(color.g).toBeCloseTo(1);
    expect(color.b).toBeCloseTo(0);
  });

  it('clamps damage outside [0, 0.9] to the gradient endpoints', () => {
    const color = new THREE.Color();
    feaGradientColor(-1, color);
    expect(color.b).toBeCloseTo(1);
    expect(color.r).toBeCloseTo(0);

    feaGradientColor(2, color);
    expect(color.r).toBeCloseTo(1);
    expect(color.b).toBeCloseTo(0);
  });

  it('has a monotonic hue progression (red up, blue down, green hump)', () => {
    const prev = new THREE.Color();
    const cur = new THREE.Color();
    feaGradientColor(0, prev);
    for (let i = 1; i <= 18; i += 1) {
      feaGradientColor((i / 18) * 0.9, cur);
      expect(cur.r).toBeGreaterThanOrEqual(prev.r - 1e-9);
      expect(cur.b).toBeLessThanOrEqual(prev.b + 1e-9);
      prev.copy(cur);
    }
  });
});

describe('damageForDistance (backend Gaussian mirror)', () => {
  it('is peakDamage at d=0 and peakDamage/e at d=lambda', () => {
    expect(damageForDistance(0, 0.01, 0.8)).toBeCloseTo(0.8);
    const lambda = 0.01;
    expect(damageForDistance(lambda, lambda, 0.8)).toBeCloseTo(0.8 / Math.E);
  });

  it('decreases monotonically and vanishes far away', () => {
    let prev = damageForDistance(0, 0.01, 0.9);
    for (let i = 1; i <= 20; i += 1) {
      const cur = damageForDistance(i * 0.002, 0.01, 0.9);
      expect(cur).toBeLessThanOrEqual(prev + 1e-9);
      prev = cur;
    }
    expect(damageForDistance(10, 0.01, 0.9)).toBeLessThan(1e-6);
  });

  it('clamps to [0, 1] and guards degenerate inputs', () => {
    expect(damageForDistance(0, 0.01, 5)).toBe(1);
    expect(damageForDistance(0, 0.01, -2)).toBe(0);
    expect(damageForDistance(0, 0, 0.5)).toBe(0);
    expect(damageForDistance(Number.NaN, 0.01, 0.5)).toBe(0);
  });
});

describe('dentFactorFor (plastic dent boost)', () => {
  it('is 0 below the dent threshold and 1 at the threshold', () => {
    expect(dentFactorFor(0)).toBe(0);
    expect(dentFactorFor(0.5)).toBe(0);
    expect(dentFactorFor(0.69)).toBe(0);
    expect(dentFactorFor(0.7)).toBe(1);
  });

  it('caps the plastic boost at 3.0 (backend PLASTIC_AMPLIFICATION_MAX)', () => {
    expect(dentFactorFor(0.85)).toBeCloseTo(2.0, 6);
    expect(dentFactorFor(0.9)).toBeCloseTo(2.333333, 6);
    expect(dentFactorFor(1)).toBe(3.0);
  });

  it('is monotone non-decreasing', () => {
    let prev = -Infinity;
    for (let i = 0; i <= 100; i += 1) {
      const cur = dentFactorFor(i / 100);
      expect(cur).toBeGreaterThanOrEqual(prev);
      prev = cur;
    }
  });
});

describe('dentDepthFactorFor (backend depth cap parity)', () => {
  it('applies the 1.5 cap to the COMBINED gauss*amp product', () => {
    // amp = 3.0 at damage 1.0; gauss = 0.8 -> depth factor min(1.5, 2.4) = 1.5.
    expect(dentDepthFactorFor(0.8, 1.0)).toBe(1.5);
    // gauss = 0.5, amp = 2.0 -> min(1.5, 1.0) = 1.0 (under the cap).
    expect(dentDepthFactorFor(0.5, 0.85)).toBeCloseTo(1.0, 6);
  });

  it('matches the backend depth = delta * min(1.5, gauss*amp) exactly', () => {
    // Backend: depth = delta_max * gaussian * amp, capped at 1.5*delta_max.
    const gauss = Math.exp(-1); // d = lambda
    const amp = 1 + 2 * ((0.8 - 0.7) / 0.3);
    expect(dentDepthFactorFor(gauss, 0.8)).toBeCloseTo(Math.min(1.5, gauss * amp), 10);
  });

  it('is zero below the dent threshold and guards degenerate gaussians', () => {
    expect(dentDepthFactorFor(0.9, 0.5)).toBe(0);
    expect(dentDepthFactorFor(0, 1)).toBe(0);
    expect(dentDepthFactorFor(Number.NaN, 1)).toBe(0);
    expect(dentDepthFactorFor(-1, 1)).toBe(0);
  });
});

describe('impactPulseFor (dynamic heatmap surge)', () => {
  it('is 0 before the impact and 1 at the moment of impact', () => {
    expect(impactPulseFor(0.2, 0.3, 0.3)).toBe(0);
    expect(impactPulseFor(0.3, 0.3, 0.3)).toBeCloseTo(1, 6);
  });

  it('decays exponentially after the impact', () => {
    const pulse = impactPulseFor(0.45, 0.3, 0.3);
    expect(pulse).toBeGreaterThan(0);
    expect(pulse).toBeLessThan(1);
    expect(impactPulseFor(0.6, 0.3, 0.3)).toBeLessThan(pulse);
    expect(impactPulseFor(5, 0.3, 0.3)).toBeLessThan(1e-6);
  });

  it('guards missing/non-finite inputs', () => {
    expect(impactPulseFor(Number.NaN, 0.3, 0.3)).toBe(0);
    expect(impactPulseFor(0.5, Number.NaN, 0.3)).toBe(0);
  });
});

describe('yieldNormFor / yieldMaskFor (yield threshold masking)', () => {
  it('maps the dent threshold onto the auto-normalized ramp', () => {
    // Field max 1.0: the yield level sits exactly at the dent threshold.
    expect(yieldNormFor(1, 0.7)).toBeCloseTo(0.7, 6);
    // A field peaking below the dent threshold clamps the mask off.
    expect(yieldNormFor(0.5, 0.7)).toBe(1);
    // Degenerate inputs fall back to "mask off".
    expect(yieldNormFor(0, 0.7)).toBe(0);
    expect(yieldNormFor(Number.NaN, 0.7)).toBe(0);
    expect(yieldNormFor(1, 0)).toBe(1);
  });

  it('masks damage above the yield level and leaves safe zones dark', () => {
    const yn = yieldNormFor(1, 0.7);
    expect(yieldMaskFor(0.5, yn)).toBe(0);
    expect(yieldMaskFor(0.7, yn)).toBe(0);
    expect(yieldMaskFor(0.85, yn)).toBeGreaterThan(0);
    expect(yieldMaskFor(0.85, yn)).toBeLessThan(1);
    expect(yieldMaskFor(1, yn)).toBeCloseTo(1, 6);
    // yn >= 1 disables the mask entirely.
    expect(yieldMaskFor(1, 1)).toBe(0);
  });
});

describe('crackIntensityFor (plastic-damage striations)', () => {
  it('ramps from 0 at the dent threshold to 1 at/above the tear threshold', () => {
    expect(crackIntensityFor(0.5, 0.7, 0.92)).toBe(0);
    expect(crackIntensityFor(0.7, 0.7, 0.92)).toBe(0);
    expect(crackIntensityFor(0.81, 0.7, 0.92)).toBeCloseTo(0.5, 6);
    expect(crackIntensityFor(0.92, 0.7, 0.92)).toBe(1);
    expect(crackIntensityFor(1, 0.7, 0.92)).toBe(1);
  });

  it('guards degenerate thresholds', () => {
    expect(crackIntensityFor(0.95, 0.95, 0.95)).toBe(1);
    expect(crackIntensityFor(0.5, 0.7, 0.7)).toBe(0);
    expect(crackIntensityFor(Number.NaN, 0.7, 0.92)).toBe(0);
  });
});

describe('applyFeaObjectField (geometryFactory)', () => {
  function buildMesh(): THREE.Mesh {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(9), 3));
    geometry.setAttribute('aDamage', new THREE.BufferAttribute(new Float32Array(3), 1));
    geometry.setAttribute('aDisplacement', new THREE.BufferAttribute(new Float32Array(9), 3));
    return new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
  }

  it('rejects a vertex_count mismatch', () => {
    const mesh = buildMesh();
    const field: FeaObjectField = {
      object_id: 'o1',
      vertex_count: 2,
      damage: [0.1, 0.2],
      displacement: [
        [0, 0, 0],
        [0, 0, 0],
      ],
      stress_pa: [1e6, 2e6],
    };
    expect(applyFeaObjectField(mesh, field)).toBe(false);
  });

  it('writes matching fields and flags needsUpdate', () => {
    const mesh = buildMesh();
    const field: FeaObjectField = {
      object_id: 'o1',
      vertex_count: 3,
      damage: [0.1, 0.7, 0.95],
      displacement: [
        [0, 0, -0.001],
        [0, 0, -0.002],
        [0, 0, -0.003],
      ],
      stress_pa: [1e6, 2e6, 3e6],
    };
    expect(applyFeaObjectField(mesh, field)).toBe(true);
    const damage = mesh.geometry.getAttribute('aDamage') as THREE.BufferAttribute;
    const displacement = mesh.geometry.getAttribute('aDisplacement') as THREE.BufferAttribute;
    const damageArr = damage.array as Float32Array;
    expect(damageArr[0]).toBeCloseTo(0.1, 6);
    expect(damageArr[1]).toBeCloseTo(0.7, 6);
    expect(damageArr[2]).toBeCloseTo(0.95, 6);
    // r168 `needsUpdate` is a write-only setter backed by `version`.
    expect(damage.version).toBe(1);
    expect(displacement.version).toBe(1);
    const disp = displacement.array as Float32Array;
    expect(disp[6]).toBeCloseTo(0, 6);
    expect(disp[7]).toBeCloseTo(0, 6);
    expect(disp[8]).toBeCloseTo(-0.003, 6);
  });

  it('returns false when attributes are missing', () => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(9), 3));
    const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
    const field: FeaObjectField = {
      object_id: 'o1',
      vertex_count: 3,
      damage: [0, 0, 0],
      displacement: [
        [0, 0, 0],
        [0, 0, 0],
        [0, 0, 0],
      ],
      stress_pa: [0, 0, 0],
    };
    expect(applyFeaObjectField(mesh, field)).toBe(false);
  });
});

describe('createFeaUniforms / updateFeaUniforms (no per-frame allocation)', () => {
  it('bakes procedural peak damage from stress ratio', () => {
    const fea: FeaResult = {
      computed: true,
      peak: null,
      yield_stress_pa: 4e7,
      safety_factor: 1.5,
      impact_window_s: 0.3,
      dent_threshold: 0.7,
      tear_threshold: 0.92,
      objects: [],
      procedural: [
        {
          object_id: 'o1',
          impact_point_model_m: [0.01, 0.02, 0.03],
          falloff_radius_m: 0.004,
          contact_normal_model: [0, 0, -1],
          peak_stress_pa: 6e7,
          yield_stress_pa: 4e7,
          max_compression_m: 0.0008,
        },
      ],
      assumptions: [],
      flags: [],
    };
    const uniforms = createFeaUniforms(fea);
    expect(uniforms.uPeakDamage.value).toBeCloseTo(1); // 6e7/4e7 clamped
    expect(uniforms.uFalloffRadius.value).toBe(0.004);
    expect(uniforms.uMaxCompression.value).toBe(0.0008);
    expect((uniforms.uImpactPointModel.value as THREE.Vector3).toArray()).toEqual([0.01, 0.02, 0.03]);

    const fea2: FeaResult = { ...fea, yield_stress_pa: 1.2e8, procedural: [{ ...fea.procedural[0], yield_stress_pa: 1.2e8 }] };
    const uniforms2 = createFeaUniforms(fea2);
    expect(uniforms2.uPeakDamage.value).toBeCloseTo(0.5);
  });

  it('auto-normalizes the contour to the field maximum via uDamageScale', () => {
    const fea: FeaResult = {
      computed: true,
      peak: null,
      yield_stress_pa: 4e7,
      safety_factor: 2,
      impact_window_s: 0.05,
      dent_threshold: 0.7,
      tear_threshold: 0.92,
      objects: [
        {
          object_id: 'shell',
          vertex_count: 3,
          damage: [0.0, 0.00025, 0.000125],
          displacement: [],
          stress_pa: [],
        },
      ],
      procedural: [
        {
          object_id: 'shell',
          impact_point_model_m: [0, 0, 0],
          falloff_radius_m: 0.01,
          contact_normal_model: [0, 0, 1],
          peak_stress_pa: 1e4,
          yield_stress_pa: 4e7,
          max_compression_m: 0.001,
        },
      ],
      assumptions: [],
      flags: [],
    };
    const uniforms = createFeaUniforms(fea);
    // Field max is 0.00025 (vertex field dominates the procedural 1e4/4e7).
    expect(uniforms.uDamageScale.value).toBeCloseTo(4000);
    // An all-zero field keeps the scale at 1 (no heatmap).
    const flat = createFeaUniforms({ ...fea, objects: [], procedural: [] });
    expect(flat.uDamageScale.value).toBe(1);

    // A pathological tiny-but-nonzero field caps the scale at 1e6 so the
    // contour cannot saturate the whole model.
    const tiny = createFeaUniforms({
      ...fea,
      objects: [
        {
          object_id: 'shell',
          vertex_count: 1,
          damage: [1e-12],
          displacement: [],
          stress_pa: [],
        },
      ],
      procedural: [],
    });
    expect(tiny.uDamageScale.value).toBe(1e6);

    // feaFieldMaxDamage scans objects + procedural peaks.
    expect(feaFieldMaxDamage(fea)).toBeCloseTo(0.00025);
    expect(feaFieldMaxDamage(null)).toBe(0);

    // The continuous plate layer is baked from the plate config.
    const withPlate = createFeaUniforms(null, {
      a: 0.1,
      b: 0.06,
      cx: 0,
      cy: 0,
      xExtent: 0.1,
      yExtent: 0.06,
      peakDamage: 0.5,
    });
    expect(withPlate.uPlateA.value).toBe(0.1);
    expect(withPlate.uPlatePeakDamage.value).toBe(0.5);
    const noPlate = createFeaUniforms(null);
    expect(noPlate.uPlateA.value).toBe(0);
  });

  it('updates mode, dent window and time without reallocating', () => {
    const uniforms = createFeaUniforms(null);
    const before = uniforms.uFeaMode;
    updateFeaUniforms(uniforms, {
      mode: 'yield',
      impactWindow01: 0.5,
      impactWindowActive: true,
      time: 1.25,
    });
    expect(uniforms.uFeaMode).toBe(before);
    expect(uniforms.uFeaMode.value).toBe(1);
    expect(uniforms.uImpactWindow01.value).toBe(0.5);
    expect(uniforms.uImpactWindowActive.value).toBe(1);
    expect(uniforms.uTime.value).toBe(1.25);

    updateFeaUniforms(uniforms, {
      mode: 'fea',
      impactWindow01: 0,
      impactWindowActive: false,
      time: 2,
    });
    expect(uniforms.uFeaMode.value).toBe(0);
    expect(uniforms.uImpactWindowActive.value).toBe(0);

    updateFeaUniforms(uniforms, { mode: 'default', impactWindow01: 0, impactWindowActive: false, time: 3 });
    expect(uniforms.uFeaMode.value).toBe(-1);
  });

  it('carries the dynamic impact pulse / drop time / yield norm uniforms', () => {
    const fea: FeaResult = {
      computed: true,
      peak: null,
      yield_stress_pa: 4e7,
      safety_factor: 2,
      impact_window_s: 0.3,
      dent_threshold: 0.7,
      tear_threshold: 0.92,
      objects: [
        {
          object_id: 'shell',
          vertex_count: 1,
          damage: [0.8],
          displacement: [],
          stress_pa: [],
        },
      ],
      procedural: [],
      assumptions: [],
      flags: [],
    };
    const uniforms = createFeaUniforms(fea);
    // Field max 0.8 > dent threshold 0.7: the yield level sits at 0.875 on
    // the auto-normalized ramp.
    expect(uniforms.uYieldNorm.value).toBeCloseTo(0.875, 6);
    expect(uniforms.uImpactPulse.value).toBe(0);
    expect(uniforms.uDropTime.value).toBe(0);
    expect(uniforms.uImpactTime.value).toBe(0);

    updateFeaUniforms(uniforms, {
      mode: 'yield',
      impactWindow01: 1,
      impactWindowActive: true,
      time: 1,
      impactPulse: 0.42,
      dropTime: 0.6,
      impactTime: 0.3,
    });
    expect(uniforms.uImpactPulse.value).toBeCloseTo(0.42, 6);
    expect(uniforms.uDropTime.value).toBeCloseTo(0.6, 6);
    expect(uniforms.uImpactTime.value).toBeCloseTo(0.3, 6);

    // A field whose peak never reaches the dent threshold disables the mask.
    const safe = createFeaUniforms({ ...fea, objects: [{ ...fea.objects[0], damage: [0.3] }] });
    expect(safe.uYieldNorm.value).toBe(1);
  });

  it('decorates a material with injectable shader hooks (compile-safe guards)', () => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(9), 3));
    geometry.setAttribute('aDamage', new THREE.BufferAttribute(new Float32Array(3), 1));
    geometry.setAttribute('aDisplacement', new THREE.BufferAttribute(new Float32Array(9), 3));
    const material = new THREE.MeshStandardMaterial();
    const uniforms = createFeaUniforms(null);
    const decorated = decorateForFea(material, geometry, uniforms);
    expect(decorated).toBe(material);
    expect(material.defines?.USE_FEA_ATTRIBUTES).toBe('1');
    expect(material.userData.feaUniforms).toBe(uniforms);
    expect(typeof material.onBeforeCompile).toBe('function');

    const bare = new THREE.MeshStandardMaterial();
    const bareGeometry = new THREE.BufferGeometry();
    bareGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(9), 3));
    decorateForFea(bare, bareGeometry);
    expect(bare.defines?.USE_FEA_ATTRIBUTES).toBeUndefined();
  });

  it('fully overrides diffuseColor with the gradient (no material-color mix)', () => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(9), 3));
    const material = new THREE.MeshStandardMaterial();
    const decorated = decorateForFea(material, geometry);
    const shader = {
      uniforms: {} as Record<string, THREE.IUniform>,
      vertexShader: '#include <common>\n#include <begin_vertex>\n',
      fragmentShader: '#include <common>\n#include <color_fragment>\n',
    };
    (decorated.onBeforeCompile as (shader: unknown) => void)(
      shader as unknown as THREE.WebGLProgramParametersWithUniforms,
    );

    // Heatmap mode must replace the base material color entirely with the gradient contour...
    expect(shader.fragmentShader).toContain(
      'diffuseColor.rgb = feaGradientColorFrag( feaVisDithered );',
    );
    // ...never blending with it, and no stale FEA_HEATMAP_MIX interpolation.
    expect(shader.fragmentShader).not.toContain('mix( diffuseColor.rgb, feaGradientColorFrag');
    expect(shader.fragmentShader).not.toContain('FEA_HEATMAP_MIX');
    expect(shader.fragmentShader).toContain('if ( uFeaMode > -0.5 ) {');
    // The continuous procedural Gaussian + plate-field layers keep the
    // heatmap visible on sparse meshes and edge-only primitives — and every
    // uniform they use MUST be declared in the fragment prefix (an
    // undeclared uniform is a compile error that silently hides the mesh).
    expect(shader.fragmentShader).toContain('float feaProcedural = uPeakDamage * exp(');
    expect(shader.fragmentShader).toContain('float feaPlate = 0.0;');
    expect(shader.fragmentShader).toContain('sin( FEA_PI * feaPx / uPlateA ) * sin( FEA_PI * feaPy / uPlateB )');
    expect(shader.fragmentShader).toContain('uniform float uPlateA;');
    expect(shader.fragmentShader).toContain('uniform float uPlateB;');
    expect(shader.fragmentShader).toContain('uniform float uPlatePeakDamage;');
    expect(shader.fragmentShader).toContain('clamp( max( vFeaDamage * feaProg, max( feaProcedural, feaPlate ) ), 0.0, 1.0 )');
    expect(shader.fragmentShader).toContain('uniform vec3 uImpactPointModel;');
    expect(shader.fragmentShader).toContain('uniform float uFalloffRadius;');
    expect(shader.fragmentShader).toContain('uniform float uPeakDamage;');
    expect(shader.fragmentShader).toContain('uniform float uDamageScale;');
    expect(shader.vertexShader).toContain('uniform float uDamageScale;');
    // Auto-normalized contour: the gradient spans the field's own maximum.
    expect(shader.fragmentShader).toContain('float feaDamageVis = clamp( feaDamage * uDamageScale');
    // GLSL ES 1.00 float literals: no bare integers (t - 0 would fail to compile).
    expect(shader.fragmentShader).toContain('f = ( t - 0.0 ) / 0.28;');
    expect(shader.fragmentShader).not.toContain('( t - 0 ) /');
    // The vertex shader complements the attribute dent procedurally, with
    // the backend-parity depth factor: amp capped at 3.0, the 1.5 cap on
    // the COMBINED gauss*amp product.  GLSL ES 1.00 float literals only.
    expect(shader.vertexShader).toContain('feaGaussProc');
    expect(shader.vertexShader).toContain('min( 3.0, 1.0 + 2.0 * max( 0.0, ( uPeakDamage * feaGaussProc - 0.7 ) / 0.3 ) )');
    expect(shader.vertexShader).toContain('min( 1.5, feaGaussProc * feaPlasticProc )');
    expect(shader.vertexShader).toContain('min( 1.5, feaGauss * feaPlastic )');
    expect(shader.vertexShader).not.toContain('min( 3,');
    // Dynamic impact pulse + radial ripple: the heatmap animates across
    // playback frames instead of being a static contour.
    expect(shader.fragmentShader).toContain('uniform float uImpactPulse;');
    expect(shader.fragmentShader).toContain('uniform float uDropTime;');
    expect(shader.fragmentShader).toContain('uniform float uImpactTime;');
    expect(shader.fragmentShader).toContain('uniform float uYieldNorm;');
    expect(shader.fragmentShader).toContain('feaPulse = clamp( exp( -feaSince * 6.0 ), 0.0, 1.0 );');
    expect(shader.fragmentShader).toContain('float feaRipple = 0.0;');
    expect(shader.fragmentShader).toContain('feaDamageVis = clamp( feaDamageVis + feaRipple * 0.25, 0.0, 1.0 );');
    // YIELD SHADER is visually distinct: steel-gray base, hot-tinted
    // plastic zone, tear cutout — NOT the same gradient as the heatmap.
    // The whitening and the tear cutout use TRUE damage, never the
    // auto-normalized contour, so a tiny field cannot white-out or tear
    // the whole model. The yield mask ramps from uYieldNorm to the peak.
    expect(shader.fragmentShader).toContain('diffuseColor.rgb = vec3( 0.60, 0.62, 0.66 );');
    expect(shader.fragmentShader).toContain('float feaYieldMask = smoothstep( uYieldNorm, 1.0, feaDamageVis );');
    expect(shader.fragmentShader).toContain('vec3( 1.0, 0.36, 0.05 )');
    expect(shader.fragmentShader).toContain('if ( feaDamage > 0.92 ) {');
    expect(shader.fragmentShader).toContain('discard;');
    expect(shader.fragmentShader).toContain('float feaW = clamp( ( feaDamage - 0.7 )');
    // Micro-crack striations inside the plastic zone, flickered by the pulse.
    expect(shader.fragmentShader).toContain('float feaStripe = smoothstep( 0.45, 0.75, fract( vFeaPosition.y * 140.0 + feaNoise * 2.0 ) );');
    expect(shader.fragmentShader).toContain('float feaFlicker = 0.35 + 0.65 * feaPulse;');
    // The heatmap gradient is exclusive to the fea branch.
    expect(shader.fragmentShader).toContain('} else if ( uFeaMode > -0.5 ) {');
  });
});
