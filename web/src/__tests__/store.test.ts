import { describe, it, expect } from 'vitest';
import {
  reducer,
  initialState,
  createAnalysisRequest,
  createAnalysisRequestKey,
  computeObjectEntries,
} from '../state/projectStore';
import {
  selectEvidenceCount,
  selectSolverModelBadge,
} from '../state/selectors';
import {
  IDENTITY_TRANSFORM,
  type PipelineResult,
  type PipelineRequest,
  type GeometryPreview,
  type ImpactEstimate,
  type QualificationResult,
} from '../api/contracts';
import type { ProjectState } from '../state/projectStore';
import type { Mode as ProjectMode } from '../state/projectStore';

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
  drop_simulation: null,
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

    state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: 'k1' });
    state = reducer(state, { type: 'ANALYZE_START', version: 2, requestKey: 'k2' });

    // Stale response from v1 should be discarded
    state = reducer(state, { type: 'ANALYZE_OK', version: 1, requestKey: 'k1', result: mockPipelineResult });
    expect(state.lastResult).toBeNull();
    expect(state.runStatus).toBe('running');

    // Matching response from v2 should be accepted
    state = reducer(state, { type: 'ANALYZE_OK', version: 2, requestKey: 'k2', result: mockPipelineResult });
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
    state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: 'k1' });
    state = reducer(state, { type: 'ANALYZE_OK', version: 1, requestKey: 'k1', result: mockPipelineResult });

    state = reducer(state, { type: 'ANALYZE_START', version: 2, requestKey: 'k2' });
    expect(state.stale).toBe(true);

    state = reducer(state, { type: 'ANALYZE_ERROR', version: 2, requestKey: 'k2', message: 'Solver timeout' });
    expect(state.lastResult).toEqual(mockPipelineResult);
    expect(state.runStatus).toBe('error');
    expect(state.runError).toBe('Solver timeout');
    expect(state.stale).toBe(true);
  });

  it('SET_RENDER_MODE updates renderMode without touching staleness', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: 'k1' });
    state = reducer(state, { type: 'ANALYZE_OK', version: 1, requestKey: 'k1', result: mockPipelineResult });
    expect(state.renderMode).toBe('default');

    state = reducer(state, { type: 'SET_RENDER_MODE', mode: 'yield' });
    expect(state.renderMode).toBe('yield');
    expect(state.stale).toBe(false);
    expect(state.lastResult).toEqual(mockPipelineResult);

    state = reducer(state, { type: 'SET_RENDER_MODE', mode: 'fea' });
    expect(state.renderMode).toBe('fea');
    expect(state.stale).toBe(false);
  });

  it('SET_DROP_PLAYING tracks the playback state and LEAVE_TEST resets it', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    expect(state.dropPlaying).toBe(false);
    state = reducer(state, { type: 'SET_DROP_PLAYING', playing: true });
    expect(state.dropPlaying).toBe(true);
    state = reducer(state, { type: 'SET_DROP_PLAYING', playing: false });
    expect(state.dropPlaying).toBe(false);

    state = reducer(state, {
      type: 'RUN_DROP_TEST',
      test: 'drop',
      config: { height_m: 0.75, surface: 'concrete', drop_count: 1, orientation: 'flat' },
    });
    state = reducer(state, { type: 'SET_DROP_PLAYING', playing: true });
    state = reducer(state, { type: 'LEAVE_TEST' });
    expect(state.dropPlaying).toBe(false);
  });

  it('ANALYZE_OK captures the fea field into feaResult', () => {
    const feaResult = {
      computed: true,
      peak: {
        object_id: 'shell',
        vertex_index: 7,
        location_model_m: [0.01, 0.02, 0.03] as [number, number, number],
        damage: 0.85,
        stress_pa: 4.8e7,
        stress_mpa: 48.0,
      },
      yield_stress_pa: 5.6e7,
      safety_factor: 1.17,
      impact_window_s: 0.3,
      dent_threshold: 0.7,
      tear_threshold: 0.92,
      objects: [
        {
          object_id: 'shell',
          vertex_count: 4,
          damage: [0.1, 0.85, 0.3, 0.05],
          displacement: [
            [0, 0, 0],
            [0, 0, -0.0004],
            [0, 0, 0],
            [0, 0, 0],
          ],
          stress_pa: [1e6, 4.8e7, 3e6, 5e5],
        },
      ],
      procedural: [],
      assumptions: [],
      flags: [],
    };
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: 'k1' });
    state = reducer(state, {
      type: 'ANALYZE_OK',
      version: 1,
      requestKey: 'k1',
      result: { ...mockPipelineResult, fea: feaResult },
    });
    expect(state.feaResult).toEqual(feaResult);
    expect(state.feaResult?.peak?.stress_mpa).toBe(48.0);

    // A result without an fea field clears it.
    state = reducer(state, { type: 'ANALYZE_START', version: 2, requestKey: 'k2' });
    state = reducer(state, {
      type: 'ANALYZE_OK',
      version: 2,
      requestKey: 'k2',
      result: mockPipelineResult,
    });
    expect(state.feaResult).toBeNull();
  });

  it('does not mark stale when re-running the SAME request', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    // The reducer now compares the completed run's request key against the
    // current configuration, so tests must pass the real derived key.
    const requestKey = createAnalysisRequestKey(createAnalysisRequest(state));
    state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey });
    state = reducer(state, { type: 'ANALYZE_OK', version: 1, requestKey, result: mockPipelineResult });
    expect(state.stale).toBe(false);

    // RUN_STUDY-style re-run of the identical request: the fresh result
    // belongs to the current configuration, so it must not be labeled stale.
    state = reducer(state, { type: 'ANALYZE_START', version: 2, requestKey });
    expect(state.stale).toBe(false);
    state = reducer(state, { type: 'ANALYZE_OK', version: 2, requestKey, result: mockPipelineResult });
    expect(state.stale).toBe(false);

    // A failed re-run of the same request keeps the result current.
    state = reducer(state, { type: 'ANALYZE_START', version: 3, requestKey });
    state = reducer(state, { type: 'ANALYZE_ERROR', version: 3, requestKey, message: 'boom' });
    expect(state.stale).toBe(false);
  });

  it('retains lastResult and marks stale on SET_MODE', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: 'k1' });
    state = reducer(state, { type: 'ANALYZE_OK', version: 1, requestKey: 'k1', result: mockPipelineResult });

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

  it('opens the navigator drawer when a geometry preview starts and completes', () => {
    expect(initialState.navOpen).toBe(false);

    let state = reducer(initialState, { type: 'PREVIEW_START', temp: null, version: 1 });
    expect(state.navOpen).toBe(true);

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
      source_name: 'housing.step',
    };
    state = reducer(state, { type: 'PREVIEW_OK', preview, version: 1 });
    expect(state.navOpen).toBe(true);
  });

  it('keeps the navigator closed on a clean start without geometry', () => {
    const state = reducer(initialState, { type: 'SET_NAV_OPEN', open: false });
    expect(state.navOpen).toBe(false);
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
    expect(req?.options?.display_tessellation).toBeUndefined();

    const entries = computeObjectEntries(state);
    expect(entries.length).toBe(1);
    expect(entries[0].id).toBe('analytic.json');
  });

  it('tags kernel-backed STEP previews as display tessellations', () => {
    const preview: GeometryPreview = {
      schema_id: 'gms.geometry-preview/1',
      supported: true,
      format: 'step',
      source_units: 'mm',
      geometry: {
        type: 'mesh',
        vertices: [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        triangles: [[0, 1, 2]],
        units: 'm',
        transform: IDENTITY_TRANSFORM,
      },
      diagnostics: [],
      source_name: 'G3-20260320.stp',
      display_asset: {
        asset_id: 'a'.repeat(64),
        url: '/api/geometry/assets/' + 'a'.repeat(64) + '.glb',
        format: 'glb',
        parts_url: '/api/geometry/assets/' + 'a'.repeat(64) + '.parts.json',
      },
    };

    const state = reducer(initialState, { type: 'PREVIEW_OK', preview });
    const req = createAnalysisRequest(state);
    expect(req?.options?.display_tessellation).toBe(true);
    expect(req?.geometry_asset_id).toBe('a'.repeat(64));
    expect(req?.objects).toBeUndefined();
  });

  it('sets the default material key via SET_DEFAULT_MATERIAL', () => {
    const state = reducer(initialState, { type: 'SET_DEFAULT_MATERIAL', key: 'ABS' });
    expect(state.defaultMaterialKey).toBe('ABS');
  });

  it('includes default_material in the analysis request when set', () => {
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
      source_name: 'case.stl',
    };
    let state = reducer(initialState, { type: 'PREVIEW_OK', preview });
    state = reducer(state, { type: 'SET_DEFAULT_MATERIAL', key: 'ABS' });

    const req = createAnalysisRequest(state);
    expect(req?.default_material).toBe('ABS');
    expect(req?.objects).toEqual([{ id: 'case.stl', geometry: preview.geometry }]);
  });

  it('omits default_material when the default material key is empty', () => {
    const state = reducer({ ...initialState, defaultMaterialKey: '' }, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    const req = createAnalysisRequest(state);
    expect(req).not.toBeNull();
    expect(req?.default_material).toBeUndefined();
  });

  it('assigns a material override to an uploaded object and clears it', () => {    const preview: GeometryPreview = {
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
      source_name: 'case.stl',
    };
    let state = reducer(initialState, { type: 'PREVIEW_OK', preview });
    state = reducer(state, {
      type: 'SET_OBJECT_MATERIAL',
      objectId: 'case.stl',
      materialKey: 'ABS',
    });

    const req = createAnalysisRequest(state);
    expect(req?.objects).toEqual([
      { id: 'case.stl', geometry: preview.geometry, material: 'ABS' },
    ]);

    state = reducer(state, {
      type: 'SET_OBJECT_MATERIAL',
      objectId: 'case.stl',
      materialKey: null,
    });
    const cleared = createAnalysisRequest(state);
    expect(cleared?.objects).toEqual([{ id: 'case.stl', geometry: preview.geometry }]);
  });

  it('clears material overrides when a new preview replaces the source', () => {
    let state = reducer(initialState, {
      type: 'PREVIEW_OK',
      preview: {
        schema_id: 'gms.geometry-preview/1',
        supported: true,
        format: 'stl',
        source_units: 'mm',
        geometry: {
          type: 'mesh',
          vertices: [[0, 0, 0]],
          triangles: [],
          units: 'm',
          transform: IDENTITY_TRANSFORM,
        },
        diagnostics: [],
        source_name: 'a.stl',
      },
    });
    state = reducer(state, { type: 'SET_OBJECT_MATERIAL', objectId: 'a.stl', materialKey: 'ABS' });
    expect(state.objectMaterials).toEqual({ 'a.stl': 'ABS' });

    state = reducer(state, { type: 'PREVIEW_START', temp: null, version: 2 });
    expect(state.objectMaterials).toEqual({});
  });

  it('expands kernel STEP previews into per-part entries once part geometry loads', () => {
    const preview: GeometryPreview = {
      schema_id: 'gms.geometry-preview/1',
      supported: true,
      format: 'step',
      source_units: 'mm',
      geometry: {
        type: 'mesh',
        vertices: [[0, 0, 0]],
        triangles: [],
        units: 'm',
        transform: IDENTITY_TRANSFORM,
      },
      diagnostics: [],
      source_name: 'G3-20260320.stp',
        display_asset: {
          asset_id: 'b'.repeat(64),
          url: '/api/geometry/assets/' + 'b'.repeat(64) + '.glb',
          format: 'glb',
          parts_url: '/api/geometry/assets/' + 'b'.repeat(64) + '.parts.json',
          parts: [
          { id: 'part-0', name: 'TD011-TOP-C', color: [0.36, 1.0, 0.41] },
          { id: 'part-1', name: 'TD011-BOT1', color: [1.0, 0.0, 0.0] },
        ],
      },
    };
    let state = reducer(initialState, { type: 'PREVIEW_OK', preview });

    // Before parts arrive, the preview stays a single entry.
    let entries = computeObjectEntries(state);
    expect(entries).toHaveLength(1);
    expect(entries[0].id).toBe('G3-20260320.stp');
    expect(entries[0].displayAssetUrl).not.toBeNull();

    state = reducer(state, {
      type: 'PARTS_OK',
      assetId: 'b'.repeat(64),
      parts: [
        {
          id: 'part-0',
          name: 'TD011-TOP-C',
          geometry: {
            type: 'mesh',
            vertices: [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            triangles: [[0, 1, 2]],
            units: 'm',
            transform: IDENTITY_TRANSFORM,
          },
        },
        {
          id: 'part-1',
          name: 'TD011-BOT1',
          geometry: {
            type: 'mesh',
            vertices: [[0, 0, 0], [2, 0, 0], [0, 2, 0]],
            triangles: [[0, 1, 2]],
            units: 'm',
            transform: IDENTITY_TRANSFORM,
          },
        },
      ],
    });

    entries = computeObjectEntries(state);
    expect(entries.map((e) => e.id)).toEqual(['part-0', 'part-1']);
    expect(entries[0].name).toBe('TD011-TOP-C');
    expect(entries[0].color).toEqual([0.36, 1.0, 0.41]);
    expect(entries[0].displayAssetUrl).toBeNull();

    state = reducer(state, { type: 'SET_OBJECT_MATERIAL', objectId: 'part-1', materialKey: 'PC/ABS' });
    const req = createAnalysisRequest(state);
    expect(req?.geometry_asset_id).toBe('b'.repeat(64));
    expect(req?.objects).toEqual([
      { id: 'part-0' },
      { id: 'part-1', material: 'PC/ABS' },
    ]);
    expect(req?.options?.display_tessellation).toBe(true);
  });

  it('falls back to a single-object request when part geometry fails to load', () => {
    const preview: GeometryPreview = {
      schema_id: 'gms.geometry-preview/1',
      supported: true,
      format: 'step',
      source_units: 'mm',
      geometry: {
        type: 'box',
        size: [1, 1, 1],
        units: 'm',
        transform: IDENTITY_TRANSFORM,
      },
      diagnostics: [],
      source_name: 'model.stp',
      display_asset: {
        asset_id: 'c'.repeat(64),
        url: '/api/geometry/assets/' + 'c'.repeat(64) + '.glb',
        format: 'glb',
        parts: [{ id: 'part-0', name: 'A' }],
      },
    };
    let state = reducer(initialState, { type: 'PREVIEW_OK', preview });
    state = reducer(state, { type: 'PARTS_ERROR', assetId: 'c'.repeat(64) });
    const req = createAnalysisRequest(state);
    expect(req?.objects).toEqual([{ id: 'model.stp', geometry: preview.geometry }]);
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

describe('stale result invalidation', () => {
  function stateWithFreshResult(): ProjectState {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: 'k1' });
    state = reducer(state, { type: 'ANALYZE_OK', version: 1, requestKey: 'k1', result: mockPipelineResult });
    return state;
  }

  function supportedPreview(sourceName: string): GeometryPreview {
    return {
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
      source_name: sourceName,
    };
  }

  it('marks stale on PREVIEW_START', () => {
    const state = reducer(stateWithFreshResult(), { type: 'PREVIEW_START', temp: null, version: 1 });
    expect(state.lastResult).toEqual(mockPipelineResult);
    expect(state.stale).toBe(true);
  });

  it('marks stale on a supported PREVIEW_OK', () => {
    const state = reducer(stateWithFreshResult(), {
      type: 'PREVIEW_OK',
      preview: supportedPreview('case.stl'),
    });
    expect(state.lastResult).toEqual(mockPipelineResult);
    expect(state.stale).toBe(true);
  });

  it('marks stale on PREVIEW_ERROR', () => {
    const state = reducer(stateWithFreshResult(), {
      type: 'PREVIEW_ERROR',
      message: 'geometry parse failed',
      diagnostics: null,
    });
    expect(state.lastResult).toEqual(mockPipelineResult);
    expect(state.stale).toBe(true);
  });

  it('marks stale on UPDATE_DRAFT', () => {
    const state = reducer(stateWithFreshResult(), { type: 'UPDATE_DRAFT', patch: { units: 'mm' } });
    expect(state.lastResult).toEqual(mockPipelineResult);
    expect(state.stale).toBe(true);
  });

  it('marks stale on RUN_DROP_TEST', () => {
    const state = reducer(stateWithFreshResult(), {
      type: 'RUN_DROP_TEST',
      test: 'drop',
      config: { height_m: 0.75, surface: 'concrete', drop_count: 1, orientation: 'flat' },
    });
    expect(state.lastResult).toEqual(mockPipelineResult);
    expect(state.stale).toBe(true);
  });

  it('strips stale validation mode and section on RUN_DROP_TEST (W4-04/W9-02)', () => {
    // A previous RUN VALIDATION must not pin the shell chain on a drop test:
    // the draft and the global mode both go back to exploration, so
    // createAnalysisRequest cannot re-inject the stale validation mode.
    const base = reducer(initialState, { type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' });
    const validated = reducer(base, { type: 'SET_MODE', mode: 'validation' as ProjectMode });
    const withSection = reducer(validated, {
      type: 'UPDATE_DRAFT',
      patch: { validation: { material: 'ABS' } } as Partial<PipelineRequest>,
    });
    const dropped = reducer(withSection, {
      type: 'RUN_DROP_TEST',
      test: 'drop',
      config: { height_m: 0.75, surface: 'concrete', drop_count: 1, orientation: 'flat' },
    });
    expect(dropped.mode).toBe('exploration');
    expect(dropped.draft?.validation).toBeUndefined();
    const request = createAnalysisRequest(dropped);
    expect(request?.mode).toBe('exploration');
    expect(request?.validation).toBeUndefined();
  });

  it('strips baseline tolerance_profile on RUN_DROP_TEST so a test launch stays fast', () => {
    // The baseline project carries structure/impact/load_case/tolerance_profile
    // (first-run solver cost). A launched test must not inherit them:
    // structure/impact/load_case are already nulled by the reducer (mirroring
    // RUN_POPULATION); tolerance_profile is dropped here.
    const base = reducer(initialState, { type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' });
    const withSections = reducer(base, {
      type: 'UPDATE_DRAFT',
      patch: {
        structure: { type: 'shell_panel' },
        impact: { restitution: 0.3 },
        load_case: { kind: 'pressure' },
        tolerance_profile: { profile: 'relaxed' },
      } as Partial<PipelineRequest>,
    });
    const dropped = reducer(withSections, {
      type: 'RUN_DROP_TEST',
      test: 'drop',
      config: { height_m: 0.75, surface: 'concrete', drop_count: 1, orientation: 'flat' },
    });
    expect(dropped.draft?.structure ?? null).toBeNull();
    expect(dropped.draft?.impact ?? null).toBeNull();
    expect(dropped.draft?.load_case ?? null).toBeNull();
    expect(dropped.draft?.tolerance_profile).toBeUndefined();
    expect(dropped.draft?.drop_simulation).toBeDefined();
  });

  it('does not carry a leftover population run into a launched drop test', () => {
    // A previous Monte Carlo run leaves draft.population; launching a test
    // afterwards must not silently re-run the 10k-unit campaign.
    const withPopulation = reducer(initialState, {
      type: 'UPDATE_DRAFT',
      patch: { population: { sample_count: 10000 } } as Partial<PipelineRequest>,
    });
    const dropped = reducer(withPopulation, {
      type: 'RUN_DROP_TEST',
      test: 'drop',
      config: { height_m: 0.75, surface: 'concrete', drop_count: 1, orientation: 'flat' },
    });
    expect(dropped.draft?.population).toBeUndefined();
    const request = createAnalysisRequest(dropped);
    expect(request?.population).toBeUndefined();
  });

  it('clears draft population when switching back to exploration mode', () => {
    const withPopulation = reducer(initialState, {
      type: 'UPDATE_DRAFT',
      patch: { population: { sample_count: 10000 } } as Partial<PipelineRequest>,
    });
    const backToExploration = reducer(withPopulation, { type: 'SET_MODE', mode: 'exploration' });
    expect(backToExploration.draft?.population).toBeUndefined();
  });

  it('sets and consumes the baseline auto-run gate (LOAD_BASELINE_OK -> CONSUME_SKIP_AUTO_RUN)', () => {
    const loaded = reducer(initialState, { type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' });
    expect(loaded.skipAutoRun).toBe(true);
    const consumed = reducer(loaded, { type: 'CONSUME_SKIP_AUTO_RUN' });
    expect(consumed.skipAutoRun).toBe(false);
    // A plain upload path never sets the gate.
    expect(initialState.skipAutoRun).toBe(false);
  });

  it('strips a completed population run from the draft so later edits do not re-run it', () => {
    // W-pop: RUN_POPULATION leaves draft.population in place only until the
    // run completes; after ANALYZE_OK the spec is stripped so an unrelated
    // draft edit (inspector/material change) cannot silently re-run the
    // 10k-unit Monte Carlo through the explicit run path.
    let state = reducer(initialState, { type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' });
    state = reducer(state, { type: 'RUN_POPULATION' });
    expect(state.draft?.population).toBeDefined();

    const populationResult: PipelineResult = {
      ...mockPipelineResult,
      run_id: 'run-pop',
      population: { sample_count: 10000, failure_rate: 0.17 },
    };
    state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: 'pop-k1' });
    state = reducer(state, { type: 'ANALYZE_OK', version: 1, requestKey: 'pop-k1', result: populationResult });

    // The completed run retains its result but the draft no longer carries
    // the population spec.
    expect(state.lastResult).toEqual(populationResult);
    expect(state.draft?.population).toBeUndefined();

    // An unrelated edit afterwards must not resurrect the campaign.
    state = reducer(state, { type: 'UPDATE_DRAFT', patch: { units: 'mm' } });
    expect(state.draft?.units).toBe('mm');
    const request = createAnalysisRequest(state);
    expect(request?.population).toBeUndefined();
    expect(request?.units).toBe('mm');
  });

  it('strips a failed population run from the draft so later edits do not retry it', () => {
    // A FAILED population run (ANALYZE_ERROR, or a 200 run whose population
    // section failed and returned no population output) must also clear the
    // draft marker; otherwise the next draft edit retries the whole campaign.
    let state = reducer(initialState, { type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' });
    state = reducer(state, { type: 'RUN_POPULATION' });
    state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: 'pop-k1' });
    state = reducer(state, { type: 'ANALYZE_ERROR', version: 1, requestKey: 'pop-k1', message: 'boom' });
    expect(state.draft?.population).toBeUndefined();
    expect(createAnalysisRequest(state)?.population).toBeUndefined();

    // 200 run whose population section produced no output (result carries
    // population: null): the draft marker is still cleared.
    let second = reducer(initialState, { type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' });
    second = reducer(second, { type: 'RUN_POPULATION' });
    second = reducer(second, { type: 'ANALYZE_START', version: 1, requestKey: 'pop-k2' });
    second = reducer(second, { type: 'ANALYZE_OK', version: 1, requestKey: 'pop-k2', result: { ...mockPipelineResult, population: null } });
    expect(second.draft?.population).toBeUndefined();
  });

  it('keeps the draft untouched when a plain (non-population) analysis completes', () => {
    let state = reducer(initialState, { type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' });
    state = reducer(state, { type: 'START_EDIT_DRAFT' });
    state = reducer(state, { type: 'UPDATE_DRAFT', patch: { units: 'mm' } });
    const draftBefore = state.draft;
    state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: 'k1' });
    state = reducer(state, { type: 'ANALYZE_OK', version: 1, requestKey: 'k1', result: mockPipelineResult });
    // The draft object is preserved (same units edit, no population present).
    expect(state.draft).toEqual(draftBefore);
    expect(state.draft?.units).toBe('mm');
    expect(state.draft?.population).toBeUndefined();
  });

  it('strips stale validation mode and section on RUN_POPULATION (W4-04/W9-02)', () => {
    const base = reducer(initialState, { type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' });
    const validated = reducer(base, { type: 'SET_MODE', mode: 'validation' as ProjectMode });
    const withSection = reducer(validated, {
      type: 'UPDATE_DRAFT',
      patch: { validation: { material: 'ABS' } } as Partial<PipelineRequest>,
    });
    const populated = reducer(withSection, { type: 'RUN_POPULATION' });
    expect(populated.mode).toBe('exploration');
    expect(populated.draft?.validation).toBeUndefined();
    const request = createAnalysisRequest(populated);
    expect(request?.mode).toBe('exploration');
    expect(request?.validation).toBeUndefined();
  });

  it('marks stale on RUN_POPULATION in both Monte Carlo and worst-case modes', () => {
    const monteCarlo = reducer(stateWithFreshResult(), { type: 'RUN_POPULATION' });
    expect(monteCarlo.lastResult).toEqual(mockPipelineResult);
    expect(monteCarlo.stale).toBe(true);

    const worstCase = reducer(stateWithFreshResult(), { type: 'RUN_POPULATION', worst_case: true });
    expect(worstCase.lastResult).toEqual(mockPipelineResult);
    expect(worstCase.stale).toBe(true);
  });

  it('marks stale on SET_OBJECT_MATERIAL', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    state = reducer(state, { type: 'PREVIEW_OK', preview: supportedPreview('case.stl') });
    state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: 'k1' });
    state = reducer(state, { type: 'ANALYZE_OK', version: 1, requestKey: 'k1', result: mockPipelineResult });

    state = reducer(state, { type: 'SET_OBJECT_MATERIAL', objectId: 'case.stl', materialKey: 'ABS' });
    expect(state.lastResult).toEqual(mockPipelineResult);
    expect(state.stale).toBe(true);
  });

  it('marks stale on SET_DEFAULT_MATERIAL', () => {
    const state = reducer(stateWithFreshResult(), { type: 'SET_DEFAULT_MATERIAL', key: 'ABS' });
    expect(state.lastResult).toEqual(mockPipelineResult);
    expect(state.stale).toBe(true);
  });

  it('marks stale on PARTS_OK and PARTS_ERROR', () => {
    const assetId = 'd'.repeat(64);
    let state = reducer(initialState, {
      type: 'PREVIEW_OK',
      preview: {
        schema_id: 'gms.geometry-preview/1',
        supported: true,
        format: 'step',
        source_units: 'mm',
        geometry: {
          type: 'box',
          size: [1, 1, 1],
          units: 'm',
          transform: IDENTITY_TRANSFORM,
        },
        diagnostics: [],
        source_name: 'model.stp',
        display_asset: {
          asset_id: assetId,
          url: '/api/geometry/assets/' + assetId + '.glb',
          format: 'glb',
          parts: [{ id: 'part-0', name: 'A' }],
        },
      },
    });
    state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: 'k1' });
    state = reducer(state, { type: 'ANALYZE_OK', version: 1, requestKey: 'k1', result: mockPipelineResult });

    state = reducer(state, {
      type: 'PARTS_OK',
      assetId,
      parts: [
        {
          id: 'part-0',
          name: 'A',
          geometry: {
            type: 'mesh',
            vertices: [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            triangles: [[0, 1, 2]],
            units: 'm',
            transform: IDENTITY_TRANSFORM,
          },
        },
      ],
    });
    expect(state.lastResult).toEqual(mockPipelineResult);
    expect(state.stale).toBe(true);

    state = reducer(state, { type: 'PARTS_ERROR', assetId });
    expect(state.lastResult).toEqual(mockPipelineResult);
    expect(state.stale).toBe(true);
  });

  it('marks stale on DISCARD_DRAFT after draft edits', () => {
    let state = stateWithFreshResult();
    state = reducer(state, { type: 'START_EDIT_DRAFT' });
    state = reducer(state, { type: 'UPDATE_DRAFT', patch: { units: 'mm' } });
    state = reducer(state, { type: 'DISCARD_DRAFT' });
    expect(state.draft).toBeNull();
    expect(state.lastResult).toEqual(mockPipelineResult);
    expect(state.stale).toBe(true);
  });

  it('clears stale and stores the new result when analysis reruns', () => {
    let state = stateWithFreshResult();
    expect(state.stale).toBe(false);

    state = reducer(state, { type: 'UPDATE_DRAFT', patch: { units: 'mm' } });
    expect(state.stale).toBe(true);
    expect(state.lastResult).toEqual(mockPipelineResult);

    const newResult: PipelineResult = { ...mockPipelineResult, run_id: 'run-2' };
    // The rerun covers the edited configuration, so its request key is the
    // derived key of the current draft (ANALYZE_OK recomputes staleness by
    // comparing it against the completed run's key).
    const requestKey = createAnalysisRequestKey(createAnalysisRequest(state));
    state = reducer(state, { type: 'ANALYZE_START', version: 2, requestKey });
    state = reducer(state, { type: 'ANALYZE_OK', version: 2, requestKey, result: newResult });
    expect(state.stale).toBe(false);
    expect(state.lastResult).toEqual(newResult);
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
    // RUN_STUDY without geometry is a feedback no-op, so the test loads a
    // baseline project first (mirroring the real launch path).
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    state = reducer(state, { type: 'RUN_STUDY' });
    expect(state.runNonce).toBe(1);
  });

  it('sets runError and stays idle when RUN_STUDY is dispatched without geometry', () => {
    const state = reducer(initialState, { type: 'RUN_STUDY' });
    expect(state.runNonce).toBe(0);
    expect(state.runStatus).toBe('idle');
    expect(state.runError).toBe('Load a model before running an analysis.');
  });

  it('prefills the Monte Carlo population draft and bumps the run nonce on RUN_POPULATION', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    expect(state.runNonce).toBe(0);

    state = reducer(state, { type: 'RUN_POPULATION' });
    expect(state.runNonce).toBe(1);
    expect(state.draft?.population).toEqual({
      sample_count: 10000,
      profile: 'esports_fps',
      lifespan_days: 730,
    });
    expect(state.draft?.drop_simulation).toMatchObject({
      test: 'drop',
      height_m: 0.75,
      drop_count: 1,
      surface: 'concrete',
      orientation: 'flat',
    });
    expect(state.draft?.impact).toBeNull();
    expect(state.draft?.load_case).toBeNull();
    expect(state.draft?.structure).toBeNull();

    const nonceBefore = state.runNonce;
    state = reducer(state, { type: 'RUN_POPULATION' });
    expect(state.runNonce).toBe(nonceBefore + 1);
  });

  it('carries the deterministic worst_case spec in the RUN_POPULATION worst-case draft', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });

    state = reducer(state, { type: 'RUN_POPULATION', worst_case: true });
    expect(state.draft?.population).toEqual({
      sample_count: 10000,
      profile: 'esports_fps',
      lifespan_days: 730,
      worst_case: {
        wall_thickness: 'min',
        shell_modulus: 'min',
        shell_strength: 'min',
        shell_density: 'max',
        com_offset: 'max',
        drop_height: 2,
        orientation: 'corner',
      },
    });
    expect(state.draft?.drop_simulation).toMatchObject({ test: 'drop', orientation: 'flat' });
  });

  it('prefills drop test configs through RUN_DROP_TEST with exclusive nulls', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });

    state = reducer(state, { type: 'RUN_DROP_TEST', test: 'drop', config: { height_m: 0.75, surface: 'concrete', drop_count: 1, orientation: 'flat' } });
    expect(state.draft?.drop_simulation).toMatchObject({
      test: 'drop',
      height_m: 0.75,
      surface: 'concrete',
      drop_count: 1,
      orientation: 'flat',
    });
    expect(state.draft?.impact).toBeNull();
    expect(state.draft?.load_case).toBeNull();
    expect(state.draft?.structure).toBeNull();

    state = reducer(state, { type: 'RUN_DROP_TEST', test: 'tumble', config: { height_m: 0.75, surface: 'foam', drop_count: 2, orientation: 'random', spin_rps: 3 } });
    expect(state.draft?.drop_simulation).toMatchObject({ test: 'tumble', spin_rps: 3, surface: 'foam' });

    state = reducer(state, { type: 'RUN_DROP_TEST', test: 'impact', config: { height_m: 1.0, surface: 'steel', drop_count: 1, orientation: 'corner' } });
    expect(state.draft?.drop_simulation).toMatchObject({ test: 'impact', surface: 'steel' });
  });

  it('preserves zero spin and an explicit mass in the RUN_DROP_TEST payload', () => {
    // Regression: truthiness guards dropped spin_rps = 0, so a tumble
    // launched with no release spin silently ran at the backend's 6 rev/s
    // tumble default — the launched drop was not the requested drop.
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    state = reducer(state, {
      type: 'RUN_DROP_TEST',
      test: 'tumble',
      config: { height_m: 0.75, surface: 'concrete', drop_count: 2, orientation: 'random', spin_rps: 0, mass_kg: 0.05 },
    });
    expect(state.draft?.drop_simulation).toMatchObject({
      test: 'tumble',
      spin_rps: 0,
      mass_kg: 0.05,
    });
    // Non-tumble tests keep spin 0 in the payload too (harmless, explicit)
    // while a null mass means "derive from the mass model" and stays absent.
    state = reducer(state, {
      type: 'RUN_DROP_TEST',
      test: 'drop',
      config: { height_m: 0.75, surface: 'concrete', drop_count: 1, orientation: 'flat', spin_rps: 0, mass_kg: null },
    });
    const config = state.draft?.drop_simulation as Record<string, unknown>;
    expect(config.spin_rps).toBe(0);
    expect(config.mass_kg).toBeUndefined();
  });

  it('clears active test and returns to normal resting mode with LEAVE_TEST', () => {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    state = reducer(state, {
      type: 'RUN_DROP_TEST',
      test: 'drop',
      config: { height_m: 0.75, surface: 'concrete', drop_count: 1, orientation: 'flat' },
    });
    expect(state.draft?.drop_simulation).toBeDefined();

    state = reducer(state, { type: 'SET_RENDER_MODE', mode: 'yield' });
    expect(state.renderMode).toBe('yield');

    state = reducer(state, { type: 'LEAVE_TEST' });
    expect(state.draft).toBeNull();
    expect(state.stale).toBe(false);
    expect(state.runStatus).toBe('idle');
    expect(state.playbackDismissed).toBe(true);
    expect(state.renderMode).toBe('default');
  });

  it('LEAVE_TEST keeps results and the FEA field for the normal-mode preview', () => {
    const feaResult = {
      computed: true,
      peak: null,
      yield_stress_pa: 5.6e7,
      safety_factor: 1.17,
      impact_window_s: 0.3,
      dent_threshold: 0.7,
      tear_threshold: 0.92,
      objects: [],
      procedural: [],
      assumptions: [],
      flags: [],
    };
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: 'k1' });
    state = reducer(state, {
      type: 'ANALYZE_OK',
      version: 1,
      requestKey: 'k1',
      result: { ...mockPipelineResult, fea: feaResult },
    });
    expect(state.lastResult).not.toBeNull();
    expect(state.feaResult).toEqual(feaResult);

    state = reducer(state, { type: 'LEAVE_TEST' });
    expect(state.lastResult).toEqual({ ...mockPipelineResult, fea: feaResult });
    expect(state.feaResult).toEqual(feaResult);
    expect(state.playbackDismissed).toBe(true);
    expect(state.stale).toBe(false);
    expect(state.runStatus).toBe('idle');

    // A fresh test launch re-arms the playback (and disables the preview).
    state = reducer(state, {
      type: 'RUN_DROP_TEST',
      test: 'drop',
      config: { height_m: 0.75, surface: 'concrete', drop_count: 1, orientation: 'flat' },
    });
    expect(state.playbackDismissed).toBe(false);
    expect(state.lastResult).toEqual({ ...mockPipelineResult, fea: feaResult });
  });

  it('builds a cheap deterministic watcher key from the analysis request', () => {
    // The watcher key must fingerprint geometry instead of serializing it:
    // a large uploaded mesh must produce a tiny, stable key.
    const meshPreview: GeometryPreview = {
      schema_id: 'gms.geometry-preview/1',
      supported: true,
      format: 'obj',
      source_units: 'mm',
      geometry: {
        type: 'mesh',
        vertices: Array.from({ length: 2000 }, (_, i) => [i * 0.001, 0.0, 0.0]),
        triangles: Array.from({ length: 3000 }, (_, i) => [i % 2000, (i + 1) % 2000, (i + 2) % 2000]),
        units: 'm',
        transform: IDENTITY_TRANSFORM,
      },
      diagnostics: [],
      source_name: 'big.obj',
    };
    let state = reducer(initialState, { type: 'PREVIEW_OK', preview: meshPreview });
    const request = createAnalysisRequest(state);
    expect(request).not.toBeNull();
    const key = createAnalysisRequestKey(request);
    // Far below the raw geometry size (raw JSON is hundreds of KB).
    expect(key.length).toBeLessThan(300);
    expect(key).toContain('geo:mesh');

    // Deterministic: identical request -> identical key.
    expect(createAnalysisRequestKey(request)).toBe(key);

    // A geometry change (vertex count) changes the key.
    const larger: GeometryPreview = {
      ...meshPreview,
      geometry: {
        type: 'mesh',
        vertices: Array.from({ length: 2001 }, (_, i) => [i * 0.001, 0.0, 0.0]),
        triangles: meshPreview.geometry!.type === 'mesh' ? meshPreview.geometry!.triangles : [],
        units: 'm',
        transform: IDENTITY_TRANSFORM,
      },
    };
    state = reducer(initialState, { type: 'PREVIEW_OK', preview: larger });
    expect(createAnalysisRequestKey(createAnalysisRequest(state))).not.toBe(key);

    // A geometry-less draft edit also changes the key (units ride along).
    state = reducer(state, { type: 'UPDATE_DRAFT', patch: { units: 'cm' } });
    expect(createAnalysisRequestKey(createAnalysisRequest(state))).not.toBe(
      createAnalysisRequestKey(request),
    );

    // Null request -> stable null key.
    expect(createAnalysisRequestKey(null)).toBe('null');
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

describe('run lifecycle regressions (CANCEL_RUN, launch guards, dedup, mid-run edits)', () => {
  function stateWithFreshResult(): ProjectState {
    let state = reducer(initialState, {
      type: 'LOAD_BASELINE_OK',
      project: mockBaselineProject,
      name: 'mouse_baseline',
    });
    const key = createAnalysisRequestKey(createAnalysisRequest(state));
    state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: key });
    state = reducer(state, {
      type: 'ANALYZE_OK',
      version: 1,
      requestKey: key,
      result: mockPipelineResult,
    });
    return state;
  }

  function meshParseInProgressState(): ProjectState {
    return reducer(initialState, {
      type: 'PREVIEW_START',
      version: 1,
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
  }

  describe('mid-run input changes and stale bookkeeping', () => {
    it('mid-run input change keeps result stale', () => {
      let state = stateWithFreshResult();
      expect(state.stale).toBe(false);

      const keyBeforeEdit = createAnalysisRequestKey(createAnalysisRequest(state));
      state = reducer(state, { type: 'ANALYZE_START', version: 2, requestKey: keyBeforeEdit });
      expect(state.runStatus).toBe('running');
      expect(state.stale).toBe(false);

      // The draft changes BETWEEN ANALYZE_START and ANALYZE_OK: the
      // completing result no longer matches the current inputs, so the
      // synchronous stale marking must survive ANALYZE_OK.
      state = reducer(state, { type: 'UPDATE_DRAFT', patch: { units: 'mm' } });
      expect(state.stale).toBe(true);

      const newResult: PipelineResult = { ...mockPipelineResult, run_id: 'run-2' };
      state = reducer(state, {
        type: 'ANALYZE_OK',
        version: 2,
        requestKey: keyBeforeEdit,
        result: newResult,
      });
      expect(state.runStatus).toBe('success');
      expect(state.lastResult).toEqual(newResult);
      expect(state.stale).toBe(true);
    });

    it('re-running the same request clears stale', () => {
      let state = stateWithFreshResult();
      state = reducer(state, { type: 'UPDATE_DRAFT', patch: { units: 'mm' } });
      expect(state.stale).toBe(true);

      // Run the request matching the edited draft: the fresh result belongs
      // to the current configuration and must clear the stale flag.
      const rerunKey = createAnalysisRequestKey(createAnalysisRequest(state));
      state = reducer(state, { type: 'ANALYZE_START', version: 2, requestKey: rerunKey });
      expect(state.runStatus).toBe('running');

      const newResult: PipelineResult = { ...mockPipelineResult, run_id: 'run-2' };
      state = reducer(state, {
        type: 'ANALYZE_OK',
        version: 2,
        requestKey: rerunKey,
        result: newResult,
      });
      expect(state.runStatus).toBe('success');
      expect(state.lastResult).toEqual(newResult);
      expect(state.stale).toBe(false);
    });
  });

  describe('CANCEL_RUN lifecycle', () => {
    it('CANCEL_RUN cancels a running run and drops late responses', () => {
      let state = reducer(initialState, {
        type: 'LOAD_BASELINE_OK',
        project: mockBaselineProject,
        name: 'mouse_baseline',
      });
      state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: 'k1' });
      expect(state.runStatus).toBe('running');
      const versionBefore = state.requestVersion;
      const cancelNonceBefore = state.cancelNonce;

      state = reducer(state, { type: 'CANCEL_RUN' });
      expect(state.runStatus).toBe('idle');
      expect(state.cancelNonce).toBe(cancelNonceBefore + 1);
      expect(state.requestVersion).toBe(versionBefore + 1);
      expect(state.runError).toBeNull();
      expect(state.inflightRequestKey).toBeNull();

      // A late ANALYZE_OK from the cancelled request (old version) is
      // dropped: no result overwrite, the run stays idle.
      const cancelled = state;
      const late = reducer(state, {
        type: 'ANALYZE_OK',
        version: versionBefore,
        requestKey: 'k1',
        result: mockPipelineResult,
      });
      expect(late).toBe(cancelled);
      expect(late.lastResult).toBeNull();
      expect(late.runStatus).toBe('idle');

      // A late ANALYZE_ERROR is dropped the same way.
      const lateError = reducer(state, {
        type: 'ANALYZE_ERROR',
        version: versionBefore,
        requestKey: 'k1',
        message: 'late failure',
      });
      expect(lateError).toBe(cancelled);
    });

    it('CANCEL_RUN is a no-op when idle', () => {
      let state = reducer(initialState, {
        type: 'LOAD_BASELINE_OK',
        project: mockBaselineProject,
        name: 'mouse_baseline',
      });
      const before = state;
      const next = reducer(state, { type: 'CANCEL_RUN' });
      expect(next).toBe(before);
      expect(next.runStatus).toBe('idle');
      expect(next.requestVersion).toBe(before.requestVersion);
      expect(next.cancelNonce).toBe(before.cancelNonce);

      // A completed (success) run is a no-op for CANCEL_RUN too.
      state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: 'k1' });
      state = reducer(state, {
        type: 'ANALYZE_OK',
        version: 1,
        requestKey: 'k1',
        result: mockPipelineResult,
      });
      const afterSuccess = state;
      expect(reducer(state, { type: 'CANCEL_RUN' })).toBe(afterSuccess);
    });

    it('CANCEL_RUN strips the one-shot population spec from the draft', () => {
      let state = reducer(initialState, {
        type: 'LOAD_BASELINE_OK',
        project: mockBaselineProject,
        name: 'mouse_baseline',
      });
      state = reducer(state, { type: 'RUN_POPULATION' });
      expect(state.draft?.population).toBeDefined();
      // The population fetch is aborted before any ANALYZE_OK/ERROR, so the
      // draft strip that normally runs at completion never fires.
      state = reducer(state, { type: 'CANCEL_RUN' });
      expect(state.draft?.population).toBeUndefined();
    });
  });

  describe('RUN_STUDY launch guards and deduplication', () => {
    it('RUN_STUDY with no geometry sets feedback and does not bump nonce', () => {
      const state = reducer(initialState, { type: 'RUN_STUDY' });
      expect(state.runStatus).toBe('idle');
      expect(state.runError).toBe('Load a model before running an analysis.');
      expect(state.runNonce).toBe(0);
    });

    it('RUN_STUDY while mesh parse is in progress sets feedback and does not bump nonce', () => {
      const parsing = meshParseInProgressState();
      expect(parsing.tempPreview).not.toBeNull();
      expect(parsing.preview).toBeNull();

      const state = reducer(parsing, { type: 'RUN_STUDY' });
      expect(state.runStatus).toBe('idle');
      expect(state.runError).toBe('Model import in progress — wait for it to finish before running.');
      expect(state.runNonce).toBe(0);
    });

    it('RUN_STUDY is deduplicated when the identical request is already running', () => {
      let state = reducer(initialState, {
        type: 'LOAD_BASELINE_OK',
        project: mockBaselineProject,
        name: 'mouse_baseline',
      });
      const key = createAnalysisRequestKey(createAnalysisRequest(state));
      state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: key });
      expect(state.runStatus).toBe('running');
      expect(state.inflightRequestKey).toBe(key);

      const nonceBefore = state.runNonce;
      const next = reducer(state, { type: 'RUN_STUDY' });
      // Identical in-flight request: the launch returns the same state
      // object without bumping the run nonce.
      expect(next).toBe(state);
      expect(next.runNonce).toBe(nonceBefore);
      expect(next.runStatus).toBe('running');
    });

    it('RUN_STUDY supersedes a different request while one is running', () => {
      let state = reducer(initialState, {
        type: 'LOAD_BASELINE_OK',
        project: mockBaselineProject,
        name: 'mouse_baseline',
      });
      const key = createAnalysisRequestKey(createAnalysisRequest(state));
      state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: key });
      state = reducer(state, { type: 'UPDATE_DRAFT', patch: { units: 'cm' } });

      const nonceBefore = state.runNonce;
      const next = reducer(state, { type: 'RUN_STUDY' });
      expect(next.runNonce).toBe(nonceBefore + 1);
      expect(next.runStatus).toBe('loading');
      expect(next.runError).toBeNull();
    });

    it('RUN_STUDY strips a leftover population spec so plain runs stay plain', () => {
      let state = reducer(initialState, {
        type: 'LOAD_BASELINE_OK',
        project: mockBaselineProject,
        name: 'mouse_baseline',
      });
      state = reducer(state, { type: 'RUN_POPULATION' });
      expect(state.draft?.population).toBeDefined();
      // Simulate the interrupted-population case: the run never completed,
      // so the ANALYZE_OK/ERROR strip never fired, and the draft still
      // carries the 10k-unit spec.
      state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: 'pop' });
      state = reducer(state, {
        type: 'ANALYZE_ERROR',
        version: 2,
        requestKey: 'pop',
        message: 'aborted by a newer run',
      });
      expect(state.draft?.population).toBeDefined();

      const next = reducer(state, { type: 'RUN_STUDY' });
      expect(next.draft?.population).toBeUndefined();
      expect(next.runStatus).toBe('loading');
      expect(next.runNonce).toBe(state.runNonce + 1);
    });
  });

  describe('RUN_DROP_TEST and RUN_POPULATION deduplication', () => {
    const dropConfig = {
      height_m: 0.75,
      surface: 'concrete',
      drop_count: 3,
      orientation: 'flat',
    } as const;

    it('RUN_DROP_TEST is deduplicated when the identical test is already running', () => {
      let state = reducer(initialState, {
        type: 'LOAD_BASELINE_OK',
        project: mockBaselineProject,
        name: 'mouse_baseline',
      });
      // Two launches first, mirroring the App's double-click flow: the first
      // launch builds the draft from the project, the second rebuilds it into
      // the canonical draft shape the dedup key is compared against.
      state = reducer(state, { type: 'RUN_DROP_TEST', test: 'drop', config: dropConfig });
      state = reducer(state, { type: 'RUN_DROP_TEST', test: 'drop', config: dropConfig });
      const inflightKey = createAnalysisRequestKey(createAnalysisRequest(state));
      state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: inflightKey });
      expect(state.runStatus).toBe('running');

      const nonceBefore = state.runNonce;
      const next = reducer(state, { type: 'RUN_DROP_TEST', test: 'drop', config: dropConfig });
      // Identical in-flight test: the launch returns the same state object
      // without bumping the run nonce.
      expect(next).toBe(state);
      expect(next.runNonce).toBe(nonceBefore);
      expect(next.runStatus).toBe('running');
    });

    it('RUN_DROP_TEST from a fresh baseline deduplicates an identical in-flight relaunch', () => {
      // Regression: the request key is canonical (order-insensitive), so a
      // relaunch that rebuilds the draft with a different key insertion order
      // still compares equal to the in-flight request and deduplicates —
      // even when the in-flight draft was built directly from the project.
      let state = reducer(initialState, {
        type: 'LOAD_BASELINE_OK',
        project: mockBaselineProject,
        name: 'mouse_baseline',
      });
      state = reducer(state, { type: 'RUN_DROP_TEST', test: 'drop', config: dropConfig });
      const inflightKey = createAnalysisRequestKey(createAnalysisRequest(state));
      state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: inflightKey });
      expect(state.runStatus).toBe('running');

      const nonceBefore = state.runNonce;
      const next = reducer(state, { type: 'RUN_DROP_TEST', test: 'drop', config: dropConfig });
      expect(next).toBe(state);
      expect(next.runNonce).toBe(nonceBefore);
      expect(next.runStatus).toBe('running');
    });

    it('RUN_DROP_TEST is not deduplicated when the test config differs', () => {
      let state = reducer(initialState, {
        type: 'LOAD_BASELINE_OK',
        project: mockBaselineProject,
        name: 'mouse_baseline',
      });
      state = reducer(state, { type: 'RUN_DROP_TEST', test: 'drop', config: dropConfig });
      state = reducer(state, { type: 'RUN_DROP_TEST', test: 'drop', config: dropConfig });
      const inflightKey = createAnalysisRequestKey(createAnalysisRequest(state));
      state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: inflightKey });

      const nonceBefore = state.runNonce;
      const next = reducer(state, {
        type: 'RUN_DROP_TEST',
        test: 'drop',
        config: { ...dropConfig, height_m: 1.5 },
      });
      expect(next).not.toBe(state);
      expect(next.runNonce).toBe(nonceBefore + 1);
      expect(next.runStatus).toBe('loading');
    });

    it('RUN_POPULATION is deduplicated when the identical population run is already running', () => {
      let state = reducer(initialState, {
        type: 'LOAD_BASELINE_OK',
        project: mockBaselineProject,
        name: 'mouse_baseline',
      });
      // See RUN_DROP_TEST above: two launches first so the in-flight key is
      // compared against the canonical rebuilt draft.
      state = reducer(state, { type: 'RUN_POPULATION' });
      state = reducer(state, { type: 'RUN_POPULATION' });
      const inflightKey = createAnalysisRequestKey(createAnalysisRequest(state));
      state = reducer(state, { type: 'ANALYZE_START', version: 1, requestKey: inflightKey });
      expect(state.runStatus).toBe('running');

      const nonceBefore = state.runNonce;
      const next = reducer(state, { type: 'RUN_POPULATION' });
      expect(next).toBe(state);
      expect(next.runNonce).toBe(nonceBefore);
    });

    it('RUN_DROP_TEST and RUN_POPULATION keep the old status logic while a mesh parses (observed behavior)', () => {
      // The parse-in-progress guard exists on RUN_STUDY only; drop tests and
      // population runs still bump the nonce and fall back to the project/
      // preview status check while the mesh is being parsed.
      let state = meshParseInProgressState();
      state = reducer(state, { type: 'RUN_DROP_TEST', test: 'drop', config: dropConfig });
      expect(state.runNonce).toBe(1);
      expect(state.runStatus).toBe('idle');
      expect(state.runError).toBeNull();

      const populated = reducer(state, { type: 'RUN_POPULATION' });
      expect(populated.runNonce).toBe(2);
      expect(populated.runStatus).toBe('idle');
      expect(populated.runError).toBeNull();
    });
  });
});
