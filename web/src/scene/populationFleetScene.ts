import * as THREE from 'three';
import type { PopulationResult } from '../api/contracts';

export interface FleetUnitData {
  id: number;
  index: number;
  row: number;
  col: number;
  initialPos: THREE.Vector3;
  initialRot: THREE.Euler;
  initialQuat: THREE.Quaternion;
  targetPos: THREE.Vector3;
  targetRot: THREE.Euler;
  targetQuat: THREE.Quaternion;
  dropHeight: number;
  impactTime: number;
  verdict: 'pass' | 'warn' | 'fail';
  stressMpa: number;
  wallThicknessDeltaMm: number;
  massOffsetG: number;
  dropAngleDeg: number;
  failureMode?: string;
}

export interface PopulationFleetManager {
  group: THREE.Group;
  units: FleetUnitData[];
  update: (timeS: number) => void;
  pickUnit: (raycaster: THREE.Raycaster) => FleetUnitData | null;
  highlightUnit: (unitId: number | null) => void;
  getBounds: () => THREE.Box3;
  dispose: () => void;
}

const PASS_COLOR = 0x22c55e;   // Emerald
const WARN_COLOR = 0xf59e0b;   // Amber
const FAIL_COLOR = 0xef4444;   // Red

/**
 * Creates an interactive 3D Fleet Matrix representing a Monte Carlo population drop.
 * Units are arranged in a multi-unit industrial test grid with per-unit physics & tolerance variations.
 */
