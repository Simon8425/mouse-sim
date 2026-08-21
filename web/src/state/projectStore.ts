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
  FeaResult,
  RenderMode,
  AiClassification,
  DropSimulationConfig,
} from '../api/contracts';
import { isGeometryJson, isRecord } from '../api/contracts';

/** Source-loading status of the pipeline baseline. */
export type SourceStatus = 'idle' | 'loading' | 'ready' | 'error';
/** Run status of the analysis pipeline. */
export type RunStatus = 'idle' | 'loading' | 'running' | 'success' | 'error';
/** Workspace mode: exploration, qualification, or shell validation. */
export type Mode = 'exploration' | 'qualification' | 'validation';

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
  /** Primary selection (last-picked object id; null when nothing is selected). */
  selectedId: string | null;
  /** Full selection set; Shift+click toggles members (multi-select). */
  selectedIds: string[];
  visibility: Record<string, boolean>;
  isolatedId: string | null;
  explode: number;
  mode: Mode;
  requestVersion: number;
  runStatus: RunStatus;
  lastResult: PipelineResult | null;
  resultRequestKey: string | null;
  /** Request key of the analysis currently in flight (null when none). */
  inflightRequestKey: string | null;
  stale: boolean;
  runError: string | null;
  /** Monotonic token bumped by CANCEL_RUN; the App aborts the fetch on change. */
  cancelNonce: number;
  theme: 'light' | 'dark';
  draft: PipelineRequest | null;
  resultsTab: string;
  severityFilter: string | null;
  qualityTier: 'ultra' | 'high' | 'medium' | 'low' | null;
  navOpen: boolean;
  inspectorOpen: boolean;
  /** Telemetry Log Debugger drawer visibility. */
  debuggerOpen: boolean;
  webglError: string | null;
  previewRequestVersion: number;
  controlOpen: boolean;
  controlMode: 'settings' | 'simulation';
  runNonce: number;
  objectMaterials: Record<string, string>;
  objectClassifications: Record<string, string>;
  /** AI component-type suggestions (objectId → suggestion) from /api/classify. */
  aiClassifications: Record<string, AiClassification>;
  /**
   * Object ids the user dismissed / applied / manually overrode during an
   * in-flight classify job.  CLASSIFY_POLL skips these so a late poll tick
   * cannot resurrect a dismissed suggestion.
   */
  dismissedClassifyIds: Record<string, true>;
  /** AI provider, model name, and API key settings */
  aiConfig: AiConfig;
  /** Active classify job state (null when idle). */
  classifyJob: { jobId: string; status: string; total: number; done: number; error: string | null } | null;
  /** Whether the AI classification review modal is open. */
  classifyModalOpen: boolean;
  defaultMaterialKey: string;
  partGeometry: Record<string, GeometryJson> | null;
  /** Viewport material mode: default palette, FEA heatmap, or yield shader. */
  renderMode: RenderMode;
  /** Per-vertex FEA damage field from the last completed run (null when none). */
  feaResult: FeaResult | null;
  /** True after LEAVE_TEST: results stay visible but drop playback is off. */
  playbackDismissed: boolean;
  /** True while the drop playback is animating (the FEA switch is hidden during drops). */
  dropPlaying: boolean;
  /** Legacy load gate retained for persisted/reducer compatibility. */
  skipAutoRun: boolean;
}

