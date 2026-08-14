import * as React from 'react';
import { useProjectStore } from '../state/projectStore';
import {
  DROP_TESTS,
  DROP_SURFACES,
  DROP_ORIENTATIONS,
  persistedConfigForTest,
  persistConfigForTest,
  clampDropConfig,
  type DropTestDefinition,
} from '../lib/studies';
import { selectObjectEntries } from '../state/selectors';
import { SHELL_FLEX_LOAD_CASE, shellDims } from './RunControls';
import type { DropTestKind, DropSurface, DropOrientation } from '../api/contracts';

export interface MissionControlProps {
  onClose: () => void;
}

export function MissionControl({ onClose }: MissionControlProps): React.ReactElement {
  const { state, dispatch } = useProjectStore();
  const panelRef = React.useRef<HTMLDivElement | null>(null);

  const [selectedTestId, setSelectedTestId] = React.useState<DropTestDefinition['id']>('drop-test');
  const selectedTest = DROP_TESTS.find((t) => t.id === selectedTestId) ?? DROP_TESTS[0];
  const [testConfig, setTestConfig] = React.useState(() => persistedConfigForTest(selectedTest));

  React.useEffect(() => {
    setTestConfig(persistedConfigForTest(selectedTest));
  }, [selectedTestId, selectedTest]);

  const updateConfig = (overrides: Partial<typeof testConfig>) => {
    const next = clampDropConfig({ ...testConfig, ...overrides });
    setTestConfig(next);
    persistConfigForTest(selectedTest, next);
  };

  const runSimulation = () => {
    const entries = selectObjectEntries(state);
    const dims = shellDims(entries);
    dispatch({
      type: 'RUN_DROP_TEST',
      test: selectedTest.test as DropTestKind,
      config: {
        height_m: testConfig.height_m,
        surface: testConfig.surface,
        drop_count: testConfig.drop_count,
        orientation: testConfig.orientation,
        spin_rps: testConfig.spin_rps,
        mass_kg: testConfig.mass_kg,
        structure: dims ? { type: 'shell_panel', ...dims } : null,
        load_case: SHELL_FLEX_LOAD_CASE,
      },
    });
    onClose();
  };

  React.useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const hasGeometry =
    selectObjectEntries(state).length > 0 ||
    state.project !== null ||
    state.preview !== null ||
    state.tempPreview !== null;
  const isRunning = state.runStatus === 'loading' || state.runStatus === 'running';

  const isSimulationMode = state.controlMode === 'simulation';
  const modalTitle = isSimulationMode ? 'Drop simulation settings' : 'Settings';

  return (
    <div className="mission-control">
      <div className="mission-control__backdrop" aria-hidden="true" onClick={onClose} />
      <div
        className="mission-control__panel"
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={modalTitle}
      >
        <header className="mission-control__header">
          <h2 className="mission-control__title">{modalTitle}</h2>
          <button
            type="button"
            className="btn btn--close-modal"
            aria-label="Close settings panel"
            onClick={onClose}
          >
            ✕
          </button>
        </header>

        <div className="mission-control__body">
          {!isSimulationMode ? (
            /* SETTINGS MODE: MATERIALS & VIEWPORT */
            <>
              <div className="mc-group">
                <label className="mc-label" htmlFor="mc-default-material">
                  Default Material
                </label>
                <select
                  id="mc-default-material"
                  className="settings-default-material"
                  value={state.defaultMaterialKey}
                  onChange={(e) =>
                    dispatch({ type: 'SET_DEFAULT_MATERIAL', key: e.target.value })
                  }
                >
                  {(state.materials && state.materials.length > 0
                    ? state.materials.map((m) => m.key)
                    : ['default']
                  ).map((key) => (
                    <option key={key} value={key}>
                      {key}
                    </option>
                  ))}
                </select>
                <p className="mc-subtext muted">
                  Applied to every component without an explicit material. Never blocks a run.
                </p>
              </div>

              <div className="mc-group" style={{ marginTop: '8px' }}>
                <label className="mc-label" htmlFor="mc-bg-theme">
                  3D Viewport Background
                </label>
                <select
                  id="mc-bg-theme"
                  className="settings-default-material"
                  value={state.theme}
                  onChange={(e) =>
                    dispatch({ type: 'SET_THEME', theme: e.target.value as 'dark' | 'light' })
                  }
                >
                  <option value="dark">Dark Studio (#141310)</option>
                  <option value="light">Pure White (#FFFFFF)</option>
                </select>
                <p className="mc-subtext muted">
                  Switch middle 3D viewport canvas between Dark Studio and Pure White background.
                </p>
              </div>
            </>
          ) : (
            /* SIMULATION MODE: DROP SIMULATION SETTINGS */
            <>
              <div className="mc-group">
                <label className="mc-label" htmlFor="mc-test-select">
                  Test Type
                </label>
                <select
                  id="mc-test-select"
                  className="settings-default-material"
                  value={selectedTestId}
                  onChange={(e) => setSelectedTestId(e.target.value as DropTestDefinition['id'])}
                >
                  {DROP_TESTS.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.title}
                    </option>
                  ))}
                </select>
                <p className="mc-subtext muted">{selectedTest.description}</p>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                <div className="mc-group">
                  <label className="mc-label">Drop height (m)</label>
                  <input
                    type="number"
                    className="settings-default-material"
                    step="0.05"
                    min="0.02"
                    max="2.0"
                    value={testConfig.height_m}
                    onChange={(e) => updateConfig({ height_m: parseFloat(e.target.value) || 0.75 })}
                  />
                </div>
                <div className="mc-group">
                  <label className="mc-label">Drop surface</label>
                  <select
                    className="settings-default-material"
                    value={testConfig.surface}
                    onChange={(e) => updateConfig({ surface: e.target.value as DropSurface })}
                  >
                    {DROP_SURFACES.map((s) => (
                      <option key={s.value} value={s.value}>
                        {s.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="mc-group">
                  <label className="mc-label">Impact orientation</label>
                  <select
                    className="settings-default-material"
                    value={testConfig.orientation}
                    onChange={(e) => updateConfig({ orientation: e.target.value as DropOrientation })}
                  >
                    {DROP_ORIENTATIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="mc-group">
                  <label className="mc-label">Drop count</label>
                  <input
                    type="number"
                    className="settings-default-material"
                    min="1"
                    max="20"
                    value={testConfig.drop_count}
                    onChange={(e) => updateConfig({ drop_count: parseInt(e.target.value, 10) || 1 })}
                  />
                </div>
              </div>
              <div className="mc-actions-row" style={{ marginTop: '10px' }}>
                <button
                  type="button"
                  className="btn btn--primary"
                  aria-label={`Start ${selectedTest.title}`}
                  onClick={runSimulation}
                  disabled={!hasGeometry || isRunning}
                  style={{
                    width: '100%',
                    justifyContent: 'center',
                    padding: '6px 12px',
                    fontSize: '11px',
                    fontWeight: 500,
                    borderRadius: '4px',
                  }}
                >
                  {isRunning ? 'Loading test…' : `Start ${selectedTest.title}`}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
