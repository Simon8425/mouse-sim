import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  probeShaderPrecision,
  selectShaderPrecision,
  createUsableWebGL2Context,
} from '../scene/SceneViewport';

describe('probeShaderPrecision', () => {
  let gl: {
    VERTEX_SHADER: number;
    FRAGMENT_SHADER: number;
    HIGH_FLOAT: number;
    MEDIUM_FLOAT: number;
    getShaderPrecisionFormat: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    gl = {
      VERTEX_SHADER: 1,
      FRAGMENT_SHADER: 2,
      HIGH_FLOAT: 3,
      MEDIUM_FLOAT: 4,
      getShaderPrecisionFormat: vi.fn(),
    };
  });

  it('returns true when both shader stages expose a usable HIGH_FLOAT precision', () => {
    gl.getShaderPrecisionFormat.mockReturnValue({ precision: 23, rangeMin: 127, rangeMax: 127 });
    expect(probeShaderPrecision(gl as unknown as WebGLRenderingContext)).toBe(true);
    expect(gl.getShaderPrecisionFormat).toHaveBeenCalledWith(1, 3);
    expect(gl.getShaderPrecisionFormat).toHaveBeenCalledWith(2, 3);
  });

  it('returns false when getShaderPrecisionFormat returns null (the "reading precision of null" crash)', () => {
    gl.getShaderPrecisionFormat.mockReturnValue(null);
    expect(probeShaderPrecision(gl as unknown as WebGLRenderingContext)).toBe(false);
  });

  it('returns false when precision is zero (unusable)', () => {
    gl.getShaderPrecisionFormat.mockReturnValue({ precision: 0, rangeMin: 0, rangeMax: 0 });
    expect(probeShaderPrecision(gl as unknown as WebGLRenderingContext)).toBe(false);
  });

  it('returns false when getShaderPrecisionFormat throws', () => {
    gl.getShaderPrecisionFormat.mockImplementation(() => {
      throw new Error('context lost');
    });
    expect(probeShaderPrecision(gl as unknown as WebGLRenderingContext)).toBe(false);
  });

  it('returns false when the fragment shader probe returns null even if vertex works', () => {
    gl.getShaderPrecisionFormat
      .mockReturnValueOnce({ precision: 23, rangeMin: 127, rangeMax: 127 })
      .mockReturnValueOnce(null);
    expect(probeShaderPrecision(gl as unknown as WebGLRenderingContext)).toBe(false);
  });

  it('selects mediump when HIGH_FLOAT is null instead of allowing Three.js to crash', () => {
    gl.getShaderPrecisionFormat.mockImplementation((_shader: number, type: number) =>
      type === gl.HIGH_FLOAT ? null : { precision: 10, rangeMin: 14, rangeMax: 14 },
    );
    expect(selectShaderPrecision(gl as unknown as WebGLRenderingContext)).toBe('mediump');
  });

  it('selects lowp when all precision queries are unavailable', () => {
    gl.getShaderPrecisionFormat.mockReturnValue(null);
    expect(selectShaderPrecision(gl as unknown as WebGLRenderingContext)).toBe('lowp');
  });
});

describe('createUsableWebGL2Context', () => {
  it('requests a webgl2 context with permissive attributes and validates it', () => {
    const versionKey = 0x8fce;
    const maxTextureKey = 0x0d33;
    const maxVertexKey = 0x8869;
    const fakeGl = {
      VERSION: versionKey,
      MAX_TEXTURE_SIZE: maxTextureKey,
      MAX_VERTEX_ATTRIBS: maxVertexKey,
      getParameter: vi.fn((param: number) => {
        if (param === versionKey) return 'WebGL 2.0 (OpenGL ES 3.0)';
        if (param === maxTextureKey) return 16384;
        if (param === maxVertexKey) return 16;
        return null;
      }),
    };
    const getContext = vi.fn(() => fakeGl);
    const canvas = {
      getContext,
      width: 800,
      height: 600,
    } as unknown as HTMLCanvasElement;
    const result = createUsableWebGL2Context(canvas);
    expect(result).toBe(fakeGl);
    expect(getContext).toHaveBeenCalledWith('webgl2', expect.objectContaining({ antialias: false }));
  });

  it('returns null when the VERSION parameter is null (the "reading indexOf of null" crash)', () => {
    const fakeGl = {
      VERSION: null,
      MAX_TEXTURE_SIZE: 16384,
      MAX_VERTEX_ATTRIBS: 16,
      getParameter: vi.fn(() => null),
      getExtension: vi.fn(() => ({ loseContext: vi.fn() })),
    };
    const canvas = {
      getContext: vi.fn(() => fakeGl),
      width: 800,
      height: 600,
    } as unknown as HTMLCanvasElement;
    const result = createUsableWebGL2Context(canvas);
    expect(result).toBeNull();
  });

  it('returns null when no webgl2 context can be created', () => {
    const canvas = {
      getContext: vi.fn(() => null),
      width: 800,
      height: 600,
    } as unknown as HTMLCanvasElement;
    const result = createUsableWebGL2Context(canvas);
    expect(result).toBeNull();
  });

  it('returns null when getContext throws', () => {
    const canvas = {
      getContext: vi.fn(() => {
        throw new Error('blocked');
      }),
      width: 800,
      height: 600,
    } as unknown as HTMLCanvasElement;
    const result = createUsableWebGL2Context(canvas);
    expect(result).toBeNull();
  });
});
