import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useEffect } from 'react';
import { AiClassifyModal } from '../components/AiClassifyModal';
import { ProjectProvider, useProjectStore, type ProjectAction } from '../state/projectStore';

function DispatchHelper({ actions }: { actions: ProjectAction[] }) {
  const { dispatch } = useProjectStore();
  useEffect(() => {
    for (const a of actions) {
      dispatch(a);
    }
  }, [dispatch, actions]);
  return null;
}

function ModalFixture({ actions = [] }: { actions?: ProjectAction[] }) {
  return (
    <ProjectProvider>
      <DispatchHelper actions={actions} />
      <AiClassifyModal />
    </ProjectProvider>
  );
}

describe('AiClassifyModal component', () => {
  it('renders modal with classified suggestions when open', () => {
    render(
      <ModalFixture
        actions={[
          {
            type: 'CLASSIFY_POLL',
            status: 'done',
            total: 2,
            done: 2,
            error: null,
            results: [
              {
                object_id: 'part-0',
                component_type: 'top_shell',
                confidence: 0.98,
                reasons: ['Large outer curved cover', 'Matches full upper housing'],
              },
              {
                object_id: 'part-1',
                component_type: 'bottom_shell',
                confidence: 0.95,
                reasons: ['Lower flat base profile'],
              },
            ],
          },
          { type: 'SET_CLASSIFY_MODAL_OPEN', open: true },
        ]}
      />
    );

    expect(screen.getByText('AI Component Classification')).toBeDefined();
    expect(screen.getByText('2 recognized')).toBeDefined();
    expect(screen.getByDisplayValue('Top Shell / Upper Housing')).toBeDefined();
    expect(screen.getByDisplayValue('Bottom Shell / Base Plate')).toBeDefined();
    expect(screen.getByText('98%')).toBeDefined();
    expect(screen.getByText('part-0')).toBeDefined();
  });

  it('allows individual accept and accept all', () => {
    render(
      <ModalFixture
        actions={[
          {
            type: 'CLASSIFY_POLL',
            status: 'done',
            total: 1,
            done: 1,
            error: null,
            results: [
              {
                object_id: 'part-0',
                component_type: 'top_shell',
                confidence: 0.98,
                reasons: ['Large outer curved cover'],
              },
            ],
          },
          { type: 'SET_CLASSIFY_MODAL_OPEN', open: true },
        ]}
      />
    );

    const acceptBtn = screen.getByRole('button', { name: 'Accept' });
    expect(acceptBtn).toBeDefined();
    fireEvent.click(acceptBtn);

    // After accepting the only suggestion, the modal closes
    expect(screen.queryByText('AI Component Classification')).toBeNull();
  });

  it('allows dismiss all', () => {
    render(
      <ModalFixture
        actions={[
          {
            type: 'CLASSIFY_POLL',
            status: 'done',
            total: 1,
            done: 1,
            error: null,
            results: [
              {
                object_id: 'part-0',
                component_type: 'top_shell',
                confidence: 0.98,
                reasons: [],
              },
            ],
          },
          { type: 'SET_CLASSIFY_MODAL_OPEN', open: true },
        ]}
      />
    );

    const dismissAllBtn = screen.getByRole('button', { name: 'Dismiss all' });
    expect(dismissAllBtn).toBeDefined();
    fireEvent.click(dismissAllBtn);

    expect(screen.queryByText('AI Component Classification')).toBeNull();
  });

  it('counts a user-edited unresolved entry as recognized', () => {
    render(
      <ModalFixture
        actions={[
          {
            type: 'CLASSIFY_POLL',
            status: 'done',
            total: 1,
            done: 1,
            error: null,
            results: [
              {
                object_id: 'part-0',
                component_type: 'unresolved',
                confidence: 0.0,
                reasons: [],
              },
            ],
          },
          { type: 'SET_CLASSIFY_MODAL_OPEN', open: true },
        ]}
      />
    );

    // Initially the entry is unresolved: no recognized badge.
    expect(screen.getByText('0 recognized')).toBeDefined();
    // The select has no "unresolved" option (COMPONENT_ROLES are the 12
    // canonical roles), so it falls back to the first option; changing it to
    // a real role is what the user does to resolve an entry.
    const select = screen.getByRole('combobox') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'top_shell' } });

    // After the user picks a real role the badge and button count it.
    expect(screen.getByText('1 recognized')).toBeDefined();
    expect(
      screen.getByRole('button', { name: 'Accept recognized (1)' })
    ).toBeDefined();
  });

  it('accept-all applies an edited role even when the original confidence was 0', () => {
    render(
      <ModalFixture
        actions={[
          {
            type: 'CLASSIFY_POLL',
            status: 'done',
            total: 1,
            done: 1,
            error: null,
            results: [
              {
                object_id: 'part-0',
                component_type: 'unresolved',
                confidence: 0.0,
                reasons: [],
              },
            ],
          },
          { type: 'SET_CLASSIFY_MODAL_OPEN', open: true },
        ]}
      />
    );

    const select = screen.getByRole('combobox') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'top_shell' } });
    fireEvent.click(screen.getByRole('button', { name: 'Accept recognized (1)' }));

    expect(screen.queryByText('AI Component Classification')).toBeNull();
  });
});
