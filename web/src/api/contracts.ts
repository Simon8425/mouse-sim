export const SCHEMA_IDS = {
  WEB_HEALTH: 'gms.web-health/1',
  WEB_ERROR: 'gms.web-error/1',
  WEB_BASELINE: 'gms.web-baseline/1',
  WEB_MATERIAL_CATALOG: 'gms.web-material-catalog/1',
  GEOMETRY_PREVIEW: 'gms.geometry-preview/1',
  WEB_ANALYSIS_REQUEST: 'gms.web-analysis-request/1',
  WEB_ANALYSIS_RESPONSE: 'gms.web-analysis-response/1',
  PROJECT: 'gms.project/1',
  PROJECT_DOCUMENT: 'gms.project-document',
  PIPELINE_RESULT: 'gms.pipeline-result/1',
} as const;

export type Vec3 = [number, number, number];

export type Mat3Rows = [Vec3, Vec3, Vec3];

export interface RigidTransformJson {
  rotation: Mat3Rows;
  translation: Vec3;
  units: string;
}

export const GEOMETRY_TYPES = ['box', 'sphere', 'cylinder', 'cone', 'frustum', 'mesh', 'compound'] as const;

export type GeometryType = (typeof GEOMETRY_TYPES)[number];

export interface BoxGeometryJson {
  type: 'box';
  size: Vec3;
  units: string;
  transform: RigidTransformJson;
}

export interface SphereGeometryJson {
  type: 'sphere';
  radius: number;
  units: string;
  transform: RigidTransformJson;
}

export interface CylinderGeometryJson {
  type: 'cylinder';
  radius: number;
  height: number;
  units: string;
  transform: RigidTransformJson;
}

export interface ConeGeometryJson {
  type: 'cone';
  base_radius: number;
  height: number;
  units: string;
  transform: RigidTransformJson;
}

export interface FrustumGeometryJson {
  type: 'frustum';
  bottom_radius: number;
  top_radius: number;
  height: number;
  units: string;
  transform: RigidTransformJson;
}

export interface MeshGeometryJson {
  type: 'mesh';
  vertices: number[][];
  triangles: number[][];
  units: string;
  transform: RigidTransformJson;
}

export interface CompoundGeometryJson {
  type: 'compound';
  children: GeometryJson[];
  transform: RigidTransformJson;
}

export type GeometryJson =
  | BoxGeometryJson
  | SphereGeometryJson
  | CylinderGeometryJson
  | ConeGeometryJson
  | FrustumGeometryJson
  | MeshGeometryJson
  | CompoundGeometryJson;

export interface ImportDiagnostic {
  code: string;
  severity: string;
  message: string;
  details: Record<string, unknown>;
}

export interface WebHealth {
  schema_id: 'gms.web-health/1';
  engine_version: string;
  api_version: string;
  supported_formats: string[];
  solver_capabilities: string[];
  cache_active: boolean;
  max_json_bytes: number;
  max_geometry_bytes: number;
  deterministic: boolean;
  step_backend?: string;
  step_kernel_backend?: string;
  step_kernel_available?: boolean;
  advanced_step_backend?: string;
  advanced_step_uses_kernel?: boolean;
}

export interface WebErrorEnvelope {
  schema_id: 'gms.web-error/1';
  status: number;
  error: {
    code: string;
    severity: string;
    phase: string;
    message: string;
    details?: Record<string, unknown>;
  };
}

export interface WebBaselineResponse {
  schema_id: 'gms.web-baseline/1';
  source: string;
  project: PipelineRequest;
}

export interface MaterialEntry {
  key: string;
  name: string;
  family: string | null;
  density_kg_m3: number | null;
  young_modulus_pa: number | null;
  approval_state: string;
  confidence: string;
  source_type: string;
}

export interface WebMaterialCatalog {
  schema_id: 'gms.web-material-catalog/1';
  catalog_source: string;
  materials: MaterialEntry[];
}

export interface GeometryPreview {
  schema_id: 'gms.geometry-preview/1';
  supported: boolean;
  format: string;
  source_units: string | null;
  geometry: GeometryJson | null;
  diagnostics: ImportDiagnostic[];
  source_name: string | null;
  display_asset?: DisplayAsset | null;
}

export interface DisplayAsset {
  asset_id: string;
  url: string;
  format: 'glb';
  sha256?: string;
  bytes?: number;
  object_count?: number;
  triangle_count?: number;
  backend?: string;
  tessellation_deflection_mm?: number;
  parts?: DisplayAssetPart[];
  parts_url?: string;
}

export interface DisplayAssetPart {
  id: string;
  name: string;
  color?: [number, number, number];
}

export interface AssetPartGeometry extends DisplayAssetPart {
  geometry: GeometryJson;
}

export interface AssetPartsResponse {
  parts: AssetPartGeometry[];
}

