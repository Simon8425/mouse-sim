import { describe, it, expect } from 'vitest';
import { reducer, initialState } from '../state/projectStore';

describe('multi-selection', () => {
  it('SELECT single id stores one entry and syncs selectedId', () => {
    let s = reducer(initialState, { type: 'SELECT', id: 'a' });
    expect(s.selectedIds).toEqual(['a']);
    expect(s.selectedId).toBe('a');
    s = reducer(s, { type: 'SELECT', id: 'b' });
    expect(s.selectedIds).toEqual(['b']);
    expect(s.selectedId).toBe('b');
  });

  it('SELECT null clears both fields', () => {
    let s = reducer(initialState, { type: 'SELECT', id: 'a' });
    s = reducer(s, { type: 'SELECT', id: null });
    expect(s.selectedIds).toEqual([]);
    expect(s.selectedId).toBeNull();
  });

  it('SELECT_TOGGLE adds unknown ids and removes known ids, preserving order', () => {
    let s = reducer(initialState, { type: 'SELECT_TOGGLE', id: 'a' });
    expect(s.selectedIds).toEqual(['a']);
    s = reducer(s, { type: 'SELECT_TOGGLE', id: 'b' });
    expect(s.selectedIds).toEqual(['a', 'b']);
    expect(s.selectedId).toBe('b');
    s = reducer(s, { type: 'SELECT_TOGGLE', id: 'a' });
    expect(s.selectedIds).toEqual(['b']);
    expect(s.selectedId).toBe('b');
    s = reducer(s, { type: 'SELECT_TOGGLE', id: 'b' });
    expect(s.selectedIds).toEqual([]);
    expect(s.selectedId).toBeNull();
  });

  it('custom ids replace the whole set (Shift+click computed full list)', () => {
    let s = reducer(initialState, { type: 'SELECT', ids: ['a', 'b', 'c'] });
    expect(s.selectedIds).toEqual(['a', 'b', 'c']);
    expect(s.selectedId).toBe('c');
  });

  it('resetGeometryView clears the selection set', () => {
    let s = reducer(initialState, { type: 'SELECT', ids: ['a', 'b'] });
    s = reducer(s, { type: 'PREVIEW_START', temp: null, version: 1 });
    expect(s.selectedIds).toEqual([]);
    expect(s.selectedId).toBeNull();
  });
});
