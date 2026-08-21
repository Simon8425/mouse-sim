import * as THREE from 'three';
import type { FeaResult } from '../api/contracts';

/**
 * FEA stress visualization shader engine (three r168).
 *
 * Extends MeshStandardMaterial via `onBeforeCompile` — the material is never
 * replaced, so lighting, shadows, OutlinePass and ACES tone mapping keep
 * working. The extension is data-driven:
 *
 *  - Attribute path: per-vertex `aDamage` (float) + `aDisplacement` (vec3)
 *    attributes written by geometryFactory/applyFeaObjectField. Vertex denting
 *    and fragment color/discard read the attribute damage.
 *  - Procedural path: the same Gaussian falloff
 *    σ_v(d) = σ_peak·exp(−(d/λ)²), D = min(1, σ_v/σ_yield) is recomputed in
 *    the vertex shader from the impact point / falloff radius uniforms on the
 *    untransformed local position.
 *
 * All per-vertex attributes are declared behind `#ifdef USE_FEA_ATTRIBUTES`,
 * a define set on the material ONLY when the geometry actually carries the
 * attributes, so geometries without them keep compiling and rendering
 * unchanged on the first frame.
 *
 * Damage gradient (Blue → Cyan → Green → Yellow → Red over D ∈ [0, 0.90]) is a
 * 5-stop piecewise ramp with eased (smoothstep) interpolation between stops,
 * implemented identically in TS (feaGradientColor) and GLSL
 * (feaGradientColorFrag). The stops are shared constants: the GLSL ramp is
 * generated from FEA_GRADIENT_STOPS so the two can never drift apart. The
 * yellow stop at 0.72 (vs 0.5 in a naive linear ramp) makes mid-range stress
 * read yellow/orange instead of saturating red too early.
 */

/** Damage at which plastic denting starts (backend dent_threshold). */
export const FEA_DENT_THRESHOLD = 0.7;
/** Damage above which fragments tear away (backend tear_threshold). */
export const FEA_TEAR_THRESHOLD = 0.92;
/** Damage at which the heatmap gradient saturates to red. */
export const FEA_GRADIENT_END = 0.9;
/** Width of the plastic dent boost ramp (1 + 2·(D−0.7)/0.3), fixed by the backend. */
export const FEA_PLASTIC_RAMP = 0.3;
/** Plastic amplification cap, mirroring the backend PLASTIC_AMPLIFICATION_MAX. */
export const FEA_PLASTIC_AMPLIFICATION_MAX = 3.0;
/** Dent depth cap (× compression), mirroring the backend DENT_DEPTH_CAP_FACTOR. */
export const FEA_DENT_DEPTH_CAP = 1.5;
/** Peak whitening amplitude (fraction of material color toward white). */
export const FEA_WHITENING_AMPLITUDE = 0.45;

const FEA_GRADIENT_STOPS: ReadonlyArray<{ position: number; color: readonly [number, number, number] }> = [
  { position: 0.0, color: [0, 0, 1] }, // blue
  { position: 0.28, color: [0, 1, 1] }, // cyan
  { position: 0.5, color: [0, 1, 0] }, // green
  { position: 0.72, color: [1, 1, 0] }, // yellow
  { position: 1.0, color: [1, 0, 0] }, // red
];

/**
 * Pure sRGB intent gradient: Blue → Cyan → Green → Yellow → Red mapped over
 * D ∈ [0.0, 0.90] (clamped). Writes into `target` and returns it. The ramp
 * mirrors feaGradientColorFrag in GLSL exactly (both interpolate the same 5
 * stops with the same smoothstep easing, and both are generated from
 * FEA_GRADIENT_STOPS), so CPU helpers and GPU output match closely.
 */
