import type {
  WebHealth,
  WebBaselineResponse,
  MaterialEntry,
  WebMaterialCatalog,
  GeometryPreview,
  WebAnalysisRequest,
  WebAnalysisResponse,
  AssetPartsResponse,
  AiClassification,
} from './contracts';
import { isRecord } from './contracts';
import {
  ApiError,
  ApiNetworkError,
  isAbortError,
  isGeometryPreviewEnvelope,
  parseWebErrorBody,
} from './errors';

export interface NormalizeInput {
  format: string;
  units?: string;
  name?: string;
  body: ArrayBuffer | Blob;
}

export class ApiClient {
  constructor(private readonly baseUrl = '') {}

  private async request<T>(
    path: string,
    options?: RequestInit,
    accept422 = false,
    isValidBody?: (body: unknown) => boolean,
  ): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    let response: Response;
    try {
      response = await fetch(url, options);
    } catch (err: unknown) {
      if (isAbortError(err)) throw err;
      throw new ApiNetworkError('Network request failed', err);
    }

    let body: unknown = null;
    const contentType = response.headers.get('content-type') ?? '';
    if (contentType.includes('application/json')) {
      try {
        body = await response.json();
      } catch {
        // Ignored, body remains null
      }
    }

    const acceptedStatus = response.ok || (accept422 && response.status === 422);
    if (acceptedStatus && (isValidBody ? isValidBody(body) : isRecord(body))) {
      return body as T;
    }

    if (!response.ok) {
      const apiErr = parseWebErrorBody(body, response.status);
      if (apiErr) {
        throw apiErr;
      }

      throw new ApiError(`HTTP ${response.status}: ${response.statusText}`, {
        status: response.status,
      });
    }

    throw new ApiError(`Invalid response format from ${path}`, {
      status: response.status,
    });
  }

  async getHealth(signal?: AbortSignal): Promise<WebHealth> {
    return this.request<WebHealth>('/api/health', { method: 'GET', signal });
  }

  async getBaseline(signal?: AbortSignal): Promise<WebBaselineResponse> {
    return this.request<WebBaselineResponse>('/api/projects/baseline', { method: 'GET', signal });
  }

  async getMaterials(signal?: AbortSignal): Promise<MaterialEntry[]> {
    const res = await this.request<WebMaterialCatalog | MaterialEntry[]>('/api/materials', {
      method: 'GET',
      signal,
    });
    if (Array.isArray(res)) return res;
    if (isRecord(res) && Array.isArray((res as WebMaterialCatalog).materials)) {
      return (res as WebMaterialCatalog).materials;
    }
    return [];
  }

  async getAssetParts(assetId: string, signal?: AbortSignal): Promise<AssetPartsResponse> {
    return this.request<AssetPartsResponse>(`/api/geometry/assets/${assetId}.parts.json`, {
      method: 'GET',
      signal,
    });
  }

  async normalizeGeometry(
    input: NormalizeInput,
    signal?: AbortSignal,
  ): Promise<GeometryPreview> {
    const params = new URLSearchParams();
    params.set('format', input.format);
    if (input.units && input.units.trim() !== '') {
      params.set('units', input.units.trim());
    }
    if (input.name && input.name.trim() !== '') {
      params.set('name', input.name.trim());
    }

    const blob =
      input.body instanceof Blob
        ? input.body
        : new Blob([input.body], { type: 'application/octet-stream' });

    return this.request<GeometryPreview>(
      `/api/geometry/normalize?${params.toString()}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: blob,
        signal,
      },
      true, // Accept 422 as valid envelope carrying geometry preview error diagnostics
      isGeometryPreviewEnvelope,
    );
  }

  async analyze(
    request: WebAnalysisRequest,
    signal?: AbortSignal,
  ): Promise<WebAnalysisResponse> {
    return this.request<WebAnalysisResponse>('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal,
    });
  }

  async startClassify(
    assetId: string,
    partIds?: string[],
    options?: { apiKey?: string; model?: string; provider?: string; endpoint?: string },
    signal?: AbortSignal,
  ): Promise<ClassifyJob> {
    return this.request<ClassifyJob>('/api/classify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        asset_id: assetId,
        part_ids: partIds ?? null,
        api_key: options?.apiKey || undefined,
        model: options?.model || undefined,
        provider: options?.provider || undefined,
        endpoint: options?.endpoint || undefined,
      }),
      signal,
    });
  }

  async getClassifyJob(jobId: string, signal?: AbortSignal): Promise<ClassifyJob> {
    return this.request<ClassifyJob>(`/api/classify/jobs/${encodeURIComponent(jobId)}`, {
      method: 'GET',
      signal,
    });
  }
}

export interface ClassifyJob {
  schema_id?: string;
  job_id: string;
  status: 'queued' | 'running' | 'done' | 'error';
  total: number;
  done: number;
  results: AiClassification[];
  error?: string | null;
}

export function createClient(baseUrl?: string): ApiClient {
  return new ApiClient(baseUrl);
}
