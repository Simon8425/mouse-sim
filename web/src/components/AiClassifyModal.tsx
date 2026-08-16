import React from 'react';
import { useProjectStore, COMPONENT_ROLES } from '../state/projectStore';
import type { AiClassification } from '../api/contracts';

function getConfidenceTone(confidence?: number): 'ok' | 'warn' | 'dim' {
  if (confidence === undefined || confidence === null) return 'dim';
  if (confidence >= 0.8) return 'ok';
  if (confidence >= 0.5) return 'warn';
  return 'dim';
}

export function AiClassifyModal(): React.ReactElement | null {
  const { state, dispatch } = useProjectStore();
  const [editedRoles, setEditedRoles] = React.useState<Record<string, string>>({});
  const modalRef = React.useRef<HTMLDivElement | null>(null);

  const rawEntries = Object.entries(state.aiClassifications);
  const totalCount = rawEntries.length;

  // A user-edited role overrides the original suggestion for bucketing: an
  // entry the user gave a real role counts as recognized even if the model's
  // confidence was 0 (and vice versa for unresolved).
  const effectiveRole = (objectId: string, item: AiClassification | undefined): string =>
    editedRoles[objectId] || item?.component_type || 'unresolved';

  const recognizedEntries = rawEntries.filter(([objectId, item]) => {
    const role = effectiveRole(objectId, item);
    if (role === 'unresolved') return false;
    if (editedRoles[objectId]) return true;
    return (item?.confidence ?? 0) > 0;
  });

  const handleClose = React.useCallback(() => {
    dispatch({ type: 'SET_CLASSIFY_MODAL_OPEN', open: false });
  }, [dispatch]);

  const handleAcceptAll = React.useCallback(() => {
    // For any item edited by the user, apply the edited role first
    for (const [objectId, role] of Object.entries(editedRoles)) {
      if (role && role !== 'unresolved') {
        dispatch({ type: 'CLASSIFY_APPLY_ONE', objectId, role });
      }
    }
    dispatch({ type: 'CLASSIFY_APPLY_ALL' });
  }, [dispatch, editedRoles]);

  const handleDismissAll = React.useCallback(() => {
    dispatch({ type: 'CLASSIFY_DISMISS_ALL' });
  }, [dispatch]);

  // Handle escape key
  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleClose]);

  // Focus trap focus on mount
  React.useEffect(() => {
    modalRef.current?.focus();
  }, []);

  if (!state.classifyModalOpen || totalCount === 0) {
    return null;
  }

  // Find part names from display_asset parts
  const partNamesById: Record<string, string> = {};
  if (state.preview?.display_asset?.parts) {
    for (const part of state.preview.display_asset.parts) {
      if (part.id) {
        partNamesById[part.id] = part.name || part.id;
      }
    }
  }

  return (
    <div
      className="ai-classify-panel"
      role="dialog"
      aria-label="AI Component Classification"
      ref={modalRef}
      tabIndex={-1}
    >
      <header className="ai-classify-header">
        <div className="ai-classify-header__title-row">
          <h2 className="ai-classify-title">AI Component Classification</h2>
          <span className="ai-classify-badge">{recognizedEntries.length} recognized</span>
        </div>
        <button
          type="button"
          className="inspector-panel__close-btn"
          aria-label="Close AI classification modal"
          onClick={handleClose}
          title="Close AI classification panel"
        >
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </header>

      <div className="ai-classify-body" role="list">
        {rawEntries.length === 0 ? (
          <div className="ai-classify-empty">
            <span>No components to classify.</span>
          </div>
        ) : (
          rawEntries.map(([objectId, item]) => {
            const partName = partNamesById[objectId] || objectId;
            const currentRole = effectiveRole(objectId, item);
            const tone = getConfidenceTone(item?.confidence);
            const pct =
              item?.confidence !== undefined && item.confidence > 0
                ? `${Math.round(item.confidence * 100)}%`
                : currentRole !== 'unresolved'
                ? 'Manual'
                : '0%';
            const isSelected = state.selectedId === objectId;

            return (
              <div
                key={objectId}
                className={`ai-classify-row${isSelected ? ' is-selected' : ''}`}
                role="listitem"
                onClick={() => dispatch({ type: 'SELECT', id: objectId })}
              >
                <span className="ai-classify-row__name" title={partName}>
                  {partName}
                </span>
                <div className="ai-classify-row__right" onClick={(e) => e.stopPropagation()}>
                  <span className={`ai-classify-pill ai-classify-pill--${tone}`}>
                    {pct}
                  </span>
                  <select
                    id={`role-${objectId}`}
                    className="ai-classify-pill-select"
                    value={currentRole}
                    aria-label={`Role for ${partName}`}
                    onChange={(e) =>
                      setEditedRoles({ ...editedRoles, [objectId]: e.target.value })
                    }
                  >
                    <option value="unresolved">Unresolved</option>
                    {COMPONENT_ROLES.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            );
          })
        )}
      </div>

      <footer className="ai-classify-footer">
        <button
          type="button"
          className="ai-classify-footer__btn-dismiss"
          onClick={handleDismissAll}
        >
          Dismiss all
        </button>
        <div className="ai-classify-footer__right">
          <button
            type="button"
            className="ai-classify-footer__btn-close"
            onClick={handleClose}
          >
            Close
          </button>
          <button
            type="button"
            className="ai-classify-footer__btn-accept"
            onClick={handleAcceptAll}
          >
            Accept recognized ({recognizedEntries.length})
          </button>
        </div>
      </footer>
    </div>
  );
}
