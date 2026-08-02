import { describe, it, expect } from 'vitest';
import {
  worldBounds,
  boundsCenter,
} from '../lib/geometryBounds';
import type { BoxGeometryJson, RigidTransformJson } from '../api/contracts';

describe('geometryBounds library', () => {
  it('computes box parity bounds correctly', () => {
    const transform: RigidTransformJson = {
      rotation: [
        [0, -1, 0],
        [1, 0, 0],
        [0, 0, 1],
      ],
      translation: [1, 2, 3],
      units: 'm',
    };

    const box: BoxGeometryJson = {
      type: 'box',
      size: [2, 4, 6],
      units: 'm',
      transform,
    };

    const bounds = worldBounds(box);
    expect(bounds.min[0]).toBeCloseTo(-1);
    expect(bounds.min[1]).toBeCloseTo(1);
    expect(bounds.min[2]).toBeCloseTo(0);

    expect(bounds.max[0]).toBeCloseTo(3);
    expect(bounds.max[1]).toBeCloseTo(3);
    expect(bounds.max[2]).toBeCloseTo(6);

    const center = boundsCenter(bounds);
    expect(center[0]).toBeCloseTo(1);
    expect(center[1]).toBeCloseTo(2);
    expect(center[2]).toBeCloseTo(3);
  });
});
