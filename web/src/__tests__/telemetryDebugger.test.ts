/**
 * Telemetry debugger — core logic tests.
 */
import { describe, expect, it } from 'vitest';
import {
  auditEnergy,
  classifyDropStatus,
  estimateAcceleration,
  EnergyAuditor,
  kineticRot,
  kineticTrans,
  quatToEulerDeg,
  RingBuffer,
  TelemetryEventDetector,
} from '../lib/telemetryDebugger';
import type { TelemetryFrame } from '../api/telemetryDebuggerContracts';
import { effectiveContactModulus, framesToCsv, meshAreaAndVolume, meshBounds } from '../lib/telemetrySessionBuilder';

describe('RingBuffer', () => {
  it('keeps the latest N items with O(1) push', () => {
    const ring = new RingBuffer<number>(4);
    for (let i = 0; i < 10; i += 1) ring.push(i);
    expect(ring.length).toBe(4);
    expect(ring.toArray()).toEqual([6, 7, 8, 9]);
    expect(ring.at(0)).toBe(6);
    expect(ring.at(3)).toBe(9);
    expect(ring.at(4)).toBeUndefined();
  });

  it('clears', () => {
    const ring = new RingBuffer<number>(2);
    ring.push(1);
    ring.push(2);
    ring.clear();
    expect(ring.length).toBe(0);
    expect(ring.toArray()).toEqual([]);
  });
});

describe('energy audit', () => {
  const src = {
    position_m: [0, 0, 1] as [number, number, number],
    velocity_m_s: [0, 0, 0] as [number, number, number],
    angular_velocity_rad_s: [0, 0, 0] as [number, number, number],
    mass_kg: 0.1,
    inertia_diag_kg_m2: [1e-5, 1e-5, 1e-5] as [number, number, number],
    gravity_m_s2: 9.81,
    floor_z: 0,
    release_energy_j: 0.1 * 9.81 * 1,
  };

  it('computes translational kinetic energy', () => {
    expect(kineticTrans([2, 0, 0], 0.5)).toBeCloseTo(1, 10);
    expect(kineticTrans([0, 0, 0], 0.5)).toBe(0);
  });

  it('computes rotational kinetic energy from the diagonal inertia', () => {
    expect(kineticRot([2, 0, 0], [0.5, 1, 1])).toBeCloseTo(1, 10);
    expect(kineticRot([1, 2, 3], [1, 2, 3])).toBeCloseTo(0.5 * (1 + 8 + 27), 10);
  });

  it('audits potential/kinetic/dissipated/drift from release energy', () => {
    const a = auditEnergy({ ...src, position_m: [0, 0, 0.5], velocity_m_s: [0, 0, 3.13] });
    // Potential: m g h = 0.1*9.81*0.5
    expect(a.potential_j).toBeCloseTo(0.1 * 9.81 * 0.5, 6);
    // Total must not exceed release: release = m g h0 = 0.1*9.81*1
    expect(a.total_j).toBeLessThanOrEqual(0.1 * 9.81 * 1 + 1e-9);
    // Drift stays near 0 when the audit closes exactly.
    expect(a.drift_pct).toBeLessThan(1);
  });

  it('tracks max drift across pushes', () => {
    const auditor = new EnergyAuditor(src);
    auditor.push([0, 0, 1], [0, 0, 0], [0, 0, 0], 0);
    auditor.push([0, 0, 0.5], [0, 0, 3.13], [0, 0, 0], 0.0166);
    expect(auditor.maxDriftPct).toBeGreaterThanOrEqual(0);
    expect(auditor.lastAccelerationG).toBeGreaterThan(0);
  });
});

describe('acceleration estimation', () => {
  it('finite-differences velocity', () => {
    const a = estimateAcceleration([0, 0, 10], [0, 0, 5], 0.5);
    expect(a[2]).toBeCloseTo(10, 10);
    expect(a[0]).toBe(0);
  });

  it('guards zero dt', () => {
    expect(estimateAcceleration([1, 2, 3], [0, 0, 0], 0)).toEqual([0, 0, 0]);
  });
});

describe('status classifier', () => {
  const base = { velZ: 0, z: 0.5, floorZ: 0, inContactWindow: false, prevStatus: 'free_fall' as const, settled: false };

  it('free fall until a high-G impact spike inside the contact window', () => {
    expect(classifyDropStatus({ ...base, accelG: 1 })).toBe('free_fall');
    expect(classifyDropStatus({ ...base, accelG: 5, inContactWindow: true })).toBe('impact');
  });

  it('rebounds when vertical velocity turns upward after impact', () => {
    expect(classifyDropStatus({ ...base, accelG: 0, prevStatus: 'impact', velZ: 0.6 })).toBe('rebound');
    expect(classifyDropStatus({ ...base, accelG: 0, prevStatus: 'impact', velZ: -0.1 })).toBe('impact');
  });

  it('settles at rest and rolls near the floor', () => {
    expect(classifyDropStatus({ ...base, accelG: 0, settled: true })).toBe('settled');
    expect(classifyDropStatus({ ...base, accelG: 0, z: 0.001, velZ: 0 })).toBe('rolling');
  });
});

describe('quaternion → Euler', () => {
  it('identity maps to zero angles', () => {
    const [r, p, y] = quatToEulerDeg([1, 0, 0, 0]);
    expect(r).toBeCloseTo(0, 6);
    expect(p).toBeCloseTo(0, 6);
    expect(y).toBeCloseTo(0, 6);
  });

  it('90° roll about x', () => {
    const [r] = quatToEulerDeg([Math.SQRT1_2, Math.SQRT1_2, 0, 0]);
    expect(r).toBeCloseTo(90, 5);
  });
});

