/**
 * Telemetry Log Debugger — session assembly.
 *
 * Builds a TelemetryLogSession from the analysis result (model specs, floor
 * spec, drop config, FEA snapshot) plus the live frame history. Material
 * elastic constants and floor constants are taken from the reference table
 * below (the backend payload carries only the surface key, restitution and
 * friction); measured summary quantities are computed from the stream.
 */
import type {
  DropSimulationResult,
} from '../api/contracts';
import type {
  PhysicsFloorSpec,
  PhysicsModelSpec,
  TelemetryFrame,
  TelemetryLogSession,
} from '../api/telemetryDebuggerContracts';
import { norm3, RingBuffer } from './telemetryDebugger';

/** Reference material/floor constants (typical engineering values). */
export const MATERIAL_REFERENCE: Record<string, { name: string; density_kg_m3: number; young_modulus_pa: number; poissons_ratio: number; yield_strength_pa: number; ultimate_strength_pa: number }> = {
  abs: { name: 'ABS', density_kg_m3: 1050, young_modulus_pa: 2.3e9, poissons_ratio: 0.35, yield_strength_pa: 45e6, ultimate_strength_pa: 40e6 },
  pc: { name: 'Polycarbonate', density_kg_m3: 1200, young_modulus_pa: 2.4e9, poissons_ratio: 0.37, yield_strength_pa: 62e6, ultimate_strength_pa: 70e6 },
  pom: { name: 'POM (Acetal)', density_kg_m3: 1410, young_modulus_pa: 2.8e9, poissons_ratio: 0.35, yield_strength_pa: 60e6, ultimate_strength_pa: 68e6 },
  pa12: { name: 'PA12 Nylon', density_kg_m3: 1010, young_modulus_pa: 1.85e9, poissons_ratio: 0.35, yield_strength_pa: 45e6, ultimate_strength_pa: 50e6 },
  aluminum: { name: 'Aluminum', density_kg_m3: 2700, young_modulus_pa: 69e9, poissons_ratio: 0.33, yield_strength_pa: 276e6, ultimate_strength_pa: 310e6 },
  steel: { name: 'Steel', density_kg_m3: 7850, young_modulus_pa: 200e9, poissons_ratio: 0.3, yield_strength_pa: 250e6, ultimate_strength_pa: 460e6 },
};

export const FLOOR_REFERENCE: Record<string, { name: string; young_modulus_pa: number; poissons_ratio: number }> = {
  concrete: { name: 'Concrete', young_modulus_pa: 30e9, poissons_ratio: 0.2 },
  wood: { name: 'Wood', young_modulus_pa: 11e9, poissons_ratio: 0.3 },
  foam: { name: 'Foam', young_modulus_pa: 3e6, poissons_ratio: 0.1 },
  steel: { name: 'Steel', young_modulus_pa: 200e9, poissons_ratio: 0.3 },
};

/** Hertzian reduced contact modulus E*. */
export function effectiveContactModulus(
  eModelPa: number,
  nuModel: number,
  eSurfacePa: number,
  nuSurface: number,
): number {
  const a = (1 - nuModel * nuModel) / Math.max(1, eModelPa);
  const b = (1 - nuSurface * nuSurface) / Math.max(1, eSurfacePa);
  return 1 / (a + b);
}

/** AABB envelope of a mesh geometry (metres). */
export function meshBounds(
  vertices: number[][],
): { min: [number, number, number]; max: [number, number, number] } {
  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
  for (const v of vertices) {
    if (v[0] < minX) minX = v[0];
    if (v[1] < minY) minY = v[1];
    if (v[2] < minZ) minZ = v[2];
    if (v[0] > maxX) maxX = v[0];
    if (v[1] > maxY) maxY = v[1];
    if (v[2] > maxZ) maxZ = v[2];
  }
  if (!Number.isFinite(minX) || !Number.isFinite(maxX)) return { min: [0, 0, 0], max: [0, 0, 0] };
  return { min: [minX, minY, minZ], max: [maxX, maxY, maxZ] };
}

/** Surface area and enclosed volume of a triangle mesh. */
export function meshAreaAndVolume(
  vertices: number[][],
  triangles: number[][],
): { area_m2: number; volume_cm3: number } {
  let area = 0;
  let volume = 0;
  for (const tri of triangles) {
    if (tri.length < 3) continue;
    const [a, b, c] = [vertices[tri[0]], vertices[tri[1]], vertices[tri[2]]];
    if (!a || !b || !c) continue;
    const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
    const vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
    const cx = uy * vz - uz * vy;
    const cy = uz * vx - ux * vz;
    const cz = ux * vy - uy * vx;
    area += 0.5 * Math.sqrt(cx * cx + cy * cy + cz * cz);
    // Signed tetrahedron volume wrt origin; absolute sum gives volume for a
    // watertight, consistently wound mesh.
    volume += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6;
  }
  return { area_m2: area, volume_cm3: Math.abs(volume) * 1e6 };
}

