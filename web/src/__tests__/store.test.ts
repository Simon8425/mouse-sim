import { describe, it, expect } from 'vitest';
import {
  reducer,
  initialState,
  createAnalysisRequest,
  computeObjectEntries,
} from '../state/projectStore';
import {
  selectEvidenceCount,
  selectSolverModelBadge,
} from '../state/selectors';
import { STUDY_PRESETS } from '../lib/studies';
import {
  IDENTITY_TRANSFORM,
  type PipelineResult,
  type PipelineRequest,
  type GeometryPreview,
  type ImpactEstimate,
  type QualificationResult,
} from '../api/contracts';
import type { ProjectState } from '../state/projectStore';

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

  it('resets geometry view state when baseline or import replaces the source', () => {
    let state: ProjectState = {
      ...initialState,
      selectedId: 'old-object',
      isolatedId: 'old-object',
      visibility: { 'old-object': false },
      theme: 'dark' as const,
    };

    state = reducer(state, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    expect(state.selectedId).toBeNull();
    expect(state.isolatedId).toBeNull();
    expect(state.visibility).toEqual({});
    expect(state.theme).toBe('dark');

    const preview: GeometryPreview = {
      schema_id: 'gms.geometry-preview/1',
      supported: true,
      format: 'json',
      source_units: 'mm',
      geometry: {
        type: 'box',
        size: [10, 10, 10],
        units: 'mm',
        transform: IDENTITY_TRANSFORM,
      },
      diagnostics: [],
      source_name: 'new.json',
    };
    state = reducer(
      { ...state, selectedId: 'shell_top', isolatedId: 'shell_top', visibility: { shell_top: false } },
      { type: 'PREVIEW_OK', preview },
    );
    expect(state.selectedId).toBeNull();
    expect(state.isolatedId).toBeNull();
    expect(state.visibility).toEqual({});
    expect(state.theme).toBe('dark');
  });

  it('retains unsupported preview diagnostics without making them analyzable', () => {
    const preview: GeometryPreview = {
      schema_id: 'gms.geometry-preview/1',
      supported: false,
      format: 'step',
      source_units: null,
      geometry: null,
      diagnostics: [
        {
          code: 'cad_converter_missing',
          severity: 'error',
          message: 'STEP conversion is unavailable',
          details: { plugin: 'server-cad' },
        },
      ],
      source_name: 'housing.step',
    };

    let state = reducer(initialState, { type: 'PREVIEW_START', temp: null, version: 1 });
    state = reducer(state, { type: 'PREVIEW_OK', preview, version: 1 });

    expect(state.preview).toEqual(preview);
    expect(state.previewStatus).toBe('error');
    expect(state.previewDiagnostics).toEqual(preview.diagnostics);
    expect(state.previewError).toBe('STEP conversion is unavailable');
    expect(createAnalysisRequest(state)).toBeNull();
  });

  it('ignores stale preview responses from an older request generation', () => {
    let state = reducer(initialState, { type: 'PREVIEW_START', temp: null, version: 1 });
    state = reducer(state, {
      type: 'PREVIEW_START',
      version: 2,
      temp: {
        id: 'new.obj',
        name: 'new.obj',
        geometry: {
          type: 'mesh',
          vertices: [[0, 0, 0]],
          triangles: [],
          units: 'm',
          transform: IDENTITY_TRANSFORM,
        },
        diagnostics: [],
      },
    });

    const oldPreview: GeometryPreview = {
      schema_id: 'gms.geometry-preview/1',
      supported: true,
      format: 'json',
      source_units: 'mm',
      geometry: null,
      diagnostics: [],
      source_name: 'old.json',
    };
    state = reducer(state, { type: 'PREVIEW_OK', preview: oldPreview, version: 1 });

    expect(state.preview).toBeNull();
    expect(state.previewStatus).toBe('working');
    expect(state.tempPreview?.name).toBe('new.obj');
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

describe('mission control store actions and selectors', () => {
  it('opens and closes the mission control panel', () => {
    let state = reducer(initialState, { type: 'SET_CONTROL_OPEN', open: true });
    expect(state.controlOpen).toBe(true);
    state = reducer(state, { type: 'SET_CONTROL_OPEN', open: false });
    expect(state.controlOpen).toBe(false);
  });

  it('bumps the run nonce on RUN_STUDY to re-trigger analysis', () => {
    const state = reducer(initialState, { type: 'RUN_STUDY' });
    expect(state.runNonce).toBe(1);
  });

  it('prefills study sections through UPDATE_DRAFT with exclusive nulls', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });

    state = reducer(state, { type: 'UPDATE_DRAFT', patch: STUDY_PRESETS[0].patch });
    expect(state.draft?.impact).toEqual({
      fall_height_m: 0.75,
      restitution: 0.3,
      contact_stiffness_n_per_m: 100000,
    });
    expect(state.draft?.load_case).toBeNull();
    expect(state.draft?.structure).toBeNull();

    state = reducer(state, { type: 'UPDATE_DRAFT', patch: STUDY_PRESETS[1].patch });
    expect(state.draft?.load_case).toEqual({ name: 'shell_flex', kind: 'pressure', magnitude: { value: 5, unit: 'kPa' } });
    expect(state.draft?.structure).toEqual({ type: 'shell_panel', a_m: 0.11, b_m: 0.065, t_m: 0.002, material: 'ABS' });
    expect(state.draft?.impact).toBeNull();

    state = reducer(state, { type: 'UPDATE_DRAFT', patch: STUDY_PRESETS[2].patch });
    expect(state.draft?.impact).toMatchObject({ orientation: 'face', contact_normal: [0, 0, 1] });
  });

  it('counts qualification gates as reported evidence', () => {
    const state: ProjectState = { ...initialState, lastResult: mockPipelineResult };
    expect(selectEvidenceCount(state)).toBe(0);
  });

  it('counts readiness and analysis-integrity gates separately but together', () => {
    const state: ProjectState = {
      ...initialState,
      lastResult: {
        ...mockPipelineResult,
        qualification: {
          ...mockPipelineResult.qualification!,
          gates: [
            { key: 'METHOD_APPROVED', label: 'Method', passed: true, evaluable: true, blocker: true, explanation: '' },
            { key: 'GEOMETRY_APPROVED', label: 'Geometry', passed: true, evaluable: true, blocker: true, explanation: '' },
          ],
          integrity_gates: [
            { key: 'ANALYSIS_VALIDITY', label: 'Validity', passed: true, evaluable: true, blocker: true, explanation: '' },
          ],
        },
      },
    };
    expect(selectEvidenceCount(state)).toBe(3);
  });

  it('exposes the screening surrogate model id from solver metadata', () => {
    const impactResult: ImpactEstimate = {
      impact_energy_j: 0.5,
      closing_velocity_m_s: 3.8,
      effective_mass_kg: 0.07,
      impulse_n_s: 0.27,
      peak_force_n: 320,
      peak_acceleration_m_s2: 4571,
      contact_duration_s: 0.0017,
      contact_compression_m: 0.0003,
      method_id: 'energy_quasi_static_v1',
      flags: [],
      assumptions: [],
      unsupported_failure_modes: ['fatigue'],
      validity: 'valid',
      load_path_stress_pa: null,
      safety_factor: 'not_available',
      qualification_blocked: true,
    };
    const qualification: QualificationResult = {
      mode: 'exploration',
      qualified: false,
      evidence_disposition: 'exploration_only',
      gates: [
        { key: 'CONVERGENCE', label: 'Solver convergence evidence', passed: false, evaluable: false, blocker: true, explanation: 'no analysis method provided' },
        { key: 'CORRELATION', label: 'Required correlation records', passed: false, evaluable: false, blocker: true, explanation: 'none' },
      ],
      blocking_keys: ['CONVERGENCE'],
      summary: 'screening only',
    };
    const result: PipelineResult = {
      ...mockPipelineResult,
      qualification,
      impact: {
        mass_kg: 0.07,
        result: {
          ...impactResult,
          solver_metadata: { model_id: 'screening_surrogate_v1' },
        } as unknown as ImpactEstimate,
        reason: null,
        unsupported_failure_modes: ['fatigue'],
      },
    };

    const state: ProjectState = { ...initialState, lastResult: result };
    expect(selectEvidenceCount(state)).toBe(2);
    expect(selectSolverModelBadge(state)).toBe('screening_surrogate_v1');
  });
});