export function feaGradientColor(damage: number, target: THREE.Color): THREE.Color {
  const t = Math.min(1, Math.max(0, damage / FEA_GRADIENT_END));
  let i = 0;
  while (i < FEA_GRADIENT_STOPS.length - 2 && t > FEA_GRADIENT_STOPS[i + 1].position) i += 1;
  const a = FEA_GRADIENT_STOPS[i];
  const b = FEA_GRADIENT_STOPS[i + 1];
  const span = b.position - a.position;
  const f = span > 0 ? Math.min(1, Math.max(0, (t - a.position) / span)) : 0;
  const s = f * f * (3 - 2 * f); // cubic smoothstep easing between stops
  target.setRGB(
    a.color[0] + (b.color[0] - a.color[0]) * s,
    a.color[1] + (b.color[1] - a.color[1]) * s,
    a.color[2] + (b.color[2] - a.color[2]) * s,
  );
  return target;
}

/**
 * Pure helper mirroring the backend Gaussian damage falloff:
 * peakDamage · exp(−(d/λ)²), clamped to [0, 1]. No WebGL needed.
 */
export function damageForDistance(distanceM: number, falloffRadiusM: number, peakDamage: number): number {
  if (!Number.isFinite(distanceM) || !Number.isFinite(falloffRadiusM) || !Number.isFinite(peakDamage)) {
    return 0;
  }
  if (falloffRadiusM <= 0) return 0;
  const ratio = distanceM / falloffRadiusM;
  const value = peakDamage * Math.exp(-(ratio * ratio));
  return Math.min(1, Math.max(0, value));
}

/**
 * Pure helper mirroring the backend plastic dent boost: 0 below the dent
 * threshold, else 1 + 2·(D − 0.7)/0.3, capped at 3.0 (the backend
 * PLASTIC_AMPLIFICATION_MAX — the shader previously capped at 1.5, HALF
 * the backend value, under-denting the procedural path).
 */
export function dentFactorFor(damage: number): number {
  if (damage < FEA_DENT_THRESHOLD) return 0;
  return Math.min(FEA_PLASTIC_AMPLIFICATION_MAX, 1 + 2 * ((damage - FEA_DENT_THRESHOLD) / FEA_PLASTIC_RAMP));
}

/**
 * Dent depth factor mirroring the backend depth cap: the depth is
 * delta_max · gaussian · amplification capped at DENT_DEPTH_CAP_FACTOR ·
 * delta_max, i.e. depth_factor = min(1.5, gaussian · amplification). The
 * cap applies to the COMBINED gaussian·amplification product, never to the
 * amplification alone (a vertex at gauss = 0.8 with amp = 3.0 dents
 * 1.5·delta_max, not 0.8·1.5·delta_max).
 */
export function dentDepthFactorFor(gaussian: number, damage: number): number {
  if (!Number.isFinite(gaussian) || gaussian <= 0) return 0;
  return Math.min(FEA_DENT_DEPTH_CAP, gaussian * dentFactorFor(damage));
}

/**
 * Impact pulse envelope driving the dynamic heatmap surge and the
 * crack-striation flicker: 0 before the impact time, 1 at the moment of
 * impact, then exponential decay (≈ 1/e every 1/6 s). Each drop's impact
 * re-triggers its own pulse, so the visualization animates across playback
 * frames and impacts.
 */
export function impactPulseFor(dropTime: number, impactTime: number, windowS: number): number {
  void windowS; // retained for API stability; the decay is window-independent
  if (!Number.isFinite(dropTime) || !Number.isFinite(impactTime)) return 0;
  const since = dropTime - impactTime;
  if (since < 0) return 0;
  // Peak AT the moment of impact, decaying over ~1/6 s (≈ 1/e per 1/6 s).
  return Math.max(0, Math.min(1, Math.exp(-since * 6)));
}

/**
 * Normalized yield threshold: where the backend dent threshold (true
 * damage) sits on the auto-normalized visual ramp (feaDamageVis ∈ [0, 1]).
 * A field whose peak never reaches the dent threshold clamps to 1, so the
 * yield mask honestly stays off for safe runs.
 */
export function yieldNormFor(fieldMaxDamage: number, dentThreshold: number): number {
  if (!Number.isFinite(fieldMaxDamage) || fieldMaxDamage <= 0) return 0;
  if (!Number.isFinite(dentThreshold) || dentThreshold <= 0) return 1;
  return Math.min(1, Math.max(0, dentThreshold / fieldMaxDamage));
}

