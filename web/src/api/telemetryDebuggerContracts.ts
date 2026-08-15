/**
 * Telemetry Log Debugger — contracts.
 *
 * The debugger records, inspects and exports the live Rapier drop simulation
 * plus the static analysis payload (model specs, floor spec, drop config,
 * FEA snapshot). Live telemetry is appended to a bounded ring buffer in the
 * scene runtime and exported on demand; nothing here touches the physics
 * integrator or the backend engine.
 */

/** Model & assembly specifications (static, from the analysis result). */
export interface PhysicsModelSpec {
  name: string;
  mass_kg: number;
  com_offset_m: [number, number, number];
  inertia_tensor_kg_m2: number[][];
  /** Axis-aligned bounding-box extents in model frame, metres. */
  dimensions_m: { x: number; y: number; z: number };
  material: {
    name: string;
    density_kg_m3: number;
    young_modulus_pa: number;
    poissons_ratio: number;
    yield_strength_pa: number;
    ultimate_strength_pa: number;
    friction_coefficient: number;
  };
}

/** Target floor/surface specification (static). */
export interface PhysicsFloorSpec {
  surface_id: 'concrete' | 'wood' | 'foam' | 'steel';
  young_modulus_pa: number;
  poissons_ratio: number;
  restitution: number;
  friction_static: number;
  friction_dynamic: number;
  /** Hertzian reduced contact modulus E* (see spec docstring). */
  effective_modulus_pa: number;
}

/** One recorded substep of the live simulation. */
export interface TelemetryFrame {
  index: number;
  t_s: number;
  status: 'free_fall' | 'impact' | 'rebound' | 'rolling' | 'settled';
  position_m: [number, number, number];
  quaternion_wxyz: [number, number, number, number];
  velocity_m_s: [number, number, number];
  angular_velocity_rad_s: [number, number, number];
  acceleration_g: number;
  energy_j: {
    kinetic_trans: number;
    kinetic_rot: number;
    potential: number;
    total: number;
    dissipated: number;
  };
  contact: {
    active: boolean;
    point_m?: [number, number, number];
    normal_force_n?: number;
    penetration_depth_m?: number;
  };
  fea?: {
    peak_stress_pa: number;
    safety_factor: number;
    damage: number;
  };
}

/** Full debugger session: metadata + specs + bounded frame history. */
export interface TelemetryLogSession {
  session_id: string;
  timestamp: string;
  model: PhysicsModelSpec;
  floor: PhysicsFloorSpec;
  drop_config: {
    height_m: number;
    orientation: string;
    initial_spin_rps: number;
    gravity_m_s2: number;
  };
  summary: {
    duration_s: number;
    total_frames: number;
    peak_g_force: number;
    peak_stress_mpa: number;
    min_safety_factor: number;
    rebound_count: number;
    restitution_measured: number;
    energy_drift_max_pct: number;
  };
  frames: TelemetryFrame[];
}

/** Diagnostic events detected from the telemetry stream. */
export type TelemetryEventLevel = 'INFO' | 'EVENT' | 'WARNING' | 'ANOMALY';

export interface TelemetryEvent {
  level: TelemetryEventLevel;
  code: string;
  t_s: number;
  message: string;
}

/** Drop status classifier, mirroring the lifecycle states. */
export type DropStatus = 'free_fall' | 'impact' | 'rebound' | 'rolling' | 'settled';

export interface ImpactDetection {
  t_s: number;
  velocity_m_s: [number, number, number];
  contact_point_m?: [number, number, number];
  peak_force_n?: number;
}

export interface ReboundApex {
  t_s: number;
  height_m: number;
  energy_retention_pct: number;
}
