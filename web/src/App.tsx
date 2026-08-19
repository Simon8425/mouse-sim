import * as React from 'react';

import { useProjectStore, createAnalysisRequestKey } from './state/projectStore';
import {
  selectObjectEntries,
  selectAnalysisRequest,
  selectFindingSeverities,
} from './state/selectors';
import { createClient } from './api/client';
import { errorMessage, isAbortError } from './api/errors';
import { isRecord, type Vec3 } from './api/contracts';
import { ModelTree } from './components/ModelTree';
import { InspectorPanel } from './components/InspectorPanel';
import { ResultsRail } from './components/ResultsRail';
import { FileDropzone } from './components/FileDropzone';
import { ViewportToolbar } from './components/ViewportToolbar';
import { WebGLFallback } from './components/WebGLFallback';
import { MissionControl } from './components/MissionControl';
import { AiClassifyModal } from './components/AiClassifyModal';
import { RunControls } from './components/RunControls';
import {
  SceneViewport,
  type SceneViewportHandle,
  type LiveDropData,
} from './scene/SceneViewport';
import { worldBounds, boundsCenter } from './lib/geometryBounds';
import type { OverlaySpec } from './scene/overlays';
import type { QualityTier } from './scene/materialPalette';
import type { RenderStats } from './scene/sceneRuntime';

/**
 * Root application component: wires the project store, analysis pipeline,
 * and scene viewport together inside the application chrome.
 */