/**
 * Yield mask: 0 below the (normalized) yield level, ramping to 1 at the
 * field peak. Mirrors the GLSL smoothstep(uYieldNorm, 1.0, feaDamageVis).
 */
export function yieldMaskFor(normalizedDamage: number, yieldNorm: number): number {
  if (!Number.isFinite(normalizedDamage) || !Number.isFinite(yieldNorm)) return 0;
  const dn = Math.min(1, Math.max(0, normalizedDamage));
  const yn = Math.min(1, Math.max(0, yieldNorm));
  if (yn >= 1) return 0;
  if (yn <= 0) return dn > 0.999 ? 1 : 0;
  return Math.min(1, Math.max(0, (dn - yn) / (1 - yn)));
}

/**
 * Crack/tear intensity from TRUE damage: 0 below the dent threshold,
 * ramping to 1 at/above the tear threshold. Mirrors the GLSL feaW ramp.
 */
export function crackIntensityFor(
  damage: number,
  dentThreshold: number,
  tearThreshold: number,
): number {
  if (!Number.isFinite(damage)) return 0;
  const span = tearThreshold - dentThreshold;
  if (!(span > 0)) return damage >= tearThreshold ? 1 : 0;
  return Math.min(1, Math.max(0, (damage - dentThreshold) / span));
}

/** Uniform record the runtime agent updates per frame (see updateFeaUniforms). */
export type FeaUniforms = Record<string, THREE.IUniform>;

/**
 * Continuous plate-field layer config: maps the object's local bounding box
 * onto the simply-supported panel domain exactly like the backend
 * (x_panel = a/2 + (x - cx) * a / xExtent) so the fragment shader can
 * evaluate the dominant Navier term per-fragment. This keeps the heatmap
 * visible on vertex-sparse primitives (e.g. a box whose vertices all sit on
 * the plate boundary, where the per-vertex field is exactly zero).
 */
export interface FeaPlateConfig {
  a: number;
  b: number;
  cx: number;
  cy: number;
  xExtent: number;
  yExtent: number;
  peakDamage: number;
}

/** Maximum damage across the per-vertex fields and procedural peaks. */
export function feaFieldMaxDamage(fea: FeaResult | null): number {
  if (!fea) return 0;
  let fieldMaxDamage = 0;
  if (typeof fea.peak?.damage === 'number' && Number.isFinite(fea.peak.damage)) {
    fieldMaxDamage = Math.max(fieldMaxDamage, fea.peak.damage);
  }
  if (typeof fea.peak?.stress_mpa === 'number' && fea.peak.stress_mpa > 0) {
    const yieldMpa = fea.yield_stress_pa ? fea.yield_stress_pa / 1e6 : 40;
    fieldMaxDamage = Math.max(fieldMaxDamage, Math.min(1, fea.peak.stress_mpa / yieldMpa));
  }
  for (const field of fea.objects) {
    for (const value of field.damage) {
      if (typeof value === 'number' && Number.isFinite(value) && value > fieldMaxDamage) {
        fieldMaxDamage = value;
      }
    }
  }
  for (const entry of fea.procedural) {
    if (entry.yield_stress_pa > 0 && entry.peak_stress_pa > 0) {
      const entryPeak = Math.min(1, Math.max(0, entry.peak_stress_pa / entry.yield_stress_pa));
      if (entryPeak > fieldMaxDamage) fieldMaxDamage = entryPeak;
    }
  }
  return fieldMaxDamage;
}

/** Per-frame options for updateFeaUniforms — no allocations. */
export interface FeaUpdateOptions {
  mode: 'default' | 'fea' | 'yield';
  impactWindow01: number;
  impactWindowActive: boolean;
  time: number;
  /** Impact pulse amplitude in [0, 1] (0 when idle/static). */
  impactPulse?: number;
  /** Drop playback time in seconds; 0 when there is no drop simulation. */
  dropTime?: number;
  /** First impact time of the active drop (seconds). */
  impactTime?: number;
}