describe('event detector', () => {
  const floorZ = 0;
  const yieldPa = 45e6;
  const mk = (over: Partial<TelemetryFrame>): TelemetryFrame => ({
    index: 0,
    t_s: 0,
    status: 'free_fall',
    position_m: [0, 0, 0.5],
    quaternion_wxyz: [1, 0, 0, 0],
    velocity_m_s: [0, 0, 0],
    angular_velocity_rad_s: [0, 0, 0],
    acceleration_g: 0,
    energy_j: { kinetic_trans: 0, kinetic_rot: 0, potential: 0.5, total: 0.5, dissipated: 0 },
    contact: { active: false },
    ...over,
  });

  it('emits IMPACT_DETECTED on the first impact frame', () => {
    const d = new TelemetryEventDetector(floorZ, yieldPa);
    const audit = { kinetic_trans_j: 0, kinetic_rot_j: 0, potential_j: 0.1, total_j: 0.1, dissipated_j: 0, drift_pct: 0 };
    const events = d.push(mk({ t_s: 0.5, status: 'impact', acceleration_g: 4, velocity_m_s: [0, 0, -3] }), audit, true, false);
    expect(events.map((e) => e.code)).toContain('IMPACT_DETECTED');
  });

  it('flags drift anomalies above 1.5%', () => {
    const d = new TelemetryEventDetector(floorZ, yieldPa);
    const audit = { kinetic_trans_j: 0, kinetic_rot_j: 0, potential_j: 0.1, total_j: 0.1, dissipated_j: 0, drift_pct: 2.0 };
    const events = d.push(mk({ t_s: 0.1 }), audit, false, false);
    expect(events.map((e) => e.code)).toContain('ENERGY_DRIFT_ANOMALY');
  });

  it('flags plastic yield when stress exceeds yield', () => {
    const d = new TelemetryEventDetector(floorZ, yieldPa);
    const audit = { kinetic_trans_j: 0, kinetic_rot_j: 0, potential_j: 0.1, total_j: 0.1, dissipated_j: 0, drift_pct: 0 };
    const events = d.push(mk({ t_s: 0.2, fea: { peak_stress_pa: 50e6, safety_factor: 0.9, damage: 0.05 } }), audit, false, false);
    expect(events.map((e) => e.code)).toContain('PLASTIC_YIELD_WARNING');
  });
});

describe('mesh envelope', () => {
  it('computes bounds of a unit box mesh', () => {
    const verts = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]];
    const b = meshBounds(verts);
    expect(b.min).toEqual([0, 0, 0]);
    expect(b.max).toEqual([1, 1, 1]);
  });

  it('computes area and volume of a tetrahedron', () => {
    const verts = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]];
    const tris = [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]];
    const { area_m2, volume_cm3 } = meshAreaAndVolume(verts, tris);
    expect(area_m2).toBeCloseTo(2.366, 2);
    expect(volume_cm3).toBeCloseTo(1 / 6 * 1e6, 3);
  });
});

describe('contact modulus', () => {
  it('is finite and smaller than the rigid-surface modulus', () => {
    // Hertzian 1/E* = (1−ν₁²)/E₁ + (1−ν₂²)/E₂. With a soft polymer (2.3 GPa)
    // against concrete (30 GPa) the reduced modulus exceeds the polymer E,
    // because the model's (1−ν²)/E term dominates.
    const e = effectiveContactModulus(2.3e9, 0.35, 30e9, 0.2);
    expect(e).toBeGreaterThan(0);
    expect(e).toBeLessThan(30e9);
    expect(e).toBeGreaterThan(2.3e9);
    // Equal materials: 1/E* = 2(1−ν²)/E.
    const same = effectiveContactModulus(2.3e9, 0.35, 2.3e9, 0.35);
    expect(same).toBeCloseTo(2.3e9 / (2 * (1 - 0.35 * 0.35)), 3);
  });
});

describe('CSV serialization', () => {
  const frame: TelemetryFrame = {
    index: 3,
    t_s: 0.5,
    status: 'impact',
    position_m: [0, 0, 0.2],
    quaternion_wxyz: [1, 0, 0, 0],
    velocity_m_s: [0, 0, -3],
    angular_velocity_rad_s: [0.1, 0.2, 0.3],
    acceleration_g: 5,
    energy_j: { kinetic_trans: 0.1, kinetic_rot: 0.01, potential: 0.2, total: 0.31, dissipated: 0.05 },
    contact: { active: true, point_m: [0, 0, 0], normal_force_n: 12, penetration_depth_m: 0.001 },
    fea: { peak_stress_pa: 40e6, safety_factor: 1.1, damage: 0.2 },
  };

  it('emits a header plus one row with all columns', () => {
    const csv = framesToCsv([frame]);
    const lines = csv.trim().split('\n');
    expect(lines).toHaveLength(2);
    expect(lines[0]).toContain('index,t_s,status');
    expect(lines[1]).toContain('3,0.500000,impact');
    expect(lines[1]).toContain('40');
  });

  it('serializes empty lists to a bare header', () => {
    expect(framesToCsv([]).trim()).toBe('index,t_s,status,z_mm,vx,vy,vz,speed,g_load,wx,wy,wz,qw,qx,qy,qz,ke_trans_mj,ke_rot_mj,pe_mj,total_mj,dissipated_mj,drift_pct,stress_mpa,safety_factor');
  });
});
