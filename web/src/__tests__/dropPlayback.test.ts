import { describe, expect, it } from 'vitest';
import { floorCorrectionForModel, resolveDropSample, rotatedBoundsMinZ } from '../scene/sceneRuntime';
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
});
