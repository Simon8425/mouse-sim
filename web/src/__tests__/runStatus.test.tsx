import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useEffect } from 'react';
import { RunStatus } from '../components/RunStatus';
import { ProjectProvider, useProjectStore, type ProjectAction } from '../state/projectStore';
import type { ProjectState } from '../state/projectStore';
import type { PipelineRequest } from '../api/contracts';
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
  it('does not render anything in header when there is no run error', () => {
    renderRunStatus();
    expect(screen.queryByText('Analysis')).not.toBeInTheDocument();
    expect(screen.queryByText('Running…')).not.toBeInTheDocument();
    expect(screen.queryByText('Idle')).not.toBeInTheDocument();
  });

  it('renders the runError message on failure', () => {
    renderRunStatus([
      { type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' },
      { type: 'ANALYZE_START', version: 1, requestKey: 'k1' },
      { type: 'ANALYZE_ERROR', version: 1, requestKey: 'k1', message: 'Solver timed out' },
    ]);

    expect(screen.getByText('Solver timed out')).toBeInTheDocument();
  });
});
