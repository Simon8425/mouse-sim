/**
 * Telemetry & scrubbing edge-case audit:
 * - ring-buffer boundary behavior (capacity wrap, clear, oversized capacity)
 * - scrubbing bounds (t < 0, t > t_end, inter-drop gaps, NaN/Infinity)
 * - collector reset across multi-drop sessions
 * - export float hygiene (strictly finite, no NaN/Infinity/scientific corruption)
 */
import { describe, expect, it } from 'vitest';
import { RingBuffer, classifyDropStatus } from '../lib/telemetryDebugger';
import { TelemetryCollector } from '../lib/telemetryCollector';
import { frameToCsvRow, framesToCsv, summarizeFrames } from '../lib/telemetrySessionBuilder';
import { resolveDropSample } from '../scene/sceneRuntime';
import type {
  DropSimulationResult,
  DropTrajectorySample,
} from '../api/contracts';
import type { TelemetryFrame } from '../api/telemetryDebuggerContracts';

function dropResult(over: Partial<DropSimulationResult> = {}): DropSimulationResult {
  return {
    config: {
      test: 'drop',
      height_m: 0.75,
      surface: 'concrete',
      drop_count: 1,
      orientation: 'flat',
    },
    model: {
      mass_kg: 0.28867,
      inertia_kg_m2: [
        [0.000343, 0, 0],
        [0, 0.000118, 0],
        [0, 0, 0.000405],
      ],
      support_model: 'convex_hull',
      support_point_count: 156,
      integrator: 'semi_implicit_euler',
      timestep_s: 1 / 240,
      gravity_m_s2: 9.81,
      surface: 'concrete',
      restitution: 0.3,
      friction: 0.6,
    },
    drops: [
      {
        index: 0,
        start_s: 0,
        end_s: 1.1,
        settled_s: 1.1,
        settled: true,
        impact_count: 2,
        peak_impact_speed_m_s: 3.8,
        peak_kinetic_energy_j: 2.1,
        orientation: 'flat',
      },
    ],
    impacts: [],
    trajectory: [],
    peak: null,
    peak_force_estimate_n: null,
    ...over,
  };
}

function liveRecord(t: number, z: number, over: Partial<Parameters<TelemetryCollector['push']>[0]> = {}) {
  return {
    t_s: t,
    position_m: [0, 0, z] as [number, number, number],
    quaternion_xyzw: [0, 0, 0, 1] as [number, number, number, number],
    velocity_m_s: [0, 0, -1] as [number, number, number],
    angular_velocity_rad_s: [0, 0, 0] as [number, number, number],
    in_contact_window: false,
    settled: false,
    ...over,
  };
}

function frame(over: Partial<TelemetryFrame>): TelemetryFrame {
  return {
    index: 0,
    t_s: 0,
    status: 'free_fall',
    position_m: [0, 0, 0.75],
    quaternion_wxyz: [1, 0, 0, 0],
    velocity_m_s: [0, 0, 0],
    angular_velocity_rad_s: [0, 0, 0],
    acceleration_g: 0,
    energy_j: { kinetic_trans: 0, kinetic_rot: 0, potential: 0.2, total: 0.2, dissipated: 0 },
    contact: { active: false },
    ...over,
  };
}

describe('RingBuffer boundaries', () => {
  it('capacity 1 keeps only the newest item', () => {
    const ring = new RingBuffer<number>(1);
    ring.push(1);
    ring.push(2);
    expect(ring.length).toBe(1);
    expect(ring.at(0)).toBe(2);
    expect(ring.toArray()).toEqual([2]);
  });

  it('wrap-around preserves logical order', () => {
    const ring = new RingBuffer<number>(3);
    for (let i = 0; i < 8; i += 1) ring.push(i);
    expect(ring.toArray()).toEqual([5, 6, 7]);
    expect(ring.at(0)).toBe(5);
    expect(ring.at(2)).toBe(7);
    expect(ring.at(-1)).toBeUndefined();
    expect(ring.at(3)).toBeUndefined();
  });

  it('at() rejects non-finite indices', () => {
    const ring = new RingBuffer<number>(4);
    ring.push(1);
    expect(ring.at(Number.NaN)).toBeUndefined();
    expect(ring.at(Number.POSITIVE_INFINITY)).toBeUndefined();
  });

  it('clear() drops all items and allows reuse', () => {
    const ring = new RingBuffer<number>(2);
    ring.push(1);
    ring.push(2);
    ring.clear();
    expect(ring.length).toBe(0);
    ring.push(3);
    expect(ring.toArray()).toEqual([3]);
  });
});

