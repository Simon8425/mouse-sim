import { describe, expect, it } from 'vitest';
import {
  exactModelLowestZ,
  floorCorrectionForModel,
  impactWindowProgress,
  resolveDropSample,
  rotatedBoundsMinZ,
} from '../scene/sceneRuntime';
import { resolveActiveDrop } from '../scene/SceneViewport';
import type { DropSimulationDrop, DropTrajectorySample } from '../api/contracts';
import * as THREE from 'three';

function sample(t: number, z: number): DropTrajectorySample {
  return [t, 0, 0, z, 1, 0, 0, 0];
}

// Two drops: 0..0.5s samples at 60Hz, gap until 0.85s, then 0.85..1.35s.
const DENSE_END = 0.5;
const GAP_START = 0.85;
const TRAJECTORY: DropTrajectorySample[] = [
  ...Array.from({ length: 31 }, (_, i) => sample(i / 60, i / 60)),
  ...Array.from({ length: 31 }, (_, i) => sample(GAP_START + i / 60, (i + 1) / 60)),
];

const DROPS: DropSimulationDrop[] = [
  {
    index: 0,
    start_s: 0,
    end_s: 0.5,
    settled_s: 0.5,
    impact_count: 1,
    peak_impact_speed_m_s: 3,
    peak_kinetic_energy_j: 0.5,
    orientation: 'flat',
  },
  {
    index: 1,
    start_s: 0.85,
    end_s: 1.35,
    settled_s: 0.5,
    impact_count: 1,
    peak_impact_speed_m_s: 3,
    peak_kinetic_energy_j: 0.5,
    orientation: 'flat',
  },
];

describe('resolveDropSample', () => {
  it('interpolates within a dense region', () => {
    const resolved = resolveDropSample(0.253, TRAJECTORY);
    expect(resolved).not.toBeNull();
    expect(resolved!.a[0]).toBeLessThanOrEqual(0.253);
    expect(resolved!.b[0]).toBeGreaterThan(0.253);
    expect(resolved!.alpha).toBeGreaterThan(0);
    expect(resolved!.alpha).toBeLessThan(1);
  });

  it('holds the rest pose during an inter-drop gap', () => {
    const resolved = resolveDropSample(0.7, TRAJECTORY);
    expect(resolved).not.toBeNull();
    // Gap between sample t=0.5 (last of drop 0) and t=0.85 (first of drop 1).
    expect(resolved!.a[0]).toBe(0.5);
    expect(resolved!.b).toBe(resolved!.a);
    expect(resolved!.alpha).toBe(0);
    expect(resolved!.a[3]).toBeCloseTo(DENSE_END, 5);
  });

  it('clamps before the first and after the last sample', () => {
    const before = resolveDropSample(-1, TRAJECTORY);
    expect(before!.a[0]).toBe(0);
    const after = resolveDropSample(10, TRAJECTORY);
    expect(after!.a[0]).toBe(TRAJECTORY[TRAJECTORY.length - 1][0]);
    expect(after!.b).toBe(after!.a);
  });

  it('returns null for an empty trajectory', () => {
    expect(resolveDropSample(0, [])).toBeNull();
  });
});

describe('floorCorrectionForModel', () => {
  it('raises a rendered model that has penetrated the display floor', () => {
    expect(floorCorrectionForModel(-0.12, -0.01)).toBeCloseTo(0.11, 8);
  });

  it('does not move a model already above the floor or invalid bounds', () => {
    expect(floorCorrectionForModel(0.02, -0.01)).toBe(0);
    expect(floorCorrectionForModel(Number.NaN, -0.01)).toBe(0);
  });
});

describe('rotatedBoundsMinZ', () => {
  const MIN = [-0.05, -0.05, -0.03] as [number, number, number];
  const MAX = [0.05, 0.05, 0.05] as [number, number, number];

  it('returns the static minimum z for the identity orientation', () => {
    expect(rotatedBoundsMinZ(MIN, MAX, new THREE.Quaternion())).toBeCloseTo(-0.03, 10);
  });

  it('tracks the lowest corner under rotation (pi about X flips z)', () => {
    const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI);
    expect(rotatedBoundsMinZ(MIN, MAX, q)).toBeCloseTo(-0.05, 10);
  });

  it('falls back to the static minimum for non-finite quaternions', () => {
    const q = new THREE.Quaternion(Number.NaN, 0, 0, 1);
    expect(rotatedBoundsMinZ(MIN, MAX, q)).toBeCloseTo(-0.03, 10);
  });
});

