import * as React from 'react';
import { useProjectStore } from '../state/projectStore';
import {
  DROP_TESTS,
  DROP_SURFACES,
  DROP_ORIENTATIONS,
  PAUSE_BETWEEN_DROPS_OPTIONS,
  persistedConfigForTest,
  persistConfigForTest,
  clampDropConfig,
  type DropTestDefinition,
} from '../lib/studies';
import { selectObjectEntries } from '../state/selectors';
import { SHELL_FLEX_LOAD_CASE, shellDims } from './RunControls';
import type { DropTestKind, DropSurface, DropOrientation } from '../api/contracts';

const AI_MODELS = [
  { id: 'xiaomi/mimo-v2.5', label: 'xiaomi/mimo-v2.5', provider: 'Xiaomi', endpoint: '', apiKey: '' },
  { id: 'openai/gpt-5.6-luna-pro', label: 'gpt 5.6 luna', provider: 'OpenAI', endpoint: '', apiKey: '' },
  { id: 'google/gemini-3.7-flash', label: 'google/gemini-3.7-flash', provider: 'Google', endpoint: '', apiKey: '' },
  { id: 'x-ai/grok-4.6', label: 'x-ai/grok-4.6', provider: 'xAI', endpoint: '', apiKey: '' },
  {
    id: 'qwen3.5-4b',
    label: 'Local (qwen3.5-4b @ 127.0.0.1:1234)',
    provider: 'Local',
    endpoint: 'http://127.0.0.1:1234',
    apiKey: '',
  },
];

export interface MissionControlProps {
  onClose: () => void;
}

