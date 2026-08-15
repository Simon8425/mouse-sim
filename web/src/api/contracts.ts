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
  classification?: AiClassification;
  [key: string]: unknown;
}

/**
 * AI component-type classification (heuristic, OpenRouter vision, or user).
 * component_type uses the canonical taxonomy (COMPONENT_ROLES values or
 * 'unresolved').
 */
export interface AiClassification {
  object_id?: string;
  component_type?: string;
  source?: 'heuristic' | 'openrouter_vision' | 'user' | 'imported' | string;
  confidence?: number;
  reasons?: string[];
  needs_review?: boolean;
  cached?: boolean;
}

export type DropTestKind = 'drop' | 'impact' | 'tumble' | 'population';
export type DropSurface = 'concrete' | 'wood' | 'foam' | 'steel';
export type DropOrientation = 'flat' | 'edge' | 'corner' | 'random';

/** Orientation mode string, or an explicit unit quaternion object [w, x, y, z]. */
export type ValidationOrientation = DropOrientation | { quaternion_wxyz: [number, number, number, number] };

/** Surface table key, or an explicit {type, definition} override for a measured test. */
export interface ValidationMeasuredSurface {
  type: string;
  definition?: {
    thickness_m?: number;
    hardness?: number | string;
    mounting?: string;
    notes?: string;
  };
}

/** Sensor definition of a measured test (exact measurement definition). */
export interface ValidationSensor {
  model?: string;
  location_body_m?: Vec3;
  sampling_rate_hz?: number;
  filter?: string;
  sync?: string;
  quantity?: 'resultant_peak_g' | 'axis_peak_g';
  axis?: 'x' | 'y' | 'z' | '-x' | '-y' | '-z';
  notes?: string;
}

/** One physical instrumented drop test recorded for comparison. */
export interface ValidationMeasuredTest {
  test_id: string;
  cad_revision?: string;
  material?: string;
  prototype_id?: string;
  height_m: number;
  surface: string | ValidationMeasuredSurface;
  orientation: ValidationOrientation;
  environment?: Record<string, unknown>;
  sensor?: ValidationSensor;
  measured_peak_accel_g?: number;
  measured_impact_duration_s?: number;
  measured_settle_s?: number;
  measured_peak_accel_g_uncertainty?: number;
  measured_impact_duration_s_uncertainty?: number;
  measured_settle_s_uncertainty?: number;
}

/** Measured prototype pin: absolute overrides for mass/CoM/inertia. */
export interface ValidationPrototype {
  mass_kg?: number;
  com_m?: Vec3;
  inertia_kg_m2?: Mat3Rows;
  thickness_m?: number;
  material?: string;
  cad_revision?: string;
}

/**
 * The pinned validation section (request key "validation"). Makes the shell
 * chain SELF-CONTAINED: geometry, material, drop, contact and structural
 * configuration are pinned explicitly; nothing is silently inherited.
 * Mirrors mouse_sim/shell_validation.py exactly.
 */
export interface ValidationSection {
  geometry: {
    revision: string;
    units?: string;
    quality?: string;
  };
  material: string;
  prototype?: ValidationPrototype;
  drop: {
    height_m: number;
    surface: DropSurface;
    orientation: ValidationOrientation;
    gravity_m_s2?: number;
    mass_scale?: number;
    inertia_scale?: number;
    com_override_m?: Vec3;
  };
  contact: {
    stiffness_n_per_m: number;
    restitution?: number;
    friction?: number;
    timestep_s?: number;
    substeps?: number;
  };
  structural: {
    model: string;
    boundary_assumptions?: string;
    supported_validity?: string;
  };
  contact_stiffness_sweep_n_per_m?: number[];
  sensitivity?: {
    perturbation_fraction?: number;
    parameters?: string[];
  };
  measured_tests?: ValidationMeasuredTest[];
}

export interface DropSimulationConfig {
  test: DropTestKind;
  height_m: number;
  surface: DropSurface;
  drop_count: number;
  orientation: DropOrientation;
  spin_rps?: number;
  mass_kg?: number | null;
  seed?: number | null;
  pause_between_drops_s?: number;
  drop_interval_s?: number;
}

