import * as React from 'react';
import { useProjectStore } from '../state/projectStore';
import { createClient } from '../api/client';
import { cancelPendingParses, parseInWorker, type PreviewFormat } from '../workers/workerProtocol';
import { errorMessage, isAbortError, isUnsupportedGeometryPreview } from '../api/errors';
import { LENGTH_UNITS, type LengthUnit } from '../lib/units';
import type { GeometryPreview, ImportDiagnostic, MeshGeometryJson } from '../api/contracts';

export interface FileDropzoneProps {
  onClose?: () => void;
  variant?: 'flat' | 'modal';
}

export function FileDropzone({ onClose, variant = 'modal' }: FileDropzoneProps): React.ReactElement {
  const { state, dispatch } = useProjectStore();
  const clientRef = React.useRef(createClient());
  const inputRef = React.useRef<HTMLInputElement | null>(null);
  const uploadVersionRef = React.useRef(0);
  const abortRef = React.useRef<AbortController | null>(null);
  const onCloseRef = React.useRef(onClose);
  onCloseRef.current = onClose;

  const [selectedFile, setSelectedFile] = React.useState<File | null>(null);
  const [selectedUnits, setSelectedUnits] = React.useState<LengthUnit>('mm');
  const [isParsing, setIsParsing] = React.useState(false);
  const processingRef = React.useRef(false);
  const processingNameRef = React.useRef<string | null>(null);

  React.useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseRef.current?.();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      uploadVersionRef.current += 1;
      abortRef.current?.abort();
      document.removeEventListener('keydown', handleKeyDown);
    };
    // Mounted once per dialog lifetime: the cleanup aborts the in-flight
    // upload on unmount only. Depending on `onClose` here would re-run the
    // cleanup on every store dispatch (App re-renders pass a fresh callback),
    // silently aborting the very request the dialog just started.
  }, []);

  const invalidateUpload = (): number => {
    abortRef.current?.abort();
    // A newer file supersedes any in-flight worker parse: reject its pending
    // promise immediately so a stale result can never resolve into a preview.
    cancelPendingParses();
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

  const normalizeServerSide = async (
    format: 'json' | 'step' | 'stl',
    file: File,
    controller: AbortController,
    version: number,
  ): Promise<void> => {
    try {
      const buf = await file.arrayBuffer();
      if (!isCurrentUpload(version)) return;
      const preview = await clientRef.current.normalizeGeometry(
        {
          format,
          units: selectedUnits,
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
      if (isCurrentUpload(version)) {
        abortRef.current = null;
        processingRef.current = false;
        processingNameRef.current = null;
        setIsParsing(false);
      }
    }
  };

  const handleFileSelect = (file: File) => {
    if (processingRef.current) return;
    const name = file.name.toLowerCase();
    if (file.size > 250 * 1024 * 1024) {
      const version = invalidateUpload();
      processingRef.current = false;
      processingNameRef.current = null;
      setIsParsing(false);
      setSelectedFile(null);
      dispatch({ type: 'PREVIEW_START', temp: null, version });
      reportPreviewError(
        version,
        `File ${file.name} exceeds the 250 MB maximum upload limit (${(file.size / (1024 * 1024)).toFixed(1)} MB)`,
        null,
      );
      return;
    }
    if (name.endsWith('.json') || name.endsWith('.step') || name.endsWith('.stp')) {
      const version = invalidateUpload();
      const controller = new AbortController();
      abortRef.current = controller;
      processingRef.current = true;
      processingNameRef.current = file.name;
      setIsParsing(true);
      setSelectedFile(null);
      dispatch({ type: 'PREVIEW_START', temp: null, version });
      void normalizeServerSide(name.endsWith('.json') ? 'json' : 'step', file, controller, version);
    } else if (name.endsWith('.obj') || name.endsWith('.stl')) {
      invalidateUpload();
      processingRef.current = false;
      processingNameRef.current = null;
      setIsParsing(false);
      setSelectedFile(file);
    } else {
      const version = invalidateUpload();
      processingRef.current = false;
      processingNameRef.current = null;
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
    // STL goes through the server-side importer (which routes it through the
    // FreeCAD kernel for a CAD-quality GLB display and a canonical analysis
    // mesh). This mirrors the JSON/STEP flow: no client-side temp preview, so
    // the scene never flashes a differently-framed mesh before the asset
    // loads.
    if (file.name.toLowerCase().endsWith('.stl')) {
      // Mirrors the JSON/STEP flow: mark the preview working first so the
      // PREVIEW_OK dispatch (version-guarded) is accepted.
      setIsParsing(true);
      dispatch({ type: 'PREVIEW_START', temp: null, version });
      await normalizeServerSide('stl', file, controller, version);
      return;
    }
    const format: PreviewFormat = 'obj';

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

  const isFlat = variant === 'flat';
  const processingName = processingNameRef.current ?? 'geometry file';

  if (isFlat) {
    return (
      <div
        className="intro-dropzone"
        role="button"
        tabIndex={0}
        aria-label="Drop geometry file (.json, .obj, .stl, .step/.stp) or click to browse"
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

        {isParsing && !selectedFile ? (
          <div className="dropzone-processing" role="status" aria-live="polite">
            <div className="import-progress-minimal">
              <div className="import-progress-minimal__text">
                <span>
                  Importing <code>{processingName}</code>
                </span>
              </div>
              <div className="import-progress-minimal__track" aria-hidden="true">
                <span />
              </div>
            </div>
          </div>
        ) : !selectedFile ? (
          <div className="intro-dropzone__center">
            <div className="intro-dropzone__icon">
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                aria-hidden="true"
              >
                <path
                  d="M12 16.5V9.75m0 0l-3 3m3-3l3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            <p className="intro-dropzone__prompt">
              Drop CAD file (.step, .stl, .obj, .json) or browse
            </p>
          </div>
        ) : (
          <div
            className="dropzone-unit-selector intro-dropzone__unit-selector"
            onClick={(e) => e.stopPropagation()}
          >
            <p>
              Select input length units for <strong>{selectedFile.name}</strong>:
            </p>
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

  return (
    <div className="file-dropzone-modal" onClick={onClose}>
      <div
        className="file-dropzone-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Upload geometry dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="file-dropzone-header">
          <h2 className="file-dropzone-title">Upload Geometry</h2>
          {onClose ? (
            <button
              type="button"
              className="btn btn--close-modal"
              onClick={onClose}
              aria-label="Close upload dialog"
            >
              ✕
            </button>
          ) : null}
        </header>

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

        {isParsing && !selectedFile ? (
          <div className="dropzone-processing" role="status" aria-live="polite">
            <div className="import-progress-minimal">
              <div className="import-progress-minimal__text">
                <span>
                  Importing <code>{processingName}</code>
                </span>
              </div>
              <div className="import-progress-minimal__track" aria-hidden="true">
                <span />
              </div>
            </div>
          </div>
        ) : !selectedFile ? (
          <div
            role="button"
            tabIndex={0}
            aria-label="Drop geometry file (.json, .obj, .stl, .step/.stp) or click to browse"
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
            <div className="dropzone-icon-container">
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                aria-hidden="true"
              >
                <path
                  d="M12 16.5V9.75m0 0l-3 3m3-3l3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            <p className="dropzone-prompt">Drop CAD file (.step, .stl, .obj, .json) or browse</p>
          </div>
        ) : (
          <div className="dropzone-unit-selector">
            <p>
              Select input length units for <strong>{selectedFile.name}</strong>:
            </p>
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
                  onClose?.();
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
    </div>
  );
}