/**
 * Build the uniform set once per decorated material (shared objects; per-frame
 * updates only mutate `.value`). Static procedural values are pre-baked from
 * `fea.procedural[0]` when present; the runtime agent may still overwrite any
 * uniform value directly, or pass this record into decorateForFea()/
 * feaDecoratedMaterial().
 */
export function createFeaUniforms(
  fea: FeaResult | null,
  plate?: FeaPlateConfig | null,
): FeaUniforms {
  const procedural = fea?.procedural?.[0] ?? null;
  const yieldStress = fea?.yield_stress_pa ?? null;
  const peakDamage =
    procedural && yieldStress != null && yieldStress > 0 && procedural.peak_stress_pa > 0
      ? Math.min(1, Math.max(0, procedural.peak_stress_pa / yieldStress))
      : 0;

  // Auto-normalization scale for the visual contour: 1 / field maximum so
  // the Blue->Red ramp always spans the field's own peak. When the field is
  // all zeros the scale stays 1 (no heatmap); the scale is capped so a
  // pathological tiny-but-nonzero field cannot saturate the whole model.
  const fieldMaxDamage = feaFieldMaxDamage(fea);
  const damageScale = fieldMaxDamage > 0 ? Math.min(1 / fieldMaxDamage, 1e6) : 1;

  const normal = new THREE.Vector3(0, 0, 1);
  if (procedural) {
    normal.set(
      procedural.contact_normal_model[0],
      procedural.contact_normal_model[1],
      procedural.contact_normal_model[2],
    );
    if (normal.lengthSq() > 0) normal.normalize();
    else normal.set(0, 0, 1);
  }

  return {
    uFeaMode: { value: 0 },
    uImpactWindow01: { value: 0 },
    uImpactWindowActive: { value: 0 },
    uTime: { value: 0 },
    uImpactPointModel: {
      value: procedural
        ? new THREE.Vector3(
            procedural.impact_point_model_m[0],
            procedural.impact_point_model_m[1],
            procedural.impact_point_model_m[2],
          )
        : new THREE.Vector3(),
    },
    uFalloffRadius: {
      value:
        procedural && Number.isFinite(procedural.falloff_radius_m) && procedural.falloff_radius_m > 0
          ? procedural.falloff_radius_m
          : 0,
    },
    uImpactNormalModel: { value: normal },
    uMaxCompression: {
      value:
        typeof procedural?.max_compression_m === 'number' &&
        Number.isFinite(procedural.max_compression_m)
          ? procedural.max_compression_m
          : 0,
    },
    uPeakDamage: { value: peakDamage },
    uDamageScale: { value: damageScale },
    // Dynamic impact pulse (heatmap surge / crack flicker).
    uImpactPulse: { value: 0 },
    uDropTime: { value: 0 },
    uImpactTime: { value: 0 },
    // Yield threshold on the auto-normalized visual ramp: above this the
    // yield mask / crack striations trigger (see yieldNormFor).
    uYieldNorm: {
      value:
        fieldMaxDamage > 0 && fea && typeof fea.dent_threshold === 'number' && fea.dent_threshold > 0
          ? yieldNormFor(fieldMaxDamage, fea.dent_threshold)
          : 1,
    },
    // Continuous plate-field layer (dominant Navier term, fragment space).
    uPlateA: { value: plate && plate.a > 0 ? plate.a : 0 },
    uPlateB: { value: plate && plate.b > 0 ? plate.b : 0 },
    uPlateCx: { value: plate?.cx ?? 0 },
    uPlateCy: { value: plate?.cy ?? 0 },
    uPlateXExtent: { value: plate && plate.xExtent > 0 ? plate.xExtent : 1 },
    uPlateYExtent: { value: plate && plate.yExtent > 0 ? plate.yExtent : 1 },
    uPlatePeakDamage: {
      value: typeof plate?.peakDamage === 'number' && Number.isFinite(plate.peakDamage)
        ? Math.min(1, Math.max(0, plate.peakDamage))
        : 0,
    },
  };
}

