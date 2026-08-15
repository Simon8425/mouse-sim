/**
 * Store tests for the AI classification actions.
 */
import { describe, expect, it } from 'vitest';
import { reducer, initialState, createAnalysisRequest, COMPONENT_ROLES } from '../state/projectStore';

describe('AI classification store actions', () => {
  it('CLASSIFY_START sets the job state', () => {
    const state = reducer(initialState, { type: 'CLASSIFY_START', jobId: 'cj-abc' });
    expect(state.classifyJob).toEqual({ jobId: 'cj-abc', status: 'queued', total: 0, done: 0, error: null });
  });

  it('CLASSIFY_POLL merges results and updates job progress', () => {
    let state = reducer(initialState, { type: 'CLASSIFY_START', jobId: 'cj-abc' });
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'running',
      total: 2,
      done: 1,
      error: null,
      results: [
        { object_id: 'part-0', component_type: 'scroll_wheel', confidence: 0.9 },
        { object_id: 'part-1', component_type: 'unresolved', confidence: 0.1 },
      ],
    });
    expect(state.classifyJob?.status).toBe('running');
    expect(state.classifyJob?.done).toBe(1);
    expect(state.aiClassifications['part-0']?.component_type).toBe('scroll_wheel');
    expect(state.aiClassifications['part-1']?.component_type).toBe('unresolved');
  });

  it('CLASSIFY_APPLY_ALL writes non-unresolved suggestions into roles', () => {
    let state = reducer(initialState, { type: 'CLASSIFY_START', jobId: 'cj-abc' });
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'done',
      total: 2,
      done: 2,
      error: null,
      results: [
        { object_id: 'part-0', component_type: 'scroll_wheel', confidence: 0.9 },
        { object_id: 'part-1', component_type: 'unresolved', confidence: 0.1 },
      ],
    });
    state = reducer(state, { type: 'CLASSIFY_APPLY_ALL' });
    expect(state.objectClassifications['part-0']).toBe('scroll_wheel');
    expect(state.objectClassifications['part-1']).toBeUndefined();
  });

  it('CLASSIFY_APPLY_ONE applies and clears a single suggestion', () => {
    let state = reducer(initialState, { type: 'CLASSIFY_START', jobId: 'cj-abc' });
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'done',
      total: 1,
      done: 1,
      error: null,
      results: [{ object_id: 'part-0', component_type: 'pcb', confidence: 0.8 }],
    });
    state = reducer(state, { type: 'CLASSIFY_APPLY_ONE', objectId: 'part-0' });
    expect(state.objectClassifications['part-0']).toBe('pcb');
    expect(state.aiClassifications['part-0']).toBeUndefined();
  });

  it('manual role assignment clears the AI suggestion for that part', () => {
    let state = reducer(initialState, { type: 'CLASSIFY_START', jobId: 'cj-abc' });
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'done',
      total: 1,
      done: 1,
      error: null,
      results: [{ object_id: 'part-0', component_type: 'battery', confidence: 0.9 }],
    });
    state = reducer(state, {
      type: 'SET_OBJECT_CLASSIFICATION',
      objectId: 'part-0',
      role: 'top_shell',
    });
    expect(state.objectClassifications['part-0']).toBe('top_shell');
    expect(state.aiClassifications['part-0']).toBeUndefined();
  });

  it('CLASSIFY_ERROR records the failure', () => {
    const state = reducer(initialState, { type: 'CLASSIFY_ERROR', message: 'boom' });
    expect(state.classifyJob?.status).toBe('error');
    expect(state.classifyJob?.error).toBe('boom');
  });

  it('resetGeometryView clears AI state on source reload', () => {
    let state = reducer(initialState, { type: 'CLASSIFY_START', jobId: 'cj-abc' });
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'done',
      total: 1,
      done: 1,
      error: null,
      results: [{ object_id: 'part-0', component_type: 'pcb', confidence: 0.9 }],
    });
    // LOAD_BASELINE_OK calls resetGeometryView.
    state = reducer(state, { type: 'LOAD_BASELINE_OK', project: {}, name: 'x' });
    expect(state.aiClassifications).toEqual({});
    expect(state.classifyJob).toBeNull();
  });

  it('createAnalysisRequest includes user-reviewed classifications', () => {
    let state = { ...initialState, preview: null as never, draft: null as never, project: null as never };
    // Use a single-body preview path to keep the fixture small.
    const geometry = {
      type: 'box',
      size: [0.06, 0.04, 0.01],
      units: 'm',
      transform: { rotation: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], translation: [0, 0, 0], units: 'm' },
    };
    state = {
      ...state,
      preview: { supported: true, geometry, source_name: 'test' } as never,
      objectClassifications: { test: 'top_shell' },
    };
    const request = createAnalysisRequest(state);
    const objects = request?.objects as Array<Record<string, unknown>> | undefined;
    expect(objects?.[0]?.classification).toEqual({ component_type: 'top_shell', confidence: 0.95, source: 'user' });
  });

  it('SET_CLASSIFY_MODAL_OPEN toggles the modal', () => {
    let state = reducer(initialState, { type: 'SET_CLASSIFY_MODAL_OPEN', open: true });
    expect(state.classifyModalOpen).toBe(true);
    state = reducer(state, { type: 'SET_CLASSIFY_MODAL_OPEN', open: false });
    expect(state.classifyModalOpen).toBe(false);
  });

  it('CLASSIFY_DISMISS_ALL clears suggestions and closes modal', () => {
    let state = reducer(initialState, { type: 'CLASSIFY_START', jobId: 'cj-abc' });
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'done',
      total: 1,
      done: 1,
      error: null,
      results: [{ object_id: 'part-0', component_type: 'pcb', confidence: 0.9 }],
    });
    state = reducer(state, { type: 'SET_CLASSIFY_MODAL_OPEN', open: true });
    state = reducer(state, { type: 'CLASSIFY_DISMISS_ALL' });
    expect(state.aiClassifications).toEqual({});
    expect(state.classifyModalOpen).toBe(false);
  });

  it('CLASSIFY_POLL auto-opens modal on completion when suggestions exist', () => {
    let state = reducer(initialState, { type: 'CLASSIFY_START', jobId: 'cj-abc' });
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'running',
      total: 1,
      done: 0,
      error: null,
      results: [],
    });
    expect(state.classifyModalOpen).toBe(false);
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'done',
      total: 1,
      done: 1,
      error: null,
      results: [{ object_id: 'part-0', component_type: 'top_shell', confidence: 0.95 }],
    });
    expect(state.classifyModalOpen).toBe(true);
  });

  it('dismissed suggestions are not resurrected by a later poll', () => {
    let state = reducer(initialState, { type: 'CLASSIFY_START', jobId: 'cj-abc' });
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'running',
      total: 1,
      done: 0,
      error: null,
      results: [{ object_id: 'part-0', component_type: 'top_shell', confidence: 0.95 }],
    });
    state = reducer(state, { type: 'CLASSIFY_CLEAR', objectId: 'part-0' });
    expect(state.aiClassifications['part-0']).toBeUndefined();
    // The job is still running: a later tick returns the same suggestion.
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'running',
      total: 1,
      done: 1,
      error: null,
      results: [{ object_id: 'part-0', component_type: 'top_shell', confidence: 0.95 }],
    });
    expect(state.aiClassifications['part-0']).toBeUndefined();
  });

  it('applied suggestions are not resurrected by a later poll', () => {
    let state = reducer(initialState, { type: 'CLASSIFY_START', jobId: 'cj-abc' });
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'running',
      total: 1,
      done: 0,
      error: null,
      results: [{ object_id: 'part-0', component_type: 'top_shell', confidence: 0.95 }],
    });
    state = reducer(state, { type: 'CLASSIFY_APPLY_ONE', objectId: 'part-0' });
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'done',
      total: 1,
      done: 1,
      error: null,
      results: [{ object_id: 'part-0', component_type: 'top_shell', confidence: 0.95 }],
    });
    expect(state.aiClassifications['part-0']).toBeUndefined();
    expect(state.objectClassifications['part-0']).toBe('top_shell');
  });

  it('manual role assignment is not overridden by a later poll', () => {
    let state = reducer(initialState, { type: 'CLASSIFY_START', jobId: 'cj-abc' });
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'running',
      total: 1,
      done: 0,
      error: null,
      results: [{ object_id: 'part-0', component_type: 'top_shell', confidence: 0.95 }],
    });
    state = reducer(state, {
      type: 'SET_OBJECT_CLASSIFICATION',
      objectId: 'part-0',
      role: 'battery',
    });
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'done',
      total: 1,
      done: 1,
      error: null,
      results: [{ object_id: 'part-0', component_type: 'top_shell', confidence: 0.95 }],
    });
    expect(state.aiClassifications['part-0']).toBeUndefined();
    expect(state.objectClassifications['part-0']).toBe('battery');
  });

  it('CLASSIFY_START clears tombstones for a fresh review cycle', () => {
    let state = reducer(initialState, { type: 'CLASSIFY_START', jobId: 'cj-abc' });
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'running',
      total: 1,
      done: 0,
      error: null,
      results: [{ object_id: 'part-0', component_type: 'top_shell', confidence: 0.95 }],
    });
    state = reducer(state, { type: 'CLASSIFY_CLEAR', objectId: 'part-0' });
    expect(state.dismissedClassifyIds['part-0']).toBe(true);
    // A new job resets the review cycle.
    state = reducer(state, { type: 'CLASSIFY_START', jobId: 'cj-new' });
    expect(state.dismissedClassifyIds).toEqual({});
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'done',
      total: 1,
      done: 1,
      error: null,
      results: [{ object_id: 'part-0', component_type: 'top_shell', confidence: 0.95 }],
    });
    expect(state.aiClassifications['part-0']?.component_type).toBe('top_shell');
  });

  it('resetGeometryView clears tombstones', () => {
    let state = reducer(initialState, { type: 'CLASSIFY_START', jobId: 'cj-abc' });
    state = reducer(state, {
      type: 'CLASSIFY_POLL',
      status: 'running',
      total: 1,
      done: 0,
      error: null,
      results: [{ object_id: 'part-0', component_type: 'top_shell', confidence: 0.95 }],
    });
    state = reducer(state, { type: 'CLASSIFY_CLEAR', objectId: 'part-0' });
    state = reducer(state, { type: 'LOAD_BASELINE_OK', project: {}, name: 'x' });
    expect(state.dismissedClassifyIds).toEqual({});
  });

  it('exposes the canonical role taxonomy', () => {
    expect(COMPONENT_ROLES.some((r) => r.value === 'top_shell')).toBe(true);
    expect(COMPONENT_ROLES.some((r) => r.value === 'scroll_wheel')).toBe(true);
    expect(COMPONENT_ROLES.some((r) => r.value === 'screw_boss')).toBe(true);
  });
});