export interface DropSimulationDrop {
  index: number;
  start_s: number;
  end_s: number;
  settled_s: number;
  settled?: boolean;
  impact_count: number;
  peak_impact_speed_m_s: number;
  peak_kinetic_energy_j: number;
  peak_raw_kinetic_energy_j?: number;
  orientation: DropOrientation;
  orientation_quaternion_wxyz?: number[];
  gravity_vector_body?: number[];
  /** Initial angular velocity in the body-fixed principal frame [rad/s]. */
  initial_angular_velocity_rad_s?: number[];
  initial_velocity_m_s?: number[];
  starting_pose_m?: number[];
  energy?: {
    release_j: number | null;
    first_impact_j: number | null;
    settled_j: number | null;
    lost_contact_j: number;
    lost_drag_j: number;
    drift_pct: number;
  };
  checks?: DropSimulationCheck[];
}

export interface DropSimulationCheck {
  code: string;
  severity: 'error' | 'warning';
  message: string;
}

export interface DropSimulationImpact {
  drop: number;
  t_s: number;
  impact_speed_m_s: number;
  kinetic_energy_j: number;
  raw_kinetic_energy_j?: number;
  contact_location?: number[];
  contact_normal?: number[];
  contact_point_speed?: number;
  tangent_speed?: number;
  incidence_angle_deg?: number;
  manifold_size?: number;
}

export type DropTrajectorySample = [
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
];

/**
 * Inputs used by the drop-derived linear-spring quasi-static peak force
 * estimate attached to the drop simulation result.
 */
export interface DropPeakForceEstimate {
  mass_kg?: number;
  restitution?: number;
  energy_j?: number;
  impact_speed_m_s?: number;
  contact_stiffness_n_per_m?: number;
  model?: string;
}

export interface DropSimulationResult {
  config: DropSimulationConfig;
  model: {
    mass_kg: number;
    inertia_kg_m2: number[][];
    support_model: string;
    support_point_count: number;
    integrator: string;
    timestep_s: number;
    gravity_m_s2: number;
    surface: DropSurface;
    restitution?: number;
    friction?: number;
    com_offset_m?: number[];
    orientation_quaternion_wxyz?: number[];
    gravity_vector_body?: number[];
    /** Initial angular velocity in the body-fixed principal frame [rad/s]. */
    initial_angular_velocity_rad_s?: number[];
    initial_velocity_m_s?: number[];
    starting_pose_m?: number[];
    variation?: {
      unit_seed: number | null;
      mass_scale: number;
      inertia_scale: number[];
      com_offset_m: number[];
      friction_scale: number;
      restitution_scale: number;
    };
  };
  drops: DropSimulationDrop[];
  impacts: DropSimulationImpact[];
  checks?: DropSimulationCheck[];
  peak: DropSimulationImpact | null;
  peak_force_estimate_n: number | null;
  peak_force_estimate?: DropPeakForceEstimate | null;
  contact_stiffness_n_per_m?: number | null;
  trajectory: DropTrajectorySample[];
}

