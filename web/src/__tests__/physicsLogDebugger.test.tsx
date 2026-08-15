/**
 * PhysicsLogDebugger component test — tab rendering and empty states.
 */
import { describe, expect, it } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { RefObject } from 'react';
import type { SceneViewportHandle } from '../scene/SceneViewport';
import { ProjectProvider } from '../state/projectStore';
import { PhysicsLogDebugger } from '../components/PhysicsLogDebugger';

function renderDebugger() {
  const viewportRef: RefObject<SceneViewportHandle | null> = { current: null };
  return render(
    <ProjectProvider>
      <PhysicsLogDebugger viewportRef={viewportRef} />
    </ProjectProvider>,
  );
}

describe('PhysicsLogDebugger', () => {
  it('renders all five tabs', () => {
    renderDebugger();
    for (const label of ['Overview', 'Stream', 'Charts', 'Events', 'Export']) {
      expect(screen.getByRole('tab', { name: label })).toBeTruthy();
    }
  });

  it('shows model specs on the overview tab before drop run', () => {
    renderDebugger();
    expect(screen.getByText('Model & Mass Properties')).toBeTruthy();
    expect(screen.getByText(/Material/)).toBeTruthy();
  });

  it('switches to the stream tab and shows the empty stream message', () => {
    renderDebugger();
    fireEvent.click(screen.getByRole('tab', { name: 'Stream' }));
    expect(screen.getByText(/No frames recorded yet/)).toBeTruthy();
  });

  it('switches to the export tab with disabled export actions', () => {
    renderDebugger();
    fireEvent.click(screen.getByRole('tab', { name: 'Export' }));
    expect(screen.getByRole('button', { name: 'Export JSON' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Export JSON' }).hasAttribute('disabled')).toBe(true);
  });
});
