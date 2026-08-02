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

  return (
    <div className="guide-card" role="dialog" aria-label="Geometry upload guide">
      <svg className="guide-card__art" viewBox="0 0 120 64" aria-hidden="true" focusable="false">
        <rect x="18" y="14" width="84" height="36" rx="8" fill="none" stroke="currentColor" strokeWidth="3" />
        <line x1="18" y1="22" x2="102" y2="22" stroke="currentColor" strokeWidth="3" />
        <circle cx="60" cy="34" r="4" fill="currentColor" />
      </svg>
      <h2>Upload geometry to analyze</h2>
      <p className="guide-card__body">
        Provide the mouse assembly (or a single part) as one of these formats:
      </p>
      <ul className="guide-card__list">
        <li>
          <strong>JSON</strong> — analytic primitives: box, sphere, cylinder, cone, frustum, mesh,
          or compound. Each object needs an <code>id</code> and a <code>geometry</code> entry with
          its type and dimensions (e.g. <code>{'{ "type": "box", "size": [40, 20, 4] }'}</code>);
          optionally add <code>material</code>, <code>structural_behavior</code>, and{' '}
          <code>classification</code>. A project document may add <code>load_case</code>,{' '}
          <code>structure</code>, <code>fixtures</code>, <code>impact</code>, and{' '}
          <code>tolerance_profile</code>.
        </li>
        <li>
          <strong>OBJ / STL</strong> — triangle meshes. You will be asked to pick the input units.
          Use closed, watertight meshes so volume, mass, and structural results are reliable.
        </li>
        <li>
          <strong>STEP / STP</strong> — not supported yet; requires the server CAD converter
          plugin.
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
