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
  const baselineText = 'No geometry loaded — upload a part to begin';

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
      <div className="guide-card__baseline">
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
          <strong>STEP / STP</strong> — faceted B-rep files are converted on the server.
          Advanced assemblies use the installed FreeCAD/OCCT tessellator and preserve
          placements, colors, holes, and curved surfaces; a missing kernel reports a blocker.
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