/**
 * Cheap per-frame uniform updates. uFeaMode: 'default' → −1 (FEA fully off),
 * 'fea' → 0 (heatmap only: no denting, no whitening, no discard),
 * 'yield' → 1 (heatmap + whitening + discard + denting).
 */
export function updateFeaUniforms(uniforms: FeaUniforms, opts: FeaUpdateOptions): void {
  uniforms.uFeaMode.value = opts.mode === 'yield' ? 1 : opts.mode === 'fea' ? 0 : -1;
  uniforms.uImpactWindow01.value = opts.impactWindow01;
  uniforms.uImpactWindowActive.value = opts.impactWindowActive ? 1 : 0;
  uniforms.uTime.value = opts.time;
  uniforms.uImpactPulse.value = opts.impactPulse ?? 0;
  uniforms.uDropTime.value = opts.dropTime ?? 0;
  uniforms.uImpactTime.value = opts.impactTime ?? 0;
}

/** GLSL ES 1.00 has no implicit int->float conversion: emit a float literal. */
function glslFloat(value: number): string {
  return Number.isInteger(value) ? `${value}.0` : `${value}`;
}

const FEA_VERTEX_PREFIX = /* glsl */ `
uniform float uFeaMode;
uniform float uImpactWindow01;
uniform float uImpactWindowActive;
uniform vec3 uImpactPointModel;
uniform float uFalloffRadius;
uniform vec3 uImpactNormalModel;
uniform float uMaxCompression;
uniform float uPeakDamage;
uniform float uDamageScale;
uniform float uImpactPulse;
uniform float uDropTime;
uniform float uImpactTime;
uniform float uYieldNorm;

varying float vFeaDamage;
varying vec3 vFeaPosition;

#ifdef USE_FEA_ATTRIBUTES
	attribute float aDamage;
	attribute vec3 aDisplacement;
#endif
`;

const FEA_VERTEX_BODY = /* glsl */ `
	// --- FEA vertex (after begin_vertex) ---
	float feaProg = mix( 1.0, uImpactWindow01, uImpactWindowActive );
	float feaDamage = 0.0;
	float feaDent = 0.0;
	#ifdef USE_FEA_ATTRIBUTES
		feaDamage = aDamage;
		float feaDamageNorm = aDamage * uDamageScale;
		feaDent = step( 0.5, uFeaMode ) * feaProg * step( ${glslFloat(FEA_DENT_THRESHOLD)}, feaDamageNorm );
		transformed += aDisplacement * feaDent;
		// Procedural dent complement: sparse meshes have no vertex inside the
		// impact zone, so the attribute dent would be invisible — dent
		// continuously wherever the attribute damage is below the threshold.
		// The depth factor mirrors the backend: min(1.5, gauss·amp) with
		// amp = 1 + 2·(D−0.7)/0.3 capped at 3.0 — the 1.5 cap applies to the
		// COMBINED product (backend DENT_DEPTH_CAP_FACTOR), never to the
		// amplification alone.
		float feaGaussProc = exp( -dot( position - uImpactPointModel, position - uImpactPointModel ) / max( uFalloffRadius * uFalloffRadius, 1e-6 ) );
		float feaPlasticProc = min( ${glslFloat(FEA_PLASTIC_AMPLIFICATION_MAX)}, 1.0 + 2.0 * max( 0.0, ( uPeakDamage * feaGaussProc - ${glslFloat(FEA_DENT_THRESHOLD)} ) / ${glslFloat(FEA_PLASTIC_RAMP)} ) );
		transformed -= uImpactNormalModel * uMaxCompression * min( ${glslFloat(FEA_DENT_DEPTH_CAP)}, feaGaussProc * feaPlasticProc ) * step( 0.5, uFeaMode ) * feaProg * ( 1.0 - step( ${glslFloat(FEA_DENT_THRESHOLD)}, feaDamageNorm ) );
	#else
		// Procedural path: same Gaussian on the untransformed local position.
		float feaGauss = exp( -dot( position - uImpactPointModel, position - uImpactPointModel ) / max( uFalloffRadius * uFalloffRadius, 1e-6 ) );
		feaDamage = uPeakDamage * feaGauss;
		float feaDamageNorm = feaDamage * uDamageScale;
		float feaPlastic = min( ${glslFloat(FEA_PLASTIC_AMPLIFICATION_MAX)}, 1.0 + 2.0 * max( 0.0, ( feaDamageNorm - ${glslFloat(FEA_DENT_THRESHOLD)} ) / ${glslFloat(FEA_PLASTIC_RAMP)} ) );
		feaDent = step( 0.5, uFeaMode ) * feaProg;
		transformed -= uImpactNormalModel * uMaxCompression * min( ${glslFloat(FEA_DENT_DEPTH_CAP)}, feaGauss * feaPlastic ) * feaDent;
	#endif
	vFeaDamage = clamp( feaDamage, 0.0, 1.0 );
	vFeaPosition = position;
	// --- end FEA vertex ---
`;

