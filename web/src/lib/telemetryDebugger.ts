/**
 * Telemetry Log Debugger — pure pipeline logic.
 *
 * Everything in this module is allocation-light and unit-testable: the ring
 * buffer, per-frame energy auditing, the drop-status classifier, the
 * acceleration estimator, and the diagnostic event detector. The scene
 * runtime calls these while appending frames; the UI calls them for exports.
 */
import type {
  DropStatus,
  ImpactDetection,
  ReboundApex,
  TelemetryEvent,
  TelemetryFrame,
} from '../api/telemetryDebuggerContracts';

/** Energy audit for one frame. */
export interface EnergyAudit {
  kinetic_trans_j: number;
  kinetic_rot_j: number;
  potential_j: number;
  total_j: number;
  dissipated_j: number;
  drift_pct: number;
}

/** Static parameters the energy auditor needs (per-frame kinematics are pushed). */
export interface TelemetrySource {
  mass_kg: number;
  inertia_diag_kg_m2: [number, number, number];
  gravity_m_s2: number;
  floor_z: number;
  /** Total mechanical energy at release, J. Used for drift computation. */
  release_energy_j: number;
}

/** Finite-difference acceleration (m/s²) with half-step guard. */
export function estimateAcceleration(
  vNow: [number, number, number],
  vPrev: [number, number, number],
  dt: number,
): [number, number, number] {
  if (!(dt > 0)) return [0, 0, 0];
  return [
    (vNow[0] - vPrev[0]) / dt,
    (vNow[1] - vPrev[1]) / dt,
    (vNow[2] - vPrev[2]) / dt,
  ];
}

/** Translational kinetic energy: ½ m |v|². */
export function kineticTrans(v: [number, number, number], massKg: number): number {
  const s = v[0] * v[0] + v[1] * v[1] + v[2] * v[2];
  return 0.5 * massKg * s;
}

/** Rotational kinetic energy about the body diagonal inertia: ½ Σ Iᵢωᵢ². */
export function kineticRot(w: [number, number, number], inertiaDiag: [number, number, number]): number {
  return 0.5 * (inertiaDiag[0] * w[0] * w[0] + inertiaDiag[1] * w[1] * w[1] + inertiaDiag[2] * w[2] * w[2]);
}

/**
 * Full energy audit for one frame. Dissipated = release − total (clamped ≥ 0);
 * drift is the normalized deviation of (total + dissipated) from release.
 */
export function auditEnergy(frame: {
  position_m: [number, number, number];
  velocity_m_s: [number, number, number];
  angular_velocity_rad_s: [number, number, number];
} & TelemetrySource): EnergyAudit {
  const kt = kineticTrans(frame.velocity_m_s, frame.mass_kg);
  const kr = kineticRot(frame.angular_velocity_rad_s, frame.inertia_diag_kg_m2);
  const pe = frame.mass_kg * frame.gravity_m_s2 * Math.max(0, frame.position_m[2] - frame.floor_z);
  const total = kt + kr + pe;
  const dissipated = Math.max(0, frame.release_energy_j - total);
  const e0 = frame.release_energy_j;
  const driftPct = e0 > 0 ? (Math.abs(total + dissipated - e0) / e0) * 100 : 0;
  return { kinetic_trans_j: kt, kinetic_rot_j: kr, potential_j: pe, total_j: total, dissipated_j: dissipated, drift_pct: driftPct };
}

/** Squared norm of a 3-vector. */
export function normSq3(v: number[]): number {
  return v[0] * v[0] + v[1] * v[1] + v[2] * v[2];
}

/** Norm of a 3-vector. */
export function norm3(v: number[]): number {
  return Math.sqrt(normSq3(v));
}

/**
 * Classify the drop lifecycle from kinematics + contact evidence.
 * Impact requires a positive acceleration spike while airborne (FEA contact
 * windows and backend impact records corroborate, but never drive, the live
 * classification). A rebound requires vertical velocity directed upward after
 * an impact. Contact proximity (z within 2mm of the floor) reads as rolling.
 */
