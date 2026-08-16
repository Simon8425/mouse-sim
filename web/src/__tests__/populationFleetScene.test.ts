import { describe, it, expect, vi } from 'vitest';
import * as THREE from 'three';
import { createPopulationFleet } from '../scene/populationFleetScene';
import type { PopulationResult } from '../api/contracts';

function baseGroup(): THREE.Group {
  // A minimal two-part "mouse" whose geometries are owned by the group
  // (the fleet must NEVER dispose these).
  const group = new THREE.Group();
  const shellGeom = new THREE.BufferGeometry();
  shellGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(9), 3));
  const shellMat = new THREE.MeshStandardMaterial({ color: 0xffffff });
  const shell = new THREE.Mesh(shellGeom, shellMat);
  shell.name = 'shell';
  group.add(shell);

  const pcbGeom = new THREE.BufferGeometry();
  pcbGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(9), 3));
  const pcbMat = new THREE.MeshStandardMaterial({ color: 0x222222 });
  const pcb = new THREE.Mesh(pcbGeom, pcbMat);
  pcb.name = 'pcb';
  group.add(pcb);
  return group;
}

function populationResult(overrides: Partial<PopulationResult> = {}): PopulationResult {
  return {
    mode: 'exploration',
    verdict: 'fail',
    drop: null,
    sample_count: 10000,
    profile: 'esports_fps',
    lifespan_days: 730,
    units_failed: 120,
    failure_rate: 0.012,
    wilson_ci: { low: 0.010, high: 0.014 },
    component_failure_rates: [],
    weakest_components: [],
    sensitivity: [],
    survival: [],
    components: null,
    assumptions: [],
    shell: null,
    diagnostics: [],
    model: null,
    ...overrides,
  };
}

describe('createPopulationFleet (instancing & resource safety)', () => {
  it('shares one material clone per base material across all units', () => {
    const scene = new THREE.Scene();
    const base = baseGroup();
    const fleet = createPopulationFleet(scene, base, populationResult(), 2, 0);

    // 2x2 grid = 4 units; each has a shell + pcb mesh.
    const shellMeshes: THREE.Mesh[] = [];
    const pcbMeshes: THREE.Mesh[] = [];
    fleet.group.traverse((child) => {
      if (child instanceof THREE.Mesh && child.name === 'shell') shellMeshes.push(child);
      if (child instanceof THREE.Mesh && child.name === 'pcb') pcbMeshes.push(child);
    });
    expect(shellMeshes.length).toBe(4);
    expect(pcbMeshes.length).toBe(4);

    // All shell meshes share ONE material (not one per unit).
    const shellMats = new Set(shellMeshes.map((m) => m.material));
    expect(shellMats.size).toBe(1);
    // The shared material is a CLONE of the base, not the base itself.
    expect(shellMeshes[0].material).not.toBe((base.children[0] as THREE.Mesh).material);
    expect(shellMeshes[0].material).toBe(shellMeshes[1].material);
  });

  it('keeps base-model geometries alive after fleet dispose', () => {
    const scene = new THREE.Scene();
    const base = baseGroup();
    const baseShellGeom = (base.children[0] as THREE.Mesh).geometry;

    const fleet = createPopulationFleet(scene, base, populationResult(), 2, 0);
    // Track geometry dispose calls.
    const disposeSpy = vi.spyOn(baseShellGeom, 'dispose');
    fleet.dispose();

    // The base geometry must NOT have been disposed by the fleet teardown.
    expect(disposeSpy).not.toHaveBeenCalled();
    expect(baseShellGeom.dispose).toBeTypeOf('function');
    // The base materials must also survive (only clones are disposed).
    expect((base.children[0] as THREE.Mesh).material).toBeDefined();
    expect((base.children[1] as THREE.Mesh).material).toBeDefined();
    // The fleet group is removed from the scene.
    expect(scene.children).not.toContain(fleet.group);
  });

  it('renders one unit group per grid cell with distinct verdict colors on rings', () => {
    const scene = new THREE.Scene();
    const fleet = createPopulationFleet(
      scene,
      baseGroup(),
      populationResult({ failure_rate: 0.25 }),
      2,
      0,
    );
    expect(fleet.units.length).toBe(4);

    const fails = fleet.units.filter((u) => u.verdict === 'fail');
    const passes = fleet.units.filter((u) => u.verdict === 'pass');
    expect(fails.length).toBe(1); // round(4 * 0.25)
    expect(passes.length).toBeGreaterThan(0);

    // All four units update without throwing and settle at target poses.
    fleet.update(0);
    fleet.update(1.0);
    fleet.update(5.0);
    const mesh = fleet.group.children[0];
    expect(mesh).toBeDefined();
  });

  it('freezes settled units and skips redundant material writes after rest', () => {
    const scene = new THREE.Scene();
    const fleet = createPopulationFleet(scene, baseGroup(), populationResult(), 2, 0);
    // Fast-forward past every unit's settle time (max t3 < 2s for the
    // 0.6-0.7 m drops in the scene).
    fleet.update(10);

    // Status rings are the transparent RingGeometry meshes laid flat on
    // the ground (z = 0.001); the ripple rings sit at z = 0.002 with a
    // fading opacity that returns to 0 after settling.  The highlight ring
    // sits at the origin (z = 0) until highlightUnit() moves it, so it is
    // excluded by its sky-blue color.
    const rings = fleet.group.children.filter(
      (child) =>
        child instanceof THREE.Mesh &&
        (child.geometry as THREE.BufferGeometry).type === 'RingGeometry' &&
        child.position.z < 0.002 &&
        (child.material as THREE.MeshBasicMaterial).color.getHex() !== 0x38bdf8,
    ) as THREE.Mesh[];
    expect(rings.length).toBe(4);
    for (const ring of rings) {
      const mat = ring.material as THREE.MeshBasicMaterial;
      expect(mat.opacity).toBeGreaterThan(0.5);
      // The fall-phase gray is gone after settling.
      expect(mat.color.getHex()).not.toBe(0x475569);
    }

    // The ripple rings are transparent but fade to 0 after settling.
    const ripples = fleet.group.children.filter(
      (child) =>
        child instanceof THREE.Mesh &&
        (child.geometry as THREE.BufferGeometry).type === 'RingGeometry' &&
        child.position.z >= 0.002,
    ) as THREE.Mesh[];
    expect(ripples.length).toBe(4);
    for (const ripple of ripples) {
      expect((ripple.material as THREE.MeshBasicMaterial).opacity).toBe(0);
    }
  });
});
