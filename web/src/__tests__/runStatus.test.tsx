import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { useEffect } from 'react';
import { RunStatus } from '../components/RunStatus';
import { ProjectProvider, useProjectStore, type ProjectAction } from '../state/projectStore';
import type { ProjectState } from '../state/projectStore';
import type { PipelineResult, PipelineRequest } from '../api/contracts';
import { IDENTITY_TRANSFORM } from '../api/contracts';

const mockBaselineProject: PipelineRequest = {
  schema_id: 'gms.project/1',
  mode: 'exploration',
  units: 'mm',
  objects: [
    {
      id: 'shell_top',
      geometry: { type: 'box', size: [110, 65, 2.5], units: 'mm', transform: IDENTITY_TRANSFORM },
      material: 'ABS',
    },
  ],
};

const mockPipelineResult: PipelineResult = {
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
  qualification: {
    mode: 'exploration',
    qualified: true,
    evidence_disposition: 'exploration_only',
    gates: [],
    blocking_keys: [],
    summary: 'OK',
  },
  manifest: null,
  errors: [],
};

function DispatchHelper({ action }: { action: ProjectAction }) {
  const { dispatch } = useProjectStore();
  useEffect(() => {
    dispatch(action);
  }, [dispatch, action]);
  return null;
}

function StateProbe({ onState }: { onState: (state: ProjectState) => void }) {
  const { state } = useProjectStore();
  useEffect(() => {
    onState(state);
  });
  return null;
}

function renderRunStatus(actions: ProjectAction[] = [], probe?: (state: ProjectState) => void) {
  return render(
    <ProjectProvider>
      {actions.map((action, index) => (
        <DispatchHelper key={index} action={action} />
      ))}
      {probe ? <StateProbe onState={probe} /> : null}
      <RunStatus />
    </ProjectProvider>,
  );
}

describe('RunStatus', () => {
  it('shows the Analysis mode label by default', () => {
    renderRunStatus();
    expect(screen.getByText('Analysis')).toBeInTheDocument();
    expect(screen.queryByText('Qualification')).not.toBeInTheDocument();
  });

  it('shows the Qualification mode label in qualification mode', () => {
    renderRunStatus([{ type: 'SET_MODE', mode: 'qualification' }]);
    expect(screen.getByText('Qualification')).toBeInTheDocument();
    expect(screen.queryByText('Analysis')).not.toBeInTheDocument();
  });

  it('shows Running with a Cancel button while running and CANCEL_RUN returns to idle', async () => {
    const user = userEvent.setup();
    let cancelNonce = -1;
    renderRunStatus(
      [{ type: 'ANALYZE_START', version: 1, requestKey: 'k1' }],
      (state) => {
        cancelNonce = state.cancelNonce;
      },
    );

    expect(screen.getByText('Running…')).toBeInTheDocument();
    const cancel = screen.getByRole('button', { name: 'Cancel running analysis' });
    expect(cancel).toHaveTextContent('Cancel');

    await user.click(cancel);
    await waitFor(() => {
      expect(cancelNonce).toBe(1);
    });
    expect(screen.getByText('Idle')).toBeInTheDocument();
    expect(screen.queryByText('Running…')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Cancel running analysis' }),
    ).not.toBeInTheDocument();
  });

  it('shows Running and Cancel while a launch is loading', () => {
    renderRunStatus([
      { type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' },
      { type: 'RUN_STUDY' },
    ]);
    expect(screen.getByText('Running…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel running analysis' })).toBeInTheDocument();
  });

  it('renders the runError message on failure and never renders a STALE marker', () => {
    renderRunStatus([
      { type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' },
      { type: 'ANALYZE_START', version: 1, requestKey: 'k1' },
      { type: 'ANALYZE_OK', version: 1, requestKey: 'k1', result: mockPipelineResult },
      // A different request fails while the previous result is retained and
      // marked stale: the status bar must show the error, not a stale marker.
      { type: 'ANALYZE_START', version: 2, requestKey: 'k2' },
      { type: 'ANALYZE_ERROR', version: 2, requestKey: 'k2', message: 'Solver timed out' },
    ]);

    expect(screen.getByText('Failed')).toBeInTheDocument();
    expect(screen.getByText('Solver timed out')).toBeInTheDocument();
    expect(screen.queryByText('STALE')).not.toBeInTheDocument();
  });

  it('does not render a Cancel button when idle', () => {
    renderRunStatus();
    expect(screen.getByText('Idle')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Cancel running analysis' }),
    ).not.toBeInTheDocument();
  });
});
