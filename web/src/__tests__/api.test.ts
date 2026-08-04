import { afterEach, describe, expect, it, vi } from 'vitest';
import { createClient } from '../api/client';
import { ApiError, isUnsupportedGeometryPreview } from '../api/errors';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('geometry preview API handling', () => {
  it('preserves unsupported 422 preview envelopes and diagnostics', async () => {
    const preview = {
      schema_id: 'gms.geometry-preview/1',
      supported: false,
      format: 'step',
      source_units: null,
      geometry: null,
      diagnostics: [
        {
          code: 'cad_converter_missing',
          severity: 'error',
          message: 'STEP conversion is unavailable',
          details: { plugin: 'server-cad' },
        },
      ],
      source_name: 'housing.step',
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(preview), {
        status: 422,
        headers: { 'content-type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await createClient().normalizeGeometry({
      format: 'step',
      name: 'housing.step',
      body: new ArrayBuffer(0),
    });

    expect(result).toEqual(preview);
    expect(isUnsupportedGeometryPreview(result)).toBe(true);
    expect(result.diagnostics[0].details).toEqual({ plugin: 'server-cad' });
  });

  it('still converts a non-preview 422 envelope into ApiError', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          schema_id: 'gms.web-error/1',
          status: 422,
          error: {
            code: 'E_INVALID_FORMAT',
            severity: 'error',
            phase: 'web',
            message: 'unsupported geometry format',
          },
        }),
        {
          status: 422,
          headers: { 'content-type': 'application/json' },
        },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      createClient().normalizeGeometry({
        format: 'unknown',
        body: new ArrayBuffer(0),
      }),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
