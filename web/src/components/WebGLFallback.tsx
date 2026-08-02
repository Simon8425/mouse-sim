export interface WebGLFallbackProps {
  reason: string;
}

export function WebGLFallback({ reason }: WebGLFallbackProps) {
  return (
    <div className="webgl-fallback" role="alert">
      <h3>3D Viewport Unavailable</h3>
      <p>{reason}</p>
      <p className="muted">
        The analysis API and results rail remain fully operational. Try using a browser with WebGL 2.0 support.
      </p>
    </div>
  );
}
