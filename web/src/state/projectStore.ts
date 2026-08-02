/**
 * Project workspace store: central UI state, reducer, and derived selectors.
 */
import * as React from 'react';
import type { Dispatch, ReactNode } from 'react';
import type {
  PipelineRequest,
  GeometryPreview,
  GeometryJson,
  MeshGeometryJson,
  ImportDiagnostic,
  WebHealth,
  MaterialEntry,
  PipelineResult,
} from '../api/contracts';
import { isGeometryJson, isRecord } from '../api/contracts';

/** Source-loading status of the pipeline baseline. */
export type SourceStatus = 'idle' | 'loading' | 'ready' | 'error';
/** Run status of the analysis pipeline. */
export type RunStatus = 'idle' | 'running' | 'success' | 'error';
/** Workspace mode: exploration or qualification. */
export type Mode = 'exploration' | 'qualification';

/** In-progress mesh preview held while analysis is pending. */
export interface TempPreview {
  id: string;
  name: string;
  geometry: MeshGeometryJson;
  diagnostics: string[];
}

/** Complete UI state of the project workspace. */
export interface ProjectState {
  sourceStatus: SourceStatus;
  sourceError: string | null;
  project: PipelineRequest | null;
  projectName: string;
  health: WebHealth | null;
  healthError: string | null;
  materials: MaterialEntry[] | null;
  preview: GeometryPreview | null;
  previewStatus: 'idle' | 'working' | 'ready' | 'error';
  previewError: string | null;
  previewDiagnostics: ImportDiagnostic[] | null;
  tempPreview: TempPreview | null;
  selectedId: string | null;
  visibility: Record<string, boolean>;
  isolatedId: string | null;
  explode: number;
  mode: Mode;
  requestVersion: number;
  runStatus: RunStatus;
  lastResult: PipelineResult | null;
  stale: boolean;
  runError: string | null;
  theme: 'light' | 'dark';
  draft: PipelineRequest | null;
  resultsTab: string;
  severityFilter: string | null;
  qualityTier: 'high' | 'medium' | 'low' | null;
  navOpen: boolean;
  inspectorOpen: boolean;
  webglError: string | null;
}

/** Union of all actions accepted by the project reducer. */
export type ProjectAction =
  | { type: 'LOAD_BASELINE_START' }
  | { type: 'LOAD_BASELINE_OK'; project: PipelineRequest; name: string }
  | { type: 'LOAD_BASELINE_ERROR'; message: string }
  | { type: 'HEALTH_OK'; health: WebHealth }
  | { type: 'HEALTH_ERROR'; message: string }
  | { type: 'MATERIALS_OK'; materials: MaterialEntry[] }
  | { type: 'MATERIALS_ERROR'; message: string }
  | { type: 'SET_MODE'; mode: Mode }
  | { type: 'SELECT'; id: string | null }
  | { type: 'TOGGLE_VISIBILITY'; id: string }
  | { type: 'SET_VISIBILITY'; id: string; visible: boolean }
  | { type: 'ISOLATE'; id: string }
  | { type: 'CLEAR_ISOLATION' }
  | { type: 'SET_EXPLODE'; factor: number }
  | { type: 'PREVIEW_START'; temp: TempPreview | null }
  | { type: 'PREVIEW_OK'; preview: GeometryPreview }
  | { type: 'PREVIEW_ERROR'; message: string; diagnostics: ImportDiagnostic[] | null }
  | { type: 'CLEAR_PREVIEW' }
  | { type: 'ANALYZE_START'; version: number }
  | { type: 'ANALYZE_OK'; version: number; result: PipelineResult }
  | { type: 'ANALYZE_ERROR'; version: number; message: string }
  | { type: 'SET_THEME'; theme: 'light' | 'dark' }
  | { type: 'UPDATE_DRAFT'; patch: Partial<PipelineRequest> }
  | { type: 'START_EDIT_DRAFT' }
  | { type: 'DISCARD_DRAFT' }
  | { type: 'APPLY_DRAFT' }
  | { type: 'SET_TAB'; tab: string }
  | { type: 'SET_SEVERITY_FILTER'; severity: string | null }
  | { type: 'SET_QUALITY_TIER'; tier: 'high' | 'medium' | 'low' | null }
  | { type: 'SET_NAV_OPEN'; open: boolean }
  | { type: 'SET_INSPECTOR_OPEN'; open: boolean }
  | { type: 'SET_WEBGL_ERROR'; message: string | null };

