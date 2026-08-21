import type { DropSimulationImpact, DropSimulationResult, DropSimulationDrop } from '../api/contracts';

/**
 * Drop-impact → peak-damage model that powers the heat/yield map on EVERY
 * drop, even when no structural FEA solve ran.
 *
 * Mirrors the backend's disclosed screening heuristic
 * (`mouse_sim/fea.py` → FEA_STRESS_FROM_FORCE_HEURISTIC):
 *
 *   sigma_peak = clamp( F * 18000 Pa/N , 15 MPa , 85 MPa )
 *   D_peak     = min( 1 , sigma_peak / sigma_yield )
 *
 * The per-drop contact force comes from a linear-spring quasi-static model
 * consistent with the backend's drop estimate:
 *
 *   F_i = sqrt( 2 * k * E_i )        (k = contact stiffness, E_i = peak KE)
 *
 * and when the resolved stiffness is unavailable the result's stored peak
 * force scales by sqrt(E_i / E_peak) (same F ∝ sqrt(KE) relation). The
 * yield reference is the resolved material's yield when a structural FEA
 * result exists, otherwise the ABS-class screening yield (40 MPa, as in
 * `components_mech._ABS_YIELD_PA`); both are disclosed via `method`.
 *
 * Everything is deterministic (no wall-clock, no randomness) and clamped:
 * NaN/Inf can never reach the shader.
 */

/** Alpha-beta screening heuristic: stress per Newton of contact force. */
export const STRESS_FROM_FORCE_SLOPE_PA_PER_N = 18000;
export const STRESS_FROM_FORCE_MIN_PA = 15e6;
export const STRESS_FROM_FORCE_MAX_PA = 85e6;
/** ABS-class screening yield used when no resolved structural yield exists. */
export const DEFAULT_YIELD_PA = 40e6;
/** Damage thresholds shared with the backend + shader. */
export const DENT_THRESHOLD = 0.7;
export const TEAR_THRESHOLD = 0.92;

export interface DropImpactDamage {
  /** Peak damage D = sigma_peak / sigma_yield, clamped to [0, 1]. */
  peakDamage: number;
  /** Clamped screening peak stress (Pa). */
  peakStressPa: number;
  /** Yield reference used (Pa). */
  yieldPa: number;
  /** Peak contact force (N) derived for this drop. */
  peakForceN: number;
  /** Peak impact speed (m/s) for this drop. */
  impactSpeedMs: number;
  /** Peak kinetic energy (J) for this drop. */
  kineticEnergyJ: number;
  /** Disclosure of which force/energy source was used. */
  method: string;
}

function finitePositive(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0;
}