export function App(): React.ReactElement {
  const { state, dispatch } = useProjectStore();

  const clientRef = React.useRef(createClient());
  const viewportRef = React.useRef<SceneViewportHandle | null>(null);

  // Render quality is pinned to the low tier (no shadow maps or post
  // processing); only the render resolution is raised (see sceneRuntime).
  const quality: QualityTier = 'low';

  const [stats, setStats] = React.useState<RenderStats | null>(null);
  const [uploadOpen, setUploadOpen] = React.useState(false);
  const [resultsOpen, setResultsOpen] = React.useState(false);
  const [liveDropData, setLiveDropData] = React.useState<LiveDropData | null>(null);

  // Results open automatically once a run finishes — the user never has to
  // hunt for the panel after clicking Run. On narrow viewports the panel
  // would crush the scene, so there it stays collapsed (toggle available).
  React.useEffect(() => {
    if (state.runStatus === 'success' || state.runStatus === 'error') {
      if (window.innerWidth >= 900) {
        setResultsOpen(true);
      }
    }
  }, [state.runStatus]);

  // Theme is locked to neutral dark (#141414).
  React.useEffect(() => {
    dispatch({ type: 'SET_THEME', theme: 'dark' });
    document.documentElement.dataset.theme = 'dark';
    window.localStorage.setItem('mouse-sim-theme', 'dark');
  }, [dispatch]);

  // Server health check on mount.
  React.useEffect(() => {
    let cancelled = false;
    clientRef.current
      .getHealth()
      .then((res) => {
        if (cancelled) return;
        dispatch({ type: 'HEALTH_OK', health: res });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        dispatch({ type: 'HEALTH_ERROR', message: errorMessage(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [dispatch]);

  // Material library on mount.
  React.useEffect(() => {
    let cancelled = false;
    clientRef.current
      .getMaterials()
      .then((materials) => {
        if (cancelled) return;
        dispatch({ type: 'MATERIALS_OK', materials });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        dispatch({ type: 'MATERIALS_ERROR', message: errorMessage(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [dispatch]);

  // Per-part geometry for kernel-backed STEP previews (lazy fetch of the
  // parts asset; the preview envelope only carries part metadata).
  const partsAssetId = state.preview?.display_asset?.asset_id ?? null;
  const needsPartGeometry =
    partsAssetId !== null &&
    (state.preview?.display_asset?.parts?.length ?? 0) > 0 &&
    state.partGeometry === null;
  React.useEffect(() => {
    if (!needsPartGeometry || partsAssetId === null) return;
    let cancelled = false;
    clientRef.current
      .getAssetParts(partsAssetId)
      .then((response) => {
        if (cancelled) return;
        dispatch({ type: 'PARTS_OK', assetId: partsAssetId, parts: response.parts });
      })
      .catch(() => {
        if (cancelled) return;
        dispatch({ type: 'PARTS_ERROR', assetId: partsAssetId });
      });
    return () => {
      cancelled = true;
    };
  }, [needsPartGeometry, partsAssetId, dispatch]);

  // Click-away to deselect: when an object is selected, clicking empty chrome
  // space (the inspector/rail/drawer background or gaps around the model)
  // clears the selection and closes the inspector. Real interactive surfaces
  // are excluded so controls never behave unexpectedly:
  //   - the canvas (the picker already selects/deselects on the model and on
  //     empty 3D space),
  //   - the model tree (rows select; its own empty-area click also deselects),
  //   - buttons/selects/inputs/links and the other panels/overlays.
  React.useEffect(() => {
    if (state.selectedIds.length === 0) return;
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target || !(target instanceof Element)) return;
      // The viewport canvas is handled by the picker: empty 3D space clears
      // the selection, model clicks select, Shift+clicks toggle members.
      if (target.closest('canvas')) return;
      if (target.closest('.model-tree')) return;
      if (
        target.closest(
          'button, select, input, textarea, a, [role="button"], [role="menuitem"], ' +
            '.mission-control, .file-dropzone, .ai-classify, .fea-hud, .results-rail__toggle, ' +
            '.model-row__mat-pill, .model-row__floating-card',
        )
      ) {
        return;
      }
      dispatch({ type: 'SELECT', id: null });
      dispatch({ type: 'SET_INSPECTOR_OPEN', open: false });
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [state.selectedIds, dispatch]);

  // Analysis is explicit. Model loading and draft edits prepare the request;
  // only an incremented runNonce (from a Run button) may start the pipeline.
  // The token guard prevents stale responses from overwriting newer runs.
  const tokenRef = React.useRef(0);
  const abortRef = React.useRef<AbortController | null>(null);
  const lastStartedKeyRef = React.useRef<string | null>(null);
  // The analysis request is memoized on the state fields the selector reads:
  // createAnalysisRequest builds fresh objects each call, and stringifying the
  // per-part geometry on every dispatch would stall the main thread.
  const analysisRequest = React.useMemo(
    () => selectAnalysisRequest(state),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      state.project,
      state.preview,
      state.tempPreview,
      state.draft,
      state.mode,
      state.objectMaterials,
      state.partGeometry,
      state.defaultMaterialKey,
    ],
  );
  const analysisRequestRef = React.useRef(analysisRequest);
  analysisRequestRef.current = analysisRequest;

  // Loading a new source invalidates any pending or in-flight analysis for the
  // previous source. This is cancellation only; it must never start analysis
  // for the newly loaded geometry.
  React.useEffect(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, [state.previewRequestVersion]);

  // Explicit user cancellation (CANCEL_RUN): invalidate the token so any late
  // response is dropped, then abort the in-flight fetch.
  React.useEffect(() => {
    if (state.cancelNonce === 0) return;
    tokenRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
  }, [state.cancelNonce]);

  React.useEffect(() => {
    if (state.runNonce === 0) return;
    const request = analysisRequestRef.current;
    if (!request) return;
    const key = createAnalysisRequestKey(request);
    // Duplicate launch of the request already in flight (double-click on the
    // same Run button): keep the live fetch, repair the transient 'loading'
    // state the second launch wrote, and do NOT start a second request.
    // abortRef is non-null exactly while a fetch is live (nulled on
    // completion, error, abort, cancel, and source change).
    if (abortRef.current !== null && lastStartedKeyRef.current === key) {
      dispatch({ type: 'ANALYZE_START', version: tokenRef.current, requestKey: key });
      return;
    }
    const token = ++tokenRef.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    lastStartedKeyRef.current = key;
    dispatch({ type: 'ANALYZE_START', version: token, requestKey: key });
    clientRef.current
      .analyze(
        {
          schema_id: 'gms.web-analysis-request/1',
          request,
          options: { strict: false, use_cache: true },
        },
        controller.signal,
      )
      .then((res) => {
        if (abortRef.current === controller) abortRef.current = null;
        dispatch({ type: 'ANALYZE_OK', version: token, requestKey: key, result: res.result });
      })
      .catch((err: unknown) => {
        if (abortRef.current === controller) abortRef.current = null;
        if (isAbortError(err)) return;
        dispatch({ type: 'ANALYZE_ERROR', version: token, requestKey: key, message: errorMessage(err) });
      });
    return () => {
      if (abortRef.current === controller) {
        abortRef.current?.abort();
      }
    };
  }, [state.runNonce, dispatch]);

  const entries = React.useMemo(
    () => selectObjectEntries(state),
    // The selector reads the geometry sources plus the lazily-fetched per-part
    // geometry for kernel-backed STEP previews (state.partGeometry). Avoid
    // rebuilding scene entries when render stats or unrelated UI state changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [state.project, state.preview, state.tempPreview, state.partGeometry],
  );



  // Visibility is applied per object inside the scene runtime (no rebuild);
  // isolation still filters the entry set.
  const shownEntries = React.useMemo(
    () =>
      state.isolatedId
        ? entries.filter((entry) => entry.id === state.isolatedId)
        : entries,
    [entries, state.isolatedId],
  );
  // The scene renders what the tree lists. When a kernel-backed STEP preview
  // carries per-part geometry, each part is its own selectable object, so
  // clicking a component picks exactly that part. The native GLB remains the
  // display representation only for previews without parts (single entry).
  const sceneEntries = shownEntries;
  const lastResult = state.lastResult;
  const findingSeveritiesRef = React.useRef(selectFindingSeverities(state));
  findingSeveritiesRef.current = selectFindingSeverities(state);

  // Display overlays derived from the last result, the current request,
  // and the entries actually shown in the scene.
  const overlays = React.useMemo<OverlaySpec>(() => {
    const spec: OverlaySpec = {
      loadVector: null,
      fixtures: null,
      stressBadge: null,
      contactPlane: null,
      severityMarkers: null,
      selectionAnchor: null,
    };

    // No test is active (LEAVE_TEST) and no FEA/stress visualization is
    // requested: nothing but plain geometry markers may remain on screen.
    const testActive =
      state.draft?.drop_simulation != null ||
      (!state.playbackDismissed && state.lastResult?.drop_simulation != null);
    if (!testActive) return spec;

    const result = lastResult;
    if (result) {
      const response = result.structural?.response;
      if (response) {
        if (response.filtered_location) {
          spec.stressBadge = { location: response.filtered_location };
        }
      }
      const loadCase = result.structural?.load_case;
      if (loadCase && isRecord(loadCase)) {
        const dir = loadCase.direction ?? loadCase.normal ?? loadCase.vector;
        if (Array.isArray(dir) && dir.length === 3 && dir.every((n) => typeof n === 'number')) {
          spec.loadVector = { origin: [0, 0, 0], direction: [dir[0], dir[1], dir[2]] };
        }
      }
      const fixtures = result.structural?.fixtures;
      if (Array.isArray(fixtures)) {
        spec.fixtures = [];
        for (const fixture of fixtures) {
          if (!isRecord(fixture)) continue;
          const loc = fixture.location ?? fixture.point ?? fixture.position;
          if (Array.isArray(loc) && loc.length === 3 && loc.every((n) => typeof n === 'number')) {
            spec.fixtures.push({
              name: typeof fixture.name === 'string' ? fixture.name : 'fixture',
              location: [loc[0], loc[1], loc[2]],
            });
          }
        }
        if (spec.fixtures.length === 0) spec.fixtures = null;
      }
    }

    // For drop simulations or when a drop test is active/stale/loading, suppress static
    // structural overlays so stress/load vectors never float under/over the floor.
    if (result?.drop_simulation || state.draft?.drop_simulation || state.stale || state.playbackDismissed) {
      spec.stressBadge = null;
      spec.loadVector = null;
      spec.fixtures = null;
      spec.contactPlane = null;
    }

    // Contact plane from the current analysis request's impact section
    // (display aid, labeled assumption).
    const impactReq = analysisRequest?.impact;
    if (impactReq && isRecord(impactReq)) {
      const normal = impactReq.contact_normal;
      if (Array.isArray(normal) && normal.length === 3 && normal.every((n) => typeof n === 'number')) {
        spec.contactPlane = { normal: [normal[0], normal[1], normal[2]], point: [0, 0, 0] };
      }
    }

    // In FEA render modes the heatmap is the focus: suppress the structural
    // load vector, the translucent contact-plane square and the in-scene 3D
    // stress badge (the HUD overlay reports the numbers) so nothing distracts
    // from the contour.
    if (state.renderMode !== 'default') {
      spec.contactPlane = null;
      spec.loadVector = null;
      spec.stressBadge = null;
    }

    // Severity markers from validation findings. Kernel-backed STEP previews
    // are display tessellations whose per-part topology warnings are expected
    // approximations; rendering a marker at every part centroid would flood
    // the scene with dots, so markers are suppressed for those previews.
    const displayAsset = state.preview?.display_asset ?? null;
    const severities = findingSeveritiesRef.current;
    const markers: { id: string; location: Vec3; severity: string }[] = [];
    if (!displayAsset) {
      for (const entry of shownEntries) {
        const severity = severities.get(entry.id);
        if (!severity) continue;
        if (severity !== 'warning' && severity !== 'error' && severity !== 'blocker') continue;
        markers.push({
          id: entry.id,
          location: boundsCenter(worldBounds(entry.geometry)),
          severity,
        });
      }
    }
    spec.severityMarkers = markers.length > 0 ? markers : null;
    return spec;
  }, [lastResult, analysisRequest, shownEntries, state.preview?.display_asset, state.renderMode, state.stale, state.draft?.drop_simulation, state.playbackDismissed]);

  const isIntro = entries.length === 0 && !state.webglError;

  const leftInset = isIntro ? 0 : (state.navOpen ? 294 : 0);
  // The inspector details panel only hosts exactly one part; with a multi-
  // selection it stays closed (the model-tree rows show the full set).
  const rightDrawerOpen =
    state.classifyModalOpen ||
    (state.inspectorOpen && state.selectedIds.length === 1);
  const rightInset =
    (rightDrawerOpen ? 374 : 0) +
    (isIntro ? 0 : resultsOpen ? 334 : 42);

  const topBarRightOffset = rightDrawerOpen
    ? 374 + (isIntro ? 0 : resultsOpen ? 334 : 42) + 14
    : isIntro
    ? 14
    : resultsOpen
    ? 334 + 14
    : 56;

  const insets = React.useMemo(
    () => ({ left: leftInset, right: rightInset, top: 0, bottom: 0 }),
    [leftInset, rightInset],
  );

  return (
    <div className="app" data-theme={state.theme}>
      <div className="workspace">
        {!isIntro ? (
          <aside
            className={`drawer drawer--nav${state.navOpen ? ' is-open' : ''}`}
            aria-label="Model navigator"
          >
            <ModelTree />
          </aside>
        ) : null}
        <main
          className="viewport-column"
          onDragOver={(e) => {
            e.preventDefault();
            if (!isIntro && e.dataTransfer.types.includes('Files')) {
              setUploadOpen(true);
            }
          }}
        >
          {state.webglError ? (
            <WebGLFallback reason={state.webglError} />
          ) : isIntro ? (
            <FileDropzone variant="flat" />
          ) : (
            <>
              <div className="viewport-column__top-bar" style={{ right: `${topBarRightOffset}px` }}>
                <ViewportToolbar viewportRef={viewportRef} stats={stats} />
                <div className="viewport-column__header">
                  <RunControls
                    onReplaceModel={() => setUploadOpen(!uploadOpen)}
                    uploadOpen={uploadOpen}
                  />
                </div>
              </div>
              <SceneViewport
                ref={viewportRef}
                insets={insets}
                entries={sceneEntries}
                visibility={state.visibility}
                selectedIds={state.selectedIds}
                explode={state.explode}
                theme={state.theme}
                quality={quality}
                overlays={overlays}
                dropSimulation={
                  // A stale result's trajectory belongs to the previous
                  // model/inputs; replaying it against the current model can
                  // render the model displaced (floating or under the floor).
                  // Only playback trajectories that match the current inputs,
                  // and only while the test has not been dismissed (LEAVE_TEST
                  // keeps the results visible but exits playback).
                  state.stale || state.playbackDismissed
                    ? null
                    : (state.lastResult?.drop_simulation ?? null)
                }
                populationResult={
                  state.stale || state.playbackDismissed
                    ? null
                    : (state.lastResult?.population ?? null)
                }
                renderMode={state.renderMode}
                feaResult={state.lastResult?.fea ?? null}
                onDropEnded={() => viewportRef.current?.setDropPlayback?.(false)}
                onPlaybackStateChange={(playing) => dispatch({ type: 'SET_DROP_PLAYING', playing })}
                onLiveDropData={setLiveDropData}
                onPick={(id, meta) => {
                  if (id === null) {
                    // Empty 3D space: clear the whole selection.
                    dispatch({ type: 'SELECT', id: null });
                    dispatch({ type: 'SET_INSPECTOR_OPEN', open: false });
                    return;
                  }
                  if (meta?.shiftKey) {
                    // Shift+click toggles a member of the multi-selection.
                    dispatch({ type: 'SELECT_TOGGLE', id });
                    const wasSelected = state.selectedIds.includes(id);
                    dispatch({ type: 'SET_INSPECTOR_OPEN', open: state.selectedIds.length - (wasSelected ? 1 : 0) === 1 });
                    return;
                  }
                  if (state.selectedIds.includes(id)) {
                    // Clicking an already-selected object un-selects it —
                    // standard CAD "click to release" so a click on what the
                    // user perceives as background (but is on a large solid's
                    // silhouette) reliably deselects.
                    dispatch({ type: 'SELECT_TOGGLE', id });
                    dispatch({ type: 'SET_INSPECTOR_OPEN', open: false });
                    return;
                  }
                  dispatch({ type: 'SELECT', id });
                  dispatch({ type: 'SET_INSPECTOR_OPEN', open: true });
                }}
                onStats={setStats}
                onWebGLUnsupported={(reason) =>
                  dispatch({ type: 'SET_WEBGL_ERROR', message: reason })
                }
              />
            </>
          )}
        </main>
        {state.classifyModalOpen ? (
          <aside
            className="drawer drawer--ai-review is-open"
            aria-label="AI Component Classification"
          >
            <AiClassifyModal />
          </aside>
        ) : (
          <aside
            className={`drawer drawer--inspector${state.inspectorOpen && state.selectedIds.length === 1 ? ' is-open' : ''}`}
            aria-label="Inspector"
          >
            <InspectorPanel />
          </aside>
        )}

        {!isIntro ? (
          <div className={`results-rail-dock${resultsOpen ? ' is-open' : ''}`}>
            <ResultsRail
              open={resultsOpen}
              onToggleOpen={() => setResultsOpen((open) => !open)}
              liveDropData={liveDropData}
            />
          </div>
        ) : null}
      </div>
      {state.controlOpen ? (
        <MissionControl
          onClose={() => dispatch({ type: 'SET_CONTROL_OPEN', open: false })}
        />
      ) : null}
      {uploadOpen && !isIntro ? (
        <FileDropzone variant="modal" onClose={() => setUploadOpen(false)} />
      ) : null}
    </div>
  );
}