/** Principal moments (diagonal of a near-diagonal tensor). */
export function principalMoments(inertia: number[][]): [number, number, number] {
  const ixx = inertia[0]?.[0] ?? 0;
  const iyy = inertia[1]?.[1] ?? 0;
  const izz = inertia[2]?.[2] ?? 0;
  return [Math.abs(ixx), Math.abs(iyy), Math.abs(izz)] as [number, number, number];
}

/** Build the static model spec from a drop simulation result. */
export function buildModelSpec(result: DropSimulationResult): PhysicsModelSpec {
  const model = result.model;
  const modelName = (model as unknown as { name?: string }).name || result.config?.test || 'G3 Mouse Model';
  const rawName = modelName.toLowerCase();
  let mat = MATERIAL_REFERENCE.abs;
  for (const [k, v] of Object.entries(MATERIAL_REFERENCE)) {
    if (rawName.includes(k) || rawName.includes(v.name.toLowerCase())) {
      mat = v;
      break;
    }
  }
  const dims = { x: 0.125, y: 0.065, z: 0.040 }; // standard mouse envelope (125 x 65 x 40 mm)
  return {
    name: modelName,
    mass_kg: model.mass_kg ?? 0.1,
    com_offset_m: (model.com_offset_m ? [model.com_offset_m[0], model.com_offset_m[1], model.com_offset_m[2]] : [0, 0, 0]) as [number, number, number],
    inertia_tensor_kg_m2: model.inertia_kg_m2 ?? [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
    dimensions_m: dims,
    material: {
      name: mat.name,
      density_kg_m3: mat.density_kg_m3,
      young_modulus_pa: mat.young_modulus_pa,
      poissons_ratio: mat.poissons_ratio,
      yield_strength_pa: mat.yield_strength_pa,
      ultimate_strength_pa: mat.ultimate_strength_pa,
      friction_coefficient: model.friction ?? 0.60,
    },
  };
}

/** Build the floor spec from the result's surface key + measured restitution. */
export function buildFloorSpec(result: DropSimulationResult): PhysicsFloorSpec {
  const model = result.model;
  const key = String(model.surface ?? 'concrete').toLowerCase();
  const ref = FLOOR_REFERENCE[key] ?? FLOOR_REFERENCE.concrete;
  const mat = MATERIAL_REFERENCE.abs;
  const restitution = model.restitution ?? 0.35;
  const friction = model.friction ?? 0.60;
  const eModel = mat.young_modulus_pa;
  const nuModel = mat.poissons_ratio;
  const eSurface = ref.young_modulus_pa;
  const nuSurface = ref.poissons_ratio;
  return {
    surface_id: (key as PhysicsFloorSpec['surface_id']) ?? 'concrete',
    young_modulus_pa: eSurface,
    poissons_ratio: nuSurface,
    restitution,
    friction_static: friction,
    friction_dynamic: friction * 0.8,
    effective_modulus_pa: effectiveContactModulus(eModel, nuModel, eSurface, nuSurface),
  };
}

/** Compute summary statistics from the recorded frame history. */
export function summarizeFrames(
  frames: TelemetryFrame[],
  releaseEnergyJ: number,
): {
  duration_s: number;
  total_frames: number;
  peak_g_force: number;
  peak_stress_mpa: number;
  min_safety_factor: number;
  rebound_count: number;
  restitution_measured: number;
  energy_drift_max_pct: number;
} {
  let peakG = 0;
  let peakStressMpa = 0;
  let minSf = Infinity;
  let maxDrift = 0;
  let reboundCount = 0;
  let lastStatus: string | undefined;
  let apexEnergy = 0;
  for (const f of frames) {
    if (f.acceleration_g > peakG) peakG = f.acceleration_g;
    const fea = f.fea;
    if (fea) {
      if (fea.peak_stress_pa / 1e6 > peakStressMpa) peakStressMpa = fea.peak_stress_pa / 1e6;
      if (fea.safety_factor < minSf) minSf = fea.safety_factor;
    }
    // Drift is tracked by the auditor (max over the stream); the frame energy
    // block does not carry drift, so derive it from the dissipated/release
    // ratio for the export summary.
    const drift = f.energy_j.dissipated > 0 && releaseEnergyJ > 0
      ? (f.energy_j.dissipated / releaseEnergyJ) * 100
      : 0;
    if (drift > maxDrift) maxDrift = drift;
    if (f.status === 'rebound' && lastStatus !== 'rebound') {
      reboundCount += 1;
      apexEnergy = f.energy_j.total;
    }
    lastStatus = f.status;
  }
  const restitution = releaseEnergyJ > 0 ? Math.sqrt(Math.max(0, apexEnergy / releaseEnergyJ)) : 0;
  return {
    duration_s: frames.length ? frames[frames.length - 1].t_s - frames[0].t_s : 0,
    total_frames: frames.length,
    peak_g_force: peakG,
    peak_stress_mpa: peakStressMpa,
    min_safety_factor: Number.isFinite(minSf) ? minSf : 0,
    rebound_count: reboundCount,
    restitution_measured: restitution,
    energy_drift_max_pct: maxDrift,
  };
}

/** Build a full TelemetryLogSession from a result + recorded frames. */
export function buildTelemetrySession(
  result: DropSimulationResult,
  frames: TelemetryFrame[],
): TelemetryLogSession {
  const model = buildModelSpec(result);
  const floor = buildFloorSpec(result);
  const config = result.config;
  const drop0 = result.drops?.[0];
  const gravity = model && result.model.gravity_m_s2 > 0 ? result.model.gravity_m_s2 : 9.80665;
  const releaseJ = drop0?.energy?.release_j ?? 0.5 * model.mass_kg * gravity * (config?.height_m ?? 0.75);
  const summary = summarizeFrames(frames, releaseJ);
  return {
    session_id: `telemetry-${Date.now()}`,
    timestamp: new Date().toISOString(),
    model,
    floor,
    drop_config: {
      height_m: config?.height_m ?? 0.75,
      orientation: drop0?.orientation ?? config?.orientation ?? 'flat',
      initial_spin_rps: config?.spin_rps ?? 0,
      gravity_m_s2: gravity,
    },
    summary,
    frames: frames.slice(),
  };
}

/** Flatten one frame to CSV columns (matching the table). */
export function frameToCsvRow(f: TelemetryFrame): (string | number)[] {
  return [
    f.index,
    f.t_s.toFixed(6),
    f.status,
    f.position_m[2] * 1000,
    f.velocity_m_s[0],
    f.velocity_m_s[1],
    f.velocity_m_s[2],
    norm3(f.velocity_m_s),
    f.acceleration_g,
    f.angular_velocity_rad_s[0],
    f.angular_velocity_rad_s[1],
    f.angular_velocity_rad_s[2],
    f.quaternion_wxyz[0],
    f.quaternion_wxyz[1],
    f.quaternion_wxyz[2],
    f.quaternion_wxyz[3],
    f.energy_j.kinetic_trans * 1000,
    f.energy_j.kinetic_rot * 1000,
    f.energy_j.potential * 1000,
    f.energy_j.total * 1000,
    f.energy_j.dissipated * 1000,
    // Drift is tracked per-frame by the auditor; the frame block carries only
    // the partition, so export the dissipated/release ratio as the drift proxy.
    f.energy_j.dissipated > 0 ? (f.energy_j.dissipated / Math.max(1e-9, f.energy_j.total + f.energy_j.dissipated)) * 100 : 0,
    f.fea ? f.fea.peak_stress_pa / 1e6 : '',
    f.fea ? f.fea.safety_factor : '',
  ];
}

export const TELEMETRY_CSV_HEADERS = [
  'index', 't_s', 'status', 'z_mm', 'vx', 'vy', 'vz', 'speed', 'g_load',
  'wx', 'wy', 'wz', 'qw', 'qx', 'qy', 'qz',
  'ke_trans_mj', 'ke_rot_mj', 'pe_mj', 'total_mj', 'dissipated_mj', 'drift_pct',
  'stress_mpa', 'safety_factor',
];

export function framesToCsv(frames: TelemetryFrame[]): string {
  const lines = [TELEMETRY_CSV_HEADERS.join(',')];
  for (const f of frames) {
    lines.push(frameToCsvRow(f).map((v) => (typeof v === 'number' ? (Number.isFinite(v) ? String(v) : '') : v)).join(','));
  }
  return lines.join('\n');
}

/** Full JSON export payload (session + frames). */
export function sessionToJson(session: TelemetryLogSession): string {
  return JSON.stringify(session, null, 2);
}

/** Ring-buffer-backed frame history with capacity management. */
export class FrameHistory {
  private ring: RingBuffer<TelemetryFrame>;

  constructor(capacity = 4096) {
    this.ring = new RingBuffer<TelemetryFrame>(capacity);
  }

  push(frame: TelemetryFrame): void {
    this.ring.push(frame);
  }

  get length(): number {
    return this.ring.length;
  }

  at(i: number): TelemetryFrame | undefined {
    return this.ring.at(i);
  }

  toArray(): TelemetryFrame[] {
    return this.ring.toArray();
  }

  clear(): void {
    this.ring.clear();
  }
}