const FEA_FRAGMENT_PREFIX = /* glsl */ `
uniform float uFeaMode;
uniform float uTime;
uniform float uImpactWindow01;
uniform float uImpactWindowActive;
uniform vec3 uImpactPointModel;
uniform float uFalloffRadius;
uniform float uPeakDamage;
uniform float uDamageScale;
uniform float uImpactPulse;
uniform float uDropTime;
uniform float uImpactTime;
uniform float uYieldNorm;
uniform float uPlateA;
uniform float uPlateB;
uniform float uPlateCx;
uniform float uPlateCy;
uniform float uPlateXExtent;
uniform float uPlateYExtent;
uniform float uPlatePeakDamage;

varying float vFeaDamage;
varying vec3 vFeaPosition;

#define FEA_PI 3.141592653589793

float feaHashNoise( vec3 p, float seed ) {
	vec3 q = floor( p * 384.0 );
	return fract( sin( dot( q + seed * 0.317, vec3( 127.1, 311.7, 74.7 ) ) ) * 43758.5453123 );
}

${buildGradientColorFrag()}
`;

/**
 * Emit feaGradientColorFrag from FEA_GRADIENT_STOPS so the GPU ramp always
 * matches the CPU ramp: one source of truth, same smoothstep easing, same
 * stops. The emitted GLSL uses an if/else chain (GLSL ES 1.00 has no dynamic
 * array indexing) mirroring the TS segment walk in feaGradientColor.
 */
function buildGradientColorFrag(): string {
  const stops = FEA_GRADIENT_STOPS;
  const lines = [
    'vec3 feaGradientColorFrag( float damage ) {',
    `\tfloat t = clamp( damage, 0.0, ${FEA_GRADIENT_END} ) / ${FEA_GRADIENT_END};`,
    '\tfloat f;',
    '\tvec3 a;',
    '\tvec3 b;',
  ];
  for (let i = 0; i < stops.length - 1; i += 1) {
    const from = stops[i];
    const to = stops[i + 1];
    const span = to.position - from.position;
    const isLast = i === stops.length - 2;
    if (i === 0) {
      lines.push(`\tif ( t < ${glslFloat(to.position)} ) {`);
    } else if (!isLast) {
      lines.push(`\t} else if ( t < ${glslFloat(to.position)} ) {`);
    } else {
      lines.push('\t} else {');
    }
    lines.push(
      `\t\ta = vec3( ${from.color.join(', ')} );`,
      `\t\tb = vec3( ${to.color.join(', ')} );`,
      `\t\tf = ( t - ${glslFloat(from.position)} ) / ${glslFloat(span)};`,
    );
  }
  lines.push('\t}');
  lines.push('\tf = f * f * ( 3.0 - 2.0 * f );');
  lines.push('\treturn mix( a, b, f );');
  lines.push('}');
  return lines.join('\n');
}

