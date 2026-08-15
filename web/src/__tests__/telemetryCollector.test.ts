/**
 * Telemetry session builder + collector tests.
 */
import { describe, expect, it } from 'vitest';
import type { DropSimulationResult, FeaResult } from '../api/contracts';
import { TelemetryCollector, type LiveTelemetryRecord } from '../lib/telemetryCollector';
import {
  buildFloorSpec,
  buildModelSpec,
  buildTelemetrySession,
  framesToCsv,
  summarizeFrames,
} from '../lib/telemetrySessionBuilder';

function makeResult(over: Partial<DropSimulationResult> = {}): DropSimulationResult {
  return {
    test: 'Unit test drop',
    config: { test: 'Unit test drop', height_m: 0.75, surface: 'concrete', drop_count: 1, orientation: 'flat' },
    model: {
      mass_kg: 0.1,
      inertia_kg_m2: [[1e-5, 0, 0], [0, 1.2e-5, 0], [0, 0, 1.1e-5]],
      support_model: 'convex_hull',
      support_point_count: 8,
      integrator: 'semi_implicit_euler',
      timestep_s: 1 / 240,
      gravity_m_s2: 9.81,
      surface: 'concrete',
      restitution: 0.25,
      friction: 0.65,
      com_offset_m: [0.001, -0.002, 0.003],
      gravity_vector_body: [0, 0, -9.81],
      initial_angular_velocity_rad_s: [0, 0, 0],
      initial_velocity_m_s: [0, 0, 0],
      starting_pose_m: [0, 0, 0.75],
    },
    drops: [{
      index: 0,
      start_s: 0,
      end_s: 1.0,
      settled_s: 0.9,
      settled: true,
      impact_count: 1,
      peak_impact_speed_m_s: 3.8,
      peak_kinetic_energy_j: 0.72,
      orientation: 'flat',
      orientation_quaternion_wxyz: [1, 0, 0, 0],
      gravity_vector_body: [0, 0, -9.81],
      initial_angular_velocity_rad_s: [0, 0, 0],
      initial_velocity_m_s: [0, 0, 0],
      starting_pose_m: [0, 0, 0.75],
      energy: { release_j: 0.73575, first_impact_j: 0.7, settled_j: 0.05, lost_contact_j: 0.03, lost_drag_j: 0, drift_pct: 0.01 },
    }],
    impacts: [{ drop: 0, t_s: 0.39, impact_speed_m_s: 3.8, kinetic_energy_j: 0.72 }],
    peak: { drop: 0, t_s: 0.39, impact_speed_m_s: 3.8, kinetic_energy_j: 0.72 },
    trajectory: [[0, 0, 0, 0.75, 1, 0, 0, 0]],
    checks: [],
    ...over,
  } as DropSimulationResult;
}

const fea: FeaResult = {
  computed: true,
  peak: { object_id: 'shell', vertex_index: 12, location_model_m: [0, 0, 0.01], damage: 0.2, stress_pa: 40e6, stress_mpa: 40 },
  yield_stress_pa: 45e6,
  safety_factor: 1.12,
  impact_window_s: 0.3,
  dent_threshold: 0.7,
  tear_threshold: 0.92,
  objects: [],
  procedural: [],
  assumptions: [],
  flags: [],
};

function rec(t: number, z: number, vz: number, over: Partial<LiveTelemetryRecord> = {}): LiveTelemetryRecord {
  return {
    t_s: t,
    position_m: [0, 0, z],
    quaternion_xyzw: [0, 0, 0, 1],
    velocity_m_s: [0, 0, vz],
    angular_velocity_rad_s: [0, 0, 0],
    in_contact_window: false,
    settled: false,
    ...over,
  };
}

describe('buildModelSpec', () => {
  it('maps model fields and material reference', () => {
    const spec = buildModelSpec(makeResult());
    expect(spec.mass_kg).toBe(0.1);
    expect(spec.com_offset_m).toEqual([0.001, -0.002, 0.003]);
    expect(spec.material.name).toBe('ABS');
    expect(spec.material.yield_strength_pa).toBe(45e6);
    expect(spec.dimensions_m.x).toBeGreaterThan(0);
  });
});

