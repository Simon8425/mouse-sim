import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useEffect } from 'react';
import { TestRunDialog } from '../components/TestRunCard';
import { DROP_TESTS, configForTest, persistConfigForTest } from '../lib/studies';
import type { DropTestDefinition } from '../lib/studies';
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

const dropTest = DROP_TESTS.find((t) => t.test === 'drop') as DropTestDefinition;
const impactTest = DROP_TESTS.find((t) => t.test === 'impact') as DropTestDefinition;
const tumbleTest = DROP_TESTS.find((t) => t.test === 'tumble') as DropTestDefinition;

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

function renderDialog(
  test: DropTestDefinition,
  actions: ProjectAction[] = [],
  onClose = vi.fn(),
  probe?: (state: ProjectState) => void,
) {
  const utils = render(
    <ProjectProvider>
      {actions.map((action, index) => (
        <DispatchHelper key={index} action={action} />
      ))}
      {probe ? <StateProbe onState={probe} /> : null}
      <TestRunDialog test={test} onClose={onClose} />
    </ProjectProvider>,
  );
  return { ...utils, onClose };
}

describe('TestRunDialog', () => {
  beforeEach(() => {
    for (const test of DROP_TESTS) {
      persistConfigForTest(test, configForTest(test));
    }
  });

  it('opens with the persisted defaults for each test', () => {
    const first = renderDialog(dropTest);
    expect(screen.getByRole('dialog', { name: 'Drop Test' })).toBeInTheDocument();
    expect(screen.getByLabelText('Drop Test height')).toHaveValue(0.75);
    expect(screen.getByLabelText('Drop Test drop count')).toHaveValue(3);
    expect(screen.getByLabelText('Drop Test surface')).toHaveValue('concrete');
    expect(screen.getByLabelText('Drop Test orientation')).toHaveValue('flat');
    first.unmount();

    const second = renderDialog(impactTest);
    expect(screen.getByLabelText('Impact Test height')).toHaveValue(1.0);
    expect(screen.getByLabelText('Impact Test drop count')).toHaveValue(1);
    expect(screen.getByLabelText('Impact Test orientation')).toHaveValue('corner');
    second.unmount();

    renderDialog(tumbleTest);
    expect(screen.getByLabelText('Tumble Test height')).toHaveValue(0.75);
    expect(screen.getByLabelText('Tumble Test drop count')).toHaveValue(2);
    expect(screen.getByLabelText('Tumble Test orientation')).toHaveValue('random');
    expect(screen.getByLabelText('Tumble Test spin')).toHaveValue(4);
  });

  it('shows the spin field only for the tumble test', () => {
    const first = renderDialog(impactTest);
    expect(screen.queryByLabelText('Impact Test spin')).not.toBeInTheDocument();
    first.unmount();

    renderDialog(tumbleTest);
    expect(screen.getByLabelText('Tumble Test spin')).toBeInTheDocument();
  });

  it('clamps height and drop count edits into the RUN payload', async () => {
    const user = userEvent.setup();
    let runNonce = -1;
    let capturedDraft: Record<string, unknown> | null | undefined;
    renderDialog(
      dropTest,
      [{ type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' }],
      vi.fn(),
      (state) => {
        runNonce = state.runNonce;
        capturedDraft = state.draft;
      },
    );

    const height = screen.getByLabelText('Drop Test height');
    fireEvent.change(height, { target: { value: '99' } });
    expect(height).toHaveValue(2);

    const count = screen.getByLabelText('Drop Test drop count');
    fireEvent.change(count, { target: { value: '99' } });
    expect(count).toHaveValue(20);

    await user.click(screen.getByRole('button', { name: 'Run Drop Test' }));
    await waitFor(() => {
      expect(runNonce).toBe(1);
    });
    expect(capturedDraft?.drop_simulation).toMatchObject({
      test: 'drop',
      height_m: 2,
      drop_count: 20,
    });
  });

  it('disables Run without geometry and enables it after LOAD_BASELINE_OK', () => {
    const first = renderDialog(dropTest);
    expect(screen.getByRole('button', { name: 'Run Drop Test' })).toBeDisabled();
    first.unmount();

    renderDialog(dropTest, [
      { type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' },
    ]);
    expect(screen.getByRole('button', { name: 'Run Drop Test' })).toBeEnabled();
  });

  it('dispatches RUN_DROP_TEST with the config and calls onClose', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    let runNonce = -1;
    let capturedDraft: Record<string, unknown> | null | undefined;
    const first = renderDialog(
      dropTest,
      [{ type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' }],
      onClose,
      (state) => {
        runNonce = state.runNonce;
        capturedDraft = state.draft;
      },
    );
    expect(runNonce).toBe(0);

    await user.click(screen.getByRole('button', { name: 'Run Drop Test' }));
    await waitFor(() => {
      expect(runNonce).toBe(1);
    });
    expect(capturedDraft?.drop_simulation).toMatchObject({
      test: 'drop',
      height_m: 0.75,
      surface: 'concrete',
      drop_count: 3,
      orientation: 'flat',
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    first.unmount();

    const onCloseTumble = vi.fn();
    let tumbleDraft: Record<string, unknown> | null | undefined = null;
    renderDialog(
      tumbleTest,
      [{ type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' }],
      onCloseTumble,
      (state) => {
        tumbleDraft = state.draft;
      },
    );
    await user.click(screen.getByRole('button', { name: 'Run Tumble Test' }));
    await waitFor(() => {
      expect(tumbleDraft?.drop_simulation).toMatchObject({
        test: 'tumble',
        height_m: 0.75,
        drop_count: 2,
        orientation: 'random',
        spin_rps: 4,
      });
    });
    expect(onCloseTumble).toHaveBeenCalledTimes(1);
  });

  it('closes on Escape and on backdrop click', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const { container } = renderDialog(dropTest, [], onClose);

    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);

    const backdrop = container.querySelector('.mission-control__backdrop');
    expect(backdrop).not.toBeNull();
    await user.click(backdrop as Element);
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