const FEA_FRAGMENT_BODY = /* glsl */ `
	// --- FEA fragment (after color_fragment) ---
	// Dynamic impact scaling during drop simulation:
	// While the drop is active/falling, progress advances from 0 to 1 at the moment of impact.
	// When inspecting post-drop or static models, uImpactWindowActive is 0 so progress is 1.0.
	float feaProg = mix( 1.0, uImpactWindow01, uImpactWindowActive );
	// Impact pulse: an exponential-decay surge peaking AT the impact moment
	// (~1/e per 1/6 s), re-triggered at every impact time. It drives the
	// heatmap ripple and the crack striation flicker so the contour animates
	// across playback frames. uImpactTime <= 0 means "no drop impact known"
	// and disables the pulse (static inspection stays static).
	float feaSince = uDropTime - uImpactTime;
	float feaPulse = 0.0;
	if ( uImpactTime > 0.0 && feaSince >= 0.0 ) {
		// Exponential decay peaking AT the impact moment (~1/e per 1/6 s),
		// mirroring impactPulseFor() on the CPU side.
		feaPulse = clamp( exp( -feaSince * 6.0 ), 0.0, 1.0 );
	}
	// Continuous procedural base layer: the same Gaussian evaluated on the
	// interpolated fragment position keeps the heatmap visible even on sparse
	// meshes whose vertices miss the impact zone (the per-vertex field from
	// vFeaDamage would otherwise be zero everywhere).
	float feaProcedural = uPeakDamage * exp( -dot( vFeaPosition - uImpactPointModel, vFeaPosition - uImpactPointModel ) / max( uFalloffRadius * uFalloffRadius, 1e-6 ) ) * feaProg;
	// Continuous plate-field layer: the dominant Navier term evaluated on the
	// interpolated fragment position (same bbox->panel mapping as the backend
	// and applyFeaPlateField). Keeps the heatmap visible on vertex-sparse
	// primitives whose per-vertex field is zero (box vertices on the edges).
	float feaPlate = 0.0;
	if ( uPlateA > 0.0 && uPlateB > 0.0 ) {
		float feaPx = uPlateA / 2.0 + ( vFeaPosition.x - uPlateCx ) * ( uPlateA / max( uPlateXExtent, 1e-9 ) );
		float feaPy = uPlateB / 2.0 + ( vFeaPosition.y - uPlateCy ) * ( uPlateB / max( uPlateYExtent, 1e-9 ) );
		feaPlate = uPlatePeakDamage * sin( FEA_PI * feaPx / uPlateA ) * sin( FEA_PI * feaPy / uPlateB ) * feaProg;
		feaPlate = max( feaPlate, 0.0 );
	}
	float feaDamage = clamp( max( vFeaDamage * feaProg, max( feaProcedural, feaPlate ) ), 0.0, 1.0 );
	float feaDamageVis = clamp( feaDamage * uDamageScale, 0.0, 1.0 );
	// Radial impact ripple: a smooth wave expanding from the impact point
	// while the pulse is active, modulating the displayed damage so the
	// heatmap visibly surges outward at every impact. The wave uses the
	// Gaussian scale so it tracks the actual stress falloff shape.
	float feaRipple = 0.0;
	if ( uImpactTime > 0.0 && uFalloffRadius > 1e-6 ) {
		float feaRad = length( vFeaPosition - uImpactPointModel ) / uFalloffRadius;
		float feaWave = 0.5 + 0.5 * sin( feaRad * 6.28318 - feaSince * 18.0 );
		feaRipple = feaPulse * exp( -feaRad * feaRad ) * feaWave;
	}
	feaDamageVis = clamp( feaDamageVis + feaRipple * 0.25, 0.0, 1.0 );
	vec3 baseColor = diffuseColor.rgb;
	if ( uFeaMode > 0.5 ) {
		// YIELD SHADER: a steel-gray base with a clearly masked plastic
		// zone. The mask ramps from the normalized yield threshold to the
		// field peak, so everything ABOVE the material yield strength lights
		// up hot (contour) instead of a faint broad tint.
		vec3 yieldColor = vec3( 0.60, 0.62, 0.66 );
		float feaYieldMask = smoothstep( uYieldNorm, 1.0, feaDamageVis );
		yieldColor = mix( yieldColor, vec3( 1.0, 0.36, 0.05 ), feaYieldMask * 0.85 );
		// Micro-crack striations inside the plastic zone: deterministic
		// per-fragment noise striping animated by the impact pulse (and a
		// slow drift while paused), plus stress-whitening that ramps with
		// true damage from the dent threshold to the tear threshold.
		float feaW = clamp( ( feaDamage - ${FEA_DENT_THRESHOLD} ) / ${FEA_TEAR_THRESHOLD - FEA_DENT_THRESHOLD}, 0.0, 1.0 );
		if ( feaW > 0.001 ) {
			float feaNoise = feaHashNoise( vFeaPosition, uTime );
			float feaStripe = smoothstep( 0.45, 0.75, fract( vFeaPosition.y * 140.0 + feaNoise * 2.0 ) );
			float feaFlicker = 0.35 + 0.65 * feaPulse;
			yieldColor = mix( yieldColor, vec3( 1.0 ), feaNoise * ${FEA_WHITENING_AMPLITUDE} * feaW * feaFlicker );
			yieldColor = mix( yieldColor, vec3( 0.05, 0.05, 0.06 ), feaStripe * feaW * feaFlicker );
			yieldColor = mix( yieldColor, vec3( 1.0, 0.15, 0.03 ), 0.5 * feaW * feaFlicker );
		}
		if ( feaDamage > ${FEA_TEAR_THRESHOLD} && feaProg > 0.5 ) {
			discard;
		}
		diffuseColor.rgb = mix( baseColor, yieldColor, feaProg );
	} else if ( uFeaMode > -0.5 ) {
		// FEA HEATMAP: Full von Mises stress gradient contour mapped across the full range.
		float feaDither = ( feaHashNoise( vFeaPosition, 0.37 ) - 0.5 ) / 48.0;
		float feaVisDithered = clamp( feaDamageVis + feaDither, 0.0, 1.0 );
		vec3 feaColor = feaGradientColorFrag( feaVisDithered );
		diffuseColor.rgb = mix( baseColor, feaColor, feaProg );
	}
	// --- end FEA fragment ---
`;

