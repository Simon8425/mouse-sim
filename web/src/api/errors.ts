import { isRecord, type WebErrorEnvelope } from './contracts';

/**
 * Standard API error class carrying status code, error code, severity, details,
 * and optional error envelope.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly severity: string;
  readonly details: Record<string, unknown> | null;
  readonly envelope: WebErrorEnvelope | null;

  constructor(
    message: string,
    opts?: {
      status?: number;
      code?: string;
      severity?: string;
      details?: Record<string, unknown> | null;
      envelope?: WebErrorEnvelope | null;
    },
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = opts?.status ?? 500;
    this.code = opts?.code ?? 'UNKNOWN';
    this.severity = opts?.severity ?? 'error';
    this.details = opts?.details ?? null;
    this.envelope = opts?.envelope ?? null;
  }
}

/**
 * Specialized error for network failures.
 */
export class ApiNetworkError extends ApiError {
  constructor(message = 'Network error', cause?: unknown) {
    super(message, { status: 0, code: 'NETWORK', severity: 'error' });
    this.name = 'ApiNetworkError';
    if (cause !== undefined) {
      this.cause = cause;
    }
  }
}

/**
 * Type guard checking if an error is an AbortError.
 */
export function isAbortError(err: unknown): boolean {
  return (
    typeof err === 'object' &&
    err !== null &&
    'name' in err &&
    (err as { name: unknown }).name === 'AbortError'
  );
}

/**
 * Attempts to parse a gms.web-error/1 envelope from a response body.
 */
export function parseWebErrorBody(body: unknown, status: number): ApiError | null {
  if (!isRecord(body)) return null;
  if (body.schema_id !== 'gms.web-error/1') return null;
  const errObj = body.error;
  if (!isRecord(errObj)) return null;
  if (typeof errObj.code !== 'string' || typeof errObj.message !== 'string') return null;

  const severity = typeof errObj.severity === 'string' ? errObj.severity : 'error';
  const details = isRecord(errObj.details) ? errObj.details : null;

  const envelope: WebErrorEnvelope = {
    schema_id: 'gms.web-error/1',
    status,
    error: {
      code: errObj.code,
      severity,
      phase: typeof errObj.phase === 'string' ? errObj.phase : 'unknown',
      message: errObj.message,
      ...(details !== null ? { details } : {}),
    },
  };

  return new ApiError(errObj.message, {
    status,
    code: errObj.code,
    severity,
    details,
    envelope,
  });
}

/**
 * Formats any error into a display string without throwing.
 */
export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === 'string') return err;
  if (isRecord(err) && typeof err.message === 'string') return err.message;
  return String(err);
}
