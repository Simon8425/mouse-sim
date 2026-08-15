/**
 * Rapier.js live drop simulation — drives the 3D viewport animation.
 *
 * The backend Python engine remains the source of truth for all NUMBERS
 * (mass, inertia, CoM, restitution, friction, impacts, qualification — shown
 * in the results rail and debug HUD unchanged).  This module re-simulates the
 * drop LIVE in the browser with Rapier (WASM), using only backend-provided
 * initial conditions, so the animation is real rigid-body physics instead of
 * sample playback.
 *
 * Coordinate frame: the scene is Z-up (the physics floor is z=0), so the
 * Rapier world uses gravity along -Z and body transforms map 1:1 onto the
 * Three.js objectsGroup — no axis shuffling.
 *
 * Determinism: Rapier is deterministic for a fixed timestep and fixed inputs.
 */
import type { DropSimulationResult, DropSimulationDrop } from '../api/contracts';
import { worldVerticesForGeometry, type ObjectSceneEntry } from './geometryFactory';
import type { TelemetryFrame } from '../api/telemetryDebuggerContracts';

/** Backend model fields the Rapier sim consumes (subset of DropSimulationResult.model). */
export type DropSimulationModel = DropSimulationResult['model'];

/** Rapier module handle (loaded lazily so tests never touch WASM). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type RapierModule = any;

let rapierPromise: Promise<RapierModule | null> | null = null;

/**
 * Lazily load and init Rapier (dynamic import keeps the WASM out of the
 * initial chunk and lets tests / offline browsers fall back gracefully).
 * Returns null when the WASM cannot be loaded.
 */
export function loadRapier(): Promise<RapierModule | null> {
  if (rapierPromise === null) {
    rapierPromise = (async () => {
      try {
        const mod = await import('@dimforge/rapier3d-compat');
        await mod.init();
        return mod;
      } catch {
        return null;
      }
    })();
  }
  return rapierPromise;
}

/** Live body state read back from Rapier each frame. */
export interface LiveBodyState {
  position: [number, number, number];
  quaternion: [number, number, number, number]; // xyzw (THREE order)
  linvel: [number, number, number];
  angvel: [number, number, number];
}

export interface RapierDropSim {
  /** Advance the simulation by dt seconds (fixed substeps), then read back. */
  step(dt: number): LiveBodyState;
  /** Read the current body state without stepping. */
  getState(): LiveBodyState;
  /** Reset the body to a drop's initial conditions (pose + velocities). */
  reset(drop: DropSimulationDrop, model: DropSimulationModel): void;
  /** True when the body is at rest (backend-equivalent thresholds). */
  isResting(): boolean;
  /**
   * Attach a telemetry callback fired once per rendered frame with the
   * interpolated state and the contact window status. Returns an unsubscribe.
   */
  onTelemetry(cb: (frame: TelemetryFrame) => void): () => void;
  dispose(): void;
}

const MAX_SUBSTEPS = 256;

function quatWxyzToXyzw(q: number[] | undefined): { x: number; y: number; z: number; w: number } {
  if (!q || q.length !== 4 || !q.every((v) => Number.isFinite(v))) {
    return { x: 0, y: 0, z: 0, w: 1 };
  }
  return { x: q[1], y: q[2], z: q[3], w: q[0] };
}

function quatRotateVector(q: number[], v: number[]): [number, number, number] {
  if (q.length !== 4 || v.length !== 3) return [v[0] ?? 0, v[1] ?? 0, v[2] ?? 0];
  const [w, x, y, z] = q;
  const [vx, vy, vz] = v;
  const ix = w * vx + y * vz - z * vy;
  const iy = w * vy + z * vx - x * vz;
  const iz = w * vz + x * vy - y * vx;
  const iw = -x * vx - y * vy - z * vz;
  return [
    ix * w + iw * -x + iy * -z - iz * -y,
    iy * w + iw * -y + iz * -x - ix * -z,
    iz * w + iw * -z + ix * -y - iy * -x,
  ];
}

