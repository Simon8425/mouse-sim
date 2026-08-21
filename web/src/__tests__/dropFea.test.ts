import { describe, it, expect } from 'vitest';
import type { DropSimulationResult } from '../api/contracts';
import {
  stressFromForce,
  peakForceForDrop,
  computeDropDamage,
  activeDropIndexAt,
  peakImpactDamage,
  STRESS_FROM_FORCE_MIN_PA,
  STRESS_FROM_FORCE_MAX_PA,
  DEFAULT_YIELD_PA,
} from '../lib/dropFea';

function makeSim(
  drops: { index: number; start_s: number; end_s: number; v: number; e: number }[],
  extra: Partial<DropSimulationResult> = {},
): DropSimulationResult {
  return {
    config: {} as unknown as DropSimulationResult['config'],
    model: { mass_kg: 0.1, inertia_kg_m2: [], support_model: '', support_point_count: 4, integrator: '', timestep_s: 0.001, gravity_m_s2: 9.81, surface: 'concrete' as never },
    drops: drops.map((d) => ({
      index: d.index,
      start_s: d.start_s,
      end_s: d.end_s,
      settled_s: d.end_s,
      settled: true,
      impact_count: 1,
      peak_impact_speed_m_s: d.v,
      peak_kinetic_energy_j: d.e,
      orientation: 'flat' as never,
    })),
    impacts: [],
    drop2: undefined as never,
    peak: null,
    peak_force_estimate_n: null,
    contact_stiffness_n_per_m: null,
    trajectory: [],
    ...extra,
  } as unknown as DropSimulationResult;
}

describe('stressFromForce', () => {
  it('maps force to stress with the screening slope (1000 N -> 18 MPa)', () => {
    expect(stressFromForce(1000)).toBeCloseTo(18e6);
  });

  it('clamps to [15, 85] MPa', () => {
    expect(stressFromForce(100)).toBeCloseTo(STRESS_FROM_FORCE_MIN_PA, 3);
    expect(stressFromForce(10000)).toBeCloseTo(STRESS_FROM_FORCE_MAX_PA, 3);
    expect(stressFromForce(0)).toBe(0);
  });
});

describe('peakForceForDrop', () => {
  it('uses the linear-spring model from stiffness + energy', () => {
    const sim = makeSim([{ index: 0, start_s: 0, end_s: 1, v: 2, e: 0.1 }], {
      contact_stiffness_n_per_m: 2e5,
    });
    const f = peakForceForDrop(sim, 0);
    // F = sqrt(2*k*E) = sqrt(2*2e5*0.1) = sqrt(4e4) = 200
    expect(f).toBeCloseTo(200, 6);
  });

  it('scales the stored peak force by sqrt(E/E_peak)', () => {
    const sim = makeSim(
      [
        { index: 0, start_s: 0, end_s: 1, v: 1, e: 0.02 },
        { index: 1, start_s: 1, end_s: 2, v: 2, e: 0.08 },
      ],
      { peak_force_estimate_n: 400, peak: { kinetic_energy_j: 0.08 } as never },
    );
    const f0 = peakForceForDrop(sim, 0);
    // E0/Epeak = 0.02/0.08 = 0.25 -> sqrt = 0.5 -> F = 200
    expect(f0).toBeCloseTo(200, 6);
    const f1 = peakForceForDrop(sim, 1);
    expect(f1).toBeCloseTo(400, 6);
  });
});

describe('computeDropDamage', () => {
  it('produces a bounded, deterministic damage from the worst drop', () => {
    const sim = makeSim(
      [{ index: 0, start_s: 0, end_s: 1, v: 3, e: 0.45 }],
      { contact_stiffness_n_per_m: 3e5 },
    );
    const d = computeDropDamage(sim, 0);
    expect(d).not.toBeNull();
    expect(d!.peakDamage).toBeGreaterThanOrEqual(0);
    expect(d!.peakDamage).toBeLessThanOrEqual(1);
    expect(d!.yieldPa).toBe(DEFAULT_YIELD_PA);
    expect(d!.peakStressPa).toBeGreaterThan(0);
    // F = sqrt(2*3e5*0.45) = sqrt(270000) = 519.6 -> clamped 85 MPa cap
    expect(d!.peakStressPa).toBeLessThanOrEqual(STRESS_FROM_FORCE_MAX_PA);
    // Same inputs, same result (determinism)
    const d2 = computeDropDamage(sim, 0);
    expect(d!.peakDamage).toBe(d2!.peakDamage);
  });

  it('respects a structural yield override', () => {
    const sim = makeSim([{ index: 0, start_s: 0, end_s: 1, v: 5, e: 0.8 }], {
      contact_stiffness_n_per_m: 2e6,
    });
    const soft = computeDropDamage(sim, 0, 30e6);
    const hard = computeDropDamage(sim, 0, 300e6);
    expect(soft!.peakDamage).toBeGreaterThan(hard!.peakDamage);
    expect(soft!.yieldPa).toBe(30e6);
    expect(hard!.yieldPa).toBe(300e6);
  });

  it('returns null for an empty/unknown drop', () => {
    const sim = makeSim([], {});
    expect(computeDropDamage(sim, 0)).toBeNull();
    expect(peakImpactDamage(sim)).toBeNull();
  });
});

describe('activeDropIndexAt', () => {
  it('returns the drop whose window contains the playback time', () => {
    const sim = makeSim([
      { index: 0, start_s: 0, end_s: 1, v: 1, e: 0.02 },
      { index: 1, start_s: 1, end_s: 2, v: 2, e: 0.08 },
    ]);
    expect(activeDropIndexAt(sim, 0.2)).toBe(0);
    expect(activeDropIndexAt(sim, 1.5)).toBe(1);
    expect(activeDropIndexAt(sim, 0)).toBe(0);
  });

  it('is bounded and deterministic past the last drop', () => {
    const sim = makeSim([{ index: 0, start_s: 0, end_s: 1, v: 1, e: 0.02 }]);
    expect(activeDropIndexAt(sim, 99)).toBe(0);
  });
});