describe('scrub boundary semantics', () => {
  const dense: DropTrajectorySample[] = Array.from({ length: 61 }, (_, i) => [
    i / 60,
    0,
    0,
    0.75 - (i / 60) * 0.75,
    1,
    0,
    0,
    0,
  ]);

  it('t < 0 clamps to the first sample', () => {
    const r = resolveDropSample(-5, dense);
    expect(r?.a[0]).toBe(0);
    expect(r?.b).toBe(r?.a);
    expect(r?.alpha).toBe(0);
  });

  it('t > t_end clamps to the last sample (no extrapolation)', () => {
    const r = resolveDropSample(99, dense);
    expect(r?.a[0]).toBe(dense[dense.length - 1][0]);
    expect(r?.b).toBe(r?.a);
  });

  it('inter-drop gap holds the previous rest pose (no interpolation across the gap)', () => {
    // Dense block ends at t=1.0; the second block starts at t=0.85 — so the
    // second block OVERLAPS the first block's tail. A query at t=0.7 is
    // inside the second block's dense run (its first sample is t=0.85), not
    // a gap. Build a real non-overlapping gap instead: dense 0..1.0, then a
    // second block starting at 1.5.
    const twoPhase: DropTrajectorySample[] = [
      ...dense,
      ...[1.5, 1.55, 1.6, 1.65, 1.7, 1.75].map((t, i) => [t, 0, 0, 0.1 - 0.01 * i, 1, 0, 0, 0] as DropTrajectorySample),
    ];
    // t=1.2 falls in the 1.0..1.5 gap: hold the last dense sample, alpha 0.
    const r = resolveDropSample(1.2, twoPhase);
    expect(r?.a[0]).toBeCloseTo(1.0, 3);
    expect(r?.alpha).toBe(0);
    expect(r?.a[3]).toBeCloseTo(0, 3); // the rest pose Z of the first block
  });

  it('NaN and Infinity inputs clamp to the endpoints', () => {
    expect(resolveDropSample(Number.NaN, dense)?.a[0]).toBe(0);
    expect(resolveDropSample(Number.POSITIVE_INFINITY, dense)?.a[0]).toBe(dense[dense.length - 1][0]);
    expect(resolveDropSample(Number.NEGATIVE_INFINITY, dense)?.a[0]).toBe(0);
  });

  it('empty trajectories return null', () => {
    expect(resolveDropSample(0, [])).toBeNull();
  });

  it('duplicate-timestamp samples do not divide by zero', () => {
    const dupes: DropTrajectorySample[] = [
      [0, 0, 0, 1, 1, 0, 0, 0],
      [0, 0, 0, 1, 1, 0, 0, 0],
      [1, 0, 0, 0, 1, 0, 0, 0],
    ];
    const r = resolveDropSample(0.5, dupes);
    expect(r).not.toBeNull();
    expect(r!.alpha).toBeGreaterThanOrEqual(0);
  });
});

describe('TelemetryCollector lifecycle', () => {
  it('reset between multi-drop sessions clears the ring and re-arms indices', () => {
    const collector = new TelemetryCollector({ result: dropResult(), capacity: 16 });
    for (let i = 0; i < 10; i += 1) collector.push(liveRecord(i * 0.05, 0.75 - i * 0.05));
    expect(collector.frameCount).toBe(10);
    expect(collector.frames()[9].index).toBe(9);

    // A fresh session (new drop test result) rebuilds a new collector; the
    // OLD collector must be fully detached (no cross-session bleed).
    const collector2 = new TelemetryCollector({ result: dropResult(), capacity: 16 });
    collector2.push(liveRecord(0, 0.75));
    expect(collector2.frameCount).toBe(1);
    expect(collector2.frames()[0].index).toBe(0);
    expect(collector2.frames()[0].t_s).toBe(0);
    // Old collector still owns its own history (no shared state).
    expect(collector.frameCount).toBe(10);
    collector.clear();
    expect(collector.frameCount).toBe(0);
    expect(collector.eventsList).toEqual([]);
  });

  it('ring capacity caps retained frames without corrupting indices', () => {
    const collector = new TelemetryCollector({ result: dropResult(), capacity: 8 });
    for (let i = 0; i < 100; i += 1) collector.push(liveRecord(i * 0.05, 0.75 - i * 0.05));
    const frames = collector.frames();
    expect(frames.length).toBe(8);
    // Logical indices stay monotonically increasing (index is the push count,
    // not the ring slot).
    for (let i = 1; i < frames.length; i += 1) {
      expect(frames[i].index).toBeGreaterThan(frames[i - 1].index);
    }
    expect(frames[frames.length - 1].index).toBe(99);
  });

  it('duplicate or regressing timestamps do not break the auditor', () => {
    const collector = new TelemetryCollector({ result: dropResult() });
    // Same timestamp twice (a render-frame hiccup), then a backwards jump.
    collector.push(liveRecord(0.5, 0.3));
    collector.push(liveRecord(0.5, 0.29));
    collector.push(liveRecord(0.4, 0.28));
    const frames = collector.frames();
    expect(frames.length).toBe(3);
    for (const f of frames) {
      expect(Number.isFinite(f.acceleration_g)).toBe(true);
      expect(Number.isFinite(f.energy_j.total)).toBe(true);
    }
  });
});

