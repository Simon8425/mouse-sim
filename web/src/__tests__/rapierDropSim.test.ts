import { describe, expect, it, vi, beforeEach } from 'vitest';

// Mock Rapier BEFORE importing the module under test (vitest hoists vi.mock).
// The mock mirrors the exact API surface rapierDropSim.ts uses.
const worldStep = vi.fn();
const worldFree = vi.fn();
const createCollider = vi.fn();
const createRigidBody = vi.fn();

// A chainable stub: every setter returns the SAME proxy so the fluent
// RigidBodyDesc / ColliderDesc chains work, and records the last call.
function chainable(record: Record<string, unknown>) {
  const target: Record<string, unknown> = {};
  const handler: ProxyHandler<Record<string, unknown>> = {
    get(_t, prop) {
      if (prop === 'then') return undefined;
      if (typeof prop === 'string' && prop.startsWith('set')) {
        // Record setter calls with their first arg (the meaningful value).
        return (...args: unknown[]) => {
          record[prop] = args.length === 1 ? args[0] : args;
          return proxy;
        };
      }
      return proxy;
    },
  };
  const proxy = new Proxy(target, handler);
  return proxy;
}

const bodyRecord: Record<string, unknown> = {};
const colliderRecord: Record<string, unknown> = {};
const body = {
  translation: () => ({ x: bodyRecord.translationX ?? 0, y: bodyRecord.translationY ?? 0, z: bodyRecord.translationZ ?? 0.75 }),
  rotation: () => ({ x: 0, y: 0, z: 0, w: 1 }),
  linvel: () => ({ x: 0, y: 0, z: 0 }),
  angvel: () => ({ x: 0, y: 0, z: 0 }),
  setTranslation: (v: unknown, wake: unknown) => { bodyRecord.translation = v; bodyRecord.translationWake = wake; },
  setRotation: (v: unknown, wake: unknown) => { bodyRecord.rotation = v; bodyRecord.rotationWake = wake; },
  setLinvel: (v: unknown, wake: unknown) => { bodyRecord.linvel = v; bodyRecord.linvelWake = wake; },
  setAngvel: (v: unknown, wake: unknown) => { bodyRecord.angvel = v; bodyRecord.angvelWake = wake; },
  setLinearDamping: (d: unknown) => { bodyRecord.linDamping = d; },
  setAngularDamping: (d: unknown) => { bodyRecord.angDamping = d; },
  setAdditionalMassProperties: (...args: unknown[]) => { bodyRecord.massProps = args; },
  wakeUp: () => { bodyRecord.wake = true; },
  sleep: () => { bodyRecord.sleeping = true; },
  isSleeping: () => Boolean(bodyRecord.sleeping),
};

vi.mock('@dimforge/rapier3d-compat', () => {
  createRigidBody.mockReturnValue(body);
  return {
    init: vi.fn().mockResolvedValue(undefined),
    World: vi.fn().mockImplementation(() => ({
      step: worldStep,
      free: worldFree,
      timestep: 1 / 240,
      createRigidBody,
      createCollider,
    })),
    RigidBodyDesc: {
      dynamic: () => chainable(bodyRecord),
      fixed: () => chainable(bodyRecord),
    },
    ColliderDesc: {
      cuboid: () => chainable(colliderRecord),
      convexHull: () => chainable(colliderRecord),
    },
    CoefficientCombineRule: {
      Average: 0,
      Min: 1,
      Multiply: 2,
      Max: 3,
    },
  };
});

import { buildRapierDropSim } from '../scene/rapierDropSim';
import type { DropSimulationResult } from '../api/contracts';

function model(): DropSimulationResult['model'] {
  return {
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
    com_offset_m: [0.000143, -0.003114, 0.017019],
    orientation_quaternion_wxyz: [1, 0, 0, 0],
    initial_velocity_m_s: [0, 0, 0],
    initial_angular_velocity_rad_s: [0, 0, 0],
    starting_pose_m: [0, 0, 0.75],
  };
}

function drop(index: number): DropSimulationResult['drops'][number] {
  return {
    index,
    start_s: index * 1.7,
    end_s: index * 1.7 + 1.1,
    settled_s: 1.1,
    settled: true,
    impact_count: 2,
    peak_impact_speed_m_s: 3.8,
    peak_kinetic_energy_j: 2.1,
    orientation: 'flat',
    orientation_quaternion_wxyz: [1, 0, 0, 0],
    initial_angular_velocity_rad_s: [0, 0, 0],
    starting_pose_m: [0, 0, 0.75],
  };
}

const ENTRIES: { id: string; geometry: { type: 'mesh'; vertices: number[][]; transform: unknown; triangles: number[][] } }[] = [
  {
    id: 'part-0',
    geometry: {
      type: 'mesh',
      vertices: [
        [-0.05, -0.05, -0.05],
        [0.05, -0.05, -0.05],
        [0.05, 0.05, -0.05],
        [-0.05, 0.05, -0.05],
        [-0.05, -0.05, 0.05],
        [0.05, -0.05, 0.05],
        [0.05, 0.05, 0.05],
        [-0.05, 0.05, 0.05],
      ],
      transform: null,
      triangles: [[0, 1, 2]],
    },
  },
];

describe('buildRapierDropSim', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    for (const key of Object.keys(bodyRecord)) delete bodyRecord[key];
    for (const key of Object.keys(colliderRecord)) delete colliderRecord[key];
  });

  it('builds a world, ground collider, and dynamic body from backend model fields', async () => {
    const sim = await buildRapierDropSim(model(), ENTRIES as never, [drop(0)]);
    expect(sim).not.toBeNull();
    expect(createRigidBody).toHaveBeenCalled();
    expect(createCollider).toHaveBeenCalled();
    // The dynamic body desc chain was built with the backend start pose (3 numbers).
    expect(bodyRecord.setTranslation).toEqual([0, 0, 0.75]);
    expect(bodyRecord.setLinvel).toEqual([0, 0, 0]);
    expect(bodyRecord.setAngvel).toEqual({ x: 0, y: 0, z: 0 });
    // Collider mass properties were applied (mass + CoM + diagonal inertia).
    const massProps = colliderRecord.setMassProperties as unknown[];
    expect(massProps).toBeDefined();
    expect(massProps[0]).toBeCloseTo(0.28867, 5);
    sim?.dispose();
    expect(worldFree).toHaveBeenCalled();
  });

  it('resets the body to a drop initial condition', async () => {
    const sim = await buildRapierDropSim(model(), ENTRIES as never, [drop(0)]);
    const d = drop(2);
    sim?.reset(d, model());
    // reset() calls the BODY's setters (not the desc chain), which record
    // into bodyRecord.translation / .angvel via the body object.
    expect(bodyRecord.translation).toEqual({ x: 0, y: 0, z: 0.75 });
    expect(bodyRecord.angvel).toEqual({ x: 0, y: 0, z: 0 });
    expect(bodyRecord.wake).toBe(true);
    sim?.dispose();
  });

  it('steps the world and reads the body state back', async () => {
    const sim = await buildRapierDropSim(model(), ENTRIES as never, [drop(0)]);
    const state = sim?.step(1 / 60);
    expect(worldStep).toHaveBeenCalled();
    expect(state).not.toBeNull();
    expect(state?.position[2]).toBeCloseTo(0.75, 5);
    sim?.dispose();
  });

  it('reports resting when body is sleeping', async () => {
    bodyRecord.sleeping = true;
    body.isSleeping = () => true;
    const sim = await buildRapierDropSim(model(), ENTRIES as never, [drop(0)]);
    expect(sim?.isResting()).toBe(true);
    sim?.dispose();
  });
});
