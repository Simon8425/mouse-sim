/**
 * Run controls — the primary action bar above the viewport: pick a material,
 * pick a test, and run. The run includes the structural analysis (shell panel
 * sized from the loaded model under a 5 kPa flex load), so the results panel
 * reports stress, safety factor, and deformation alongside the drop.
 */
import * as React from 'react';
import { useProjectStore, type ObjectEntry } from '../state/projectStore';
import { selectObjectEntries } from '../state/selectors';
import { worldBounds } from '../lib/geometryBounds';
import {
  DROP_TESTS,
  configForTest,
  type DropTestDefinition,
} from '../lib/studies';
import type { DropTestKind } from '../api/contracts';

/** Shell-flex load case: uniform 5 kPa pressure on the panel. */
const SHELL_FLEX_LOAD_CASE = {
  kind: 'pressure',
  magnitude_pa: 5000,
  distribution: 'uniform',
  direction: [0, 0, -1],
} as const;

/** Union bounds extents (meters) across all loaded geometry entries. */
function shellDims(entries: ObjectEntry[]): { a_m: number; b_m: number; t_m: number } | null {
  let minX = Infinity;
  let minY = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let maxZ = -Infinity;
  for (const entry of entries) {
    const bounds = worldBounds(entry.geometry);
    minX = Math.min(minX, bounds.min[0]);
    minY = Math.min(minY, bounds.min[1]);
    minZ = Math.min(minZ, bounds.min[2]);
    maxX = Math.max(maxX, bounds.max[0]);
    maxY = Math.max(maxY, bounds.max[1]);
    maxZ = Math.max(maxZ, bounds.max[2]);
  }
  if (![minX, minY, minZ, maxX, maxY, maxZ].every(Number.isFinite)) return null;
  const extents = [maxX - minX, maxY - minY, maxZ - minZ]
    .filter((value) => value > 0)
    .sort((a, b) => b - a);
  if (extents.length < 3) return null;
  return { a_m: extents[0], b_m: extents[1], t_m: extents[2] };
}

export function RunControls(): React.ReactElement {
  const { state, dispatch } = useProjectStore();
  const [testId, setTestId] = React.useState<DropTestDefinition['id']>('drop-test');
  const hasGeometry = state.project !== null || state.preview !== null;
  const isRunning = state.runStatus === 'loading' || state.runStatus === 'running';
  const test = DROP_TESTS.find((definition) => definition.id === testId) ?? DROP_TESTS[0];

  const run = () => {
    const config = configForTest(test);
    const entries = selectObjectEntries(state);
    const dims = shellDims(entries);
    dispatch({
      type: 'RUN_DROP_TEST',
      test: test.test as DropTestKind,
      config: {
        height_m: config.height_m,
        surface: config.surface,
        drop_count: config.drop_count,
        orientation: config.orientation,
        spin_rps: config.spin_rps,
        structure: dims ? { type: 'shell_panel', ...dims } : null,
        load_case: SHELL_FLEX_LOAD_CASE,
      },
    });
  };

  return (
    <div className="run-controls">
      <label className="run-controls__field">
        <span>Material</span>
        <select
          value={state.defaultMaterialKey}
          aria-label="Material for analysis"
          onChange={(event) =>
            dispatch({ type: 'SET_DEFAULT_MATERIAL', key: event.target.value })
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
      </label>
      <label className="run-controls__field">
        <span>Test</span>
        <select
          value={testId}
          aria-label="Test type"
          onChange={(event) => setTestId(event.target.value as DropTestDefinition['id'])}
        >
          {DROP_TESTS.map((definition) => (
            <option key={definition.id} value={definition.id}>
              {definition.title}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        className="btn btn--primary"
        aria-label="Run test"
        onClick={run}
        disabled={!hasGeometry || isRunning}
        title={
          isRunning
            ? 'A run is in progress'
            : hasGeometry
              ? `Run ${test.title}`
              : 'Upload a model first'
        }
      >
        {isRunning ? 'Running…' : 'Run'}
      </button>
    </div>
  );
}