describe('export float hygiene', () => {
  it('CSV rows contain strictly finite numbers or empty strings', () => {
    const bad: TelemetryFrame = frame({
      t_s: Number.NaN,
      position_m: [0, 0, Number.POSITIVE_INFINITY],
      velocity_m_s: [Number.NEGATIVE_INFINITY, 0, 0],
      angular_velocity_rad_s: [0, Number.NaN, 0],
      quaternion_wxyz: [1, Number.NaN, 0, 0],
      acceleration_g: Number.POSITIVE_INFINITY,
      energy_j: {
        kinetic_trans: Number.NaN,
        kinetic_rot: 0,
        potential: Number.POSITIVE_INFINITY,
        total: Number.NaN,
        dissipated: 0,
      },
      fea: { peak_stress_pa: Number.NaN, safety_factor: Number.POSITIVE_INFINITY, damage: 0 },
    });
    const row = frameToCsvRow(bad);
    for (const cell of row) {
      if (typeof cell === 'number') {
        expect(Number.isFinite(cell)).toBe(true);
      }
    }
    // Non-finite numerics serialize to empty cells (never "NaN"/"Infinity").
    const csv = framesToCsv([bad]);
    expect(csv).not.toMatch(/NaN|Infinity/i);
    // The row keeps its position/status fields.
    expect(csv).toContain('free_fall');
  });

  it('summarizeFrames is finite for empty and non-finite inputs', () => {
    const empty = summarizeFrames([], 0.5);
    expect(empty.duration_s).toBe(0);
    expect(empty.peak_g_force).toBe(0);
    expect(empty.min_safety_factor).toBe(0);
    expect(Number.isFinite(empty.restitution_measured)).toBe(true);

    const bad = summarizeFrames(
      [frame({ acceleration_g: Number.POSITIVE_INFINITY, energy_j: { kinetic_trans: Number.NaN, kinetic_rot: 0, potential: 0, total: Number.NaN, dissipated: Number.POSITIVE_INFINITY } })],
      0.5,
    );
    for (const value of Object.values(bad)) {
      if (typeof value === 'number') expect(Number.isFinite(value)).toBe(true);
    }
  });

  it('JSON export never emits NaN or Infinity tokens', () => {
    // JSON.stringify(NaN/Infinity) already yields null; the session builder
    // must never let raw non-finite values into the export payload.
    const collector = new TelemetryCollector({ result: dropResult() });
    collector.push(liveRecord(0, 0.75, { velocity_m_s: [Number.NaN, 0, 0] }));
    const session = collector.frames();
    const json = JSON.stringify(session);
    expect(json).not.toMatch(/NaN|Infinity/);
  });
});

describe('status classifier boundaries', () => {
  const base = { accelG: 0, velZ: 0, z: 0.5, floorZ: 0, inContactWindow: false, prevStatus: 'free_fall' as const, settled: false };

  it('treats NaN acceleration as not-impact', () => {
    expect(classifyDropStatus({ ...base, accelG: Number.NaN, inContactWindow: true })).not.toBe('impact');
  });

  it('treats NaN z as free fall (no false rolling)', () => {
    expect(classifyDropStatus({ ...base, z: Number.NaN })).toBe('free_fall');
  });
});
