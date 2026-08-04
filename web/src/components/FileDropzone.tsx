import * as React from 'react';
import { useProjectStore } from '../state/projectStore';
import { createClient } from '../api/client';
import { parseInWorker, type PreviewFormat } from '../workers/workerProtocol';
import { errorMessage, isAbortError, isUnsupportedGeometryPreview } from '../api/errors';
import { LENGTH_UNITS, type LengthUnit } from '../lib/units';
import type { GeometryPreview, ImportDiagnostic, MeshGeometryJson } from '../api/contracts';

export interface FileDropzoneProps {
  onClose?: () => void;
}

export function FileDropzone({ onClose }: FileDropzoneProps): React.ReactElement {
  const { state, dispatch } = useProjectStore();
  const clientRef = React.useRef(createClient());
  const inputRef = React.useRef<HTMLInputElement | null>(null);
  const uploadVersionRef = React.useRef(0);
  const abortRef = React.useRef<AbortController | null>(null);

  const [selectedFile, setSelectedFile] = React.useState<File | null>(null);
  const [selectedUnits, setSelectedUnits] = React.useState<LengthUnit>('mm');
  const [isParsing, setIsParsing] = React.useState(false);

  React.useEffect(() => {
    return () => {
      uploadVersionRef.current += 1;
      abortRef.current?.abort();
    };
  }, []);

  const invalidateUpload = (): number => {
    abortRef.current?.abort();
    const version = Math.max(uploadVersionRef.current, state.previewRequestVersion) + 1;
    uploadVersionRef.current = version;
    abortRef.current = null;
    return version;
  };

  const isCurrentUpload = (version: number): boolean => uploadVersionRef.current === version;

  const reportPreviewError = (
    version: number,
    message: string,
    diagnostics: ImportDiagnostic[] | null,
    preview?: GeometryPreview,
  ) => {
    if (!isCurrentUpload(version)) return;
    dispatch({ type: 'PREVIEW_ERROR', message, diagnostics, preview, version });
  };

  const handleFileSelect = (file: File) => {
    const name = file.name.toLowerCase();
    if (name.endsWith('.json')) {
      // Normalize JSON immediately
      const version = invalidateUpload();
      const controller = new AbortController();
      abortRef.current = controller;
      setIsParsing(false);
      setSelectedFile(null);
      dispatch({ type: 'PREVIEW_START', temp: null, version });

      void (async () => {
        try {
          const buf = await file.arrayBuffer();
          if (!isCurrentUpload(version)) return;
          const preview = await clientRef.current.normalizeGeometry(
            {
              format: 'json',
              name: file.name,
              body: buf,
            },
            controller.signal,
          );
          if (!isCurrentUpload(version)) return;

          if (isUnsupportedGeometryPreview(preview)) {
            reportPreviewError(
              version,
              preview.diagnostics[0]?.message ?? 'Geometry preview is unsupported',
              preview.diagnostics,
              preview,
            );
            return;
          }

          dispatch({ type: 'PREVIEW_OK', preview, version });
          onClose?.();
        } catch (err: unknown) {
          if (!isCurrentUpload(version) || isAbortError(err)) return;
          reportPreviewError(version, errorMessage(err), null);
        } finally {
          if (isCurrentUpload(version)) abortRef.current = null;
        }
      })();
    } else if (name.endsWith('.obj') || name.endsWith('.stl')) {
      invalidateUpload();
      setIsParsing(false);
      setSelectedFile(file);
    } else if (name.endsWith('.step') || name.endsWith('.stp')) {
      const version = invalidateUpload();
      setIsParsing(false);
      setSelectedFile(null);
      dispatch({ type: 'PREVIEW_START', temp: null, version });
      reportPreviewError(
        version,
        'STEP file format requires server CAD converter plugin',
        null,
      );
    } else {
      const version = invalidateUpload();
      setIsParsing(false);
      setSelectedFile(null);
      dispatch({ type: 'PREVIEW_START', temp: null, version });
      reportPreviewError(version, `Unsupported file extension for ${file.name}`, null);
    }
  };

  const processMeshFile = async () => {
    const file = selectedFile;
    if (!file) return;
    const version = uploadVersionRef.current || invalidateUpload();
    const controller = new AbortController();
    abortRef.current = controller;
    const format: PreviewFormat = file.name.toLowerCase().endsWith('.obj') ? 'obj' : 'stl';

    setIsParsing(true);
    dispatch({ type: 'PREVIEW_START', temp: null, version });
    try {
      const buffer = await file.arrayBuffer();
      const parseResult = await parseInWorker(format, selectedUnits, buffer.slice(0));
      if (!isCurrentUpload(version)) return;

      const verticesList: number[][] = [];
      for (let i = 0; i < parseResult.vertices.length; i += 3) {
        verticesList.push([
          parseResult.vertices[i],
          parseResult.vertices[i + 1],
          parseResult.vertices[i + 2],
        ]);
      }

      const trianglesList: number[][] = [];
      for (let i = 0; i < parseResult.triangles.length; i += 3) {
        trianglesList.push([
          parseResult.triangles[i],
          parseResult.triangles[i + 1],
          parseResult.triangles[i + 2],
        ]);
      }

      const tempMesh: MeshGeometryJson = {
        type: 'mesh',
        vertices: verticesList,
        triangles: trianglesList,
        units: 'm',
        transform: {
          rotation: [
            [1, 0, 0],
            [0, 1, 0],
            [0, 0, 1],
          ],
          translation: [0, 0, 0],
          units: 'm',
        },
      };

      dispatch({
        type: 'PREVIEW_START',
        temp: {
          id: file.name,
          name: file.name,
          geometry: tempMesh,
          diagnostics: parseResult.warnings,
        },
        version,
      });

      // Call API normalize for canonical representation
      const preview = await clientRef.current.normalizeGeometry({
        format,
        units: selectedUnits,
        name: file.name,
        body: buffer,
      }, controller.signal);
      if (!isCurrentUpload(version)) return;

      if (isUnsupportedGeometryPreview(preview)) {
        reportPreviewError(
          version,
          preview.diagnostics[0]?.message ?? 'Geometry preview is unsupported',
          preview.diagnostics,
          preview,
        );
        return;
      }
      dispatch({ type: 'PREVIEW_OK', preview, version });
      onClose?.();
    } catch (err: unknown) {
      if (isCurrentUpload(version) && !isAbortError(err)) {
        reportPreviewError(version, errorMessage(err), null);
      }
    } finally {
      if (isCurrentUpload(version)) {
        setIsParsing(false);
        setSelectedFile(null);
        abortRef.current = null;
      }
    }
  };

  return (
    <div className="file-dropzone-modal">
      <input
        ref={inputRef}
        type="file"
        accept=".json,.obj,.stl,.step,.stp"
        style={{ display: 'none' }}
        onChange={(e) => {
          if (e.target.files && e.target.files[0]) {
            handleFileSelect(e.target.files[0]);
          }
        }}
      />

      {!selectedFile ? (
        <div
          role="button"
          tabIndex={0}
          className="dropzone-area"
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            if (e.dataTransfer.files && e.dataTransfer.files[0]) {
              handleFileSelect(e.dataTransfer.files[0]);
            }
          }}
        >
          <p>Drop geometry file (.json, .obj, .stl) or click to browse</p>
        </div>
      ) : (
        <div className="dropzone-unit-selector">
          <p>Select input length units for <strong>{selectedFile.name}</strong>:</p>
          <select
            value={selectedUnits}
            onChange={(e) => setSelectedUnits(e.target.value as LengthUnit)}
          >
            {LENGTH_UNITS.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
          <div className="dropzone-actions">
            <button
              type="button"
              className="btn btn--primary"
              disabled={isParsing}
              onClick={processMeshFile}
            >
              {isParsing ? 'Processing...' : 'Import Geometry'}
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => {
                invalidateUpload();
                setIsParsing(false);
                setSelectedFile(null);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {state.previewError ? (
        <p className="dropzone-error badge badge--error">{state.previewError}</p>
      ) : null}
    </div>
  );
}
