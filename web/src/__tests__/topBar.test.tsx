import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TopBar } from '../components/TopBar';
import { ProjectProvider } from '../state/projectStore';

function renderTopBar() {
  return render(
    <ProjectProvider>
      <TopBar
        onOpenNav={vi.fn()}
        onOpenInspector={vi.fn()}
        onOpenControl={vi.fn()}
      />
    </ProjectProvider>,
  );
}

describe('TopBar', () => {
  it('renders the app brand and the workspace buttons', () => {
    renderTopBar();

    expect(screen.getByRole('heading', { name: /mouse sim/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Toggle model navigator' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Control panel' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Toggle inspector' })).toBeInTheDocument();
  });

  it('shows the source model label as None before anything is loaded', () => {
    renderTopBar();

    expect(screen.getByText('None')).toBeInTheDocument();
  });

  it('no longer offers a Run test menu (tests run from the viewport run bar)', () => {
    renderTopBar();

    expect(screen.queryByRole('button', { name: 'Run test menu' })).not.toBeInTheDocument();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });
});
