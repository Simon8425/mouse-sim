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
      if (workerInstance) {
        workerInstance.terminate();
        workerInstance = null;
      }
    };
  }
  return workerInstance;
}

/**
 * Cancels every in-flight parse job and terminates the active worker so a
 * superseded upload never starves behind an earlier compute-heavy file.
 */
export function cancelPendingParses(): void {
  for (const pending of pendingMap.values()) {
    pending.reject(new Error('Parse superseded by a newer upload'));
  }
  pendingMap.clear();
  if (workerInstance) {
    workerInstance.terminate();
    workerInstance = null;
  }
}

export function parseInWorker(
  format: PreviewFormat,
  units: string,
  buffer: ArrayBuffer,
  timeoutMs = 30000,
): Promise<ParseOk> {
  const worker = getSingletonWorker();
  const id = `parse-${nextId++}`;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      if (pendingMap.has(id)) {
        pendingMap.delete(id);
        reject(new Error(`Geometry parse timed out after ${timeoutMs / 1000}s`));
        if (workerInstance) {
          workerInstance.terminate();
          workerInstance = null;
        }
      }
    }, timeoutMs);

    pendingMap.set(id, {
      resolve: (res) => {
        clearTimeout(timer);
        resolve(res);
      },
      reject: (err) => {
        clearTimeout(timer);
        reject(err);
      },
    });
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
