export type PreviewFormat = 'obj' | 'stl';

export interface ParseRequest {
  id: string;
  kind: 'parse';
  format: PreviewFormat;
  units: string;
  buffer: ArrayBuffer;
}

export interface ParseOk {
  id: string;
  ok: true;
  vertices: Float32Array;
  triangles: Uint32Array;
  vertexCount: number;
  triangleCount: number;
  warnings: string[];
}

export interface ParseError {
  id: string;
  ok: false;
  error: string;
}

export type WorkerResponse = ParseOk | ParseError;
export type WorkerRequestMessage = ParseRequest;
export type WorkerResponseMessage = WorkerResponse;

export function createWorker(): Worker {
  return new Worker(new URL('./geometry.worker.ts', import.meta.url), {
    type: 'module',
  });
}

let workerInstance: Worker | null = null;
let nextId = 1;
const pendingMap = new Map<
  string,
  { resolve: (res: ParseOk) => void; reject: (err: Error) => void }
>();

function getSingletonWorker(): Worker {
  if (!workerInstance) {
    workerInstance = createWorker();
    workerInstance.onmessage = (event: MessageEvent<WorkerResponseMessage>) => {
      const data = event.data;
      const pending = pendingMap.get(data.id);
      if (!pending) return;
      pendingMap.delete(data.id);
      if (data.ok) {
        pending.resolve(data);
      } else {
        pending.reject(new Error(data.error));
      }
    };
    workerInstance.onerror = (err: ErrorEvent) => {
      const error = new Error(err.message || 'Worker error');
      for (const pending of pendingMap.values()) {
        pending.reject(error);
      }
      pendingMap.clear();
    };
  }
  return workerInstance;
}

export function parseInWorker(
  format: PreviewFormat,
  units: string,
  buffer: ArrayBuffer,
): Promise<ParseOk> {
  const worker = getSingletonWorker();
  const id = `parse-${nextId++}`;
  return new Promise((resolve, reject) => {
    pendingMap.set(id, { resolve, reject });
    const msg: WorkerRequestMessage = {
      id,
      kind: 'parse',
      format,
      units,
      buffer,
    };
    worker.postMessage(msg, [buffer]);
  });
}