/** Pure reducer managing all project store state transitions. */
export function reducer(state: ProjectState, action: ProjectAction): ProjectState {
  switch (action.type) {
    case 'LOAD_BASELINE_START':
      return { ...state, sourceStatus: 'loading' };
    case 'LOAD_BASELINE_OK':
      return {
        ...state,
        project: action.project,
        projectName: action.name,
        sourceStatus: 'ready',
        sourceError: null,
      };
    case 'LOAD_BASELINE_ERROR':
      return {
        ...state,
        sourceStatus: 'error',
        sourceError: action.message,
      };
    case 'HEALTH_OK':
      return { ...state, health: action.health, healthError: null };
    case 'HEALTH_ERROR':
      return { ...state, healthError: action.message };
    case 'MATERIALS_OK':
      return { ...state, materials: action.materials };
    case 'MATERIALS_ERROR':
      return state;
    case 'SET_MODE':
      return { ...state, mode: action.mode, stale: state.lastResult != null };
    case 'SELECT':
      return { ...state, selectedId: action.id };
    case 'TOGGLE_VISIBILITY':
      return {
        ...state,
        visibility: { ...state.visibility, [action.id]: !(state.visibility[action.id] ?? true) },
      };
    case 'SET_VISIBILITY':
      return { ...state, visibility: { ...state.visibility, [action.id]: action.visible } };
    case 'ISOLATE':
      return { ...state, isolatedId: action.id };
    case 'CLEAR_ISOLATION':
      return { ...state, isolatedId: null };
    case 'SET_EXPLODE':
      return { ...state, explode: Math.min(1, Math.max(0, action.factor)) };
    case 'PREVIEW_START':
      return {
        ...state,
        tempPreview: action.temp,
        previewStatus: 'working',
        preview: null,
        previewError: null,
        previewDiagnostics: null,
      };
    case 'PREVIEW_OK':
      return {
        ...state,
        preview: action.preview,
        previewStatus: 'ready',
        tempPreview: null,
        previewError: null,
        previewDiagnostics: null,
      };
    case 'PREVIEW_ERROR':
      return {
        ...state,
        previewStatus: 'error',
        previewError: action.message,
        previewDiagnostics: action.diagnostics,
        preview: null,
        tempPreview: null,
      };
    case 'CLEAR_PREVIEW':
      return {
        ...state,
        preview: null,
        previewStatus: 'idle',
        tempPreview: null,
        previewError: null,
        previewDiagnostics: null,
      };
    case 'ANALYZE_START':
      return {
        ...state,
        requestVersion: action.version,
        runStatus: 'running',
        stale: state.lastResult != null,
        runError: null,
      };
    case 'ANALYZE_OK':
      if (action.version !== state.requestVersion) return state;
      return { ...state, runStatus: 'success', lastResult: action.result, stale: false, runError: null };
    case 'ANALYZE_ERROR':
      if (action.version !== state.requestVersion) return state;
      return { ...state, runStatus: 'error', stale: state.lastResult != null, runError: action.message };
    case 'UPDATE_DRAFT': {
      const base: PipelineRequest = state.draft ?? state.project ?? {};
      return { ...state, draft: { ...base, ...action.patch } };
    }
    case 'START_EDIT_DRAFT':
      return { ...state, draft: state.draft ?? { ...(state.project ?? {}) } };
    case 'DISCARD_DRAFT':
      return { ...state, draft: null };
    case 'APPLY_DRAFT':
      if (state.draft) {
        return { ...state, project: state.draft, draft: null, stale: state.lastResult != null };
      }
      return state;
    case 'SET_THEME':
      return { ...state, theme: action.theme };
    case 'SET_TAB':
      return { ...state, resultsTab: action.tab };
    case 'SET_SEVERITY_FILTER':
      return { ...state, severityFilter: action.severity };
    case 'SET_QUALITY_TIER':
      return { ...state, qualityTier: action.tier };
    case 'SET_NAV_OPEN':
      return { ...state, navOpen: action.open };
    case 'SET_INSPECTOR_OPEN':
      return { ...state, inspectorOpen: action.open };
    case 'SET_WEBGL_ERROR':
      return { ...state, webglError: action.message };
    default:
      return state;
  }
}

