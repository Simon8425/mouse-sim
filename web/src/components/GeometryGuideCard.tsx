import { useProjectStore } from '../state/projectStore';

export interface GeometryGuideCardProps {
  onUpload: () => void;
}

/**
 * Start-screen card shown in the middle of the viewport while no geometry is
 * loaded: explains the accepted geometry formats and opens the upload dialog.
 */
export function GeometryGuideCard({ onUpload }: GeometryGuideCardProps): React.ReactElement {
  const { state } = useProjectStore();
  const working = state.previewStatus === 'working';
  const baselineLoading = state.sourceStatus === 'loading';
  const baselineText =
    state.sourceStatus === 'ready'
      ? `Baseline available: ${state.projectName || 'server source'}`
      : state.sourceStatus === 'error'
        ? 'Baseline unavailable — upload a part to continue'
        : 'Baseline loads automatically when the engine is connected';

  return (
    <div className="guide-card" role="dialog" aria-label="Geometry upload guide">
      <span className="guide-card__kicker">Geometry</span>
      <svg className="guide-card__art" viewBox="0 0 120 64" aria-hidden="true" focusable="false">
        <rect x="18" y="14" width="84" height="36" rx="8" fill="none" stroke="currentColor" strokeWidth="3" />
        <line x1="18" y1="22" x2="102" y2="22" stroke="currentColor" strokeWidth="3" />
        <circle cx="60" cy="34" r="4" fill="currentColor" />
      </svg>
      <h2>Upload geometry to analyze</h2>
      <p className="guide-card__body">
        Load the server baseline or import a part. Analysis runs on normalized geometry only.
      </p>
      <div className={`guide-card__baseline${baselineLoading ? ' guide-card__baseline--loading' : ''}`}>
        {baselineText}
      </div>
      <ul className="guide-card__list">
        <li>
          <strong>JSON</strong> — analytic primitives or a project document with explicit geometry,
          materials, and study inputs.
        </li>
        <li>
          <strong>OBJ / STL</strong> — triangle meshes. Units are requested on import.
        </li>
        <li>
          <strong>STEP / STP</strong> — requires the server CAD converter plugin.
        </li>
      </ul>
      <div className="guide-card__actions">
        <button
          type="button"
          className="btn btn--primary"
          onClick={onUpload}
          disabled={working}
        >
          {working ? 'Processing…' : 'Choose geometry file'}
        </button>
      </div>
    </div>
  );
}