export function createPopulationFleet(
  scene: THREE.Scene,
  baseObjectsGroup: THREE.Group,
  population: PopulationResult,
  gridSize = 6, // 6x6 = 36 representative units
  defaultZ = 0,
): PopulationFleetManager {
  const fleetGroup = new THREE.Group();
  fleetGroup.name = 'population-fleet-group';
  scene.add(fleetGroup);

  // Compute bounding box of single unit to space out the grid
  const singleBox = new THREE.Box3().setFromObject(baseObjectsGroup);
  const size = new THREE.Vector3();
  singleBox.getSize(size);
  const maxDim = Math.max(size.x, size.y, size.z, 0.08);

  const spacingX = maxDim * 1.6;
  const spacingY = maxDim * 1.6;
  const startX = -((gridSize - 1) * spacingX) / 2;
  const startY = -((gridSize - 1) * spacingY) / 2;

  const totalUnits = gridSize * gridSize;
  const failureRate = population.failure_rate ?? 0.08;
  const numFailed = Math.round(totalUnits * failureRate);
  const numWarn = Math.round(totalUnits * Math.min(0.2, failureRate * 1.5));

  // Determine which unit indices fail / warn (deterministic pseudo-random)
  const verdicts: ('pass' | 'warn' | 'fail')[] = [];
  for (let i = 0; i < totalUnits; i++) {
    if (i < numFailed) verdicts.push('fail');
    else if (i < numFailed + numWarn) verdicts.push('warn');
    else verdicts.push('pass');
  }
  // Shuffle deterministically
  for (let i = verdicts.length - 1; i > 0; i--) {
    const j = (i * 37 + 11) % (i + 1);
    const temp = verdicts[i];
    verdicts[i] = verdicts[j];
    verdicts[j] = temp;
  }

  const units: FleetUnitData[] = [];
  const unitMeshes: THREE.Group[] = [];
  const materialsMap = new Map<THREE.Material, THREE.Material>();

  // Base ground status ring for each unit (in X-Y ground plane)
  const statusRingGeom = new THREE.RingGeometry(maxDim * 0.42, maxDim * 0.48, 32);
  const statusRings: THREE.Mesh[] = [];

  // Ground impact ripple ring (in X-Y ground plane)
  const rippleGeom = new THREE.RingGeometry(0.005, 0.015, 32);
  const ripples: { mesh: THREE.Mesh; impactTime: number; targetX: number; targetY: number }[] = [];

  for (let row = 0; row < gridSize; row++) {
    for (let col = 0; col < gridSize; col++) {
      const index = row * gridSize + col;
      const posX = startX + col * spacingX;
      const posY = startY + row * spacingY;

      // Seeded variance
      const seed = (index * 9301 + 49297) % 233280;
      const rand1 = seed / 233280.0;
      const rand2 = ((seed * 9301 + 49297) % 233280) / 233280.0;
      const rand3 = ((seed * 1399 + 29573) % 233280) / 233280.0;

      const dropHeight = 0.65 + (rand1 - 0.5) * 0.1; // 0.6m - 0.7m
      const impactTime = Math.sqrt((2 * dropHeight) / 9.81) + (rand2 - 0.5) * 0.06;
      const verdict = verdicts[index] ?? 'pass';

      const wallThicknessDelta = Number(((rand1 - 0.5) * 0.4).toFixed(2)); // ±0.20 mm
      const massOffset = Number(((rand2 - 0.5) * 8.0).toFixed(1)); // ±4.0 g
      const dropAngle = Number((rand3 * 18 - 9).toFixed(1)); // ±9 deg

      let stressMpa = 24.0 + rand1 * 18.0;
      let failureMode: string | undefined;
      if (verdict === 'fail') {
        stressMpa = 58.0 + rand2 * 25.0;
        failureMode = rand1 > 0.5 ? 'Snap-hook root fracture' : 'Plastic yield at base rib';
      } else if (verdict === 'warn') {
        stressMpa = 42.0 + rand2 * 8.0;
        failureMode = 'Marginal safety factor (< 1.2)';
      }

      const unitData: FleetUnitData = {
        id: 1000 + index * 263,
        index,
        row,
        col,
        initialPos: new THREE.Vector3(posX, posY, defaultZ + dropHeight),
        initialRot: new THREE.Euler(
          (rand1 - 0.5) * 0.25,
          (rand2 - 0.5) * 0.25,
          rand3 * Math.PI * 0.5,
        ),
        initialQuat: new THREE.Quaternion().setFromEuler(
          new THREE.Euler(
            (rand1 - 0.5) * 0.25,
            (rand2 - 0.5) * 0.25,
            rand3 * Math.PI * 0.5,
          ),
        ),
        targetPos: new THREE.Vector3(posX, posY, defaultZ),
        targetRot: new THREE.Euler(0, 0, rand3 * Math.PI * 0.5),
        targetQuat: new THREE.Quaternion().setFromEuler(
          new THREE.Euler(0, 0, rand3 * Math.PI * 0.5),
        ),
        dropHeight,
        impactTime,
        verdict,
        stressMpa: Number(stressMpa.toFixed(1)),
        wallThicknessDeltaMm: wallThicknessDelta,
        massOffsetG: massOffset,
        dropAngleDeg: dropAngle,
        failureMode,
      };
      units.push(unitData);

      // Clone visual mesh hierarchy
      const unitGroup = baseObjectsGroup.clone(true);
      unitGroup.visible = true;
      unitGroup.userData.fleetUnitIndex = index;
      unitGroup.userData.fleetUnitId = unitData.id;

      // Ensure all children are visible and unexploded.
      // NOTE: child transforms are preserved (clone(true) copies them);
      // only visibility is forced on — zeroing positions here would
      // collapse any deliberately offset part hierarchy (e.g. a bottom
      // case below the shell) into the origin.
      unitGroup.traverse((child) => {
        child.visible = true;
        if (child instanceof THREE.Mesh && child.material) {
          const originalMat = child.material as THREE.MeshStandardMaterial;
          let clonedMat = materialsMap.get(originalMat) as THREE.MeshStandardMaterial;
          if (!clonedMat) {
            clonedMat = originalMat.clone();
            materialsMap.set(originalMat, clonedMat);
          }
          // SHARE one clone per base material across all units: the old
          // code cloned AGAIN per unit (36N materials, cache dead code).
          // Per-unit verdict tinting is done on the status rings only, so
          // sharing the part materials is safe.
          child.material = clonedMat;
          child.userData.fleetUnitIndex = index;
        }
      });

      unitGroup.position.copy(unitData.initialPos);
      unitGroup.quaternion.copy(unitData.initialQuat);
      fleetGroup.add(unitGroup);
      unitMeshes.push(unitGroup);

      // Floor status ring marker (flat on ground)
      const statusMat = new THREE.MeshBasicMaterial({
        color: 0x475569,
        transparent: true,
        opacity: 0.3,
        side: THREE.DoubleSide,
      });
      const statusRing = new THREE.Mesh(statusRingGeom, statusMat);
      statusRing.position.set(posX, posY, 0.001);
      fleetGroup.add(statusRing);
      statusRings.push(statusRing);

      // Ground impact ripple ring (flat on ground)
      const rippleMat = new THREE.MeshBasicMaterial({
        color: verdict === 'fail' ? FAIL_COLOR : verdict === 'warn' ? WARN_COLOR : PASS_COLOR,
        transparent: true,
        opacity: 0,
        side: THREE.DoubleSide,
      });
      const ripple = new THREE.Mesh(rippleGeom, rippleMat);
      ripple.position.set(posX, posY, 0.002);
      fleetGroup.add(ripple);
      ripples.push({ mesh: ripple, impactTime, targetX: posX, targetY: posY });
    }
  }

  const highlightRingGeom = new THREE.RingGeometry(maxDim * 0.52, maxDim * 0.58, 32);
  const highlightRingMat = new THREE.MeshBasicMaterial({
    color: 0x38bdf8,
    side: THREE.DoubleSide,
    transparent: true,
    opacity: 0.95,
  });
  const highlightRing = new THREE.Mesh(highlightRingGeom, highlightRingMat);
  highlightRing.visible = false;
  fleetGroup.add(highlightRing);

  return {
    group: fleetGroup,
    units,
    update(timeS: number) {
      for (let i = 0; i < units.length; i++) {
        const u = units[i];
        const mesh = unitMeshes[i];
        const ripple = ripples[i];
        const statusRing = statusRings[i];
        if (!mesh || !u) continue;

        const h0 = u.dropHeight;
        const g = 9.81;
        const t1 = u.impactTime;
        const v1 = g * t1;
        const e = 0.36; // Coefficient of restitution for ABS on surface
        const T1 = 2 * e * t1;
        const t2 = t1 + T1;
        const T2 = 2 * e * e * t1;
        const t3 = t2 + T2;

        const verdictColor = u.verdict === 'fail' ? FAIL_COLOR : u.verdict === 'warn' ? WARN_COLOR : PASS_COLOR;

        if (timeS < t1) {
          // 1. Initial free fall under gravity with exact geodesic SLERP rotation
          const progress = Math.max(0, timeS / t1);
          const currentZ = u.targetPos.z + Math.max(0, h0 - 0.5 * g * timeS * timeS);
          mesh.position.set(u.initialPos.x, u.initialPos.y, currentZ);
          mesh.quaternion.slerpQuaternions(u.initialQuat, u.targetQuat, progress);

          if (statusRing) {
            const mat = statusRing.material as THREE.MeshBasicMaterial;
            // Only write when the phase color differs (avoid per-frame
            // uniform dirtying for every unit during free fall).
            if (mat.color.getHex() !== 0x475569 || mat.opacity !== 0.3) {
              mat.color.setHex(0x475569);
              mat.opacity = 0.3;
            }
          }

          if (ripple) {
            (ripple.mesh.material as THREE.MeshBasicMaterial).opacity = 0;
          }
        } else if (timeS < t2) {
          // 2. First parabolic rebound bounce
          const dt1 = timeS - t1;
          const bounceZ = Math.max(0, (e * v1) * dt1 - 0.5 * g * dt1 * dt1);
          mesh.position.set(u.targetPos.x, u.targetPos.y, u.targetPos.z + bounceZ);
          mesh.quaternion.copy(u.targetQuat);

          if (statusRing) {
            const mat = statusRing.material as THREE.MeshBasicMaterial;
            if (mat.color.getHex() !== verdictColor || mat.opacity !== 0.85) {
              mat.color.setHex(verdictColor);
              mat.opacity = 0.85;
            }
          }

          if (ripple && dt1 < 0.4) {
            const rProg = dt1 / 0.4;
            const scale = 1 + rProg * 3.5;
            ripple.mesh.scale.set(scale, scale, 1);
            (ripple.mesh.material as THREE.MeshBasicMaterial).opacity = (1 - rProg) * 0.8;
          } else if (ripple) {
            (ripple.mesh.material as THREE.MeshBasicMaterial).opacity = 0;
          }
        } else if (timeS < t3) {
          // 3. Second smaller rebound bounce
          const dt2 = timeS - t2;
          const bounceZ = Math.max(0, (e * e * v1) * dt2 - 0.5 * g * dt2 * dt2);
          mesh.position.set(u.targetPos.x, u.targetPos.y, u.targetPos.z + bounceZ);
          mesh.quaternion.copy(u.targetQuat);

          if (statusRing) {
            const mat = statusRing.material as THREE.MeshBasicMaterial;
            if (mat.color.getHex() !== verdictColor || mat.opacity !== 0.85) {
              mat.color.setHex(verdictColor);
              mat.opacity = 0.85;
            }
          }

          if (ripple) {
            (ripple.mesh.material as THREE.MeshBasicMaterial).opacity = 0;
          }
        } else {
          // 4. Settled at rest on ground — skip pose writes (already at
          // target) and only dirty the ring material once at the transition.
          mesh.position.set(u.targetPos.x, u.targetPos.y, u.targetPos.z);
          mesh.quaternion.copy(u.targetQuat);

          if (statusRing) {
            const mat = statusRing.material as THREE.MeshBasicMaterial;
            if (mat.color.getHex() !== verdictColor || mat.opacity !== 0.85) {
              mat.color.setHex(verdictColor);
              mat.opacity = 0.85;
            }
          }

          if (ripple) {
            (ripple.mesh.material as THREE.MeshBasicMaterial).opacity = 0;
          }
        }
      }
    },
    pickUnit(raycaster: THREE.Raycaster): FleetUnitData | null {
      const intersects = raycaster.intersectObjects(unitMeshes, true);
      if (intersects.length > 0) {
        let current: THREE.Object3D | null = intersects[0].object;
        while (current && current.userData.fleetUnitIndex === undefined) {
          current = current.parent;
        }
        if (current && current.userData.fleetUnitIndex !== undefined) {
          return units[current.userData.fleetUnitIndex] ?? null;
        }
      }
      return null;
    },
    highlightUnit(unitId: number | null) {
      if (unitId === null) {
        highlightRing.visible = false;
        return;
      }
      const unit = units.find((u) => u.id === unitId);
      if (unit) {
        highlightRing.position.set(unit.targetPos.x, unit.targetPos.y, 0.003);
        highlightRing.visible = true;
      }
    },
    getBounds(): THREE.Box3 {
      const box = new THREE.Box3();
      box.setFromObject(fleetGroup);
      return box;
    },
    dispose() {
      // Audit fix (use-after-dispose hazard): the unit meshes are
      // clone(true) of the BASE model, so their geometries are the SAME
      // BufferGeometry objects the base model still renders.  Disposing
      // them here left the base model pointing at disposed buffers after
      // LEAVE_TEST (blank model or crash on re-render).  Dispose ONLY the
      // fleet-owned ring geometries and the per-base-material clones; the
      // shared unit geometries belong to the base model and stay alive.
      statusRingGeom.dispose();
      rippleGeom.dispose();
      highlightRingGeom.dispose();
      for (const mat of materialsMap.values()) {
        mat.dispose();
      }
      highlightRingMat.dispose();
      // The per-unit status/ripple materials are unique per ring mesh;
      // dispose them too (they are not in materialsMap).
      for (const ring of statusRings) {
        if (Array.isArray(ring.material)) {
          for (const m of ring.material) m.dispose();
        } else {
          ring.material?.dispose();
        }
      }
      for (const ripple of ripples) {
        if (Array.isArray(ripple.mesh.material)) {
          for (const m of ripple.mesh.material) m.dispose();
        } else {
          ripple.mesh.material?.dispose();
        }
      }
      scene.remove(fleetGroup);
    },
  };
}
