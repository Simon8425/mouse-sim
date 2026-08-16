/**
 * Telemetry Log Debugger — live collector.
 *
 * Owns the ring-buffer frame history, the energy auditor, the event detector,
 * and the drop-status state machine. The scene runtime pushes one telemetry
 * record per rendered frame (not per substep); the collector appends a
 * TelemetryFrame to the ring and returns any diagnostic events.
 */
import type {
  DropSimulationResult,
  FeaResult,
} from '../api/contracts';
import type {
  TelemetryEvent,
  TelemetryFrame,
} from '../api/telemetryDebuggerContracts';
import {
  EnergyAuditor,
  RingBuffer,
  TelemetryEventDetector,
} from './telemetryDebugger';

export interface TelemetryCollectorOptions {
  result: DropSimulationResult;
  fea?: FeaResult | null;
  capacity?: number;
  floorZ?: number;
}

/** One raw live record fed by the runtime each frame. */
export interface LiveTelemetryRecord {
  t_s: number;
  position_m: [number, number, number];
  quaternion_xyzw: [number, number, number, number];
  velocity_m_s: [number, number, number];
  angular_velocity_rad_s: [number, number, number];
  in_contact_window: boolean;
  settled: boolean;
}

export interface CollectorSnapshot {
  frames: TelemetryFrame[];
  events: TelemetryEvent[];
}

export class TelemetryCollector {
  private readonly ring: RingBuffer<TelemetryFrame>;
  private readonly auditor: EnergyAuditor;
  private readonly detector: TelemetryEventDetector;
  private readonly events: TelemetryEvent[] = [];
  private frameIndex = 0;
  private readonly fea: FeaResult | null;

  constructor(opts: TelemetryCollectorOptions) {
    const result = opts.result;
    const model = result.model;
    const mass = model.mass_kg ?? 0.1;
    const inertia = model.inertia_kg_m2 ?? [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
    const gravity = model.gravity_m_s2 > 0 ? model.gravity_m_s2 : 9.80665;
    const floorZ = opts.floorZ ?? 0;
    const diag: [number, number, number] = [inertia[0]?.[0] ?? 0, inertia[1]?.[1] ?? 0, inertia[2]?.[2] ?? 0];
    const drop0 = result.drops?.[0];
    const releaseJ = drop0?.energy?.release_j ?? mass * gravity * (result.config?.height_m ?? 0.75);
    this.fea = opts.fea ?? null;
    this.ring = new RingBuffer<TelemetryFrame>(opts.capacity ?? 4096);
    this.auditor = new EnergyAuditor({
      mass_kg: mass,
      inertia_diag_kg_m2: diag,
      gravity_m_s2: gravity,
      floor_z: floorZ,
      release_energy_j: releaseJ,
    });
    this.detector = new TelemetryEventDetector(floorZ, this.fea?.yield_stress_pa ?? 45e6);
  }

  /** Append one live record; returns events detected at this frame. */
  push(rec: LiveTelemetryRecord): TelemetryEvent[] {
    const audit = this.auditor.push(rec.position_m, rec.velocity_m_s, rec.angular_velocity_rad_s, this.frameIndex === 0 ? 0 : rec.t_s - this.lastT);
    const accelG = this.auditor.lastAccelerationG;
    const fea = this.fea;
    const feaField = fea
      ? {
          peak_stress_pa: fea.peak?.stress_pa ?? 0,
          safety_factor: fea.safety_factor ?? 0,
          damage: fea.peak?.damage ?? 0,
        }
      : undefined;
    const frame: TelemetryFrame = {
      index: this.frameIndex,
      t_s: rec.t_s,
      status: 'free_fall',
      position_m: rec.position_m,
      quaternion_wxyz: [rec.quaternion_xyzw[3], rec.quaternion_xyzw[0], rec.quaternion_xyzw[1], rec.quaternion_xyzw[2]],
      velocity_m_s: rec.velocity_m_s,
      angular_velocity_rad_s: rec.angular_velocity_rad_s,
      acceleration_g: accelG,
      energy_j: {
        kinetic_trans: audit.kinetic_trans_j,
        kinetic_rot: audit.kinetic_rot_j,
        potential: audit.potential_j,
        total: audit.total_j,
        dissipated: audit.dissipated_j,
      },
      contact: rec.in_contact_window ? { active: true, point_m: rec.position_m, normal_force_n: undefined, penetration_depth_m: undefined } : { active: false },
      fea: feaField,
    };
    const events = this.detector.push(frame, audit, rec.in_contact_window, rec.settled);
    this.ring.push(frame);
    this.events.push(...events);
    this.frameIndex += 1;
    this.lastT = rec.t_s;
    return events;
  }

  private lastT = 0;

  get lastAccelerationG(): number {
    return this.auditor.lastAccelerationG;
  }

  get frameCount(): number {
    return this.ring.length;
  }

  get eventsList(): TelemetryEvent[] {
    return this.events;
  }

  frameAt(i: number): TelemetryFrame | undefined {
    return this.ring.at(i);
  }

  frames(): TelemetryFrame[] {
    return this.ring.toArray();
  }

  snapshot(): CollectorSnapshot {
    return { frames: this.ring.toArray(), events: this.events.slice() };
  }

  clear(): void {
    this.ring.clear();
    this.events.length = 0;
    this.frameIndex = 0;
    this.lastT = 0;
  }
}