export interface AiConfig {
  provider: string;
  model: string;
  apiKey: string;
  endpoint?: string;
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
  /**
   * Set the selection. Without `ids`, `id` selects a single object (null
   * clears). With `ids`, the full selection-set is replaced (used by
   * Shift+click toggling). `selectedId` always mirrors the last entry of
   * `selectedIds` for backward compatibility.
   */
  | { type: 'SELECT'; id?: string | null; ids?: string[] }
  | { type: 'SELECT_TOGGLE'; id: string }
  | { type: 'TOGGLE_VISIBILITY'; id: string }
  | { type: 'SET_VISIBILITY'; id: string; visible: boolean }
  | { type: 'ISOLATE'; id: string }
  | { type: 'CLEAR_ISOLATION' }
  | { type: 'SET_EXPLODE'; factor: number }
  | { type: 'PREVIEW_START'; temp: TempPreview | null; version?: number }
  | { type: 'PREVIEW_OK'; preview: GeometryPreview; version?: number }
  | {
      type: 'PREVIEW_ERROR';
      message: string;
      diagnostics: ImportDiagnostic[] | null;
      preview?: GeometryPreview | null;
      version?: number;
    }
  | { type: 'CLEAR_PREVIEW' }
  | { type: 'CONSUME_SKIP_AUTO_RUN' }
  | { type: 'ANALYZE_START'; version: number; requestKey: string }
  | { type: 'ANALYZE_OK'; version: number; requestKey: string; result: PipelineResult }
  | { type: 'ANALYZE_ERROR'; version: number; requestKey: string; message: string }
  | { type: 'CANCEL_RUN' }
  | { type: 'SET_THEME'; theme: 'light' | 'dark' }
  | { type: 'UPDATE_DRAFT'; patch: Partial<PipelineRequest> }
  | { type: 'SET_FLOOR'; surface: 'concrete' | 'wood' | 'foam' | 'steel' }
  | { type: 'SET_DROP_TEST_CONFIG'; patch: Record<string, unknown> }
  | {
      type: 'RUN_DROP_TEST';
      test: 'drop' | 'impact' | 'tumble' | 'population';
      config: {
        height_m: number;
        surface: 'concrete' | 'wood' | 'foam' | 'steel';
        drop_count: number;
        orientation: 'flat' | 'edge' | 'corner' | 'random';
        spin_rps?: number;
        mass_kg?: number | null;
        seed?: number;
        pause_between_drops_s?: number;
        structure?: Record<string, unknown> | null;
        load_case?: Record<string, unknown> | null;
      };
    }
  | { type: 'START_EDIT_DRAFT' }
  | { type: 'DISCARD_DRAFT' }
  | { type: 'APPLY_DRAFT' }
  | { type: 'LEAVE_TEST' }
  | { type: 'SET_TAB'; tab: string }
  | { type: 'SET_SEVERITY_FILTER'; severity: string | null }
  | { type: 'SET_QUALITY_TIER'; tier: 'ultra' | 'high' | 'medium' | 'low' | null }
  | { type: 'SET_NAV_OPEN'; open: boolean }
  | { type: 'SET_INSPECTOR_OPEN'; open: boolean }
  | { type: 'SET_DEBUGGER_OPEN'; open: boolean }
  | { type: 'SET_WEBGL_ERROR'; message: string | null }
  | { type: 'SET_CONTROL_OPEN'; open: boolean; mode?: 'settings' | 'simulation' }
  | { type: 'SET_CLASSIFY_MODAL_OPEN'; open: boolean }
  | { type: 'SET_AI_CONFIG'; config: Partial<AiConfig> }
  | { type: 'RUN_STUDY' }
  | { type: 'RUN_POPULATION'; worst_case?: boolean }
  | { type: 'SET_OBJECT_MATERIAL'; objectId: string; materialKey: string | null }
  | { type: 'SET_OBJECT_CLASSIFICATION'; objectId: string; role: string | null }
  | { type: 'CLASSIFY_START'; jobId: string }
  | { type: 'CLASSIFY_POLL'; status: string; total: number; done: number; error: string | null; results: AiClassification[] }
  | { type: 'CLASSIFY_APPLY_ALL' }
  | { type: 'CLASSIFY_DISMISS_ALL' }
  | { type: 'CLASSIFY_APPLY_ONE'; objectId: string; role?: string }
  | { type: 'CLASSIFY_CLEAR'; objectId: string }
  | { type: 'CLASSIFY_ERROR'; message: string }
  | { type: 'SET_DEFAULT_MATERIAL'; key: string }
  | { type: 'SET_RENDER_MODE'; mode: RenderMode }
  | { type: 'SET_DROP_PLAYING'; playing: boolean }
  | {
      type: 'PARTS_OK';
      assetId: string;
      parts: { id: string; name: string; geometry: GeometryJson }[];
    }
  | { type: 'PARTS_ERROR'; assetId: string };

export interface ComponentRoleOption {
  value: string;
  label: string;
}

export const COMPONENT_ROLES: readonly ComponentRoleOption[] = [
  { value: 'top_shell', label: 'Top Shell / Upper Housing' },
  { value: 'bottom_shell', label: 'Bottom Shell / Base Plate' },
  { value: 'main_button', label: 'Main Button (Left / Right)' },
  { value: 'side_button', label: 'Side Button (Thumb)' },
  { value: 'scroll_wheel', label: 'Scroll Wheel / Ring' },
  { value: 'encoder', label: 'Encoder / Wheel Module' },
  { value: 'pcb', label: 'PCB / Main Electronics' },
  { value: 'sensor', label: 'Sensor Assembly / Lens' },
  { value: 'foot_pad', label: 'Mouse Foot / Skate (PTFE)' },
  { value: 'battery', label: 'Battery / Mass Block' },
  { value: 'internal_structure', label: 'Internal Frame / Chassis' },
  { value: 'screw_boss', label: 'Screw Boss / Pillar' },
] as const;

/**
 * Deterministic worst-case population spec: every tolerance at the band edge
 * that minimizes shell safety factor (thinnest wall, weakest material,
 * heaviest density, worst CoM offset, highest drop, corner orientation).
 * Matches the backend `worst_case` schema — unknown keys are rejected.
 */
export const WORST_CASE_POPULATION_SPEC = {
  wall_thickness: 'min',
  shell_modulus: 'min',
  shell_strength: 'min',
  shell_density: 'max',
  com_offset: 'max',
  drop_height: 2.0,
  orientation: 'corner',
} as const;

/** Pure reducer managing all project store state transitions. */
export function reducer(state: ProjectState, action: ProjectAction): ProjectState {
  switch (action.type) {
    case 'LOAD_BASELINE_START':
      return { ...state, sourceStatus: 'loading' };
    case 'LOAD_BASELINE_OK':
      return {
        ...resetGeometryView(state),
        stale: state.lastResult != null,
        requestVersion: state.requestVersion + 1,
        runStatus: 'idle',
        runError: null,
        project: action.project,
        projectName: action.name,
        sourceStatus: 'ready',
        sourceError: null,
        draft: null,
        playbackDismissed: false,
        preview: null,
        previewStatus: 'idle',
        previewError: null,
        previewDiagnostics: null,
        tempPreview: null,
        previewRequestVersion: state.previewRequestVersion + 1,
        // Loading the baseline is an explicit "give me the model" action —
        // the heavy shell analysis must not auto-run until the user asks
        // (RUN TEST / EXPLORATION / RUN VALIDATION).
        skipAutoRun: true,
      };
    case 'LOAD_BASELINE_ERROR':
      return {
        ...state,
        sourceStatus: 'error',
        sourceError: action.message,
        requestVersion: state.requestVersion + 1,
        runStatus: 'idle',
        runError: null,
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
      // Exploration is the plain analysis mode: a population config left in
      // the draft by a previous Monte Carlo run must not silently re-run on
      // every EXPLORATION click.
      if (action.mode === 'exploration' && state.draft) {
        const draft = { ...state.draft };
        delete draft.population;
        return { ...state, mode: action.mode, stale: state.lastResult != null, draft };
      }
      return { ...state, mode: action.mode, stale: state.lastResult != null };
    case 'SELECT': {
      const selectedIds = action.ids ?? (action.id ? [action.id] : []);
      return {
        ...state,
        selectedIds,
        selectedId: selectedIds.length > 0 ? selectedIds[selectedIds.length - 1] : null,
      };
    }
    case 'SELECT_TOGGLE': {
      const id = action.id;
      const has = state.selectedIds.includes(id);
      const selectedIds = has ? state.selectedIds.filter((item) => item !== id) : [...state.selectedIds, id];
      return {
        ...state,
        selectedIds,
        selectedId: selectedIds.length > 0 ? selectedIds[selectedIds.length - 1] : null,
      };
    }
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
    case 'PREVIEW_START': {
      if (
        action.version !== undefined &&
        action.version < state.previewRequestVersion
      ) {
        return state;
      }
      const version = action.version ?? state.previewRequestVersion + 1;
      return {
        ...resetGeometryView(state),
        stale: state.lastResult != null,
        requestVersion: state.requestVersion + 1,
        runStatus: 'idle',
        runError: null,
        navOpen: true,
        previewRequestVersion: version,
        tempPreview: action.temp,
        previewStatus: 'working',
        preview: null,
        previewError: null,
        previewDiagnostics: null,
      };
    }
    case 'PREVIEW_OK':
      if (
        action.version !== undefined &&
        action.version !== state.previewRequestVersion
      ) {
        return state;
      }
      if (!action.preview.supported) {
        const message = action.preview.diagnostics[0]?.message ?? 'Geometry preview is unsupported';
        return {
          ...resetGeometryView(state),
          stale: state.lastResult != null,
          requestVersion: state.requestVersion + 1,
          runStatus: 'idle',
          runError: null,
          navOpen: true,
          preview: action.preview,
          previewStatus: 'error',
          previewError: message,
          previewDiagnostics: action.preview.diagnostics,
          tempPreview: null,
        };
      }
      return {
        ...resetGeometryView(state),
        stale: state.lastResult != null,
        requestVersion: state.requestVersion + 1,
        runStatus: 'idle',
        runError: null,
        navOpen: true,
        preview: action.preview,
        previewStatus: 'ready',
        tempPreview: null,
        previewError: null,
        previewDiagnostics: action.preview.diagnostics.length > 0 ? action.preview.diagnostics : null,
      };
    case 'PREVIEW_ERROR':
      if (
        action.version !== undefined &&
        action.version !== state.previewRequestVersion
      ) {
        return state;
      }
      return {
        ...resetGeometryView(state),
        stale: state.lastResult != null,
        requestVersion: state.requestVersion + 1,
        runStatus: 'idle',
        runError: null,
        previewStatus: 'error',
        previewError: action.message,
        previewDiagnostics: action.diagnostics ?? action.preview?.diagnostics ?? null,
        preview: action.preview ?? null,
        tempPreview: null,
      };
    case 'CLEAR_PREVIEW':
      return {
        ...resetGeometryView(state),
        stale: state.lastResult != null,
        requestVersion: state.requestVersion + 1,
        runStatus: 'idle',
        runError: null,
        preview: null,
        previewStatus: 'idle',
        tempPreview: null,
        previewError: null,
        previewDiagnostics: null,
        previewRequestVersion: state.previewRequestVersion + 1,
      };
    case 'ANALYZE_START':
      // Mark stale only when the request that produced the current result
      // differs from the request being run: re-running the SAME request
      // (e.g. RUN_STUDY) must not label its own fresh result stale.
      // A RESTART re-runs the same drop test with a fresh seed, so the keys
      // differ while the test itself is identical — the sameDropTestConfig
      // exemption keeps the "Inputs changed" nag from flashing during the
      // reload.
      return {
        ...state,
        requestVersion: action.version,
        runStatus: 'running',
        stale:
          state.lastResult != null &&
          action.requestKey !== state.resultRequestKey &&
          !sameDropTestConfig(
            state.lastResult.drop_simulation?.config,
            state.draft?.drop_simulation ?? {},
          ),
        runError: null,
        inflightRequestKey: action.requestKey,
        playbackDismissed: false,
      };
    case 'ANALYZE_OK':
      if (action.version !== state.requestVersion) return state;
      // The result is fresh only while the current draft still matches the
      // request that produced it. Inputs edited DURING the run (default
      // material, object materials, draft patches) change the draft without
      // touching the in-flight token; comparing the current request key
      // against the completed run's key keeps those results honestly stale
      // instead of clobbering the synchronous stale marking.
      {
        const currentRequest = createAnalysisRequest(state);
        const inputsMatch =
          currentRequest !== null &&
          createAnalysisRequestKey(currentRequest) === action.requestKey;
        const next: ProjectState = {
          ...state,
          runStatus: 'success',
          lastResult: action.result,
          feaResult: action.result.fea ?? null,
          resultRequestKey: action.requestKey,
          stale: state.lastResult != null && !inputsMatch,
          runError: null,
          inflightRequestKey: null,
        };
        // Population runs are one-shot (W-pop): the draft keeps the Monte Carlo
        // spec only until its run completes, then it is stripped.  The run
        // that just finished carried the draft's population spec (any run with
        // population in the draft IS a population run — createAnalysisRequest
        // spreads the whole draft), so later unrelated draft edits (inspector
        // changes, material swaps) cannot silently re-run the 10k-unit campaign
        // through a later draft edit. The result itself is retained.
        if (state.draft?.population !== undefined) {
          const draft = { ...state.draft };
          delete draft.population;
          return { ...next, draft };
        }
        return next;
      }
    case 'ANALYZE_ERROR':
      if (action.version !== state.requestVersion) return state;
      // A FAILED population run must not linger in the draft either: the run
      // that failed carried the draft's population spec, so the next draft
      // edit would otherwise retry the whole campaign automatically.  The
      // pipeline reports a failed population section as a completed run with
      // no population output, so the ANALYZE_OK strip above cannot catch it —
      // strip on the draft marker here instead.
      if (state.draft?.population !== undefined) {
        const draft = { ...state.draft };
        delete draft.population;
        return {
          ...state,
          runStatus: 'error',
          stale: state.lastResult != null && action.requestKey !== state.resultRequestKey,
          runError: action.message,
          draft,
          inflightRequestKey: null,
        };
      }
      return {
        ...state,
        runStatus: 'error',
        stale: state.lastResult != null && action.requestKey !== state.resultRequestKey,
        runError: action.message,
        inflightRequestKey: null,
      };
    case 'CANCEL_RUN':
      // Cancel only what is actually running; a no-op otherwise. The version
      // bump drops any late ANALYZE_OK/ANALYZE_ERROR from the cancelled
      // request, and cancelNonce tells the App effect to abort the fetch.
      // The one-shot population spec is stripped here too: it is normally
      // removed when a population run COMPLETES (ANALYZE_OK/ERROR), but a
      // cancelled run never reaches those actions, and a leftover spec would
      // silently turn every later launch into a population run.
      if (state.runStatus !== 'loading' && state.runStatus !== 'running') return state;
      {
        const next: ProjectState = {
          ...state,
          requestVersion: state.requestVersion + 1,
          runStatus: 'idle',
          runError: null,
          inflightRequestKey: null,
          cancelNonce: state.cancelNonce + 1,
        };
        if (next.draft?.population !== undefined) {
          const draft = { ...next.draft };
          delete draft.population;
          next.draft = draft;
        }
        return next;
      }
    case 'UPDATE_DRAFT': {
      const base: PipelineRequest = state.draft ?? state.project ?? {};
      return {
        ...state,
        draft: { ...base, ...action.patch },
        stale: state.lastResult != null,
      };
    }
    case 'CONSUME_SKIP_AUTO_RUN':
      return { ...state, skipAutoRun: false };
    case 'SET_FLOOR':
      if (state.draft?.drop_simulation) {
        return {
          ...state,
          draft: {
            ...state.draft,
            drop_simulation: { ...state.draft.drop_simulation, surface: action.surface },
          },
        };
      }
      return state;
    case 'SET_DROP_TEST_CONFIG': {
      if (state.draft?.drop_simulation) {
        return {
          ...state,
          draft: {
            ...state.draft,
            drop_simulation: { ...state.draft.drop_simulation, ...action.patch },
          },
        };
      }
      return state;
    }
    case 'RUN_DROP_TEST': {
      const base: PipelineRequest = state.draft ?? state.project ?? {};
      const config: Record<string, unknown> = {
        test: action.test,
        height_m: action.config.height_m,
        surface: action.config.surface,
        drop_count: action.config.drop_count,
        orientation: action.config.orientation,
      };
      // Optional fields are copied on an explicit "present" check, never on
      // truthiness: spin_rps = 0 is a legitimate configuration (a tumble
      // launched with no release spin must reach the simulator as 0, not be
      // dropped and silently replaced by the backend's 6 rev/s tumble
      // default), and mass_kg 0/null/undefined means "derive from the mass
      // model" (a positive value is an explicit override).
      if (action.config.spin_rps !== undefined && action.config.spin_rps !== null) {
        config.spin_rps = action.config.spin_rps;
      }
      if (
        action.config.mass_kg !== undefined &&
        action.config.mass_kg !== null &&
        action.config.mass_kg > 0
      ) {
        config.mass_kg = action.config.mass_kg;
      }
      if (action.config.seed !== undefined && action.config.seed !== null) {
        config.seed = action.config.seed;
      } else {
        // No explicit seed: a rapid double-click relaunch of the SAME test
        // that is already running must reuse the in-flight run's seed so the
        // dedup comparison below matches (a fresh seed would change the
        // request key and double-run).  Any other launch gets a fresh random
        // seed so repeated "Run" clicks produce visibly different drop poses
        // instead of the same deterministic sequence (the backend is fully
        // seeded and deterministic for a fixed seed; without this every
        // 3-drop run looked identical on every click).
        const draftSeed = state.draft?.drop_simulation?.seed ?? null;
        const inFlightReuse =
          state.runStatus === 'running' &&
          state.inflightRequestKey !== null &&
          typeof draftSeed === 'number';
        config.seed = inFlightReuse ? draftSeed : Math.floor(Math.random() * 0xffffffff);
      }
      if (
        action.config.pause_between_drops_s !== undefined &&
        action.config.pause_between_drops_s !== null
      ) {
        config.pause_between_drops_s = action.config.pause_between_drops_s;
      }
      // W4-04: a stale validation section (or validation mode) from a
      // previous RUN VALIDATION silently pinned the shell chain and
      // discarded the user's test configuration.  A drop test is an
      // exploration run: strip validation mode/section so the user's fields
      // are what reach the physics.  state.mode is reset too (W9-02).
      // tolerance_profile is dropped as well: it only feeds qualification
      // gates and adds solver time to a first run (structure/impact/load_case
      // are already nulled in the draft below, mirroring RUN_POPULATION).
      // population is dropped too: a drop test is the test the user asked
      // for — a leftover 10k-unit Monte Carlo from a previous run must not
      // ride along silently.
      const rest: Record<string, unknown> = { ...base };
      delete rest.validation;
      delete rest.mode;
      delete rest.tolerance_profile;
      delete rest.population;
      const nextState: ProjectState = {
        ...state,
        explode: 0,
        mode: 'exploration',
        renderMode: state.renderMode,
        // A restart re-runs the SAME test (identical config, fresh seed):
        // the previous result is not stale, it is being refreshed — the
        // "Inputs changed" nag must not appear while the new run loads.
        // Only a genuinely different test (height/surface/count/orientation/
        // spin changed vs the last completed run) marks the old result stale.
        stale:
          state.lastResult != null &&
          !sameDropTestConfig(
            state.lastResult.drop_simulation?.config,
            config,
          ),
        runStatus:
          state.project !== null || state.preview?.supported === true ? 'loading' : 'idle',
        runError: null,
        playbackDismissed: false,
        draft: {
          ...rest,
          mode: 'exploration',
          impact: null,
          // A standard run carries the structural section derived from the
          // model (when provided), so the results panel reports stress,
          // safety factor, and deformation alongside the drop simulation.
          load_case: action.config.load_case ?? null,
          structure: action.config.structure ?? null,
          drop_simulation: config,
        },
        runNonce: state.runNonce + 1,
      };
      // A duplicate launch of the request already in flight (double-click) is
      // a no-op: the identical test is already running.
      if (
        state.runStatus === 'running' &&
        state.inflightRequestKey !== null &&
        createAnalysisRequestKey(createAnalysisRequest(nextState)) === state.inflightRequestKey
      ) {
        return state;
      }
      return nextState;
    }
    case 'START_EDIT_DRAFT':
      return { ...state, draft: state.draft ?? { ...(state.project ?? {}) } };
    case 'DISCARD_DRAFT':
      if (state.draft) {
        return { ...state, draft: null, stale: state.lastResult != null };
      }
      return state;
    case 'APPLY_DRAFT':
      if (state.draft) {
        return { ...state, project: state.draft, draft: null, stale: state.lastResult != null };
      }
      return state;
    case 'LEAVE_TEST':
      // Results (incl. the FEA field) stay visible, but the render mode is
      // reset so the model returns to normal materials; only the test draft
      // and the drop playback are dismissed.
      return {
        ...state,
        draft: null,
        playbackDismissed: true,
        dropPlaying: false,
        renderMode: 'default',
        stale: false,
        runStatus: 'idle',
        runError: null,
        requestVersion: state.requestVersion + 1,
      };
    case 'SET_RENDER_MODE':
      return { ...state, renderMode: action.mode };
    case 'SET_DROP_PLAYING':
      return { ...state, dropPlaying: action.playing };
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
    case 'SET_OBJECT_MATERIAL': {
      const objectMaterials = { ...state.objectMaterials };
      if (action.materialKey === null) {
        delete objectMaterials[action.objectId];
      } else {
        objectMaterials[action.objectId] = action.materialKey;
      }
      return { ...state, objectMaterials, stale: state.lastResult != null };
    }
    case 'SET_OBJECT_CLASSIFICATION': {
      const objectClassifications = { ...state.objectClassifications };
      if (action.role === null) {
        delete objectClassifications[action.objectId];
      } else {
        objectClassifications[action.objectId] = action.role;
      }
      // A manual role overrides the AI suggestion for that part.
      const aiClassifications = { ...state.aiClassifications };
      delete aiClassifications[action.objectId];
      // Tombstone the id so a late poll tick cannot resurrect the suggestion.
      const dismissedClassifyIds: Record<string, true> = {
        ...state.dismissedClassifyIds,
        [action.objectId]: true,
      };
      return {
        ...state,
        objectClassifications,
        aiClassifications,
        dismissedClassifyIds,
        stale: state.lastResult != null,
      };
    }
    case 'SET_CLASSIFY_MODAL_OPEN':
      return { ...state, classifyModalOpen: action.open };
    case 'CLASSIFY_START':
      return {
        ...state,
        // A new job means a fresh review cycle: forget prior dismissals.
        dismissedClassifyIds: {},
        classifyJob: { jobId: action.jobId, status: 'queued', total: 0, done: 0, error: null },
      };
    case 'CLASSIFY_POLL': {
      const aiClassifications = { ...state.aiClassifications };
      for (const result of action.results) {
        if (result && result.object_id && result.component_type) {
          if (state.dismissedClassifyIds[result.object_id]) continue;
          aiClassifications[result.object_id] = result;
        }
      }
      const hasSuggestions = Object.keys(aiClassifications).length > 0;
      const justFinished = action.status === 'done' && state.classifyJob?.status === 'running';
      return {
        ...state,
        aiClassifications,
        classifyModalOpen: justFinished && hasSuggestions ? true : state.classifyModalOpen,
        classifyJob: {
          jobId: state.classifyJob?.jobId ?? '',
          status: action.status,
          total: action.total,
          done: action.done,
          error: action.error,
        },
      };
    }
    case 'SET_AI_CONFIG': {
      const aiConfig = { ...state.aiConfig, ...action.config };
      if (typeof window !== 'undefined') {
        try {
          window.localStorage.setItem(AI_CONFIG_STORAGE_KEY, JSON.stringify(aiConfig));
        } catch {
          // ignore
        }
      }
      return { ...state, aiConfig };
    }
    case 'CLASSIFY_APPLY_ALL': {
      const objectClassifications = { ...state.objectClassifications };
      const dismissedClassifyIds = { ...state.dismissedClassifyIds };
      for (const [objectId, suggestion] of Object.entries(state.aiClassifications)) {
        dismissedClassifyIds[objectId] = true;
        if (
          suggestion &&
          suggestion.component_type &&
          suggestion.component_type !== 'unresolved' &&
          (suggestion.confidence ?? 0) > 0
        ) {
          objectClassifications[objectId] = suggestion.component_type;
        }
      }
      return {
        ...state,
        objectClassifications,
        dismissedClassifyIds,
        aiClassifications: {},
        classifyModalOpen: false,
        stale: state.lastResult != null,
      };
    }
    case 'CLASSIFY_DISMISS_ALL': {
      const dismissedClassifyIds = { ...state.dismissedClassifyIds };
      for (const objectId of Object.keys(state.aiClassifications)) {
        dismissedClassifyIds[objectId] = true;
      }
      return {
        ...state,
        dismissedClassifyIds,
        aiClassifications: {},
        classifyModalOpen: false,
      };
    }
    case 'CLASSIFY_APPLY_ONE': {
      const suggestion = state.aiClassifications[action.objectId];
      const objectClassifications = { ...state.objectClassifications };
      const roleToApply = action.role || suggestion?.component_type;
      if (roleToApply && roleToApply !== 'unresolved') {
        objectClassifications[action.objectId] = roleToApply;
      }
      const aiClassifications = { ...state.aiClassifications };
      delete aiClassifications[action.objectId];
      const remaining = Object.keys(aiClassifications).length;
      return {
        ...state,
        objectClassifications,
        aiClassifications,
        dismissedClassifyIds: { ...state.dismissedClassifyIds, [action.objectId]: true },
        classifyModalOpen: remaining === 0 ? false : state.classifyModalOpen,
        stale: state.lastResult != null,
      };
    }
    case 'CLASSIFY_CLEAR': {
      const aiClassifications = { ...state.aiClassifications };
      delete aiClassifications[action.objectId];
      const remaining = Object.keys(aiClassifications).length;
      return {
        ...state,
        aiClassifications,
        dismissedClassifyIds: { ...state.dismissedClassifyIds, [action.objectId]: true },
        classifyModalOpen: remaining === 0 ? false : state.classifyModalOpen,
      };
    }
    case 'CLASSIFY_ERROR':
      return {
        ...state,
        classifyJob: {
          jobId: state.classifyJob?.jobId ?? '',
          status: 'error',
          total: 0,
          done: 0,
          error: action.message,
        },
      };
    case 'SET_DEFAULT_MATERIAL':
      if (typeof window !== 'undefined') {
        try {
          window.localStorage.setItem(DEFAULT_MATERIAL_STORAGE_KEY, action.key);
        } catch {
          // storage unavailable — state still updates
        }
      }
      return { ...state, defaultMaterialKey: action.key, stale: state.lastResult != null };
    case 'PARTS_OK': {
      if (state.preview?.display_asset?.asset_id !== action.assetId) return state;
      const partGeometry: Record<string, GeometryJson> = {};
      for (const part of action.parts) {
        if (!isGeometryJson(part.geometry)) continue;
        partGeometry[part.id] = part.geometry;
      }
      return { ...state, partGeometry, stale: state.lastResult != null };
    }
    case 'PARTS_ERROR':
      if (state.preview?.display_asset?.asset_id !== action.assetId) return state;
      return { ...state, partGeometry: null, stale: state.lastResult != null };
    case 'SET_INSPECTOR_OPEN':
      return { ...state, inspectorOpen: action.open };
    case 'SET_DEBUGGER_OPEN':
      return { ...state, debuggerOpen: action.open };
    case 'SET_WEBGL_ERROR':
      return { ...state, webglError: action.message };
    case 'SET_CONTROL_OPEN':
      return {
        ...state,
        controlOpen: action.open,
        controlMode: action.mode ?? (action.open ? state.controlMode : 'settings'),
      };
    case 'RUN_STUDY':
      // Explicit feedback instead of a silent no-op: launching without
      // geometry or while a mesh is still being parsed cannot run.
      if (state.tempPreview && !state.preview) {
        return {
          ...state,
          runStatus: 'idle',
          runError: 'Model import in progress — wait for it to finish before running.',
        };
      }
      if (state.project === null && state.preview?.supported !== true) {
        return {
          ...state,
          runStatus: 'idle',
          runError: 'Load a model before running an analysis.',
        };
      }
      // A plain analysis run must never silently carry a leftover one-shot
      // population spec (it is stripped when a population run completes or is
      // cancelled, but a superseded fetch can leave it in the draft). Without
      // this strip, clicking Exploration after an interrupted population run
      // would launch a fresh 10k-unit campaign.
      {
        let draft = state.draft;
        if (draft?.population !== undefined) {
          draft = { ...draft };
          delete draft.population;
        }
        const stripped: ProjectState = draft === state.draft ? state : { ...state, draft };
        if (
          stripped.runStatus === 'running' &&
          stripped.inflightRequestKey !== null &&
          createAnalysisRequestKey(createAnalysisRequest(stripped)) === stripped.inflightRequestKey
        ) {
          return state;
        }
        return {
          ...stripped,
          runNonce: stripped.runNonce + 1,
          runStatus:
            stripped.project !== null || stripped.preview?.supported === true ? 'loading' : 'idle',
          runError: null,
        };
      }
    case 'RUN_POPULATION': {
      // Population runs carry a drop test into the run so the population
      // analysis has impact evidence to correlate failure rates against; the
      // exclusive-null pattern mirrors RUN_DROP_TEST. The default action runs
      // a 10k-unit Monte Carlo; `worst_case: true` swaps the population config
      // for a deterministic worst-case corner spec (single unit, no sampling).
      const population: Record<string, unknown> = {
        sample_count: 10000,
        profile: 'esports_fps',
        lifespan_days: 730,
        contact_stiffness_n_per_m: 1e5,
      };
      if (action.worst_case) {
        population.worst_case = WORST_CASE_POPULATION_SPEC;
      }
      // W4-04: same stale-validation strip as RUN_DROP_TEST — a population
      // run is an exploration run and must not inherit validation pins.
      // state.mode is reset too (W9-02: createAnalysisRequest re-injects it).
      const rest: Record<string, unknown> = { ...(state.draft ?? state.project ?? {}) };
      delete rest.validation;
      delete rest.mode;
      const nextState: ProjectState = {
        ...state,
        explode: 0,
        mode: 'exploration',
        renderMode: 'default',
        stale: state.lastResult != null,
        runStatus:
          state.project !== null || state.preview?.supported === true ? 'loading' : 'idle',
        playbackDismissed: false,
        draft: {
          ...rest,
          mode: 'exploration',
          impact: null,
          load_case: null,
          structure: null,
          drop_simulation: {
            test: 'drop',
            height_m: 0.75,
            drop_count: 1,
            surface: 'concrete',
            orientation: 'flat',
          },
          population,
        },
        runNonce: state.runNonce + 1,
      };
      if (
        state.runStatus === 'running' &&
        state.inflightRequestKey !== null &&
        createAnalysisRequestKey(createAnalysisRequest(nextState)) === state.inflightRequestKey
      ) {
        return state;
      }
      return nextState;
    }
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
  selectedIds: [],
  visibility: {},
  isolatedId: null,
  explode: 0,
  mode: 'exploration',
  requestVersion: 0,
  runStatus: 'idle',
  lastResult: null,
  resultRequestKey: null,
  inflightRequestKey: null,
  stale: false,
  runError: null,
  cancelNonce: 0,
  theme: 'light',
  draft: null,
  resultsTab: 'overview',
  severityFilter: null,
  qualityTier: null,
  navOpen: false,
  inspectorOpen: false,
  debuggerOpen: false,
  webglError: null,
  previewRequestVersion: 0,
  controlOpen: false,
  controlMode: 'settings',
  runNonce: 0,
  objectMaterials: {},
  objectClassifications: {},
  aiClassifications: {},
  dismissedClassifyIds: {},
  aiConfig: loadPersistedAiConfig(),
  classifyJob: null,
  classifyModalOpen: false,
  defaultMaterialKey: loadPersistedDefaultMaterial(),
  partGeometry: null,
  renderMode: 'default',
  feaResult: null,
  playbackDismissed: false,
  dropPlaying: false,
  skipAutoRun: false,
};

const DEFAULT_MATERIAL_STORAGE_KEY = 'mouse-sim-default-material';
const AI_CONFIG_STORAGE_KEY = 'mouse-sim-ai-config';

function loadPersistedAiConfig(): AiConfig {
  if (typeof window === 'undefined') {
    return { provider: 'Xiaomi', model: 'xiaomi/mimo-v2.5', apiKey: '', endpoint: '' };
  }
  try {
    const raw = window.localStorage.getItem(AI_CONFIG_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return {
        provider: typeof parsed.provider === 'string' ? parsed.provider : 'Xiaomi',
        model: typeof parsed.model === 'string' ? parsed.model : 'xiaomi/mimo-v2.5',
        apiKey: typeof parsed.apiKey === 'string' ? parsed.apiKey : '',
        endpoint: typeof parsed.endpoint === 'string' ? parsed.endpoint : '',
      };
    }
  } catch {
    // fallback
  }
  return { provider: 'Xiaomi', model: 'xiaomi/mimo-v2.5', apiKey: '', endpoint: '' };
}

/** Read the persisted default material key, falling back to 'default'. */
function loadPersistedDefaultMaterial(): string {
  if (typeof window === 'undefined') return 'default';
  try {
    const stored = window.localStorage.getItem(DEFAULT_MATERIAL_STORAGE_KEY);
    return typeof stored === 'string' && stored.trim() !== '' ? stored : 'default';
  } catch {
    return 'default';
  }
}

/** Reset view state that is keyed to the currently displayed geometry source. */
function resetGeometryView(state: ProjectState): ProjectState {
  return {
    ...state,
    selectedId: null,
    selectedIds: [],
    isolatedId: null,
    visibility: {},
    objectMaterials: {},
    objectClassifications: {},
    aiClassifications: {},
    dismissedClassifyIds: {},
    classifyJob: null,
    classifyModalOpen: false,
    partGeometry: null,
    // A fresh model starts assembled; the old explode factor targets stale
    // geometry and must not carry over.
    explode: 0,
  };
}

/** A flattened geometry entry derived from the current state. */
export interface ObjectEntry {
  id: string;
  geometry: GeometryJson;
  className: string | null;
  displayAssetUrl?: string | null;
  name?: string | null;
  color?: [number, number, number] | null;
}

/** Build the analysis request from the current state, or null when there is nothing to analyze. */
export function createAnalysisRequest(state: ProjectState): PipelineRequest | null {
  // While a mesh is still being parsed there is no canonical geometry yet.
  if (state.tempPreview && !state.preview) return null;
  // An uploaded geometry preview is analyzable on its own, without a project.
  const previewGeometry = state.preview?.supported === true ? state.preview.geometry : null;
  if (!state.project && !state.draft && !previewGeometry) return null;
  const base: PipelineRequest = state.draft ?? state.project ?? {};
  const request: PipelineRequest = { ...base, mode: state.mode, units: base.units ?? 'mm' };
  if (state.defaultMaterialKey && state.defaultMaterialKey.trim() !== '') {
    request.default_material = state.defaultMaterialKey;
  }
  if (previewGeometry && isGeometryJson(previewGeometry)) {
    const parts = state.preview?.display_asset?.parts ?? null;
    const geometries = state.partGeometry;
    if (parts && parts.length > 0 && geometries && parts.every((part) => geometries[part.id])) {
      const objects = parts.map((part) => {
        const materialKey = state.objectMaterials[part.id];
        const role = state.objectClassifications[part.id];
        const entry: Record<string, unknown> = { id: part.id, geometry: geometries[part.id] };
        if (materialKey) entry.material = materialKey;
        if (role) entry.classification = { component_type: role, confidence: 0.95, source: 'user' };
        return entry;
      });
      if (state.preview?.display_asset?.parts_url) {
        request.geometry_asset_id = state.preview.display_asset.asset_id;
        request.objects = objects.map((entry) => {
          const stripped = { ...entry };
          delete stripped.geometry;
          return stripped;
        });
      } else {
        request.objects = objects;
      }
    } else {
      const objectId = state.preview?.source_name ?? 'upload';
      const materialKey = state.objectMaterials[objectId];
      const role = state.objectClassifications[objectId];
      if (state.preview?.display_asset?.parts_url) {
        request.geometry_asset_id = state.preview.display_asset.asset_id;
        delete request.objects;
      } else {
        const entry: Record<string, unknown> = { id: objectId, geometry: previewGeometry };
        if (materialKey) entry.material = materialKey;
        if (role) entry.classification = { component_type: role, confidence: 0.95, source: 'user' };
        request.objects = [entry];
      }
    }
    // Kernel-backed STEP previews are CAD display tessellations (open,
    // multi-body, arbitrary winding). The pipeline treats their topology
    // findings as approximations instead of hard validation blockers.
    if (state.preview?.display_asset) {
      request.options = { ...(request.options ?? {}), display_tessellation: true };
    }
  }
  return request;
}

/**
 * Cheap, deterministic key for request identity and stale-result bookkeeping.
 *
 * The analysis request carries full per-object geometry (mesh vertices can
 * be megabytes); stringifying it on every draft change stalls the main
 * thread.  Geometry values are replaced by a compact fingerprint (type,
 * primitive size, mesh vertex/triangle counts, and a few sampled vertex
 * coordinates) so the key stays unique per distinct request while the work
 * per draft change stays proportional to the (small) geometry-less fields.
 * Object keys are canonicalized (sorted) so that two requests differing only
 * in insertion order — e.g. a draft rebuilt by RUN_DROP_TEST — produce the
 * same key; this keeps duplicate-launch detection and stale bookkeeping
 * value-based instead of order-based. The key only feeds watcher
 * de-duplication and stale-result bookkeeping — analysis semantics are
 * untouched.
 */
export function createAnalysisRequestKey(request: PipelineRequest | null): string {
  if (request === null) return 'null';
  return canonicalStringify(request) ?? 'null';
}

/**
 * Whether two drop-test configs describe the SAME test (ignoring the seed:
 * a restart deliberately draws a fresh seed for new random drops).  Used to
 * distinguish a restart (same test -> previous result is not stale) from a
 * genuinely changed test (different height/surface/count/orientation/spin ->
 * the old result is stale).
 *
 * Only the user-chosen parameters are compared.  Fields the backend FILLS
 * IN on the echoed config (computed mass_kg, unit_seed, spin_rps: 0,
 * pause_between_drops_s: 0.5) or that a restart deliberately changes
 * (seed) never make a restart look like a different test.
 */
function sameDropTestConfig(
  previous: DropSimulationConfig | null | undefined,
  next: Record<string, unknown>,
): boolean {
  if (!previous) return false;
  for (const key of [
    'test',
    'height_m',
    'surface',
    'drop_count',
    'orientation',
    'spin_rps',
    'pause_between_drops_s',
  ] as const) {
    const a = previous[key];
    const b = next[key];
    // The backend echoes DEFAULTS (spin_rps: 0, pause_between_drops_s: 0.5)
    // that the launch config leaves undefined; a missing field and the
    // backend's default for it are the same choice.
    const normalizedA = a === undefined || a === null ? _dropConfigDefault(key) : a;
    const normalizedB = b === undefined || b === null ? _dropConfigDefault(key) : b;
    if (normalizedA !== normalizedB) return false;
  }
  return true;
}

/** Backend defaults the echoed drop config applies for absent fields. */
function _dropConfigDefault(key: string): unknown {
  switch (key) {
    case 'spin_rps':
      return 0;
    case 'pause_between_drops_s':
      return 0.5;
    default:
      return undefined;
  }
}

/** Canonical JSON serialization: object keys sorted, geometry fingerprinted. */
function canonicalStringify(value: unknown): string | undefined {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalStringify(item) ?? 'null').join(',')}]`;
  }
  const record = value as Record<string, unknown>;
  const parts: string[] = [];
  for (const key of Object.keys(record).sort()) {
    const item = record[key];
    if (item === undefined) continue;
    const serialized = key === 'geometry' && isRecord(item) ? geometryFingerprint(item) : canonicalStringify(item);
    if (serialized === undefined) continue;
    parts.push(`${JSON.stringify(key)}:${serialized}`);
  }
  return `{${parts.join(',')}}`;
}

/** Compact fingerprint of a geometry value; see createAnalysisRequestKey. */
function geometryFingerprint(geometry: Record<string, unknown>): string {
  const type = typeof geometry.type === 'string' ? geometry.type : 'unknown';
  const parts: string[] = [];
  if (Array.isArray(geometry.size)) {
    parts.push((geometry.size as unknown[]).join(','));
  }
  if (Array.isArray(geometry.vertices)) {
    const vertices = geometry.vertices as unknown[];
    parts.push(`v${vertices.length}`);
    // A few sampled coordinates catch vertex edits without serializing the
    // full mesh.
    for (const index of [0, Math.floor(vertices.length / 2), vertices.length - 1]) {
      const vertex = vertices[index];
      if (Array.isArray(vertex)) {
        parts.push((vertex as unknown[]).slice(0, 3).join(','));
      }
    }
  }
  if (Array.isArray(geometry.triangles)) {
    parts.push(`t${(geometry.triangles as unknown[]).length}`);
  }
  return `geo:${type}${parts.length > 0 ? `[${parts.join('|')}]` : ''}`;
}

/** Extract the component classification from a raw object entry, if present. */function readClassification(raw: Record<string, unknown>): string | null {
  const cls = raw.classification;
  if (isRecord(cls) && typeof cls.component_type === 'string') {
    return cls.component_type;
  }
  return null;
}

/** Flatten all geometry sources (preview, temp preview, or project objects) into object entries. */
export function computeObjectEntries(state: ProjectState): ObjectEntry[] {
  if (
    state.preview?.supported === true &&
    state.preview.geometry &&
    isGeometryJson(state.preview.geometry)
  ) {
    const parts = state.preview.display_asset?.parts ?? null;
    const geometries = state.partGeometry;
    if (parts && parts.length > 0 && geometries) {
      const entries: ObjectEntry[] = [];
      for (const part of parts) {
        const geometry = geometries[part.id];
        if (!geometry) continue;
        entries.push({
          id: part.id,
          name: part.name,
          geometry,
          className: 'mesh',
          displayAssetUrl: null,
          color: part.color ?? null,
        });
      }
      if (entries.length > 0) return entries;
    }
    return [{
      id: state.preview.source_name ?? 'upload',
      geometry: state.preview.geometry,
      className: null,
      displayAssetUrl: state.preview.display_asset?.url ?? null,
    }];
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
