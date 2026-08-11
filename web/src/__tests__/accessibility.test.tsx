import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ProjectProvider, useProjectStore, type ProjectAction } from '../state/projectStore';
import { FileDropzone } from '../components/FileDropzone';
import { RunStatus } from '../components/RunStatus';
import { useEffect } from 'react';

function DispatchHelper({ action }: { action: ProjectAction }) {
  const { dispatch } = useProjectStore();
  useEffect(() => {
    dispatch(action);
  }, [dispatch, action]);
  return null;
}

describe('accessibility (a11y) component interaction', () => {
  it('triggers file picker click on Enter key press in FileDropzone', async () => {
    render(
      <ProjectProvider>
        <FileDropzone />
      </ProjectProvider>,
    );

    const dropzone = screen.getByRole('button', {
      name: /Drop geometry file/i,
    });
    expect(dropzone).toBeInTheDocument();

    const clickSpy = vi.spyOn(HTMLInputElement.prototype, 'click');
    dropzone.focus();
    await userEvent.keyboard('{Enter}');

    expect(clickSpy).toHaveBeenCalled();
  });

  it('renders polite aria-live status region in RunStatus', () => {
    render(
      <ProjectProvider>
        <DispatchHelper action={{ type: 'ANALYZE_START', version: 1, requestKey: 'k1' }} />
        <RunStatus />
      </ProjectProvider>,
    );

    const liveRegion = screen.getByText('Running…').closest('.run-status');
    expect(liveRegion).toHaveAttribute('aria-live', 'polite');
    expect(liveRegion).toHaveAttribute('aria-atomic', 'true');
    expect(screen.getByText('Analysis')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Run progress' })).toHaveAttribute(
      'aria-valuetext',
      'Running…',
    );
    expect(screen.getByRole('button', { name: 'Cancel running analysis' })).toBeInTheDocument();
  });
});