export function MissionControl({ onClose }: MissionControlProps): React.ReactElement {
  const { state, dispatch } = useProjectStore();
  const panelRef = React.useRef<HTMLDivElement | null>(null);

  const [customModelMode, setCustomModelMode] = React.useState(false);

  const [selectedTestId, setSelectedTestId] = React.useState<DropTestDefinition['id']>('drop-test');
  const selectedTest = DROP_TESTS.find((t) => t.id === selectedTestId) ?? DROP_TESTS[0];
  const [testConfig, setTestConfig] = React.useState(() => persistedConfigForTest(selectedTest));

  const updateConfig = (next: Partial<typeof testConfig>) => {
    const merged = clampDropConfig({ ...testConfig, ...next });
    persistConfigForTest(selectedTest, merged);
    setTestConfig(merged);
    // Live-update an active test's draft so the scene floor (and any overlay
    // geometry derived from it) tracks the selection without waiting for a
    // full re-run.
    dispatch({
      type: 'SET_DROP_TEST_CONFIG',
      patch: {
        height_m: merged.height_m,
        surface: merged.surface,
        drop_count: merged.drop_count,
        orientation: merged.orientation,
        spin_rps: merged.spin_rps,
        pause_between_drops_s: merged.pause_between_drops_s,
      },
    });
  };

  const runSimulation = () => {
    if (selectedTest.test === 'population' || selectedTest.id === 'population-test') {
      dispatch({ type: 'RUN_POPULATION' });
      onClose();
      return;
    }
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

              <div className="mc-group" style={{ marginTop: '8px' }}>
                <label className="mc-label" htmlFor="mc-ai-model">
                  AI Vision Model
                </label>
                <select
                  id="mc-ai-model"
                  className="settings-default-material"
                  value={
                    customModelMode ||
                    !AI_MODELS.some(
                      (m) =>
                        m.id === (state.aiConfig?.model || '') &&
                        (m.endpoint || '') === (state.aiConfig?.endpoint || '')
                    )
                      ? 'custom'
                      : (state.aiConfig?.model || 'xiaomi/mimo-v2.5')
                  }
                  onChange={(e) => {
                    const val = e.target.value;
                    if (val === 'custom') {
                      setCustomModelMode(true);
                    } else {
                      setCustomModelMode(false);
                      const modelObj = AI_MODELS.find((m) => m.id === val);
                      dispatch({
                        type: 'SET_AI_CONFIG',
                        config: {
                          model: val,
                          provider: modelObj?.provider || 'Xiaomi',
                          endpoint: modelObj?.endpoint || '',
                          apiKey: modelObj?.apiKey ?? state.aiConfig?.apiKey ?? '',
                        },
                      });
                    }
                  }}
                >
                  <option value="xiaomi/mimo-v2.5">xiaomi/mimo-v2.5</option>
                  <option value="openai/gpt-5.6-luna-pro">gpt 5.6 luna</option>
                  <option value="google/gemini-3.7-flash">google/gemini-3.7-flash</option>
                  <option value="x-ai/grok-4.6">x-ai/grok-4.6</option>
                  <option value="qwen3.5-4b">Local (qwen3.5-4b @ 127.0.0.1:1234)</option>
                  <option value="custom">Custom Model / Local Endpoint…</option>
                </select>
                {customModelMode ? (
                  <div style={{ marginTop: '6px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    <input
                      id="mc-ai-custom-model"
                      type="text"
                      className="settings-default-material"
                      placeholder="Model ID (e.g. qwopus3.5-9b-coder)"
                      value={state.aiConfig?.model || ''}
                      onChange={(e) =>
                        dispatch({
                          type: 'SET_AI_CONFIG',
                          config: { model: e.target.value, provider: 'Custom' },
                        })
                      }
                    />
                    <input
                      id="mc-ai-endpoint"
                      type="text"
                      className="settings-default-material"
                      placeholder="Base URL / Endpoint (e.g. http://192.168.1.29:1238)"
                      value={state.aiConfig?.endpoint || ''}
                      onChange={(e) =>
                        dispatch({
                          type: 'SET_AI_CONFIG',
                          config: { endpoint: e.target.value },
                        })
                      }
                    />
                  </div>
                ) : null}
                <p className="mc-subtext muted">
                  Vision or local LLM used for automated mouse component recognition.
                </p>
              </div>

              <div className="mc-group" style={{ marginTop: '8px' }}>
                <label className="mc-label" htmlFor="mc-ai-key">
                  API Key {state.aiConfig?.endpoint ? '(Optional for local)' : ''}
                </label>
                <input
                  id="mc-ai-key"
                  type="password"
                  className="settings-default-material"
                  placeholder={
                    state.aiConfig?.endpoint
                      ? 'Not needed for local network'
                      : 'sk-or-v1-... (optional, overrides server key)'
                  }
                  value={state.aiConfig?.apiKey || ''}
                  onChange={(e) =>
                    dispatch({ type: 'SET_AI_CONFIG', config: { apiKey: e.target.value } })
                  }
                />
                <p className="mc-subtext muted">
                  {state.aiConfig?.endpoint
                    ? 'Local models on your private network do not require an API key.'
                    : 'Stored locally in your browser. Configures vision LLM classification.'}
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
                    onChange={(e) => {
                      const surface = e.target.value as DropSurface;
                      dispatch({ type: 'SET_FLOOR', surface });
                      updateConfig({ surface });
                    }}
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
                <div className="mc-group" style={{ gridColumn: 'span 2' }}>
                  <label className="mc-label">Pause between drops</label>
                  <select
                    className="settings-default-material"
                    value={testConfig.pause_between_drops_s ?? 1.0}
                    onChange={(e) => updateConfig({ pause_between_drops_s: parseFloat(e.target.value) || 1.0 })}
                  >
                    {PAUSE_BETWEEN_DROPS_OPTIONS.map((p) => (
                      <option key={p.value} value={p.value}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                  <p className="mc-subtext muted">Stable dwell duration after mouse stops moving before next drop releases.</p>
                </div>
              </div>
              <div className="mc-actions-row">
                <button
                  type="button"
                  className="btn"
                  aria-label={`Start ${selectedTest.title}`}
                  onClick={runSimulation}
                  disabled={!hasGeometry || isRunning}
                >
                  {isRunning ? 'Loading test…' : `Start ${selectedTest.title}`}
                </button>
                <button
                  type="button"
                  className="btn"
                  aria-label="Run Population analysis (10k units)"
                  title="Run 10,000-unit Monte Carlo population drop simulation"
                  disabled={!hasGeometry || isRunning}
                  onClick={() => {
                    dispatch({ type: 'RUN_POPULATION' });
                    onClose();
                  }}
                >
                  Run Population Analysis (10k units)
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
