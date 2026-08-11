import { describe, it, expect } from 'vitest';
import {
  SCHEMA_IDS,
  IDENTITY_TRANSFORM,
  isRigidTransform,
  isGeometryJson,
  isPipelineResult,
  isRecord,
  type BoxGeometryJson,
  type SphereGeometryJson,
  type CylinderGeometryJson,
  type MeshGeometryJson,
  type CompoundGeometryJson,
  type PipelineResult,
} from '../api/contracts';

describe('contracts runtime type guards', () => {
  it('exports expected schema IDs', () => {
    expect(SCHEMA_IDS.WEB_HEALTH).toBe('gms.web-health/1');
    expect(SCHEMA_IDS.PIPELINE_RESULT).toBe('gms.pipeline-result/1');
  });

  it('validates rigid transform objects', () => {
    expect(isRigidTransform(IDENTITY_TRANSFORM)).toBe(true);

    const nonFiniteRotation = {
      rotation: [
        [1, NaN, 0],
        [0, 1, 0],
        [0, 0, 1],
      ],
      translation: [0, 0, 0],
      units: 'm',
    };
    expect(isRigidTransform(nonFiniteRotation)).toBe(false);

    const invalidTranslation = {
      rotation: IDENTITY_TRANSFORM.rotation,
      translation: [0, 0],
      units: 'm',
    };
    expect(isRigidTransform(invalidTranslation)).toBe(false);

    const missingUnits = {
      rotation: IDENTITY_TRANSFORM.rotation,
      translation: [0, 0, 0],
    };
    expect(isRigidTransform(missingUnits)).toBe(false);
  });

  it('validates geometry JSON objects', () => {
    const box: BoxGeometryJson = {
      type: 'box',
      size: [1, 2, 3],
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };
    expect(isGeometryJson(box)).toBe(true);

    const sphereZeroRadius: SphereGeometryJson = {
      type: 'sphere',
      radius: 0,
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };
    expect(isGeometryJson(sphereZeroRadius)).toBe(false);

    const cylinderNegHeight: CylinderGeometryJson = {
      type: 'cylinder',
      radius: 0.1,
      height: -1,
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };
    expect(isGeometryJson(cylinderNegHeight)).toBe(false);

    const meshInvalidTriangle: MeshGeometryJson = {
      type: 'mesh',
      vertices: [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
      triangles: [[0, 1, -1]],
      units: 'm',
      transform: IDENTITY_TRANSFORM,
    };
    expect(isGeometryJson(meshInvalidTriangle)).toBe(false);

    const compound: CompoundGeometryJson = {
      type: 'compound',
      children: [box],
      transform: IDENTITY_TRANSFORM,
    };
    expect(isGeometryJson(compound)).toBe(true);

    const compoundInvalidChild = {
      type: 'compound',
      children: [sphereZeroRadius],
      transform: IDENTITY_TRANSFORM,
    };
    expect(isGeometryJson(compoundInvalidChild)).toBe(false);

    expect(isGeometryJson({ type: 'unknown', transform: IDENTITY_TRANSFORM })).toBe(false);
    expect(isGeometryJson({ type: 'box', size: [1, 1, 1] })).toBe(true);
    expect(isGeometryJson({ type: 'box', size: [1, 1] })).toBe(false);
  });

  it('validates pipeline result envelopes', () => {
    const minimalResult: PipelineResult = {
      schema_id: 'gms.pipeline-result/1',
      engine_version: '1.0.0',
      run_id: 'run-123',
      mode: 'exploration',
      lifecycle_state: 'completed',
      validity: {
        state: 'valid',
        reasons: [],
        assumptions: [],
        unsupported_failure_modes: [],
        confidence: 'high',
      },
      issues: [],
      geometry_summary: { objects: [], parse_errors: [] },
      mass: null,
      validation: null,
      structural: null,
      impact: null,
  drop_simulation: null,
      qualification: null,
      manifest: null,
      errors: [],
    };
    expect(isPipelineResult(minimalResult)).toBe(true);

    const wrongSchema = { ...minimalResult, schema_id: 'gms.invalid/1' };
    expect(isPipelineResult(wrongSchema)).toBe(false);

    const missingIssues = { ...minimalResult };
    delete (missingIssues as Partial<PipelineResult>).issues;
    expect(isPipelineResult(missingIssues)).toBe(false);

    const invalidManifest = { ...minimalResult, manifest: ['invalid array'] };
    expect(isPipelineResult(invalidManifest)).toBe(false);
  });

  it('validates record objects', () => {
    expect(isRecord({})).toBe(true);
    expect(isRecord([])).toBe(false);
    expect(isRecord(null)).toBe(false);
    expect(isRecord('string')).toBe(false);
  });

  it('accepts pipeline results carrying the shell-validation preparation fields', () => {
    const enriched: PipelineResult = {
      ...minimalResult(),
      shell: {
        status: 'pass',
        classification: 'safe',
        model_status: 'unvalidated',
        physical_validation: {
          status: 'no_measured_tests',
          independent_conditions: 0,
          note: 'internal consistency is NOT physical validation',
        },
        invalidating_assumptions: [
          { assumption: 'contact stiffness', status: 'uncalibrated', impact: 'peak force scales ~sqrt(k)' },
        ],
        inputs_trace: {
          geometry: { objects: ['shell-body'], geometry_digest: 'a1b2c3d4e5f60718' },
          material: { label: 'ABS', properties: { young_modulus: 2.3e9 } },
          mass: { mass_kg: 0.095, mass_status: 'derived', density_kg_m3: 1040 },
          drop: {
            height_m: 1.2,
            orientation_quaternion_wxyz: [1, 0, 0, 0],
            surface: 'concrete',
            restitution: 0.35,
            timestep_s: 0.0002,
          },
          contact: { contact_stiffness_n_per_m: 100000 },
          engine: { version: '1.0.0', engine_hash: 'abc123' },
          seed: 7,
        },
        validation: {
          contact_stiffness_sweep: {
            rows: [
              { contact_stiffness_n_per_m: 100000, peak_force_n: 238, peak_acceleration_m_s2: 2500 },
            ],
            note: 'sensitivity only',
          },
          uncertainty_bands: {
            basis: 'contact_stiffness_sweep',
            band: { peak_force_n: { low: 238, high: 754, nominal: 496 } },
            note: 'NOT a statistical confidence interval',
          },
          sensitivity: {
            rows: [{ parameter: 'contact_stiffness', perturbation_fraction: 0.1, mean_relative_response: 0.51 }],
            top_parameters: ['contact_stiffness'],
          },
          measured_comparison: {
            rows: [
              {
                test_id: 'DRP-01',
                height_m: 1.2,
                surface: 'concrete',
                orientation: 'flat',
                measured: { measured_peak_accel_g: 520 },
                uncertainty: { measured_peak_accel_g_uncertainty: 25 },
                simulated: { peak_acceleration_g: 530, measured_g: 520 },
                absolute_error_g: 10,
              },
            ],
            aggregate: { count: 1, bias_g: 10, rmse_g: 10 },
          },
          measured_k_sensitivity: {
            rows: [{ contact_stiffness_n_per_m: 100000, bias_g: -12.4, rmse_g: 15.1 }],
          },
        },
      },
      impact: {
        mass_kg: 0.095,
        result: null,
        reason: null,
        unsupported_failure_modes: [],
        cross_reference: {
          drop_derived_peak_force_estimate_n: 340,
          drop_derived_energy_j: 1.12,
          note: 'the impact section is a standalone quasi-static model',
        },
      },
      drop_simulation: {
        config: { test: 'drop', height_m: 1.2, surface: 'concrete', drop_count: 1, orientation: 'flat' },
        model: {
          mass_kg: 0.095,
          inertia_kg_m2: [[1e-6, 0, 0], [0, 1e-6, 0], [0, 0, 1e-6]],
          support_model: 'mesh_extreme_points',
          support_point_count: 3,
          integrator: 'semi_implicit_euler',
          timestep_s: 0.0002,
          gravity_m_s2: 9.80665,
          surface: 'concrete',
          restitution: 0.35,
          orientation_quaternion_wxyz: [1, 0, 0, 0],
          gravity_vector_body: [0, 0, -1],
          initial_angular_velocity_rad_s: [0, 0, 0],
          initial_velocity_m_s: [0, 0, 0],
          starting_pose_m: [0, 0, 0],
        },
        drops: [
          {
            index: 0,
            start_s: 0,
            end_s: 0.5,
            settled_s: 0.5,
            impact_count: 1,
            peak_impact_speed_m_s: 4.85,
            peak_kinetic_energy_j: 1.12,
            orientation: 'flat',
            orientation_quaternion_wxyz: [1, 0, 0, 0],
            gravity_vector_body: [0, 0, -1],
            initial_angular_velocity_rad_s: [0, 0, 0],
            initial_velocity_m_s: [0, 0, 0],
            starting_pose_m: [0, 0, 0],
          },
        ],
        impacts: [],
        peak: null,
        peak_force_estimate_n: 340,
        peak_force_estimate: {
          mass_kg: 0.095,
          restitution: 0.35,
          energy_j: 1.12,
          impact_speed_m_s: 4.85,
          contact_stiffness_n_per_m: 100000,
          model: 'linear-spring quasi-static estimate',
        },
        contact_stiffness_n_per_m: 100000,
        trajectory: [],
      },
      structural: {
        load_case: {},
        structure: {},
        material: 'ABS',
        fixtures: null,
        preflight: [],
        response: {
          method_id: 'shell_panel_navier_v1',
          max_displacement_m: 0.0002,
          max_displacement_location: [0.001, 0.002, 0.003],
          max_stress_pa: 12000000,
          max_stress_filtered_pa: null,
          filtered_location: null,
          safety_factor: 2.4,
          safety_factor_status: 'ok',
          reactions: {},
          force_residual_n: null,
          moment_residual_n_m: null,
          flags: [],
          assumptions: [],
          unsupported_failure_modes: [],
          validity: 'valid',
        },
        resolved_material: {
          name: 'ABS',
          properties: { young_modulus: 2.3e9 },
          provenance: { confidence: 'medium' },
          approval_state: 'draft',
        },
      },
    };
    expect(isPipelineResult(enriched)).toBe(true);
  });
});

function minimalResult(): PipelineResult {
  return {
    schema_id: 'gms.pipeline-result/1',
    engine_version: '1.0.0',
    run_id: 'run-123',
    mode: 'exploration',
    lifecycle_state: 'completed',
    validity: {
      state: 'valid',
      reasons: [],
      assumptions: [],
      unsupported_failure_modes: [],
      confidence: 'high',
    },
    issues: [],
    geometry_summary: { objects: [], parse_errors: [] },
    mass: null,
    validation: null,
    structural: null,
    impact: null,
    drop_simulation: null,
    qualification: null,
    manifest: null,
    errors: [],
  };
}
