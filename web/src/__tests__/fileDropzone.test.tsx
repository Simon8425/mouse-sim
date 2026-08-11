import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FileDropzone } from '../components/FileDropzone';
import { ProjectProvider } from '../state/projectStore';
import { IDENTITY_TRANSFORM, type GeometryPreview } from '../api/contracts';
import type { ParseOk } from '../workers/workerProtocol';

const { parseInWorkerMock, normalizeGeometryMock } = vi.hoisted(() => ({
  parseInWorkerMock: vi.fn(),
  normalizeGeometryMock: vi.fn(),
}));

vi.mock('../workers/workerProtocol', () => ({
  parseInWorker: parseInWorkerMock,
}));

vi.mock('../api/client', () => ({
  createClient: () => ({
    normalizeGeometry: normalizeGeometryMock,
  }),
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

function supportedStepPreview(): GeometryPreview {
  return {
    schema_id: 'gms.geometry-preview/1',
    supported: true,
    format: 'step',
    source_units: 'mm',
    source_name: 'housing.step',
    geometry: { type: 'box', size: [1, 1, 1], units: 'mm', transform: IDENTITY_TRANSFORM },
    diagnostics: [],
  };
}

afterEach(() => {
  parseInWorkerMock.mockReset();
  normalizeGeometryMock.mockReset();
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

  it('normalizes a supported STEP file immediately and closes the dropzone', async () => {
    const onClose = vi.fn();
    normalizeGeometryMock.mockResolvedValueOnce(supportedStepPreview());

    render(
      <ProjectProvider>
        <FileDropzone onClose={onClose} />
      </ProjectProvider>,
    );

    const input = screen.getByDisplayValue('') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [geometryFile('housing.step')] },
    });

    await waitFor(() => expect(normalizeGeometryMock).toHaveBeenCalledTimes(1));
    expect(normalizeGeometryMock).toHaveBeenCalledWith(
      { format: 'step', units: 'mm', name: 'housing.step', body: expect.any(ArrayBuffer) },
      expect.any(AbortSignal),
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('renders diagnostics when a STEP preview is unsupported', async () => {
    const onClose = vi.fn();
    normalizeGeometryMock.mockResolvedValueOnce({
      schema_id: 'gms.geometry-preview/1',
      supported: false,
      format: 'step',
      source_units: null,
      source_name: 'housing.step',
      geometry: null,
      diagnostics: [
        {
          code: 'unsupported_step_entities',
          severity: 'error',
          message: 'STEP file contains unsupported NURBS B-rep surfaces',
          details: { entities: ['b_spline_surface'] },
        },
      ],
    });

    render(
      <ProjectProvider>
        <FileDropzone onClose={onClose} />
      </ProjectProvider>,
    );

    const input = screen.getByDisplayValue('') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [geometryFile('housing.step')] },
    });

    await waitFor(() => {
      expect(
        screen.getByText('STEP file contains unsupported NURBS B-rep surfaces'),
      ).toBeInTheDocument();
    });
    expect(onClose).not.toHaveBeenCalled();
  });
});
