import { isLengthUnit, LENGTH_TO_M, type LengthUnit } from '../lib/units';
import type {
  WorkerRequestMessage,
  ParseOk,
  ParseError,
} from './workerProtocol';

self.onmessage = (event: MessageEvent<WorkerRequestMessage>) => {
  const req = event.data;
  if (!req || req.kind !== 'parse') return;

  try {
    if (!isLengthUnit(req.units)) {
      const errRes: ParseError = {
        id: req.id,
        ok: false,
        error: `unsupported units: ${req.units}`,
      };
      self.postMessage(errRes);
      return;
    }

    const factor = LENGTH_TO_M[req.units as LengthUnit];
    const warnings: string[] = [];

    const addWarning = (msg: string) => {
      if (warnings.length < 50) {
        warnings.push(msg);
      } else if (warnings.length === 50) {
        warnings.push('\u2026');
      }
    };

    const positions: number[] = [];
    const indices: number[] = [];

    if (req.format === 'obj') {
      const text = new TextDecoder().decode(req.buffer);
      const lines = text.split(/\r?\n/);
      const rawVertices: [number, number, number][] = [];

      for (let i = 0; i < lines.length; i++) {
        let line = lines[i].trim();
        const commentIdx = line.indexOf('#');
        if (commentIdx !== -1) {
          line = line.slice(0, commentIdx).trim();
        }
        if (!line) continue;

        const parts = line.split(/\s+/);
        const tag = parts[0];

        if (tag === 'v') {
          if (parts.length < 4) {
            addWarning(`Line ${i + 1}: incomplete vertex`);
            continue;
          }
          const x = parseFloat(parts[1]);
          const y = parseFloat(parts[2]);
          const z = parseFloat(parts[3]);
          if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
            addWarning(`Line ${i + 1}: NaN vertex coordinates`);
            continue;
          }
          rawVertices.push([x * factor, y * factor, z * factor]);
        } else if (tag === 'f') {
          if (parts.length < 4) {
            addWarning(`Line ${i + 1}: face has fewer than 3 vertices`);
            continue;
          }
          const faceIndices: number[] = [];
          let invalidFace = false;

          for (let j = 1; j < parts.length; j++) {
            const vPart = parts[j].split('/')[0];
            let idx = parseInt(vPart, 10);
            if (isNaN(idx)) {
              invalidFace = true;
              break;
            }
            if (idx > 0) {
              idx = idx - 1; // 1-based to 0-based
            } else if (idx < 0) {
              idx = rawVertices.length + idx; // negative index
            }
            if (idx < 0 || idx >= rawVertices.length) {
              invalidFace = true;
              break;
            }
            faceIndices.push(idx);
          }

          if (invalidFace) {
            addWarning(`Line ${i + 1}: out-of-range vertex index in face`);
            continue;
          }

          // Fan triangulate
          for (let j = 1; j < faceIndices.length - 1; j++) {
            indices.push(faceIndices[0], faceIndices[j], faceIndices[j + 1]);
          }
        }
      }

      for (const v of rawVertices) {
        positions.push(v[0], v[1], v[2]);
      }
    } else if (req.format === 'stl') {
      const buffer = req.buffer;
      const byteLength = buffer.byteLength;
      let isBinary = false;

      if (byteLength >= 84) {
        const view = new DataView(buffer);
        const count = view.getUint32(80, true);
        if (count > 0 && 84 + 50 * count === byteLength) {
          isBinary = true;
        }
      }

      if (isBinary) {
        const view = new DataView(buffer);
        const count = view.getUint32(80, true);
        const vertexMap = new Map<string, number>();

        for (let i = 0; i < count; i++) {
          const offset = 84 + i * 50;
          for (let v = 0; v < 3; v++) {
            const vOffset = offset + 12 + v * 12;
            const x = view.getFloat32(vOffset, true) * factor;
            const y = view.getFloat32(vOffset + 4, true) * factor;
            const z = view.getFloat32(vOffset + 8, true) * factor;
            const key = `${x.toFixed(6)},${y.toFixed(6)},${z.toFixed(6)}`;

            let idx = vertexMap.get(key);
            if (idx === undefined) {
              idx = positions.length / 3;
              positions.push(x, y, z);
              vertexMap.set(key, idx);
            }
            indices.push(idx);
          }
        }
      } else {
        // ASCII STL
        const text = new TextDecoder().decode(buffer);
        const lines = text.split(/\r?\n/);
        const triangleVertices: [number, number, number][] = [];

        for (let i = 0; i < lines.length; i++) {
          const line = lines[i].trim();
          if (line.startsWith('vertex')) {
            const parts = line.split(/\s+/);
            if (parts.length < 4) {
              throw new Error(`ASCII STL line ${i + 1}: incomplete vertex definition`);
            }
            const x = parseFloat(parts[1]);
            const y = parseFloat(parts[2]);
            const z = parseFloat(parts[3]);
            if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
              throw new Error(`ASCII STL line ${i + 1}: invalid vertex coordinates`);
            }
            triangleVertices.push([x * factor, y * factor, z * factor]);
          }
        }

        if (triangleVertices.length % 3 !== 0) {
          throw new Error('ASCII STL contains incomplete triangles');
        }

        const vertexMap = new Map<string, number>();
        for (let i = 0; i < triangleVertices.length; i++) {
          const [x, y, z] = triangleVertices[i];
          const key = `${x.toFixed(6)},${y.toFixed(6)},${z.toFixed(6)}`;
          let idx = vertexMap.get(key);
          if (idx === undefined) {
            idx = positions.length / 3;
            positions.push(x, y, z);
            vertexMap.set(key, idx);
          }
          indices.push(idx);
        }
      }
    }

    if (positions.length === 0 || indices.length === 0) {
      const errRes: ParseError = {
        id: req.id,
        ok: false,
        error: 'no usable vertices and faces',
      };
      self.postMessage(errRes);
      return;
    }

    const vertexArray = new Float32Array(positions);
    const indexArray = new Uint32Array(indices);

    const okRes: ParseOk = {
      id: req.id,
      ok: true,
      vertices: vertexArray,
      triangles: indexArray,
      vertexCount: vertexArray.length / 3,
      triangleCount: indexArray.length / 3,
      warnings,
    };

    (self as unknown as { postMessage: (msg: unknown, transfer: Transferable[]) => void }).postMessage(
      okRes,
      [vertexArray.buffer, indexArray.buffer],
    );
  } catch (err: unknown) {
    const errRes: ParseError = {
      id: req.id,
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
    self.postMessage(errRes);
  }
};
