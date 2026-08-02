import * as React from 'react';

import { useProjectStore } from './state/projectStore';
import {
  selectObjectEntries,
  selectAnalysisRequest,
  selectFindingSeverities,
} from './state/selectors';
import { createClient } from './api/client';
import { errorMessage, isAbortError } from './api/errors';
import { isRecord, type Vec3 } from './api/contracts';
import { TopBar } from './components/TopBar';
import { ModelTree } from './components/ModelTree';
import { InspectorPanel } from './components/InspectorPanel';
import { ResultsRail } from './components/ResultsRail';
import { FileDropzone } from './components/FileDropzone';
import { GeometryGuideCard } from './components/GeometryGuideCard';
import { ViewportToolbar } from './components/ViewportToolbar';
import { WebGLFallback } from './components/WebGLFallback';
import {
  SceneViewport,
  useDetectedQuality,
  type SceneViewportHandle,
} from './scene/SceneViewport';
import { worldBounds, boundsCenter } from './lib/geometryBounds';
import type { OverlaySpec } from './scene/overlays';
import type { RenderStats } from './scene/sceneRuntime';

/**
 * Root application component: wires the project store, analysis pipeline,
 * and scene viewport together inside the application chrome.
 */
export function App(): React.ReactElement {
  const { state, dispatch } = useProjectStore();

  const clientRef = React.useRef(createClient());
  const viewportRef = React.useRef<SceneViewportHandle | null>(null);

  const detectedTier = useDetectedQuality();
  const quality = state.qualityTier ?? detectedTier;

  const [stats, setStats] = React.useState<RenderStats | null>(null);
  const [uploadOpen, setUploadOpen] = React.useState(false);

  // Restore the persisted theme once on mount, then keep it in sync.
  const [initialTheme] = React.useState<'light' | 'dark'>(() => {
    const saved = window.localStorage.getItem('mouse-sim-theme');
    return saved === 'dark' || saved === 'light' ? saved : 'light';
  });

  React.useEffect(() => {
    dispatch({ type: 'SET_THEME', theme: initialTheme });
  }, [initialTheme, dispatch]);

  React.useEffect(() => {
    document.documentElement.dataset.theme = state.theme;
    window.localStorage.setItem('mouse-sim-theme', state.theme);
  }, [state.theme]);

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

  // Debounced analysis runner. requestKey covers draft/project/preview
  // changes; state.mode covers mode switches. The token guard prevents
  // stale responses from overwriting newer drafts.
  const tokenRef = React.useRef(0);
  const abortRef = React.useRef<AbortController | null>(null);
  const debounceRef = React.useRef<number | null>(null);
  const analysisRequest = selectAnalysisRequest(state);
  const requestKey = JSON.stringify(analysisRequest ?? null);

  React.useEffect(() => {
    if (!analysisRequest) return;
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      const token = ++tokenRef.current;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      dispatch({ type: 'ANALYZE_START', version: token });
      clientRef.current
        .analyze(
          {
            schema_id: 'gms.web-analysis-request/1',
            request: analysisRequest,
            options: { strict: false, use_cache: true },
          },
          controller.signal,
        )
        .then((res) => dispatch({ type: 'ANALYZE_OK', version: token, result: res.result }))
        .catch((err: unknown) => {
          if (isAbortError(err)) return;
          dispatch({ type: 'ANALYZE_ERROR', version: token, message: errorMessage(err) });
        });
    }, 400);
    return () => {
      if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    };
  }, [requestKey, state.mode, dispatch]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const entries = React.useMemo(
    () => selectObjectEntries(state),
    [state.project, state.preview, state.tempPreview, state.draft],
  );

  // Nothing loaded yet: show the geometry guide card instead of an empty scene.
  const showGuideCard = entries.length === 0 && !state.webglError;

  const visibleEntries = entries.filter((entry) => state.visibility[entry.id] ?? true);
  const shownEntries = state.isolatedId
    ? visibleEntries.filter((entry) => entry.id === state.isolatedId)
    : visibleEntries;

  // Display overlays derived from the last result, the current request,
  // and the entries actually shown in the scene.
  const overlays = React.useMemo<OverlaySpec>(() => {
    const spec: OverlaySpec = {
      loadVector: null,
      fixtures: null,
      displacementPin: null,
      stressBadge: null,
      contactPlane: null,
      severityMarkers: null,
      selectionAnchor: null,
    };

    const result = state.lastResult;
    if (result) {
      const response = result.structural?.response;
      if (response) {
        if (response.max_displacement_location) {
          spec.displacementPin = { location: response.max_displacement_location };
        }
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

    // Contact plane from the current analysis request's impact section
    // (display aid, labeled assumption).
    const impactReq = analysisRequest?.impact;
    if (impactReq && isRecord(impactReq)) {
      const normal = impactReq.contact_normal;
      if (Array.isArray(normal) && normal.length === 3 && normal.every((n) => typeof n === 'number')) {
        spec.contactPlane = { normal: [normal[0], normal[1], normal[2]], point: [0, 0, 0] };
      }
    }

    // Severity markers from validation findings.
    const severities = selectFindingSeverities(state);
    const markers: { id: string; location: Vec3; severity: string }[] = [];
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
    return spec;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.lastResult, analysisRequest, shownEntries]);

  return (
    <div className="app" data-theme={state.theme}>
      <TopBar
        onOpenNav={() => dispatch({ type: 'SET_NAV_OPEN', open: !state.navOpen })}
        onOpenInspector={() => dispatch({ type: 'SET_INSPECTOR_OPEN', open: !state.inspectorOpen })}
        onFit={() => viewportRef.current?.fit()}
      />
      {state.webglError ? <WebGLFallback reason={state.webglError} /> : null}
      <div className="workspace">
        <aside
          className={`drawer drawer--nav${state.navOpen ? ' is-open' : ''}`}
          aria-label="Model navigator"
        >
          <ModelTree />
        </aside>
        <main className="viewport-column">
          <ViewportToolbar viewport={viewportRef} stats={stats} />
          {showGuideCard ? null : (
            <div className="viewport-column__header">
              <button
                type="button"
                className="btn"
                onClick={() => setUploadOpen(!uploadOpen)}
                aria-expanded={uploadOpen}
              >
                Upload geometry
              </button>
            </div>
          )}
          {uploadOpen ? <FileDropzone onClose={() => setUploadOpen(false)} /> : null}
          {state.webglError ? (
            <WebGLFallback reason={state.webglError} />
          ) : showGuideCard ? (
            <GeometryGuideCard onUpload={() => setUploadOpen(true)} />
          ) : (
            <SceneViewport
              ref={viewportRef}
              entries={shownEntries}
              selectedId={state.selectedId}
              explode={state.explode}
              theme={state.theme}
              quality={quality}
              overlays={overlays}
              onPick={(id) => dispatch({ type: 'SELECT', id })}
              onStats={setStats}
              onWebGLUnsupported={(reason) =>
                dispatch({ type: 'SET_WEBGL_ERROR', message: reason })
              }
            />
          )}
        </main>
        <aside
          className={`drawer drawer--inspector${state.inspectorOpen ? ' is-open' : ''}`}
          aria-label="Inspector"
        >
          <InspectorPanel />
        </aside>
      </div>
      <ResultsRail />
    </div>
  );
}