describe('exactModelLowestZ', () => {
  const BOX_VERTICES: [number, number, number][] = [
    [-0.05, -0.05, -0.03], [0.05, -0.05, -0.03], [-0.05, 0.05, -0.03], [0.05, 0.05, -0.03],
    [-0.05, -0.05, 0.05], [0.05, -0.05, 0.05], [-0.05, 0.05, 0.05], [0.05, 0.05, 0.05],
  ];

  it('is exact for the identity orientation', () => {
    expect(exactModelLowestZ(BOX_VERTICES, new THREE.Quaternion())).toBeCloseTo(-0.03, 10);
  });

  it('rests flush where the conservative AABB bound over-lifts', () => {
    // An octahedron (like a rounded mouse shell) has EMPTY AABB corners: the
    // rotated AABB corner hull dips below every real vertex, so the
    // conservative clamp over-lifts. The exact vertex bound stays flush.
    const r = 0.05;
    const OCTA: [number, number, number][] = [
      [r, 0, 0], [-r, 0, 0], [0, r, 0], [0, -r, 0], [0, 0, r], [0, 0, -r],
    ];
    const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 4);
    const exact = exactModelLowestZ(OCTA, q);
    const conservative = rotatedBoundsMinZ(
      [-r, -r, -r],
      [r, r, r],
      q,
    );
    // The exact lowest point is ABOVE the conservative bound (never below),
    // so clamping with it leaves the model flush instead of floating.
    expect(exact).toBeGreaterThan(conservative);
    expect(exact).toBeCloseTo(-r / Math.SQRT2, 8);
    expect(conservative).toBeCloseTo(-r * Math.SQRT2, 8);
  });

  it('returns Infinity for empty vertices or non-finite quaternions', () => {
    expect(exactModelLowestZ([], new THREE.Quaternion())).toBe(Infinity);
    expect(exactModelLowestZ(BOX_VERTICES, new THREE.Quaternion(Number.NaN, 0, 0, 1))).toBe(Infinity);
  });
});

describe('impactWindowProgress', () => {
  it('is 0 before the impact and 1 after the window elapses', () => {
    expect(impactWindowProgress(0.2, 0.3, 0.3)).toBe(0);
    expect(impactWindowProgress(0.3, 0.3, 0.3)).toBe(0);
    expect(impactWindowProgress(0.6, 0.3, 0.3)).toBe(1);
    expect(impactWindowProgress(5, 0.3, 0.3)).toBe(1);
  });

  it('interpolates linearly through the window', () => {
    expect(impactWindowProgress(0.45, 0.3, 0.3)).toBeCloseTo(0.5, 8);
    expect(impactWindowProgress(0.39, 0.3, 0.3)).toBeCloseTo(0.3, 8);
  });

  it('returns 0 for missing or non-positive window durations', () => {
    expect(impactWindowProgress(0.5, 0.3, 0)).toBe(0);
    expect(impactWindowProgress(0.5, 0.3, -1)).toBe(0);
    expect(impactWindowProgress(0.5, 0.3, Number.NaN)).toBe(0);
    expect(impactWindowProgress(Number.NaN, 0.3, 0.3)).toBe(0);
    expect(impactWindowProgress(0.5, Number.NaN, 0.3)).toBe(0);
  });
});

describe('resolveActiveDrop', () => {
  it('is monotonic across gaps and boundaries', () => {
    expect(resolveActiveDrop(DROPS, 0.2)?.index).toBe(0);
    // Mid-gap: still drop 0 (not a premature jump to the last drop).
    expect(resolveActiveDrop(DROPS, 0.7)?.index).toBe(0);
    expect(resolveActiveDrop(DROPS, 0.85)?.index).toBe(1);
    expect(resolveActiveDrop(DROPS, 1.0)?.index).toBe(1);
    expect(resolveActiveDrop(DROPS, 99)?.index).toBe(1);
  });

  it('handles empty and before-start cases', () => {
    expect(resolveActiveDrop([], 0.5)).toBeNull();
    expect(resolveActiveDrop(DROPS, -1)?.index).toBe(0);
  });

  it('correctly tracks impact timing separately for multi-drop simulations', () => {
    // Drop 0 impact at t=0.38s (window 0.38..0.68s)
    const drop0Impact = 0.38;
    expect(impactWindowProgress(0.1, drop0Impact, 0.3)).toBe(0); // pre-impact in air
    expect(impactWindowProgress(0.38, drop0Impact, 0.3)).toBe(0); // moment of contact
    expect(impactWindowProgress(0.53, drop0Impact, 0.3)).toBeCloseTo(0.5, 8); // mid impact
    expect(impactWindowProgress(0.68, drop0Impact, 0.3)).toBe(1); // settled

    // Drop 1 impact at t=1.23s (window 1.23..1.53s)
    const drop1Impact = 1.23;
    expect(impactWindowProgress(0.9, drop1Impact, 0.3)).toBe(0); // drop 1 in air before impact
    expect(impactWindowProgress(1.23, drop1Impact, 0.3)).toBe(0); // moment of drop 1 contact
    expect(impactWindowProgress(1.38, drop1Impact, 0.3)).toBeCloseTo(0.5, 8); // mid drop 1 impact
    expect(impactWindowProgress(1.6, drop1Impact, 0.3)).toBe(1); // settled
  });
});