export interface ProjectObject {
  id?: string;
  name?: string;
  geometry?: GeometryJson | Record<string, unknown>;
  material?: string;
  mass_override?: number;
  measured_mass?: number;
  structural_behavior?: string;
  classification?: {
    component_type?: string;
    source?: string;
    confidence?: number | string;
  };
  [key: string]: unknown;
}

export interface PipelineRequest {
  schema_id?: string;
  mode?: string;
  units?: string;
  objects?: ProjectObject[] | Record<string, unknown>;
  materials?: unknown;
  load_case?: Record<string, unknown>;
  structure?: Record<string, unknown>;
  fixtures?: unknown;
  impact?: Record<string, unknown>;
  tolerance_profile?: Record<string, unknown>;
  options?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface WebAnalysisRequest {
  schema_id: 'gms.web-analysis-request/1';
  request: PipelineRequest;
  options?: {
    strict?: boolean;
    use_cache?: boolean;
  };
}

export interface WebAnalysisResponse {
  schema_id: 'gms.web-analysis-response/1';
  run_id: string;
  engine_version: string;
  result: PipelineResult;
  materials: MaterialEntry[];
}

export interface ValiditySummary {
  state: string;
  reasons: string[];
  assumptions: string[];
  unsupported_failure_modes: string[];
  confidence: string;
}

export interface Issue {
  code: string;
  severity: string;
  category: string;
  message: string;
  evidence_blocking: boolean;
}

export interface ErrorEntry {
  code: string;
  message: string;
  traceback?: string;
}

export interface GeometrySummaryObject {
  object_id: string;
  geometry_type: string | null;
  units: string;
  parsed: boolean;
  diagnostics: string[];
  material: string | null;
}

export interface GeometrySummary {
  objects: GeometrySummaryObject[];
  parse_errors: { object_id: string; message: string }[];
}

export interface MassObjectResult {
  object_id: string;
  mass_kg: number | null;
  mass_status: string;
  volume_m3: number | null;
  center_of_mass_m: Vec3 | null;
  inertia_tensor_kg_m2: number[][] | null;
  uncertainty_kg: number | null;
  completeness: number;
  diagnostics: string[];
  source_status: string;
  derived_status: string;
  review_status: string;
}

export interface MassResult {
  mass_kg: number | null;
  mass_status: string;
  center_of_mass_m: Vec3 | null;
  inertia_tensor_kg_m2: number[][] | null;
  uncertainty_kg: number | null;
  completeness: number;
  objects: MassObjectResult[];
  diagnostics: string[];
  source_status: string;
  derived_status: string;
  review_status: string;
}

export interface ValidationFinding {
  code: string;
  severity: string;
  state: string;
  category: string;
  message: string;
  affected_ids: string[];
  phase: string;
  evidence_blocking: boolean;
}

export interface ValidationReport {
  status: string;
  validity_state: string;
  findings: ValidationFinding[];
}

export interface PreflightFinding {
  code: string;
  severity: string;
  message: string;
  [key: string]: unknown;
}

export interface StructuralResponse {
  method_id: string;
  max_displacement_m: number | null;
  max_displacement_location: Vec3 | null;
  max_stress_pa: number | null;
  max_stress_filtered_pa: number | null;
  filtered_location: Vec3 | null;
  safety_factor: number | null;
  safety_factor_status: string;
  reactions: Record<string, number>;
  force_residual_n: number | null;
  moment_residual_n_m: number | null;
  flags: string[];
  assumptions: string[];
  unsupported_failure_modes: string[];
  validity: string;
}

export interface StructuralSection {
  load_case: Record<string, unknown>;
  structure: Record<string, unknown>;
  material: string | null;
  fixtures: unknown;
  preflight: PreflightFinding[];
  response: StructuralResponse;
}

export interface ImpactEstimate {
  impact_energy_j: number;
  closing_velocity_m_s: number;
  effective_mass_kg: number;
  impulse_n_s: number;
  peak_force_n: number;
  peak_acceleration_m_s2: number;
  contact_duration_s: number;
  contact_compression_m: number;
  method_id: string;
  flags: string[];
  assumptions: string[];
  unsupported_failure_modes: string[];
  validity: string;
  load_path_stress_pa: number | null;
  safety_factor: number | string;
  qualification_blocked: boolean;
  contact_model?: string;
  peak_force_estimate_n?: number | null;
}

export interface ImpactSection {
  mass_kg: number | null;
  result: ImpactEstimate | null;
  reason: string | null;
  unsupported_failure_modes: string[];
}

export interface QualificationGate {
  key: string;
  label: string;
  passed: boolean;
  evaluable: boolean;
  blocker: boolean;
  explanation: string;
}

export interface QualificationResult {
  mode: string;
  qualified: boolean;
  evidence_disposition: string;
  gates: QualificationGate[];
  integrity_gates?: QualificationGate[];
  blocking_keys: string[];
  summary: string;
}

export interface PipelineResult {
  schema_id: string;
  engine_version: string;
  run_id: string;
  mode: string;
  lifecycle_state: string;
  validity: ValiditySummary;
  issues: Issue[];
  geometry_summary: GeometrySummary;
  mass: MassResult | null;
  validation: ValidationReport | null;
  structural: StructuralSection | null;
  impact: ImpactSection | null;
  qualification: QualificationResult | null;
  manifest: Record<string, unknown> | null;
  errors: ErrorEntry[];
}

export const IDENTITY_TRANSFORM: RigidTransformJson = {
  rotation: [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ],
  translation: [0, 0, 0],
  units: 'm',
};

/**
 * Guard: true when value is a finite number.
 */
export function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/**
 * Guard: true when value is a plain (non-null, non-array) object.
 */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isVec3(value: unknown): value is Vec3 {
  if (!Array.isArray(value) || value.length !== 3) return false;
  return value.every(isFiniteNumber);
}

/**
 * Guard: true when value is a RigidTransformJson with a 3x3 rotation matrix
 * of finite numbers, a 3-element finite translation vector, and a string units.
 */
export function isRigidTransform(value: unknown): value is RigidTransformJson {
  if (!isRecord(value)) return false;
  if (typeof value.units !== 'string') return false;
  if (!Array.isArray(value.rotation) || value.rotation.length !== 3) return false;
  if (!Array.isArray(value.translation) || value.translation.length !== 3) return false;
  for (let i = 0; i < 3; i++) {
    const row = value.rotation[i];
    if (!Array.isArray(row) || row.length !== 3) return false;
    for (let j = 0; j < 3; j++) {
      if (!isFiniteNumber(row[j])) return false;
    }
  }
  for (let i = 0; i < 3; i++) {
    if (!isFiniteNumber(value.translation[i])) return false;
  }
  return true;
}

/**
 * Guard: true when value is a GeometryJson — a record whose type is one of
 * GEOMETRY_TYPES, with a valid transform, string units (where the type
 * declares one), and per-type fields validated: box size is a Vec3; sphere
 * radius finite > 0; cylinder/cone radius and height finite > 0; frustum
 * bottom/top radius and height finite > 0; mesh vertices are Vec3s and
 * triangles are arrays of 3 non-negative integers; compound children are all
 * valid GeometryJson values.
 */
export function isGeometryJson(value: unknown): value is GeometryJson {
  if (!isRecord(value)) return false;
  const type = value.type;
  if (typeof type !== 'string' || !(GEOMETRY_TYPES as readonly string[]).includes(type)) return false;
  if (value.transform !== undefined && !isRigidTransform(value.transform)) return false;
  if (value.units !== undefined && typeof value.units !== 'string') return false;

  switch (type) {
    case 'box':
      return isVec3(value.size);
    case 'sphere':
      return isFiniteNumber(value.radius) && value.radius > 0;
    case 'cylinder':
      return isFiniteNumber(value.radius) && value.radius > 0 && isFiniteNumber(value.height) && value.height > 0;
    case 'cone':
      return isFiniteNumber(value.base_radius) && value.base_radius > 0 && isFiniteNumber(value.height) && value.height > 0;
    case 'frustum':
      return (
        isFiniteNumber(value.bottom_radius) &&
        value.bottom_radius > 0 &&
        isFiniteNumber(value.top_radius) &&
        value.top_radius > 0 &&
        isFiniteNumber(value.height) &&
        value.height > 0
      );
    case 'mesh':
      if (!Array.isArray(value.vertices) || !Array.isArray(value.triangles)) return false;
      for (const vertex of value.vertices) {
        if (!isVec3(vertex)) return false;
      }
      for (const triangle of value.triangles) {
        if (!Array.isArray(triangle) || triangle.length !== 3) return false;
        for (const idx of triangle) {
          if (typeof idx !== 'number' || !Number.isInteger(idx) || idx < 0) return false;
        }
      }
      return true;
    case 'compound':
      return Array.isArray(value.children) && value.children.every(isGeometryJson);
    default:
      return false;
  }
}

/**
 * Guard: true when value is a PipelineResult — schema_id 'gms.pipeline-result/1',
 * string lifecycle_state, record validity and geometry_summary, arrays of
 * issues and errors, and a manifest that is either null or a record.
 */
export function isPipelineResult(value: unknown): value is PipelineResult {
  if (!isRecord(value)) return false;
  if (value.schema_id !== SCHEMA_IDS.PIPELINE_RESULT) return false;
  if (typeof value.lifecycle_state !== 'string') return false;
  if (!isRecord(value.validity)) return false;
  if (!isRecord(value.geometry_summary)) return false;
  if (!Array.isArray(value.issues)) return false;
  if (!Array.isArray(value.errors)) return false;
  if (value.manifest !== null && !isRecord(value.manifest)) return false;
  return true;
}