export function classifyDropStatus(opts: {
  accelG: number;
  velZ: number;
  z: number;
  floorZ: number;
  inContactWindow: boolean;
  prevStatus: DropStatus;
  settled: boolean;
}): DropStatus {
  if (opts.settled) return 'settled';
  const impactCandidate = opts.accelG >= 3.0 && opts.inContactWindow;
  if (impactCandidate && opts.prevStatus !== 'impact') return 'impact';
  if (opts.prevStatus === 'impact') {
    return opts.velZ > 0.05 ? 'rebound' : 'impact';
  }
  if (opts.velZ > 0.05 && opts.prevStatus === 'rebound') return 'rebound';
  if (opts.z - opts.floorZ < 0.002) return 'rolling';
  return 'free_fall';
}

/** Deterministic hash used for dither/status jitter-free sampling. */
export function hash1(i: number, salt: number): number {
  let h = (i * 0x9e3779b1 + salt * 0x85ebca6b) | 0;
  h = Math.imul(h ^ (h >>> 16), 0x45d9f3b);
  h = Math.imul(h ^ (h >>> 16), 0x45d9f3b);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

/** Convert a wxyz quaternion to Euler roll/pitch/yaw degrees. */
export function quatToEulerDeg(wxyz: [number, number, number, number]): [number, number, number] {
  const [w, x, y, z] = wxyz;
  const roll = Math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y));
  const pitch = Math.asin(Math.max(-1, Math.min(1, 2 * (w * y - z * x))));
  const yaw = Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z));
  return [roll * (180 / Math.PI), pitch * (180 / Math.PI), yaw * (180 / Math.PI)];
}

/** Bounded ring buffer with O(1) append and snapshot access. */
export class RingBuffer<T> {
  private buf: (T | undefined)[];
  private head = 0;
  private count = 0;

  constructor(readonly capacity: number) {
    this.buf = new Array<T | undefined>(capacity);
  }

  get length(): number {
    return this.count;
  }

  push(item: T): void {
    this.buf[(this.head + this.count) % this.capacity] = item;
    if (this.count < this.capacity) {
      this.count += 1;
    } else {
      this.head = (this.head + 1) % this.capacity;
    }
  }

  /** Frame at logical index i (oldest = 0). */
  at(i: number): T | undefined {
    if (i < 0 || i >= this.count) return undefined;
    return this.buf[(this.head + i) % this.capacity];
  }

  toArray(): T[] {
    const out: T[] = [];
    for (let i = 0; i < this.count; i += 1) {
      const v = this.buf[(this.head + i) % this.capacity];
      if (v !== undefined) out.push(v);
    }
    return out;
  }

  clear(): void {
    this.head = 0;
    this.count = 0;
  }
}

/** Live accumulator that computes per-frame energy + drift in one pass. */
export class EnergyAuditor {
  private prevVel: [number, number, number] = [0, 0, 0];
  private hasPrev = false;
  private driftMaxPct = 0;
  private accelG = 0;

  constructor(private readonly src: TelemetrySource) {}

  /** Acceleration magnitude in G from the last pushed frame. */
  get lastAccelerationG(): number {
    return this.accelG;
  }

  /** Append one frame's kinematics; returns the audit. */
  push(
    position: [number, number, number],
    velocity: [number, number, number],
    angularVelocity: [number, number, number],
    dt: number,
  ): EnergyAudit {
    const a = estimateAcceleration(velocity, this.hasPrev ? this.prevVel : velocity, this.hasPrev ? dt : 0);
    const audit = auditEnergy({
      position_m: position,
      velocity_m_s: velocity,
      angular_velocity_rad_s: angularVelocity,
      mass_kg: this.src.mass_kg,
      inertia_diag_kg_m2: this.src.inertia_diag_kg_m2,
      gravity_m_s2: this.src.gravity_m_s2,
      floor_z: this.src.floor_z,
      release_energy_j: this.src.release_energy_j,
    });
    this.prevVel[0] = velocity[0];
    this.prevVel[1] = velocity[1];
    this.prevVel[2] = velocity[2];
    this.hasPrev = true;
    this.accelG = norm3(a) / Math.max(1e-9, this.src.gravity_m_s2);
    if (audit.drift_pct > this.driftMaxPct) this.driftMaxPct = audit.drift_pct;
    return audit;
  }

