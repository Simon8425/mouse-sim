import { describe, it, expect } from 'vitest';
import * as React from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ProjectProvider, useProjectStore, type ProjectAction } from '../state/projectStore';
import { ViewportToolbar } from '../components/ViewportToolbar';
import type { DropSimulationResult, PipelineResult } from '../api/contracts';

function DispatchHelper({ action }: { action: ProjectAction }) {
  const { dispatch } = useProjectStore();
  React.useEffect(() => {
    dispatch(action);
  }, [dispatch, action]);
  return null;
}

function LeaveTestButton() {
  const { dispatch } = useProjectStore();
  return (
    <button type="button" onClick={() => dispatch({ type: 'LEAVE_TEST' })}>
      Leave test
    </button>
  );
}

const dropSimulationResult: DropSimulationResult = {
  config: {
    test: 'drop',
    height_m: 0.75,
    surface: 'concrete',
    drop_count: 1,
    orientation: 'flat',
  },
  model: {
    mass_kg: 0.06,
    inertia_kg_m2: [
      [1, 0, 0],
      [0, 1, 0],
      [0, 0, 1],
    ],
    support_model: 'test',
    support_point_count: 1,
    integrator: 'test',
    timestep_s: 0.001,
    gravity_m_s2: 9.81,
    surface: 'concrete',
  },
  drops: [],
  impacts: [],
  trajectory: [],
  peak: null,
  peak_force_estimate_n: null,
};

const resultWithFea: PipelineResult = {
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
  qualification: null,
  manifest: null,
  errors: [],
  fea: {
    computed: true,
    peak: null,
    yield_stress_pa: null,
    safety_factor: null,
    impact_window_s: 0.3,
    dent_threshold: 0.7,
    tear_threshold: 0.92,
    objects: [],
    procedural: [],
    assumptions: [],
    flags: [],
  },
};

function renderToolbar() {
  return render(
    <ProjectProvider>
      <DispatchHelper
        action={{
          type: 'LOAD_BASELINE_OK',
          project: { schema_id: 'gms.project/1', mode: 'exploration', units: 'm', objects: [] },
          name: 'mouse_baseline',
        }}
      />
      <DispatchHelper
        action={{
          type: 'ANALYZE_START',
          version: 1,
          requestKey: 'k1',
        }}
      />
      <DispatchHelper
        action={{
          type: 'ANALYZE_OK',
          version: 1,
          requestKey: 'k1',
          result: { ...resultWithFea, drop_simulation: dropSimulationResult },
        }}
      />
      <LeaveTestButton />
      <ViewportToolbar viewport={React.createRef()} stats={null} />
    </ProjectProvider>,
  );
}

