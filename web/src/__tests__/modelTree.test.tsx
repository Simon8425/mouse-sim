import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, beforeAll, afterEach } from 'vitest';
import { useEffect } from 'react';
import { ModelTree } from '../components/ModelTree';
import { ProjectProvider, useProjectStore, type ProjectAction } from '../state/projectStore';
import { IDENTITY_TRANSFORM, type PipelineRequest, type PipelineResult } from '../api/contracts';

const scrollIntoView = vi.fn();

beforeAll(() => {
  Element.prototype.scrollIntoView = scrollIntoView;
});

afterEach(() => {
  scrollIntoView.mockClear();
});

function DispatchHelper({ action }: { action: ProjectAction }) {
  const { dispatch } = useProjectStore();
  useEffect(() => {
    dispatch(action);
  }, [dispatch, action]);
  return null;
}

const mockProject: PipelineRequest = {
  schema_id: 'gms.project/1',
  mode: 'exploration',
  units: 'm',
  objects: [
    {
      id: 'shell_top',
      geometry: { type: 'box', size: [0.1, 0.05, 0.02], units: 'm', transform: IDENTITY_TRANSFORM },
      material: 'ABS',
    },
    {
      id: 'battery_pack',
      geometry: { type: 'box', size: [0.2, 0.1, 0.04], units: 'm', transform: IDENTITY_TRANSFORM },
      material: 'PC',
    },
  ],
};

const mockResult: PipelineResult = {
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
  validation: {
    status: 'checked',
    validity_state: 'valid',
    findings: [
      {
        code: 'THICKNESS',
        severity: 'blocker',
        state: 'open',
        category: 'geometry',
        message: 'wall below minimum',
        affected_ids: ['shell_top'],
        phase: 'preflight',
        evidence_blocking: true,
      },
    ],
  },
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

function TreeFixture({ actions = [] }: { actions?: ProjectAction[] }) {
  return (
    <ProjectProvider>
      <DispatchHelper
        action={{ type: 'LOAD_BASELINE_OK', project: mockProject, name: 'mouse_baseline' }}
      />
      {actions.map((action, index) => (
        <DispatchHelper key={index} action={action} />
      ))}
      <ModelTree />
    </ProjectProvider>
  );
}

describe('ModelTree', () => {
  it('renders a row per project object', async () => {
    render(<TreeFixture />);

    expect(await screen.findByRole('treeitem', { name: /shell_top/i })).toBeInTheDocument();
    expect(screen.getByRole('treeitem', { name: /battery_pack/i })).toBeInTheDocument();
  });

  it('clears the search filter and scrolls to the row when SELECT targets a filtered-out entry', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<TreeFixture />);

    const searchBox = screen.getByRole('textbox', { name: 'Filter models' });
    await user.type(searchBox, 'battery_pack');
    await waitFor(() => {
      expect(screen.queryByRole('treeitem', { name: /shell_top/i })).not.toBeInTheDocument();
    });

    rerender(<TreeFixture actions={[{ type: 'SELECT', id: 'shell_top' }]} />);

    const row = await screen.findByRole('treeitem', { name: /shell_top/i });
    expect(row).toHaveClass('is-selected');
    await waitFor(() => {
      expect(searchBox).toHaveValue('');
    });
    await waitFor(() => {
      expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
    });
  });

  it('clears the severity chip filter when SELECT targets an entry hidden by the chip', async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <TreeFixture
        actions={[
          { type: 'ANALYZE_START', version: 1, requestKey: 'k1' },
          { type: 'ANALYZE_OK', version: 1, requestKey: 'k1', result: mockResult },
        ]}
      />,
    );

    const blockerChip = await screen.findByRole('button', { name: /Blocker \(1\)/ });
    await user.click(blockerChip);
    await waitFor(() => {
      expect(screen.queryByRole('treeitem', { name: /battery_pack/i })).not.toBeInTheDocument();
    });

    rerender(
      <TreeFixture
        actions={[
          { type: 'ANALYZE_START', version: 1, requestKey: 'k1' },
          { type: 'ANALYZE_OK', version: 1, requestKey: 'k1', result: mockResult },
          { type: 'SELECT', id: 'battery_pack' },
        ]}
      />,
    );

    const row = await screen.findByRole('treeitem', { name: /battery_pack/i });
    expect(row).toHaveClass('is-selected');
    await waitFor(() => {
      expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
    });
  });

  it('keeps material controls aligned without competing DEFAULT chips', async () => {
    const { rerender } = render(<TreeFixture />);

    await waitFor(() => {
      expect(screen.queryByText('DEFAULT')).not.toBeInTheDocument();
      expect(screen.getByRole('treeitem', { name: /shell_top/i })).toBeInTheDocument();
      expect(screen.getByRole('treeitem', { name: /battery_pack/i })).toBeInTheDocument();
    });

    rerender(
      <TreeFixture
        actions={[
          { type: 'SET_OBJECT_MATERIAL', objectId: 'shell_top', materialKey: 'ABS' },
        ]}
      />,
    );

    await waitFor(() => {
      expect(screen.queryByText('DEFAULT')).not.toBeInTheDocument();
      expect(screen.getByRole('treeitem', { name: /shell_top/i })).toBeInTheDocument();
    });
  });
});