/**
 * Extend `material` with the FEA onBeforeCompile hook (mutates and returns the
 * same material instance — callers pass an owned clone; the shared palette
 * material must never be decorated directly). When `uniforms` is omitted a
 * default set is created; the active set is always stored on
 * `material.userData.feaUniforms` for per-frame updates.
 *
 * The `USE_FEA_ATTRIBUTES` define is set on the material only when the geometry
 * actually carries `aDamage`/`aDisplacement`, so attribute-less geometries
 * compile and render unchanged.
 */
export function decorateForFea(
  material: THREE.MeshStandardMaterial,
  geometry: THREE.BufferGeometry,
  uniforms?: FeaUniforms,
): THREE.MeshStandardMaterial {
  const hasAttributes =
    geometry.hasAttribute('aDamage') && geometry.hasAttribute('aDisplacement');
  if (hasAttributes) {
    material.defines = { ...material.defines, USE_FEA_ATTRIBUTES: '1' };
  }
  const active = uniforms ?? createFeaUniforms(null);
  material.userData.feaUniforms = active;

  material.onBeforeCompile = (shader: THREE.WebGLProgramParametersWithUniforms) => {
    for (const key of Object.keys(active)) {
      shader.uniforms[key] = active[key];
    }
    shader.vertexShader = shader.vertexShader.replace(
      '#include <common>',
      FEA_VERTEX_PREFIX + '#include <common>',
    );
    shader.vertexShader = shader.vertexShader.replace(
      '#include <begin_vertex>',
      '#include <begin_vertex>' + FEA_VERTEX_BODY,
    );
    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <common>',
      FEA_FRAGMENT_PREFIX + '#include <common>',
    );
    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <color_fragment>',
      '#include <color_fragment>' + FEA_FRAGMENT_BODY,
    );
  };
  return material;
}