describe('ViewportToolbar', () => {
  it('hides the render-mode switch while a test is active', () => {
    renderToolbar();
    // The toolbar shows the switch only in normal mode (after leaving the
    // test) — during the test only Leave Test and the playback card exist.
    expect(screen.queryByRole('group', { name: 'Render mode' })).toBeNull();
  });

  it('does not render the render-mode group in normal preview without a test', () => {
    render(
      <ProjectProvider>
        <DispatchHelper
          action={{
            type: 'LOAD_BASELINE_OK',
            project: { schema_id: 'gms.project/1', mode: 'exploration', units: 'm', objects: [{ id: 'o1', geometry: { type: 'box', size: [1, 1, 1], units: 'm', transform: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1] }, material: 'ABS' }] },
            name: 'mouse_baseline',
          }}
        />
        <ViewportToolbar viewport={React.createRef()} stats={null} />
      </ProjectProvider>,
    );

    expect(screen.queryByRole('group', { name: 'Render mode' })).toBeNull();
  });

  it('shows the enabled toggle in normal mode after LEAVE_TEST and pressing the active mode returns to default', async () => {
    const user = userEvent.setup();
    renderToolbar();

    // During the test the switch is hidden...
    expect(screen.queryByRole('group', { name: 'Render mode' })).toBeNull();

    // ...and appears once the test is left (results stay visible).
    await user.click(screen.getByRole('button', { name: 'Leave test' }));

    const group = screen.getByRole('group', { name: 'Render mode' });
    const feaButton = within(group).getByRole('button', { name: 'FEA Stress Heatmap' });
    const yieldButton = within(group).getByRole('button', { name: 'Yield Shader' });
    expect(feaButton).toBeEnabled();

    expect(feaButton).toHaveAttribute('aria-pressed', 'false');

    await user.click(feaButton);
    expect(feaButton).toHaveAttribute('aria-pressed', 'true');
    expect(yieldButton).toHaveAttribute('aria-pressed', 'false');

    await user.click(yieldButton);
    expect(yieldButton).toHaveAttribute('aria-pressed', 'true');
    expect(feaButton).toHaveAttribute('aria-pressed', 'false');

    // Pressing the ACTIVE mode exits the FEA preview back to default material.
    await user.click(yieldButton);
    expect(yieldButton).toHaveAttribute('aria-pressed', 'false');
    expect(feaButton).toHaveAttribute('aria-pressed', 'false');
  });

  it('stays hidden while the drop playback is playing (no flaky re-appearance)', () => {
    render(
      <ProjectProvider>
        <DispatchHelper
          action={{
            type: 'LOAD_BASELINE_OK',
            project: { schema_id: 'gms.project/1', mode: 'exploration', units: 'm', objects: [] },
            name: 'mouse_baseline',
          }}
        />
        <DispatchHelper
          action={{ type: 'ANALYZE_START', version: 1, requestKey: 'k1' }}
        />
        <DispatchHelper
          action={{
            type: 'ANALYZE_OK',
            version: 1,
            requestKey: 'k1',
            result: { ...resultWithFea, drop_simulation: dropSimulationResult },
          }}
        />
        <DispatchHelper action={{ type: 'SET_DROP_PLAYING', playing: true }} />
        <ViewportToolbar viewport={React.createRef()} stats={null} />
      </ProjectProvider>,
    );

    expect(screen.queryByRole('group', { name: 'Render mode' })).toBeNull();
  });

  it('remains hidden for the whole test even when playback stops', () => {
    render(
      <ProjectProvider>
        <DispatchHelper
          action={{
            type: 'LOAD_BASELINE_OK',
            project: { schema_id: 'gms.project/1', mode: 'exploration', units: 'm', objects: [] },
            name: 'mouse_baseline',
          }}
        />
        <DispatchHelper
          action={{ type: 'ANALYZE_START', version: 1, requestKey: 'k1' }}
        />
        <DispatchHelper
          action={{
            type: 'ANALYZE_OK',
            version: 1,
            requestKey: 'k1',
            result: { ...resultWithFea, drop_simulation: dropSimulationResult },
          }}
        />
        <DispatchHelper action={{ type: 'SET_DROP_PLAYING', playing: true }} />
        <DispatchHelper action={{ type: 'SET_DROP_PLAYING', playing: false }} />
        <ViewportToolbar viewport={React.createRef()} stats={null} />
      </ProjectProvider>,
    );

    expect(screen.queryByRole('group', { name: 'Render mode' })).toBeNull();
  });

  it('shows the render-mode group in normal mode after LEAVE_TEST keeps the results', async () => {
    const user = userEvent.setup();
    renderToolbar();

    await user.click(screen.getByRole('button', { name: 'Leave test' }));
    const group = screen.getByRole('group', { name: 'Render mode' });
    expect(within(group).getByRole('button', { name: 'FEA Stress Heatmap' })).toBeEnabled();
  });

  it('opens and closes the Legend disclosure via its button', async () => {
    const user = userEvent.setup();
    renderToolbar();

    const legendButton = screen.getByRole('button', { name: 'Legend' });
    const legendWrapper = legendButton.closest('.viewport-toolbar__legend');

    expect(legendButton).toHaveAttribute('aria-expanded', 'false');
    expect(legendWrapper).not.toHaveClass('is-open');

    await user.click(legendButton);
    expect(legendButton).toHaveAttribute('aria-expanded', 'true');
    expect(legendWrapper).toHaveClass('is-open');

    await user.click(legendButton);
    expect(legendButton).toHaveAttribute('aria-expanded', 'false');
    expect(legendWrapper).not.toHaveClass('is-open');
  });

  it('opens and closes the Telemetry disclosure via its button', async () => {
    const user = userEvent.setup();
    renderToolbar();

    const statsButton = screen.getByRole('button', { name: 'Telemetry' });
    const statsWrapper = statsButton.closest('.viewport-toolbar__stats');

    expect(statsButton).toHaveAttribute('aria-expanded', 'false');

    await user.click(statsButton);
    expect(statsButton).toHaveAttribute('aria-expanded', 'true');
    expect(statsWrapper).toHaveClass('is-open');
    expect(screen.getByText('Draw calls')).toBeInTheDocument();

    await user.click(statsButton);
    expect(statsButton).toHaveAttribute('aria-expanded', 'false');
    expect(statsWrapper).not.toHaveClass('is-open');
  });
});