  get maxDriftPct(): number {
    return this.driftMaxPct;
  }
}

/** Event detector: impact, rebound apex, plastic yield, drift anomaly. */
export class TelemetryEventDetector {
  private impacts: ImpactDetection[] = [];
  private apex: ReboundApex | null = null;
  private prevStatus: DropStatus = 'free_fall';
  private minZ = Infinity;

  constructor(
    private readonly floorZ: number,
    private readonly yieldStressPa: number,
  ) {}

  /** Feed one frame; returns events detected at this frame. */
  push(frame: TelemetryFrame, audit: EnergyAudit, inContactWindow: boolean, settled: boolean): TelemetryEvent[] {
    const events: TelemetryEvent[] = [];
    const status = classifyDropStatus({
      accelG: frame.acceleration_g,
      velZ: frame.velocity_m_s[2],
      z: frame.position_m[2],
      floorZ: this.floorZ,
      inContactWindow,
      prevStatus: this.prevStatus,
      settled,
    });
    if (status === 'impact' && this.prevStatus !== 'impact') {
      this.impacts.push({
        t_s: frame.t_s,
        velocity_m_s: [...frame.velocity_m_s] as [number, number, number],
        contact_point_m: frame.contact.point_m ? [...frame.contact.point_m] : undefined,
        peak_force_n: frame.contact.normal_force_n,
      });
      events.push({
        level: 'EVENT',
        code: 'IMPACT_DETECTED',
        t_s: frame.t_s,
        message: `Impact at t=${frame.t_s.toFixed(3)}s v=(${frame.velocity_m_s.map((v) => v.toFixed(2)).join(',')}) m/s`,
      });
    }
    if (this.prevStatus === 'impact' && status === 'rebound' && frame.velocity_m_s[2] > 0) {
      // Track the apex after rebound by following z.
      if (frame.position_m[2] < this.minZ) {
        this.minZ = frame.position_m[2];
      }
      if (frame.velocity_m_s[2] < 0.05 && this.minZ < Infinity) {
        const height = Math.max(0, frame.position_m[2] - this.minZ);
        const retention = Math.min(100, Math.max(0, (audit.total_j / Math.max(1e-9, audit.total_j + audit.dissipated_j)) * 100));
        this.apex = { t_s: frame.t_s, height_m: height, energy_retention_pct: retention };
        events.push({
          level: 'EVENT',
          code: 'REBOUND_APEX',
          t_s: frame.t_s,
          message: `Rebound apex h=${(height * 1000).toFixed(1)}mm energy=${retention.toFixed(1)}%`,
        });
      }
    }
    const fea = frame.fea;
    if (fea && fea.peak_stress_pa > this.yieldStressPa && this.prevStatus !== 'impact') {
      events.push({
        level: 'WARNING',
        code: 'PLASTIC_YIELD_WARNING',
        t_s: frame.t_s,
        message: `Peak stress ${(fea.peak_stress_pa / 1e6).toFixed(1)} MPa exceeded yield ${(this.yieldStressPa / 1e6).toFixed(1)} MPa`,
      });
    }
    if (audit.drift_pct > 1.5 && this.prevStatus !== 'settled') {
      events.push({
        level: 'ANOMALY',
        code: 'ENERGY_DRIFT_ANOMALY',
        t_s: frame.t_s,
        message: `Numerical drift ${audit.drift_pct.toFixed(2)}% exceeds 1.5%`,
      });
    }
    this.prevStatus = status;
    return events;
  }

  get state(): { impacts: ImpactDetection[]; apex: ReboundApex | null; minZ: number } {
    return { impacts: this.impacts, apex: this.apex, minZ: this.minZ };
  }
}