export interface PipelineRequest {
  schema_id?: string;
  mode?: string;
  units?: string;
  objects?: ProjectObject[] | Record<string, unknown>;
  /** Server-side reference for a normalized STEP asset; avoids resending its mesh. */
  geometry_asset_id?: string;
  materials?: unknown;
  default_material?: string;
  load_case?: Record<string, unknown> | null;
  structure?: Record<string, unknown> | null;
  fixtures?: unknown;
  impact?: Record<string, unknown> | null;
  drop_simulation?: Partial<DropSimulationConfig> | null;
  tolerance_profile?: Record<string, unknown> | null;
  components?: unknown[];
  population?: Record<string, unknown> | null;
  validation?: ValidationSection | null;
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

/**
 * The material definition actually used by the structural solve, or null
 * when no material could be resolved.
 */
export interface ResolvedMaterial {
  name?: string;
  properties?: Record<string, unknown>;
  provenance?: Record<string, unknown> | null;
  approval_state?: string;
}

export interface StructuralSection {
  load_case: Record<string, unknown>;
  structure: Record<string, unknown>;
  material: string | null;
  fixtures: unknown;
  preflight: PreflightFinding[];
  response: StructuralResponse;
  resolved_material?: ResolvedMaterial | null;
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

/**
 * Cross-reference emitted when BOTH a user-supplied impact section and a drop
 * simulation ran: the drop-derived integrator estimate vs the standalone
 * quasi-static impact model.
 */
export interface ImpactCrossReference {
  drop_derived_peak_force_estimate_n?: number | null;
  drop_derived_energy_j?: number | null;
  note?: string;
}

export interface ImpactSection {
  source?: string;
  mass_kg: number | null;
  result: ImpactEstimate | null;
  reason: string | null;
  unsupported_failure_modes: string[];
  cross_reference?: ImpactCrossReference | null;
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

export interface MaterialAssignment {
  object_id: string;
  material: string;
  source: 'explicit' | 'default';
}

export type ComponentStatus = 'pass' | 'warn' | 'fail' | 'not_evaluated';

export interface ComponentFinding {
  code?: string;
  severity?: string;
  message?: string;
}

export interface ComponentAssessment {
  component_id?: string;
  type?: string;
  status?: ComponentStatus | string;
  validity?: string;
  metrics?: Record<string, number | string | null>;
  findings?: ComponentFinding[];
  assumptions?: string[];
  flags?: string[];
  usage_ratio?: number | null;
}

export interface ComponentWeakest {
  component_id?: string;
  type?: string;
  status?: ComponentStatus | string;
}

export interface ComponentResult {
  components?: ComponentAssessment[];
  summary?: {
    fail_count?: number;
    warn_count?: number;
    weakest?: ComponentWeakest | null;
  } | null;
}

export type ShellStatus = 'pass' | 'warn' | 'fail' | 'not_evaluated';
export type ShellClassification =
  | 'safe'
  | 'marginal'
  | 'failed'
  | 'unsupported'
  | 'invalid_input'
  | 'insufficient_evidence';
export type SensitivityLevel = 'HIGH' | 'MEDIUM' | 'LOW' | 'NOT_OBSERVED';

/** Drop-impact loading derived for the shell model. */
export interface ShellDropLoading {
  drop_peak_speed_m_s?: number | null;
  drop_peak_energy_j?: number | null;
  drop_peak_force_n?: number | null;
}

/** Probe stability of the identified critical region across FE solves. */
export interface ShellCriticalRegionStability {
  stable?: boolean;
  probe_solves?: number;
  max_location_shift_m?: number;
  tolerance_m?: number;
  statement?: string;
}

/** Statistical basis of the shell verdict. */
export interface ShellStatisticalConfidence {
  kind?: string;
}

/**
 * Physical-validation model status — four states (freeze-phase item 12):
 * unvalidated, partially_validated, correlated, outside_validated_domain.
 */
export type ShellModelStatus =
  | 'unvalidated'
  | 'partially_validated'
  | 'correlated'
  | 'outside_validated_domain'
  | string;

/** Physical-validation state accompanying the model status. */
export interface ShellPhysicalValidation {
  status?:
    | 'no_measured_tests'
    | 'compared_not_accepted'
    | 'insufficient_conditions'
    | 'validated'
    | 'outside_validated_domain'
    | string;
  independent_conditions?: number;
  compared_conditions?: number;
  validated_domain?: {
    conditions?: [number, string, string][];
    note?: string;
  };
  identity_checked?: boolean;
  note?: string;
}

/** One "what would invalidate this result?" entry. */
export interface ShellInvalidatingAssumption {
  assumption: string;
  status: string;
  impact: string;
}

/**
 * One authoritative record of every quantity used by the shell result.
 * Deep shapes are all optional so consumers can guard every read.
 */
export interface ShellInputsTrace {
  geometry?: {
    objects?: string[];
    geometry_digest?: string;
  };
  material?: {
    label?: string | null;
    properties?: Record<string, unknown>;
  };
  mass?: {
    mass_kg?: number | null;
    mass_status?: string;
    density_kg_m3?: number | null;
  };
  inertia?: {
    center_of_mass_m?: Vec3 | null;
    inertia_tensor_kg_m2?: number[][] | null;
  };
  drop?: {
    height_m?: number | null;
    gravity_m_s2?: number | null;
    orientation?: string | null;
    orientation_quaternion_wxyz?: number[] | null;
    initial_velocity_m_s?: number[] | null;
    /** Initial angular velocity in the body-fixed principal frame [rad/s]. */
    initial_angular_velocity_rad_s?: number[] | null;
    surface?: string | null;
    restitution?: number | null;
    friction?: number | null;
    timestep_s?: number | null;
    integrator?: string | null;
  };
  contact?: {
    contact_stiffness_n_per_m?: number | null;
    peak_force_estimate?: Record<string, unknown> | null;
  };
  structural?: {
    model?: string | null;
    load_case?: Record<string, unknown> | null;
    boundary_assumptions?: string;
    safety_factor_derivation?: string;
    safety_factor?: number | null;
  };
  engine?: {
    version?: string;
    engine_hash?: string;
  };
  seed?: number;
}

/** One row of the contact-stiffness sweep. */
export interface ShellContactStiffnessSweepRow {
  contact_stiffness_n_per_m?: number | null;
  peak_force_n?: number | null;
  peak_acceleration_m_s2?: number | null;
  impulse_n_s?: number | null;
  contact_duration_s?: number | null;
  contact_compression_m?: number | null;
  load_path_stress_pa?: number | null;
  safety_factor?: number | null;
}

export interface ShellContactStiffnessSweep {
  rows?: ShellContactStiffnessSweepRow[];
  note?: string;
}

/** Low/high/nominal of one quantity across the swept stiffness values. */
export interface ShellUncertaintyBand {
  low?: number | null;
  high?: number | null;
  nominal?: number | null;
}

/** Spread of each quantity across the k sweep — NOT a confidence interval. */
export interface ShellUncertaintyBands {
  basis?: 'contact_stiffness_sweep' | 'not_computed' | string;
  band?: Partial<{
    peak_force_n: ShellUncertaintyBand;
    peak_acceleration_m_s2: ShellUncertaintyBand;
    contact_duration_s: ShellUncertaintyBand;
    contact_compression_m: ShellUncertaintyBand;
    load_path_stress_pa: ShellUncertaintyBand;
    safety_factor: ShellUncertaintyBand;
  }>;
  note?: string;
}

/** One per-output sensitivity of one parameter. */
export interface ShellSensitivityOutput {
  output?: string;
  sensitivity_up?: number | null;
  sensitivity_down?: number | null;
}

export interface ShellSensitivityRow {
  parameter?: string;
  perturbation_fraction?: number;
  mean_relative_response?: number;
  outputs?: ShellSensitivityOutput[];
}

export interface ShellSensitivity {
  rows?: ShellSensitivityRow[];
  top_parameters?: string[];
  note?: string;
}

/** One measured-vs-simulated comparison row. */
export interface ShellMeasuredComparisonRow {
  test_id?: string;
  cad_revision?: string;
  material?: string;
  prototype_id?: string;
  height_m?: number;
  surface?: string;
  orientation?: unknown;
  environment?: Record<string, unknown>;
  sensor?: Record<string, unknown>;
  measured?: Partial<{
    measured_peak_accel_g: number | null;
    measured_impact_duration_s: number | null;
    measured_settle_s: number | null;
  }>;
  uncertainty?: Partial<{
    measured_peak_accel_g_uncertainty: number | null;
    measured_impact_duration_s_uncertainty: number | null;
    measured_settle_s_uncertainty: number | null;
  }>;
  simulated?: {
    peak_acceleration_g?: number | null;
    measured_g?: number | null;
  } | null;
  absolute_error_g?: number;
  relative_error?: number;
  measured_minus_uncertainty_g?: number;
  measured_plus_uncertainty_g?: number;
  /** The compared quantity is CoM-frame equivalent only for flat impacts with a sensor at/near the CoM reading the resultant peak. */
  equivalent?: boolean;
  equivalence_note?: string;
  simulated_quantity?: string;
  settle_criterion?: string;
  surface_definition?: Record<string, unknown>;
  surface_table_parameters?: Record<string, unknown>;
  revision_mismatch?: boolean;
  revision_mismatch_note?: string;
  uncertainty_missing?: boolean;
  uncertainty_missing_metrics?: string[];
  identity_mismatch?: boolean;
  identity_mismatch_note?: string;
  missing_simulation?: boolean;
  identity_ok?: boolean;
}

export interface ShellMeasuredComparisonAggregate {
  count?: number;
  bias_g?: number;
  rmse_g?: number;
  max_abs_error_g?: number;
}

export interface ShellMeasuredComparison {
  rows?: ShellMeasuredComparisonRow[];
  aggregate?: ShellMeasuredComparisonAggregate;
  note?: string;
}

/** Bias/RMSE of the measured comparison per swept stiffness value. */
export interface ShellMeasuredKSensitivity {
  rows?: Array<{
    contact_stiffness_n_per_m?: number;
    bias_g?: number;
    rmse_g?: number;
  }>;
  note?: string;
}

/**
 * Validation-mode preparation artifacts attached to the shell result.
 * Emitted only in validation mode; nothing here modifies the physics.
 */
export interface ShellValidationPreparation {
  config?: Record<string, unknown>;
  note?: string;
  contact_stiffness_sweep?: ShellContactStiffnessSweep | null;
  uncertainty_bands?: ShellUncertaintyBands | null;
  sensitivity?: ShellSensitivity | null;
  measured_comparison?: ShellMeasuredComparison | null;
  measured_k_sensitivity?: ShellMeasuredKSensitivity | null;
  /** Shell-only explicitness: which objects are the engineering target. */
  physically_represented?: string[];
  /** Internal components that exist only as physical context. */
  context_only?: string[];
  context_note?: string;
  /** The two SEPARATE validation tracks (drop-dynamics vs structural). */
  tracks?: {
    drop_dynamics?: {
      validated_quantities?: string[];
      validates?: string[];
      note?: string;
    };
    structural?: {
      validated_quantities?: string[];
      requires?: string;
      note?: string;
    };
  };
  /** Measured prototype mass vs the geometry-derived mass, when pinned. */
  prototype_mass_disclosure?: {
    measured_kg?: number | null;
    model_kg?: number | null;
    delta_pct?: number | null;
    note?: string;
  };
}

/**
 * Authoritative shell FEA result. Every field is optional — the backend may
 * omit any of them, so consumers must guard every read.
 */
export interface ShellResult {
  status?: ShellStatus | string | null;
  classification?: ShellClassification | string | null;
  peak_stress_pa?: number | null;
  max_displacement_m?: number | null;
  min_safety_factor?: number | null;
  critical_region?: number[] | null;
  critical_region_stability?: ShellCriticalRegionStability | null;
  failure_mode?: string | null;
  physical_model_confidence?: 'high' | 'medium' | 'low' | string | null;
  statistical_confidence?: ShellStatisticalConfidence | null;
  statement?: string | null;
  assumptions?: string[];
  limitations?: string[];
  loading?: ShellDropLoading | null;
  model_status?: ShellModelStatus | null;
  physical_validation?: ShellPhysicalValidation | null;
  invalidating_assumptions?: ShellInvalidatingAssumption[];
  inputs_trace?: ShellInputsTrace | null;
  validation?: ShellValidationPreparation | null;
}

/** One component in the secondary screening list. */
export interface ScreeningComponentAssessment {
  component_id?: string;
  type?: string;
  status?: ComponentStatus | string;
  validity?: 'approximate' | 'not_evaluated' | string;
  metrics?: Record<string, number | string | null>;
  findings?: ComponentFinding[];
  assumptions?: string[];
  flags?: string[];
  usage_ratio?: number | null;
}

/** Secondary component screening block (low-confidence surrogate models). */
export interface ComponentScreeningResult {
  components?: ScreeningComponentAssessment[];
  summary?: {
    fail_count?: number;
    warn_count?: number;
    weakest?: ComponentWeakest | null;
  } | null;
  confidence?: string;
  note?: string;
}

export interface WilsonCi {
  low?: number;
  high?: number;
}

export interface ComponentFailureRate {
  component_id?: string;
  type?: string;
  failures?: number;
  rate?: number;
  wilson_ci?: WilsonCi | null;
  rank?: number;
}

export interface WeakestComponent {
  component_id?: string;
  type?: string;
  rate?: number;
  rank?: number;
}

export interface SensitivityEntry {
  parameter?: string;
  correlation?: number;
  mean_value?: number;
  std_value?: number;
  level?: SensitivityLevel | string;
}

export interface SurvivalPoint {
  usage_fraction?: number;
  survival_rate?: number;
}

/** Shell block inside a population result — Monte Carlo aggregates or a single worst-case run. */
export interface PopulationShell {
  nominal?: Record<string, unknown> | null;
  failures?: number;
  failure_rate?: number;
  wilson_ci?: WilsonCi | null;
  sensitivity?: SensitivityEntry[];
  assumptions?: string[];
  safety_factor?: number;
  peak_stress_pa?: number;
  max_displacement_m?: number;
  verdict?: string;
}

export interface PopulationResult {
  mode?: string;
  verdict?: string;
  drop?: Record<string, unknown> | null;
  sample_count?: number;
  profile?: string;
  lifespan_days?: number;
  units_failed?: number;
  failure_rate?: number;
  wilson_ci?: WilsonCi | null;
  component_failure_rates?: ComponentFailureRate[];
  weakest_components?: WeakestComponent[];
  sensitivity?: SensitivityEntry[];
  survival?: SurvivalPoint[];
  components?: ComponentAssessment[] | null;
  assumptions?: string[];
  shell?: PopulationShell | null;
  diagnostics?: string[];
  model?: Record<string, unknown> | null;
}

/**
 * FEA per-vertex field for one object. Field names are FROZEN — shared with
 * the frontend shader engine (feaStressShader.ts) and the Python backend.
 */
export interface FeaObjectField {
  object_id: string;
  vertex_count: number;
  damage: number[];
  displacement: number[][];
  stress_pa: number[];
}

/** Procedural fallback field: an analytic Gaussian stress/dent model. */
export interface FeaProceduralField {
  object_id: string;
  impact_point_model_m: Vec3;
  falloff_radius_m: number;
  contact_normal_model: Vec3;
  peak_stress_pa: number;
  yield_stress_pa: number;
  max_compression_m: number;
}

export interface FeaPeak {
  object_id: string;
  vertex_index: number;
  location_model_m: Vec3;
  damage: number;
  stress_pa: number;
  stress_mpa: number;
}

export interface FeaResult {
  computed: boolean;
  peak: FeaPeak | null;
  yield_stress_pa: number | null;
  damage_basis?:
    | 'derated_allowable'
    | 'material_yield'
    | 'material_allowable'
    | 'material_allowable_underated'
    | null;
  safety_factor: number | null;
  impact_window_s: number;
  dent_threshold: number;
  tear_threshold: number;
  center_frame?: 'panel_local' | 'world' | null;
  objects: FeaObjectField[];
  procedural: FeaProceduralField[];
  assumptions: string[];
  flags: string[];
}

/** FEA render mode consumed by the scene runtime (from contracts.ts). */
export type RenderMode = 'default' | 'fea' | 'yield';

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
  drop_simulation: DropSimulationResult | null;
  qualification: QualificationResult | null;
  material_assignments?: MaterialAssignment[] | null;
  components?: ComponentResult | null;
  component_screening?: ComponentScreeningResult | null;
  shell?: ShellResult | null;
  population?: PopulationResult | null;
  fea?: FeaResult | null;
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
