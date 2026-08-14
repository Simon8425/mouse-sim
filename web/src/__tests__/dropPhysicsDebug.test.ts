import { describe, expect, it } from 'vitest';
import { deriveLiveFrame } from '../components/DropPhysicsDebug';
import type { DropTrajectorySample } from '../api/contracts';

function sample(t: number, z: number, q = [1, 0, 0, 0]): DropTrajectorySample {
  return [t, 0, 0, z, q[0], q[1], q[2], q[3]];
}

describe('deriveLiveFrame', () => {
  it('reports real motion within a dense 60 Hz region', () => {
    const samples: DropTrajectorySample[] = [sample(0, 0), sample(1 / 60, 0.5)];
    const frame = deriveLiveFrame(samples, 0.5 / 60);
    expect(frame).not.toBeNull();
    expect(frame!.vSpeed).toBeCloseTo(30, 5); // 0.5 m in 1/60 s
    expect(frame!.speed).toBeGreaterThan(0);
    expect(frame!.rotRate).toBe(0);
  });

  it('holds the pose with zero motion across an inter-drop gap', () => {
    // Drop 0 ends at 0.5 s; the next drop's first sample is at 0.85 s.
    const samples: DropTrajectorySample[] = [
      sample(0.4833, 0.01, [0.8, 0, 0, 0.6]),
      sample(0.5, 0.01, [0.8, 0, 0, 0.6]),
      sample(0.85, 0.75, [1, 0, 0, 0]),
    ];
    const frame = deriveLiveFrame(samples, 0.6); // inside the gap
    expect(frame).not.toBeNull();
    expect(frame!.speed).toBe(0);
    expect(frame!.vSpeed).toBe(0);
    expect(frame!.hSpeed).toBe(0);
    expect(frame!.rotRate).toBe(0);
    // The pose is the previous (rest) sample, not a blend toward the next drop.
    expect(frame!.pos[2]).toBeCloseTo(0.01, 5);
    expect(frame!.settled).toBe(true);
  });

  it('does not fabricate a rotation spike across the teleport', () => {
    const samples: DropTrajectorySample[] = [
      sample(0.5, 0.01, [0.8, 0, 0, 0.6]),
      sample(0.85, 0.75, [1, 0, 0, 0]),
    ];
    const frame = deriveLiveFrame(samples, 0.51);
    expect(frame!.rotRate).toBe(0);
    expect(frame!.dq).toBe(0);
  });
});