/** Default project store state. */
export const initialState: ProjectState = {
  sourceStatus: 'idle',
  sourceError: null,
  project: null,
  projectName: 'no project',
  health: null,
  healthError: null,
  materials: null,
  preview: null,
  previewStatus: 'idle',
  previewError: null,
  previewDiagnostics: null,
  tempPreview: null,
  selectedId: null,
  visibility: {},
  isolatedId: null,
  explode: 0,
  mode: 'exploration',
  requestVersion: 0,
  runStatus: 'idle',
  lastResult: null,
  stale: false,
  runError: null,
  theme: 'light',
  draft: null,
  resultsTab: 'overview',
  severityFilter: null,
  qualityTier: null,
  navOpen: false,
  inspectorOpen: false,
  webglError: null,
};

/** A flattened geometry entry derived from the current state. */
export interface ObjectEntry {
  id: string;
  geometry: GeometryJson;
  className: string | null;
}

/** Build the analysis request from the current state, or null when there is nothing to analyze. */
export function createAnalysisRequest(state: ProjectState): PipelineRequest | null {
  // While a mesh is still being parsed there is no canonical geometry yet.
  if (state.tempPreview && !state.preview) return null;
  // An uploaded geometry preview is analyzable on its own, without a project.
  if (!state.project && !state.draft && !state.preview) return null;
  const base: PipelineRequest = state.draft ?? state.project ?? {};
  const request: PipelineRequest = { ...base, mode: state.mode, units: base.units ?? 'mm' };
  if (state.preview?.geometry && isGeometryJson(state.preview.geometry)) {
    request.objects = [{ id: state.preview.source_name ?? 'upload', geometry: state.preview.geometry }];
  }
  return request;
}

/** Extract the component classification from a raw object entry, if present. */
function readClassification(raw: Record<string, unknown>): string | null {
  const cls = raw.classification;
  if (isRecord(cls) && typeof cls.component_type === 'string') {
    return cls.component_type;
  }
  return null;
}

/** Flatten all geometry sources (preview, temp preview, or project objects) into object entries. */
export function computeObjectEntries(state: ProjectState): ObjectEntry[] {
  if (state.preview?.geometry && isGeometryJson(state.preview.geometry)) {
    return [{ id: state.preview.source_name ?? 'upload', geometry: state.preview.geometry, className: null }];
  }
  if (state.tempPreview && !state.preview) {
    return [{ id: state.tempPreview.id, geometry: state.tempPreview.geometry, className: 'mesh' }];
  }
  const entries: ObjectEntry[] = [];
  const objects = state.project?.objects;
  if (Array.isArray(objects)) {
    for (let index = 0; index < objects.length; index++) {
      const item = objects[index];
      if (!isRecord(item)) continue;
      const id = (item.id ?? item.name ?? `object-${index}`) as string;
      const candidate = item.geometry ?? item.shape;
      if (isGeometryJson(candidate)) {
        entries.push({ id, geometry: candidate, className: readClassification(item) });
      }
    }
  } else if (isRecord(objects)) {
    for (const [key, item] of Object.entries(objects)) {
      if (!isRecord(item)) continue;
      const candidate = item.geometry ?? item.shape;
      if (isGeometryJson(candidate)) {
        entries.push({ id: key, geometry: candidate, className: readClassification(item) });
      }
    }
  }
  return entries;
}

interface ProjectStoreContextValue {
  state: ProjectState;
  dispatch: Dispatch<ProjectAction>;
}

const ProjectContext = React.createContext<ProjectStoreContextValue | undefined>(undefined);

/** React context provider for the project store. */
export function ProjectProvider({ children }: { children: ReactNode }): JSX.Element {
  const [state, dispatch] = React.useReducer(reducer, initialState);
  return React.createElement(ProjectContext.Provider, { value: { state, dispatch } }, children);
}

/** Access the project store; throws when used outside ProjectProvider. */
export function useProjectStore(): ProjectStoreContextValue {
  const context = React.useContext(ProjectContext);
  if (context === undefined) {
    throw new Error('useProjectStore must be used within ProjectProvider');
  }
  return context;
}
