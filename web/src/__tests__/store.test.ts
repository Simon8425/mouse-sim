import { describe, it, expect } from 'vitest';
import {
  reducer,
  initialState,
  createAnalysisRequest,
  computeObjectEntries,
} from '../state/projectStore';
import {
  IDENTITY_TRANSFORM,
  type PipelineResult,
  type PipelineRequest,
  type GeometryPreview,
} from '../api/contracts';

const mockBaselineProject: PipelineRequest = {
  schema_id: 'gms.project/1',
  mode: 'exploration',
  units: 'm',
  objects: [
    {
      id: 'shell_top',
      geometry: { type: 'box', size: [0.1, 0.05, 0.02], units: 'm', transform: IDENTITY_TRANSFORM },
      material: 'ABS',
    },
  ],
};

const mockPipelineResult: PipelineResult = {
  schema_id: 'gms.pipeline-result/1',
  engine_version: '1.0.0',
  run_id: 'run-1',
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
  qualification: {
    mode: 'exploration',
    qualified: true,
    evidence_disposition: 'exploration_only',
    gates: [],
    blocking_keys: [],
    summary: 'OK',
  },
  manifest: null,
  errors: [],
};

describe('projectStore state reducer and selectors', () => {
  it('enforces token stale guard on out-of-order analysis responses', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });

    state = reducer(state, { type: 'ANALYZE_START', version: 1 });
    state = reducer(state, { type: 'ANALYZE_START', version: 2 });

    // Stale response from v1 should be discarded
    state = reducer(state, { type: 'ANALYZE_OK', version: 1, result: mockPipelineResult });
    expect(state.lastResult).toBeNull();
    expect(state.runStatus).toBe('running');

    // Matching response from v2 should be accepted
    state = reducer(state, { type: 'ANALYZE_OK', version: 2, result: mockPipelineResult });
    expect(state.lastResult).toEqual(mockPipelineResult);
    expect(state.runStatus).toBe('success');
    expect(state.stale).toBe(false);
  });

  it('retains last result when a subsequent analysis run fails', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    state = reducer(state, { type: 'ANALYZE_START', version: 1 });
    state = reducer(state, { type: 'ANALYZE_OK', version: 1, result: mockPipelineResult });

    state = reducer(state, { type: 'ANALYZE_START', version: 2 });
    expect(state.stale).toBe(true);

    state = reducer(state, { type: 'ANALYZE_ERROR', version: 2, message: 'Solver timeout' });
    expect(state.lastResult).toEqual(mockPipelineResult);
    expect(state.runStatus).toBe('error');
    expect(state.runError).toBe('Solver timeout');
    expect(state.stale).toBe(true);
  });

  it('retains lastResult and marks stale on SET_MODE', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    state = reducer(state, { type: 'ANALYZE_START', version: 1 });
    state = reducer(state, { type: 'ANALYZE_OK', version: 1, result: mockPipelineResult });

    state = reducer(state, { type: 'SET_MODE', mode: 'qualification' });
    expect(state.mode).toBe('qualification');
    expect(state.lastResult).toEqual(mockPipelineResult);
    expect(state.stale).toBe(true);
  });

  it('persists selection when object visibility is toggled off', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    state = reducer(state, { type: 'SELECT', id: 'shell_top' });
    expect(state.selectedId).toBe('shell_top');

    state = reducer(state, { type: 'TOGGLE_VISIBILITY', id: 'shell_top' });
    expect(state.visibility['shell_top']).toBe(false);
    expect(state.selectedId).toBe('shell_top');
  });

  it('does not mutate createAnalysisRequest when SET_EXPLODE is dispatched', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    state = reducer(state, { type: 'SET_EXPLODE', factor: 0.5 });
    expect(state.explode).toBe(0.5);

    const req = createAnalysisRequest(state);
    expect(JSON.stringify(req)).not.toContain('explode');
    expect(req?.objects).toEqual(mockBaselineProject.objects);
  });

  it('replaces entries and analysis request when PREVIEW_OK is dispatched', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });

    const preview: GeometryPreview = {
      schema_id: 'gms.geometry-preview/1',
      supported: true,
      format: 'obj',
      source_units: 'mm',
      geometry: {
        type: 'mesh',
        vertices: [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        triangles: [[0, 1, 2]],
        units: 'm',
        transform: IDENTITY_TRANSFORM,
      },
      diagnostics: [],
      source_name: 'cover.obj',
    };

    state = reducer(state, { type: 'PREVIEW_OK', preview });
    const entries = computeObjectEntries(state);
    expect(entries.length).toBe(1);
    expect(entries[0].id).toBe('cover.obj');
    expect(entries[0].geometry.type).toBe('mesh');

    const req = createAnalysisRequest(state);
    expect(Array.isArray(req?.objects)).toBe(true);
    expect((req?.objects as unknown[]).length).toBe(1);
  });

  it('builds an analysis request from an uploaded preview without a project', () => {
    const preview: GeometryPreview = {
      schema_id: 'gms.geometry-preview/1',
      supported: true,
      format: 'json',
      source_units: 'mm',
      geometry: {
        type: 'box',
        size: [40, 20, 4],
        units: 'mm',
        transform: IDENTITY_TRANSFORM,
      },
      diagnostics: [],
      source_name: 'analytic.json',
    };

    const state = reducer(initialState, { type: 'PREVIEW_OK', preview });
    expect(state.project).toBeNull();

    const req = createAnalysisRequest(state);
    expect(req).not.toBeNull();
    expect(Array.isArray(req?.objects)).toBe(true);
    expect((req?.objects as unknown[]).length).toBe(1);
    expect(req?.mode).toBe('exploration');

    const entries = computeObjectEntries(state);
    expect(entries.length).toBe(1);
    expect(entries[0].id).toBe('analytic.json');
  });

  it('returns null while a mesh preview is still being parsed without a project', () => {
    const temp = reducer(initialState, {
      type: 'PREVIEW_START',
      temp: {
        id: 'cover.obj',
        name: 'cover.obj',
        geometry: {
          type: 'mesh',
          vertices: [],
          triangles: [],
          units: 'm',
          transform: IDENTITY_TRANSFORM,
        },
        diagnostics: [],
      },
    });
    expect(temp.tempPreview).not.toBeNull();
    expect(createAnalysisRequest(temp)).toBeNull();
  });

  it('commits draft changes on APPLY_DRAFT', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });

    state = reducer(state, { type: 'START_EDIT_DRAFT' });
    state = reducer(state, { type: 'UPDATE_DRAFT', patch: { units: 'mm' } });
    expect(state.draft?.units).toBe('mm');
    expect(state.project?.units).toBe('m');

    state = reducer(state, { type: 'APPLY_DRAFT' });
    expect(state.draft).toBeNull();
    expect(state.project?.units).toBe('mm');
  });
});