function finiteNonNegative(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

export function stressFromForce(forceN: number): number {
  if (!finiteNonNegative(forceN) || forceN === 0) return 0;
  return Math.min(
    STRESS_FROM_FORCE_MAX_PA,
    Math.max(STRESS_FROM_FORCE_MIN_PA, forceN * STRESS_FROM_FORCE_SLOPE_PA_PER_N),
  );
}

export function peakEnergyForDrop(drop: DropSimulationDrop | undefined): number {
  if (!drop) return 0;
  if (finiteNonNegative(drop.peak_kinetic_energy_j)) return drop.peak_kinetic_energy_j;
  // Fallback: 0.5 * m * v^2 needs mass → caller provides it via sim.model.
  return 0;
}

function resolveStiffness(sim: DropSimulationResult): number {
  if (finitePositive(sim.contact_stiffness_n_per_m)) return sim.contact_stiffness_n_per_m;
  if (finitePositive(sim.peak_force_estimate?.contact_stiffness_n_per_m)) {
    return sim.peak_force_estimate.contact_stiffness_n_per_m;
  }
  return 0;
}

export function peakForceForDrop(sim: DropSimulationResult, dropIndex: number): number {
  const drop = sim.drops[dropIndex];
  if (!drop) return 0;
  const stiffness = resolveStiffness(sim);
  const energy = peakEnergyForDrop(drop);
  if (finitePositive(stiffness) && finitePositive(energy)) {
    // Linear-spring contact: 0.5*k*delta^2 = E, F = k*delta.
    return Math.sqrt(2 * stiffness * energy);
  }
  if (finitePositive(sim.peak_force_estimate_n)) {
    const peakImpact = sim.peak ?? null;
    const energyPeak = finitePositive(sim.peak_force_estimate?.energy_j)
      ? sim.peak_force_estimate.energy_j
      : finitePositive(peakImpact?.kinetic_energy_j)
        ? peakImpact.kinetic_energy_j
        : energy;
    if (finitePositive(energyPeak) && finitePositive(energy)) {
      // F ∝ sqrt(KE): scale the stored peak force to this drop's energy.
      return sim.peak_force_estimate_n * Math.sqrt(energy / energyPeak);
    }
    return sim.peak_force_estimate_n;
  }
  return 0;
}

export function computeDropDamage(
  sim: DropSimulationResult,
  dropIndex: number,
  yieldPaOverride?: number,
): DropImpactDamage | null {
  const drop = sim.drops[dropIndex];
  if (!drop) return null;
  const impactSpeed = finiteNonNegative(drop.peak_impact_speed_m_s) ? drop.peak_impact_speed_m_s : 0;
  const energy = peakEnergyForDrop(drop);
  const force = peakForceForDrop(sim, dropIndex);
  const yieldPa =
    finitePositive(yieldPaOverride) ? yieldPaOverride : DEFAULT_YIELD_PA;
  if (force <= 0 && energy <= 0 && impactSpeed <= 0) return null;
  // When force is unavailable (no stiffness, no stored peak force) fall back
  // to an energy-only severity so the map still paints on every drop
  // (disclosed). KE ∝ v^2, so severity ∝ sqrt(E) keeps the visual physical.
  const peakStressPa =
    force > 0 ? stressFromForce(force) : Math.min(85e6, Math.sqrt(energy) * 1e7);
  const peakDamage = Math.min(
    1,
    Math.max(0, finitePositive(yieldPa) ? peakStressPa / yieldPa : 0),
  );
  const method =
    force > 0
      ? finitePositive(resolveStiffness(sim))
        ? 'drop_spring_force'
        : 'drop_peak_force_scaled'
      : 'drop_energy_severity';
  return {
    peakDamage,
    peakStressPa,
    yieldPa,
    peakForceN: force,
    impactSpeedMs: impactSpeed,
    kineticEnergyJ: energy,
    method,
  };
}

/** The peak (worst) impact damage used when the active drop is ambiguous. */
export function peakImpactDamage(
  sim: DropSimulationResult,
  yieldPaOverride?: number,
): DropImpactDamage | null {
  let peak: DropImpactDamage | null = null;
  for (let i = 0; i < sim.drops.length; i += 1) {
    const damage = computeDropDamage(sim, i, yieldPaOverride);
    if (!damage) continue;
    if (!peak || damage.peakDamage > peak.peakDamage) peak = damage;
  }
  return peak;
}

export function firstImpactTime(sim: DropSimulationResult | null): number {
  if (!sim) return 0;
  const impact = sim.impacts?.[0] as DropSimulationImpact | undefined;
  if (impact && finiteNonNegative(impact.t_s)) return impact.t_s;
  const first = sim.drops[0];
  if (first && finitePositive(first.end_s)) return first.end_s * 0.38;
  return 0;
}

/** Active drop index at a playback time (bounded, deterministic). */
export function activeDropIndexAt(sim: DropSimulationResult | null, dropTime: number): number {
  if (!sim || sim.drops.length === 0) return 0;
  for (let i = 0; i < sim.drops.length; i += 1) {
    const drop = sim.drops[i];
    if (finiteNonNegative(drop.start_s) && dropTime >= drop.start_s && dropTime <= drop.end_s) {
      return drop.index ?? i;
    }
  }
  return sim.drops[sim.drops.length - 1].index ?? sim.drops.length - 1;
}
