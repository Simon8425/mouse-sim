import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeAll, afterEach } from 'vitest';
import { useEffect } from 'react';
import { ModelTree } from '../components/ModelTree';
import { ProjectProvider, useProjectStore, type ProjectAction } from '../state/projectStore';
import { IDENTITY_TRANSFORM, type PipelineRequest } from '../api/contracts';

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

  it('scrolls to the row and marks it selected when SELECT targets an entry', async () => {
    const { rerender } = render(<TreeFixture />);

    rerender(<TreeFixture actions={[{ type: 'SELECT', id: 'shell_top' }]} />);

    const row = await screen.findByRole('treeitem', { name: /shell_top/i });
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
