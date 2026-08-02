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
});
