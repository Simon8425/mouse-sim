export interface WebGLFallbackProps {
  reason: string;
}

export function WebGLFallback({ reason }: WebGLFallbackProps) {
  return (
    <div className="webgl-fallback" role="alert">
      <span className="webgl-fallback__kicker">Display capability / WebGL</span>
      <h3>3D viewport unavailable</h3>
      <p>{reason}</p>
      <p className="muted">
        WebGL is unavailable in this browser. The analysis service and results remain available; use a
        browser with WebGL 2.0 support to restore the viewport.
      </p>
    </div>
  );
}
