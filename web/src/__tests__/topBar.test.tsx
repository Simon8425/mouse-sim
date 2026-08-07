import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { useEffect } from 'react';
import { TopBar } from '../components/TopBar';
import { ProjectProvider, useProjectStore } from '../state/projectStore';
import type { ProjectState } from '../state/projectStore';

function StateProbe({ onState }: { onState: (state: ProjectState) => void }) {
  const { state } = useProjectStore();
  useEffect(() => {
    onState(state);
  });
  return null;
}

function renderTopBar(onState: (state: ProjectState) => void) {
  return render(
    <ProjectProvider>
      <StateProbe onState={onState} />
      <TopBar
        onOpenNav={vi.fn()}
        onOpenInspector={vi.fn()}
        onOpenControl={vi.fn()}
        onFit={vi.fn()}
      />
    </ProjectProvider>,
  );
}

describe('TopBar run test menu', () => {
  it('renders the RUN TEST trigger with menu semantics', () => {
    renderTopBar(() => {});

    const trigger = screen.getByRole('button', { name: 'Run test menu' });
    expect(trigger).toHaveTextContent('RUN TEST');
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'RUN QUALIFICATION' })).not.toBeInTheDocument();
  });

  it('opens a menu listing the three tests, a separator, and Run Qualification', async () => {
    const user = userEvent.setup();
    renderTopBar(() => {});

    const trigger = screen.getByRole('button', { name: 'Run test menu' });
    await user.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');

    const menu = screen.getByRole('menu', { name: 'Run test' });
    expect(menu).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Drop Test' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Impact Test' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Tumble Test' })).toBeInTheDocument();
    expect(screen.getByRole('separator')).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Run Qualification' })).toBeInTheDocument();
  });

  it('dispatches RUN_DROP_TEST with the test defaults and closes the menu', async () => {
    const user = userEvent.setup();
    let captured: ProjectState | null = null;
    renderTopBar((state) => {
      captured = state;
    });

    await user.click(screen.getByRole('button', { name: 'Run test menu' }));
    await user.click(screen.getByRole('menuitem', { name: 'Drop Test' }));

    await waitFor(() => {
      expect(captured?.draft?.drop_simulation).toMatchObject({
        test: 'drop',
        height_m: 0.75,
        surface: 'concrete',
        drop_count: 3,
        orientation: 'flat',
      });
    });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('dispatches RUN_DROP_TEST for impact and tumble defaults', async () => {
    const user = userEvent.setup();
    let captured: ProjectState | null = null;
    renderTopBar((state) => {
      captured = state;
    });

    await user.click(screen.getByRole('button', { name: 'Run test menu' }));
    await user.click(screen.getByRole('menuitem', { name: 'Impact Test' }));
    await waitFor(() => {
      expect(captured?.draft?.drop_simulation).toMatchObject({
        test: 'impact',
        height_m: 1.0,
        orientation: 'corner',
        drop_count: 1,
      });
    });

    await user.click(screen.getByRole('button', { name: 'Run test menu' }));
    await user.click(screen.getByRole('menuitem', { name: 'Tumble Test' }));
    await waitFor(() => {
      expect(captured?.draft?.drop_simulation).toMatchObject({
        test: 'tumble',
        height_m: 0.75,
        orientation: 'random',
        spin_rps: 4,
      });
    });
  });

  it('Run Qualification keeps the current behavior and closes the menu', async () => {
    const user = userEvent.setup();
    let captured: ProjectState | null = null;
    renderTopBar((state) => {
      captured = state;
    });

    await user.click(screen.getByRole('button', { name: 'Run test menu' }));
    await user.click(screen.getByRole('menuitem', { name: 'Run Qualification' }));

    await waitFor(() => {
      expect(captured?.mode).toBe('qualification');
      expect(captured?.resultsTab).toBe('qualification');
      expect(captured?.runNonce ?? -1).toBeGreaterThan(0);
    });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('closes on Escape and returns focus to the trigger', async () => {
    const user = userEvent.setup();
    renderTopBar(() => {});

    const trigger = screen.getByRole('button', { name: 'Run test menu' });
    await user.click(trigger);
    expect(screen.getByRole('menu')).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('closes on an outside click', async () => {
    const user = userEvent.setup();
    renderTopBar(() => {});

    await user.click(screen.getByRole('button', { name: 'Run test menu' }));
    expect(screen.getByRole('menu')).toBeInTheDocument();

    await user.click(document.body);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('supports arrow-key navigation and Enter activation', async () => {
    const user = userEvent.setup();
    let captured: ProjectState | null = null;
    renderTopBar((state) => {
      captured = state;
    });

    await user.click(screen.getByRole('button', { name: 'Run test menu' }));
    expect(screen.getByRole('menuitem', { name: 'Drop Test' })).toHaveFocus();

    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('menuitem', { name: 'Impact Test' })).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('menuitem', { name: 'Tumble Test' })).toHaveFocus();
    await user.keyboard('{ArrowUp}');
    expect(screen.getByRole('menuitem', { name: 'Impact Test' })).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('menuitem', { name: 'Tumble Test' })).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('menuitem', { name: 'Run Qualification' })).toHaveFocus();

    await user.keyboard('{ArrowUp}');
    await user.keyboard('{ArrowUp}');
    expect(screen.getByRole('menuitem', { name: 'Impact Test' })).toHaveFocus();
    await user.keyboard('{Enter}');
    await waitFor(() => {
      expect(captured?.draft?.drop_simulation).toMatchObject({ test: 'impact' });
    });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });
});
