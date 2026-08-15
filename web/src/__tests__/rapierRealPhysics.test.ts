import { describe, expect, it } from 'vitest';
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

const ENTRIES = [
  {
    id: 'part-0',
    geometry: {
      type: 'mesh' as const,
      vertices: [
        [-0.03, -0.06, -0.01],
        [0.03, -0.06, -0.01],
        [0.03, 0.06, -0.01],
        [-0.03, 0.06, -0.01],
        [-0.03, -0.06, 0.02],
        [0.03, -0.06, 0.02],
        [0.03, 0.06, 0.02],
        [-0.03, 0.06, 0.02],
      ],
      transform: null,
      triangles: [[0, 1, 2]],
    },
  },
];

describe('Real Rapier Physics Simulation', () => {
  it('falls under gravity and bounces on the ground collider', async () => {
    const sim = await buildRapierDropSim(model(), ENTRIES as never, [
      {
        index: 0,
        start_s: 0,
        end_s: 1.5,
        settled_s: 1.1,
        settled: true,
        impact_count: 2,
        peak_impact_speed_m_s: 3.8,
        peak_kinetic_energy_j: 2.1,
        orientation: 'flat',
        orientation_quaternion_wxyz: [1, 0, 0, 0],
        initial_angular_velocity_rad_s: [0, 0, 0],
        starting_pose_m: [0, 0, 0.75],
      },
    ]);

    expect(sim).not.toBeNull();
    if (!sim) return;

    const initial = sim.getState();
    expect(initial.position[2]).toBeCloseTo(0.75, 2);

    // Step 0.2 seconds (still in free fall)
    let state = initial;
    for (let i = 0; i < 12; i++) {
      state = sim.step(1 / 60);
    }
    console.log('State at t=0.2s: z =', state.position[2], 'vz =', state.linvel[2]);
    // After 0.2s: z should be 0.75 - 0.5 * 9.81 * 0.2^2 = 0.75 - 0.1962 = 0.5538
    expect(state.position[2]).toBeLessThan(0.70);
    expect(state.linvel[2]).toBeLessThan(-1.5);

    // Step to t=0.5s (should have hit floor and bounced)
    for (let i = 0; i < 18; i++) {
      state = sim.step(1 / 60);
    }
    console.log('State at t=0.5s: z =', state.position[2], 'vz =', state.linvel[2]);

    // Step to t=1.5s (should have settled on the floor)
    for (let i = 0; i < 60; i++) {
      state = sim.step(1 / 60);
    }
    console.log('State at t=1.5s: z =', state.position[2], 'vz =', state.linvel[2]);
    expect(state.position[2]).toBeGreaterThanOrEqual(0);
    expect(state.position[2]).toBeLessThan(0.05);
    expect(sim.isResting()).toBe(true);

    sim.dispose();
  });

  it('settles cleanly without ground jitter or creep on tilted drop (Drop 3)', async () => {
    const drop3 = {
      index: 3,
      start_s: 4.358,
      end_s: 5.433,
      settled_s: 1.075,
      settled: true,
      impact_count: 2,
      peak_impact_speed_m_s: 3.8,
      peak_kinetic_energy_j: 2.1,
      orientation: 'flat' as const,
      orientation_quaternion_wxyz: [0.999065, -0.027434, 0.033429, 0] as [number, number, number, number],
      initial_angular_velocity_rad_s: [-0.306662, 0.373669, 0] as [number, number, number],
      starting_pose_m: [0.015, -0.015, 0.77] as [number, number, number],
    };

    const sim = await buildRapierDropSim(model(), ENTRIES as never, [drop3]);
    expect(sim).not.toBeNull();
    if (!sim) return;

    sim.reset(drop3, model());

    // Step through 1.2 seconds of simulation (72 frames at 60 FPS)
    let state = sim.getState();
    for (let i = 0; i < 72; i++) {
      state = sim.step(1 / 60);
    }

    // Body must be settled at rest on the floor with zero linear and angular drift
    expect(sim.isResting()).toBe(true);
    expect(state.linvel[0]).toBe(0);
    expect(state.linvel[1]).toBe(0);
    expect(state.linvel[2]).toBe(0);
    expect(state.angvel[0]).toBe(0);
    expect(state.angvel[1]).toBe(0);
    expect(state.angvel[2]).toBe(0);

    // Record position, step 30 more frames while resting — position must be perfectly locked (no creep)
    const settledPos = [...state.position];
    for (let i = 0; i < 30; i++) {
      state = sim.step(1 / 60);
    }
    expect(state.position[0]).toBe(settledPos[0]);
    expect(state.position[1]).toBe(settledPos[1]);
    expect(state.position[2]).toBe(settledPos[2]);

    sim.dispose();
  });

  it('topples under gravity from an unstable tilted corner drop and settles into a stable pose', async () => {
    // 45-degree pitch/roll corner drop: unstable initial contact
    const cornerDrop = {
      index: 0,
      start_s: 0,
      end_s: 2.0,
      settled_s: 1.2,
      settled: true,
      impact_count: 2,
      peak_impact_speed_m_s: 3.8,
      peak_kinetic_energy_j: 2.1,
      orientation: 'corner' as const,
      orientation_quaternion_wxyz: [0.7071, 0.7071, 0, 0] as [number, number, number, number],
      initial_angular_velocity_rad_s: [0, 0, 0] as [number, number, number],
      starting_pose_m: [0, 0, 0.75] as [number, number, number],
    };

    const sim = await buildRapierDropSim(model(), ENTRIES as never, [cornerDrop]);
    expect(sim).not.toBeNull();
    if (!sim) return;

    // Step 1.8 seconds (108 frames)
    let state = sim.getState();
    for (let i = 0; i < 108; i++) {
      state = sim.step(1 / 60);
    }

    // Must not remain stuck in mid-air or balancing on a knife-edge: height must be on the floor
    expect(state.position[2]).toBeLessThan(0.04);
    expect(state.position[2]).toBeGreaterThanOrEqual(0);

    // Orientation must have toppled: the body rotation must not remain frozen at 45 degrees
    const qx = state.quaternion[0], qy = state.quaternion[1];
    const worldUpZ = 1 - 2 * (qx * qx + qy * qy);
    // worldUpZ will be near +1 (bottom down) or -1 (inverted), never balanced on edge (~0)
    expect(Math.abs(worldUpZ)).toBeGreaterThan(0.7);

    sim.dispose();
  });
});