describe('buildFloorSpec', () => {
  it('computes effective modulus and friction split', () => {
    const floor = buildFloorSpec(makeResult());
    expect(floor.surface_id).toBe('concrete');
    expect(floor.restitution).toBe(0.25);
    expect(floor.friction_static).toBe(0.65);
    expect(floor.friction_dynamic).toBeCloseTo(0.52, 2);
    expect(floor.effective_modulus_pa).toBeGreaterThan(0);
  });
});

describe('TelemetryCollector', () => {
  it('appends frames with energy + status + FEA snapshot', () => {
    const c = new TelemetryCollector({ result: makeResult(), fea, capacity: 16 });
    c.push(rec(0, 0.75, 0));
    c.push(rec(0.05, 0.74, -0.5));
    const frames = c.frames();
    expect(frames).toHaveLength(2);
    expect(frames[0].t_s).toBe(0);
    expect(frames[0].fea?.peak_stress_pa).toBe(40e6);
    expect(frames[1].energy_j.total).toBeGreaterThan(0);
    expect(frames[1].position_m[2]).toBe(0.74);
  });

  it('binds the ring capacity', () => {
    const c = new TelemetryCollector({ result: makeResult(), capacity: 4 });
    for (let i = 0; i < 10; i += 1) c.push(rec(i * 0.01, 0.5, 0));
    expect(c.frameCount).toBe(4);
    expect(c.frameAt(0)?.t_s).toBeCloseTo(0.06, 6);
  });

  it('detects impact events from the stream', () => {
    const c = new TelemetryCollector({ result: makeResult(), fea, capacity: 64 });
    // Free fall at 240 Hz for 0.5 s, then a hard impact: the body decelerates
    // from -4.9 m/s to rest within ~5 substeps inside the contact window,
    // producing a many-G spike on the first impact frame.
    const dt = 1 / 240;
    const impactStart = 0.5;
    for (let i = 0; i < 140; i += 1) {
      const t = i * dt;
      const inContact = t >= impactStart && t < impactStart + 0.05;
      const vz = !inContact && t < impactStart
        ? -9.81 * t
        : -(4.9 * Math.max(0, 1 - (t - impactStart) * 100));
      c.push(rec(t, 0.75 - 9.81 * t * t * 0.5, vz, { in_contact_window: inContact }));
    }
    const codes = c.eventsList.map((e) => e.code);
    expect(codes).toContain('IMPACT_DETECTED');
  });

  it('clears state', () => {
    const c = new TelemetryCollector({ result: makeResult(), capacity: 8 });
    c.push(rec(0, 0.75, 0));
    c.clear();
    expect(c.frameCount).toBe(0);
    expect(c.frames()).toEqual([]);
  });
});

describe('summarizeFrames + session build', () => {
  it('computes peak G, drift, restitution', () => {
    const c = new TelemetryCollector({ result: makeResult(), fea, capacity: 64 });
    for (let i = 0; i < 20; i += 1) {
      c.push(rec(i * 0.02, 0.75 - i * 0.02, -0.4 - i * 0.02));
    }
    const frames = c.frames();
    const summary = summarizeFrames(frames, 0.73575);
    expect(summary.total_frames).toBe(20);
    expect(summary.peak_g_force).toBeGreaterThan(0);
    expect(summary.peak_stress_mpa).toBe(40);
    expect(summary.min_safety_factor).toBeCloseTo(1.12, 2);
  });

  it('builds a full session with metadata', () => {
    const c = new TelemetryCollector({ result: makeResult(), fea, capacity: 32 });
    c.push(rec(0, 0.75, 0));
    c.push(rec(0.01, 0.74, -0.1));
    const session = buildTelemetrySession(makeResult(), c.frames());
    expect(session.session_id).toContain('telemetry-');
    expect(session.drop_config.height_m).toBe(0.75);
    expect(session.model.mass_kg).toBe(0.1);
    expect(session.frames).toHaveLength(2);
    expect(JSON.parse(JSON.stringify(session)).frames.length).toBe(2);
  });
});

describe('CSV export', () => {
  it('round-trips frames deterministically', () => {
    const c = new TelemetryCollector({ result: makeResult(), capacity: 8 });
    c.push(rec(0, 0.75, 0));
    c.push(rec(0.01, 0.74, -0.1));
    const csv = framesToCsv(c.frames());
    const lines = csv.trim().split('\n');
    expect(lines).toHaveLength(3);
    expect(lines[1]).toContain('0,0.000000,free_fall');
    expect(lines[2]).toContain('0.010000');
  });
});