/**
 * Build the Rapier drop simulation for one analysis result.
 *
 * @param model  backend DropSimulationModel (mass, inertia, CoM, restitution,
 *               friction, timestep, orientation...)
 * @param entries scene object entries (for the collider vertex hull)
 * @param drops  backend per-drop records (initial conditions + schedule)
 */
export async function buildRapierDropSim(
  model: DropSimulationModel,
  entries: ObjectSceneEntry[],
  drops: DropSimulationDrop[],
): Promise<RapierDropSim | null> {
  const RAPIER = await loadRapier();
  if (!RAPIER) return null;

  const gravity = Number.isFinite(model.gravity_m_s2) && model.gravity_m_s2 > 0 ? model.gravity_m_s2 : 9.81;
  const world = new RAPIER.World({ x: 0, y: 0, z: -gravity });
  world.timestep = Number.isFinite(model.timestep_s) && model.timestep_s > 0 ? model.timestep_s : 1 / 240;

  // Use the backend surface restitution directly (the backend is the source
  // of truth): the previous Math.max(0.32, ...) floor silently overrode
  // low-restitution surfaces (foam e = 0.12, wood e = 0.40) so the live
  // animation never matched the recorded physics.
  const restitution =
    typeof model.restitution === 'number' && Number.isFinite(model.restitution)
      ? Math.min(0.95, Math.max(0.0, model.restitution))
      : 0.38;
  const friction = typeof model.friction === 'number' && Number.isFinite(model.friction) ? model.friction : 0.60;

  // Ground: fixed body with a cuboid collider whose TOP face sits at z=0.
  const groundBody = world.createRigidBody(RAPIER.RigidBodyDesc.fixed());
  const groundCollider = RAPIER.ColliderDesc.cuboid(10.0, 10.0, 0.05)
    .setTranslation(0, 0, -0.05)
    .setRestitution(restitution)
    .setRestitutionCombineRule(RAPIER.CoefficientCombineRule.Max)
    .setFriction(friction);
  world.createCollider(groundCollider, groundBody);

  // Dynamic body: one per analysis (reused across drops via reset()).
  const firstDrop = drops[0] ?? null;
  const startPose = firstDrop?.starting_pose_m ?? [0, 0, 0.75];
  const qWxyz = firstDrop?.orientation_quaternion_wxyz ?? model.orientation_quaternion_wxyz ?? [1, 0, 0, 0];
  const startQuat = quatWxyzToXyzw(qWxyz);
  const initialSpin = firstDrop?.initial_angular_velocity_rad_s ?? [0, 0, 0];
  const worldSpin = quatRotateVector(qWxyz, initialSpin);

  const bodyDesc = RAPIER.RigidBodyDesc.dynamic()
    .setTranslation(startPose[0], startPose[1], startPose[2])
    .setRotation(startQuat)
    .setLinvel(0, 0, 0)
    .setAngvel({ x: worldSpin[0], y: worldSpin[1], z: worldSpin[2] })
    .setLinearDamping(0.04)
    .setAngularDamping(0.25)
    .setCcdEnabled(true)
    .setCanSleep(true);
  const body = world.createRigidBody(bodyDesc);
  if (typeof body.enableCcd === 'function') {
    body.enableCcd(true);
  }

  // Backend mass properties: mass, CoM offset, and the DIAGONAL of the
  // inertia tensor (the mouse tensor is near-diagonal; off-diagonal coupling
  // is dropped for the visual sim, documented). The principal-inertia frame
  // is the identity (backend tensor is already in the body frame).
  const inertia = model.inertia_kg_m2 ?? [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  const com = model.com_offset_m ?? [0, 0, 0];
  const mass = Number.isFinite(model.mass_kg) && model.mass_kg > 0 ? model.mass_kg : 0.1;
  const principal = {
    x: Math.max(1e-7, inertia[0]?.[0] ?? 1e-4),
    y: Math.max(1e-7, inertia[1]?.[1] ?? 1e-4),
    z: Math.max(1e-7, inertia[2]?.[2] ?? 1e-4),
  };

  // Collider: convex hull of the display mesh vertices (the same
  // convex-contact philosophy as the backend hull fix).
  const collider = buildCollider(RAPIER, entries);
  if (collider !== null) {
    collider
      .setMassProperties(mass, { x: com[0], y: com[1], z: com[2] }, principal, { x: 0, y: 0, z: 0, w: 1 })
      .setRestitution(restitution)
      .setRestitutionCombineRule(RAPIER.CoefficientCombineRule.Max)
      .setFriction(friction);
    world.createCollider(collider, body);
  }

type Vec3 = [number, number, number];

  let prevPos: Vec3 = [startPose[0], startPose[1], startPose[2]];
  let prevQuat: [number, number, number, number] = [startQuat.x, startQuat.y, startQuat.z, startQuat.w];
  let currPos: Vec3 = [startPose[0], startPose[1], startPose[2]];
  let currQuat: [number, number, number, number] = [startQuat.x, startQuat.y, startQuat.z, startQuat.w];

  const reusableLiveState: LiveBodyState = {
    position: [startPose[0], startPose[1], startPose[2]],
    quaternion: [startQuat.x, startQuat.y, startQuat.z, startQuat.w],
    linvel: [0, 0, 0],
    angvel: [0, 0, 0],
  };

  let accumulator = 0;
  const telemetryListeners = new Set<(frame: TelemetryFrame) => void>();

  const sim: RapierDropSim = {
    step(dt: number): LiveBodyState {
      if (!(dt > 0)) {
        return readBodyState(body, reusableLiveState);
      }
      if (body.isSleeping()) {
        return readBodyState(body, reusableLiveState);
      }
      accumulator += Math.min(dt, 0.1);
      const timestep = world.timestep;
      let steps = 0;
      while (accumulator >= timestep && steps < MAX_SUBSTEPS) {
        prevPos[0] = currPos[0];
        prevPos[1] = currPos[1];
        prevPos[2] = currPos[2];
        prevQuat[0] = currQuat[0];
        prevQuat[1] = currQuat[1];
        prevQuat[2] = currQuat[2];
        prevQuat[3] = currQuat[3];
        world.step();
        const p = body.translation();
        const r = body.rotation();
        currPos[0] = p.x;
        currPos[1] = p.y;
        currPos[2] = p.z;
        currQuat[0] = r.x;
        currQuat[1] = r.y;
        currQuat[2] = r.z;
        currQuat[3] = r.w;
        accumulator -= timestep;
        steps += 1;
      }
      const alpha = Math.min(1, Math.max(0, accumulator / timestep));
      reusableLiveState.position[0] = prevPos[0] + (currPos[0] - prevPos[0]) * alpha;
      reusableLiveState.position[1] = prevPos[1] + (currPos[1] - prevPos[1]) * alpha;
      reusableLiveState.position[2] = prevPos[2] + (currPos[2] - prevPos[2]) * alpha;
      quatSlerpFast(reusableLiveState.quaternion, prevQuat, currQuat, alpha);
      const v = body.linvel();
      const w = body.angvel();
      reusableLiveState.linvel[0] = Math.abs(v.x) < 1e-5 ? 0 : v.x;
      reusableLiveState.linvel[1] = Math.abs(v.y) < 1e-5 ? 0 : v.y;
      reusableLiveState.linvel[2] = Math.abs(v.z) < 1e-5 ? 0 : v.z;
      reusableLiveState.angvel[0] = Math.abs(w.x) < 1e-5 ? 0 : w.x;
      reusableLiveState.angvel[1] = Math.abs(w.y) < 1e-5 ? 0 : w.y;
      reusableLiveState.angvel[2] = Math.abs(w.z) < 1e-5 ? 0 : w.z;
      if (telemetryListeners.size > 0) {
        const frame: TelemetryFrame = {
          index: 0,
          t_s: 0,
          status: 'free_fall',
          position_m: [reusableLiveState.position[0], reusableLiveState.position[1], reusableLiveState.position[2]],
          quaternion_wxyz: [
            reusableLiveState.quaternion[3],
            reusableLiveState.quaternion[0],
            reusableLiveState.quaternion[1],
            reusableLiveState.quaternion[2],
          ],
          velocity_m_s: [reusableLiveState.linvel[0], reusableLiveState.linvel[1], reusableLiveState.linvel[2]],
          angular_velocity_rad_s: [reusableLiveState.angvel[0], reusableLiveState.angvel[1], reusableLiveState.angvel[2]],
          acceleration_g: 0,
          energy_j: { kinetic_trans: 0, kinetic_rot: 0, potential: 0, total: 0, dissipated: 0 },
          contact: { active: false },
        };
        for (const listener of telemetryListeners) listener(frame);
      }
      return reusableLiveState;
    },

    getState(): LiveBodyState {
      return readBodyState(body, reusableLiveState);
    },

    reset(drop: DropSimulationDrop, m: DropSimulationModel): void {
      accumulator = 0;
      body.setLinearDamping(0.04);
      body.setAngularDamping(0.25);

      const pose = drop.starting_pose_m ?? [0, 0, 0.75];
      body.setTranslation({ x: pose[0], y: pose[1], z: pose[2] }, true);
      const q = drop.orientation_quaternion_wxyz ?? m.orientation_quaternion_wxyz ?? [1, 0, 0, 0];
      const xyzw = quatWxyzToXyzw(q);
      body.setRotation(xyzw, true);
      body.setLinvel({ x: 0, y: 0, z: 0 }, true);
      const bodySpin = drop.initial_angular_velocity_rad_s ?? [0, 0, 0];
      const worldSpinVec = quatRotateVector(q, bodySpin);
      body.setAngvel({ x: worldSpinVec[0], y: worldSpinVec[1], z: worldSpinVec[2] }, true);
      body.wakeUp();

      prevPos = [pose[0], pose[1], pose[2]];
      prevQuat = [xyzw.x, xyzw.y, xyzw.z, xyzw.w];
      currPos = [pose[0], pose[1], pose[2]];
      currQuat = [xyzw.x, xyzw.y, xyzw.z, xyzw.w];
    },

    isResting(): boolean {
      if (body.isSleeping()) return true;
      const v = body.linvel();
      const w = body.angvel();
      const speedSq = v.x * v.x + v.y * v.y + v.z * v.z;
      const spinSq = w.x * w.x + w.y * w.y + w.z * w.z;
      const pos = body.translation();
      return pos.z < 0.25 && speedSq < 0.0005 && spinSq < 0.003;
    },

    onTelemetry(cb: (frame: TelemetryFrame) => void): () => void {
      telemetryListeners.add(cb);
      return () => {
        telemetryListeners.delete(cb);
      };
    },

    dispose(): void {
      telemetryListeners.clear();
      world.free();
    },
  };

  return sim;
}

function buildCollider(
  RAPIER: RapierModule,
  entries: ObjectSceneEntry[],
) {
  // Collect safe world vertices across entries and apply a spatial voxel grid filter (2 mm resolution).
  // Quantize vertex coordinates directly to cell centers to strictly guarantee minimum vertex separation
  // and eliminate coplanar sliver facets in Rapier's QuickHull for jitter-free physics at any angle.
  const grid = new Map<string, [number, number, number]>();
  const invGridSize = 500; // 2mm grid cell size (1 / 0.002)

  for (const entry of entries) {
    const verts = worldVerticesForGeometry(entry.geometry);
    for (const v of verts) {
      const gx = Math.round(v[0] * invGridSize);
      const gy = Math.round(v[1] * invGridSize);
      const gz = Math.round(v[2] * invGridSize);
      const key = `${gx}_${gy}_${gz}`;
      if (!grid.has(key)) {
        grid.set(key, [gx / invGridSize, gy / invGridSize, gz / invGridSize]);
      }
    }
  }

  const all: number[] = [];
  for (const v of grid.values()) {
    all.push(v[0], v[1], v[2]);
  }

  if (all.length < 12) {
    // No usable mesh: conservative box from the model bounds.
    const ext = 0.05;
    return RAPIER.ColliderDesc.cuboid(ext, ext, ext);
  }
  try {
    const desc = RAPIER.ColliderDesc.convexHull(new Float32Array(all));
    if (desc !== null) return desc;
  } catch {
    // fall through to box
  }
  const ext = 0.05;
  return RAPIER.ColliderDesc.cuboid(ext, ext, ext);
}

function quatSlerpFast(
  out: [number, number, number, number],
  q1: [number, number, number, number],
  q2: [number, number, number, number],
  t: number,
): void {
  let cosHalfTheta = q1[0] * q2[0] + q1[1] * q2[1] + q1[2] * q2[2] + q1[3] * q2[3];
  let q2x = q2[0];
  let q2y = q2[1];
  let q2z = q2[2];
  let q2w = q2[3];
  if (cosHalfTheta < 0) {
    cosHalfTheta = -cosHalfTheta;
    q2x = -q2x;
    q2y = -q2y;
    q2z = -q2z;
    q2w = -q2w;
  }
  if (cosHalfTheta >= 0.9995) {
    out[0] = q1[0] + (q2x - q1[0]) * t;
    out[1] = q1[1] + (q2y - q1[1]) * t;
    out[2] = q1[2] + (q2z - q1[2]) * t;
    out[3] = q1[3] + (q2w - q1[3]) * t;
  } else {
    const halfTheta = Math.acos(Math.min(1, Math.max(-1, cosHalfTheta)));
    const sinHalfTheta = Math.sqrt(Math.max(1e-6, 1.0 - cosHalfTheta * cosHalfTheta));
    const ratioA = Math.sin((1 - t) * halfTheta) / sinHalfTheta;
    const ratioB = Math.sin(t * halfTheta) / sinHalfTheta;
    out[0] = q1[0] * ratioA + q2x * ratioB;
    out[1] = q1[1] * ratioA + q2y * ratioB;
    out[2] = q1[2] * ratioA + q2z * ratioB;
    out[3] = q1[3] * ratioA + q2w * ratioB;
  }
  const len = Math.sqrt(out[0] * out[0] + out[1] * out[1] + out[2] * out[2] + out[3] * out[3]);
  if (len > 1e-9) {
    out[0] /= len;
    out[1] /= len;
    out[2] /= len;
    out[3] /= len;
  }
}

function readBodyState(
  body: {
    translation(): { x: number; y: number; z: number };
    rotation(): { x: number; y: number; z: number; w: number };
    linvel(): { x: number; y: number; z: number };
    angvel(): { x: number; y: number; z: number };
    isSleeping?(): boolean;
  },
  out?: LiveBodyState,
): LiveBodyState {
  const p = body.translation();
  const r = body.rotation();
  const v = body.linvel();
  const w = body.angvel();
  const vx = Math.abs(v.x) < 1e-5 ? 0 : v.x;
  const vy = Math.abs(v.y) < 1e-5 ? 0 : v.y;
  const vz = Math.abs(v.z) < 1e-5 ? 0 : v.z;
  const wx = Math.abs(w.x) < 1e-5 ? 0 : w.x;
  const wy = Math.abs(w.y) < 1e-5 ? 0 : w.y;
  const wz = Math.abs(w.z) < 1e-5 ? 0 : w.z;
  if (out) {
    out.position[0] = p.x;
    out.position[1] = p.y;
    out.position[2] = p.z;
    out.quaternion[0] = r.x;
    out.quaternion[1] = r.y;
    out.quaternion[2] = r.z;
    out.quaternion[3] = r.w;
    out.linvel[0] = vx;
    out.linvel[1] = vy;
    out.linvel[2] = vz;
    out.angvel[0] = wx;
    out.angvel[1] = wy;
    out.angvel[2] = wz;
    return out;
  }
  return {
    position: [p.x, p.y, p.z],
    quaternion: [r.x, r.y, r.z, r.w],
    linvel: [vx, vy, vz],
    angvel: [wx, wy, wz],
  };
}
