import * as React from 'react';
import { useProjectStore } from '../state/projectStore';
import { createClient } from '../api/client';
import { parseInWorker, type PreviewFormat } from '../workers/workerProtocol';
import { errorMessage } from '../api/errors';
import { LENGTH_UNITS, type LengthUnit } from '../lib/units';
import type { MeshGeometryJson } from '../api/contracts';

export interface FileDropzoneProps {
  onClose?: () => void;
}

export function FileDropzone({ onClose }: FileDropzoneProps): React.ReactElement {
  const { state, dispatch } = useProjectStore();
  const clientRef = React.useRef(createClient());
  const inputRef = React.useRef<HTMLInputElement | null>(null);

  const [selectedFile, setSelectedFile] = React.useState<File | null>(null);
  const [selectedUnits, setSelectedUnits] = React.useState<LengthUnit>('mm');
  const [isParsing, setIsParsing] = React.useState(false);

  const handleFileSelect = (file: File) => {
    const name = file.name.toLowerCase();
    if (name.endsWith('.json')) {
      // Normalize JSON immediately
      dispatch({ type: 'PREVIEW_START', temp: null });
      file.arrayBuffer().then((buf) => {
        clientRef.current
          .normalizeGeometry({
            format: 'json',
            name: file.name,
            body: buf,
          })
          .then((preview) => {
            dispatch({ type: 'PREVIEW_OK', preview });
            onClose?.();
          })
          .catch((err) => {
            dispatch({
              type: 'PREVIEW_ERROR',
              message: errorMessage(err),
              diagnostics: null,
            });
          });
      });
    } else if (name.endsWith('.obj') || name.endsWith('.stl')) {
      setSelectedFile(file);
    } else if (name.endsWith('.step') || name.endsWith('.stp')) {
      dispatch({
        type: 'PREVIEW_ERROR',
        message: 'STEP file format requires server CAD converter plugin',
        diagnostics: null,
      });
    } else {
      dispatch({
        type: 'PREVIEW_ERROR',
        message: `Unsupported file extension for ${file.name}`,
        diagnostics: null,
      });
    }
  };

  const processMeshFile = async () => {
    if (!selectedFile) return;
    const format: PreviewFormat = selectedFile.name.toLowerCase().endsWith('.obj') ? 'obj' : 'stl';

    setIsParsing(true);
    try {
      const buffer = await selectedFile.arrayBuffer();
      const parseResult = await parseInWorker(format, selectedUnits, buffer.slice(0));

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
          id: selectedFile.name,
          name: selectedFile.name,
          geometry: tempMesh,
          diagnostics: parseResult.warnings,
        },
      });

      // Call API normalize for canonical representation
      const preview = await clientRef.current.normalizeGeometry({
        format,
        units: selectedUnits,
        name: selectedFile.name,
        body: buffer,
      });

      dispatch({ type: 'PREVIEW_OK', preview });
      onClose?.();
    } catch (err: unknown) {
      dispatch({
        type: 'PREVIEW_ERROR',
        message: errorMessage(err),
        diagnostics: null,
      });
    } finally {
      setIsParsing(false);
      setSelectedFile(null);
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
              onClick={() => setSelectedFile(null)}
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
