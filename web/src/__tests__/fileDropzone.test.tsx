import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FileDropzone } from '../components/FileDropzone';
import { ProjectProvider } from '../state/projectStore';
import type { ParseOk } from '../workers/workerProtocol';

const { parseInWorkerMock } = vi.hoisted(() => ({ parseInWorkerMock: vi.fn() }));

vi.mock('../workers/workerProtocol', () => ({
  parseInWorker: parseInWorkerMock,
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

function parseResult(): ParseOk {
  return {
    id: 'mock-parse',
    ok: true,
    vertices: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
    triangles: new Uint32Array([0, 1, 2]),
    vertexCount: 3,
    triangleCount: 1,
    warnings: [],
  };
}

function geometryFile(name: string): File {
  const file = new File(['geometry'], name, { type: 'text/plain' });
  Object.defineProperty(file, 'arrayBuffer', {
    value: () => Promise.resolve(new ArrayBuffer(0)),
  });
  return file;
}

afterEach(() => {
  parseInWorkerMock.mockReset();
});

describe('FileDropzone preview lifecycle', () => {
  it('does not continue a stale worker response after a newer file is selected', async () => {
    const firstParse = deferred<ParseOk>();
    const secondParse = deferred<ParseOk>();
    parseInWorkerMock
      .mockReturnValueOnce(firstParse.promise)
      .mockReturnValueOnce(secondParse.promise);

    render(
      <ProjectProvider>
        <FileDropzone />
      </ProjectProvider>,
    );

    const input = screen.getByDisplayValue('') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [geometryFile('first.obj')] },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Import Geometry' }));
    await waitFor(() => expect(parseInWorkerMock).toHaveBeenCalledTimes(1));

    fireEvent.change(input, {
      target: { files: [geometryFile('second.obj')] },
    });
    await act(async () => {
      firstParse.resolve(parseResult());
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(screen.getByText(/second\.obj/)).toBeInTheDocument();
    });
    expect(parseInWorkerMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'Import Geometry' }));
    await waitFor(() => expect(parseInWorkerMock).toHaveBeenCalledTimes(2));
    await act(async () => {
      secondParse.resolve(parseResult());
      await Promise.resolve();
    });
  });
});
