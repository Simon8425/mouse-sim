import * as React from 'react';
import { createPortal } from 'react-dom';
import { useProjectStore, COMPONENT_ROLES } from '../state/projectStore';
import { selectObjectEntries } from '../state/selectors';
import { createClient } from '../api/client';

function EyeIcon({ open }: { open: boolean }): React.ReactElement {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden="true"
    >
      {open ? (
        <>
          <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" />
          <circle cx="12" cy="12" r="3" />
        </>
      ) : (
        <>
          <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
          <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
          <path d="M14.12 14.12a3 3 0 1 1-4.24-4.24" />
          <path d="M1 1l22 22" />
        </>
      )}
    </svg>
  );
}

export const ModelTree = React.memo(function ModelTree(): React.ReactElement {
  const { state, dispatch } = useProjectStore();
  const entries = selectObjectEntries(state);

  const [activePopoverId, setActivePopoverId] = React.useState<string | null>(null);
  const [popoverPos, setPopoverPos] = React.useState<{ top: number; left: number }>({ top: 100, left: 304 });

  // Close floating component card when clicking outside
  React.useEffect(() => {
    if (!activePopoverId) return;

    const handlePointerDownOutside = (e: PointerEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && !target.closest('.model-row__floating-card') && !target.closest('.model-row__mat-pill')) {
        setActivePopoverId(null);
      }
    };

    window.addEventListener('pointerdown', handlePointerDownOutside);
    return () => {
      window.removeEventListener('pointerdown', handlePointerDownOutside);
    };
  }, [activePopoverId]);

  // Close floating popover immediately if user scrolls the model tree
  React.useEffect(() => {
    const list = listRef.current;
    if (!list || !activePopoverId) return;

    const handleScroll = () => {
      setActivePopoverId(null);
    };

    list.addEventListener('scroll', handleScroll, { passive: true });
    return () => {
      list.removeEventListener('scroll', handleScroll);
    };
  }, [activePopoverId]);

  const listRef = React.useRef<HTMLDivElement | null>(null);
  const rowRefs = React.useRef(new Map<string, HTMLDivElement | null>());

  React.useEffect(() => {
    const selectedId = state.selectedId;
    if (!selectedId) return;
    const row = rowRefs.current.get(selectedId);
    if (row && listRef.current?.contains(row)) {
      row.scrollIntoView({ block: 'nearest' });
    }
  }, [state.selectedId, entries]);

  // Roving tabindex state
  const [focusedIndex, setFocusedIndex] = React.useState(0);

  const handleKeyDown = (e: React.KeyboardEvent, index: number, id: string) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const nextIndex = (index + 1) % entries.length;
      setFocusedIndex(nextIndex);
      const nextId = entries[nextIndex]?.id;
      if (nextId) rowRefs.current.get(nextId)?.focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const prevIndex = (index - 1 + entries.length) % entries.length;
      setFocusedIndex(prevIndex);
      const prevId = entries[prevIndex]?.id;
      if (prevId) rowRefs.current.get(prevId)?.focus();
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      dispatch({ type: 'SELECT', id });
    }
  };

  // Auto-classify: start a job and poll until done (or error).
  const clientRef = React.useRef<ReturnType<typeof createClient> | null>(null);
  if (clientRef.current === null) clientRef.current = createClient();
  const pollRef = React.useRef<number | null>(null);

  const stopPolling = React.useCallback(() => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  React.useEffect(() => stopPolling, [stopPolling]);

  const handleAutoClassify = async () => {
    const assetId = state.preview?.display_asset?.asset_id;
    if (!assetId) return;
    const client = clientRef.current;
    if (!client) return;
    dispatch({ type: 'CLASSIFY_START', jobId: 'starting' });
    try {
      const job = await client.startClassify(assetId, undefined, {
        apiKey: state.aiConfig.apiKey || undefined,
        model: state.aiConfig.model || undefined,
        provider: state.aiConfig.provider || undefined,
        endpoint: state.aiConfig.endpoint || undefined,
      });
      dispatch({ type: 'CLASSIFY_START', jobId: job.job_id });
      const tick = async () => {
        try {
          const status = await client.getClassifyJob(job.job_id);
          dispatch({
            type: 'CLASSIFY_POLL',
            status: status.status,
            total: status.total,
            done: status.done,
            error: status.error ?? null,
            results: status.results ?? [],
          });
          if (status.status === 'done' || status.status === 'error') {
            stopPolling();
          }
        } catch (err) {
          stopPolling();
          dispatch({
            type: 'CLASSIFY_ERROR',
            message: `AI classification failed: ${err instanceof Error ? err.message : String(err)}`,
          });
        }
      };
      await tick();
      pollRef.current = window.setInterval(tick, 1500);
    } catch (err) {
      dispatch({
        type: 'CLASSIFY_ERROR',
        message: err instanceof Error ? err.message : 'Failed to start AI classification',
      });
    }
  };

  const suggestionCount = Object.keys(state.aiClassifications).length;
  // Treat queued as in-flight so the button is disabled during the
  // start + first-poll window (prevents launching duplicate jobs).
  const isRunning =
    state.classifyJob?.status === 'running' || state.classifyJob?.status === 'queued';

  const handleButtonClick = () => {
    if (isRunning) return;
    if (suggestionCount > 0) {
      dispatch({ type: 'SET_CLASSIFY_MODAL_OPEN', open: true });
      return;
    }
    handleAutoClassify();
  };

  const classifyStatusLabel = () => {
    const job = state.classifyJob;
    if (isRunning) return `${job?.done ?? 0}/${job?.total || '…'}`;
    if (suggestionCount > 0) return `Review (${suggestionCount})`;
    if (job?.status === 'error') return 'Retry AI';
    return 'Classify';
  };

  if (entries.length === 0) {
    return (
      <div className="model-tree">
        <div className="model-tree__header">
          <div>
            <h2 className="model-tree__title">Model tree</h2>
          </div>
        </div>
        <div className="model-tree__empty-state">
          {state.previewStatus === 'working' ? (
            <div className="empty-state-loading">
              <span>Loading assembly...</span>
            </div>
          ) : (
            <span className="empty-state-text">No geometry loaded</span>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="model-tree">
      <div className="model-tree__header">
        <div>
          <h2 className="model-tree__title">Model tree</h2>
        </div>
        {state.preview?.display_asset?.parts &&
        state.preview.display_asset.parts.length > 0 &&
        state.partGeometry ? (
          <button
            type="button"
            className="model-tree__ai-button"
            title={
              state.classifyJob?.error
                ? `AI classification error: ${state.classifyJob.error}`
                : suggestionCount > 0
                  ? 'Review AI classification suggestions'
                  : 'Auto-classify components using vision model'
            }
            onClick={handleButtonClick}
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
            </svg>
            <span>{classifyStatusLabel()}</span>
          </button>
        ) : null}
        {state.classifyJob?.error ? (
          <div className="model-tree__ai-error" role="alert">
            {state.classifyJob.error}
          </div>
        ) : null}
      </div>

      <div className="model-tree__list" role="tree" aria-label="Model Hierarchy" ref={listRef}>
        {state.preview?.display_asset?.parts &&
          state.preview.display_asset.parts.length > 0 &&
          state.partGeometry ? (
          <div className="model-row model-row--parent" role="treeitem">
            <button
              type="button"
              className="model-row__eye"
              aria-label={`Toggle visibility ${state.preview.source_name ?? 'model'}`}
              aria-pressed={entries.every((entry) => state.visibility[entry.id] ?? true)}
              onClick={() => {
                const allVisible = entries.every((entry) => state.visibility[entry.id] ?? true);
                for (const entry of entries) {
                  dispatch({ type: 'SET_VISIBILITY', id: entry.id, visible: !allVisible });
                }
              }}
            >
              <EyeIcon open={entries.every((entry) => state.visibility[entry.id] ?? true)} />
            </button>
            <span className="model-row__name model-row__name--parent">
              {state.preview.source_name ?? 'Assembly'}
            </span>
            <div className="model-row__right">
              <div className="model-row__mat-pill model-row__mat-pill--static">
                <span className="model-row__pill-text">{entries.length} parts</span>
              </div>
            </div>
          </div>
        ) : null}

        {entries.map((entry, index) => {
          const isSelected = state.selectedId === entry.id;
          const isVisible = state.visibility[entry.id] ?? true;
          const displayName = entry.name || entry.id;
          const assignedMatKey = state.objectMaterials?.[entry.id];
          const assignedRole = state.objectClassifications?.[entry.id];
          const pillText = assignedMatKey || (entry.className ? entry.className : 'Default');

          return (
            <div
              key={entry.id}
              ref={(el) => {
                rowRefs.current.set(entry.id, el);
              }}
              className={`model-row${isSelected ? ' is-selected' : ''}`}
              role="treeitem"
              aria-selected={isSelected}
              tabIndex={focusedIndex === index ? 0 : -1}
              onKeyDown={(e) => handleKeyDown(e, index, entry.id)}
              onClick={() => dispatch({ type: 'SELECT', id: entry.id })}
            >
              <button
                type="button"
                className="model-row__eye"
                aria-label={`Toggle visibility ${displayName}`}
                aria-pressed={isVisible}
                onClick={(e) => {
                  e.stopPropagation();
                  dispatch({ type: 'SET_VISIBILITY', id: entry.id, visible: !isVisible });
                }}
              >
                <EyeIcon open={isVisible} />
              </button>
              <span className="model-row__name" title={displayName}>
                {displayName}
              </span>
              <div className="model-row__right">
                <button
                  type="button"
                  className={`model-row__mat-pill${activePopoverId === entry.id ? ' is-active' : ''}`}
                  aria-label={`Select material for ${displayName}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    dispatch({ type: 'SELECT', id: entry.id });
                    if (activePopoverId === entry.id) {
                      setActivePopoverId(null);
                    } else {
                      const rect = e.currentTarget.getBoundingClientRect();
                      const cardHeight = 290;
                      const cardWidth = 275;
                      const top = Math.max(14, Math.min(rect.top - 4, window.innerHeight - cardHeight - 14));
                      const left = Math.max(298, Math.min(rect.right + 12, window.innerWidth - cardWidth - 14));
                      setPopoverPos({ top, left });
                      setActivePopoverId(entry.id);
                    }
                  }}
                >
                  <span className="model-row__pill-text">
                    {pillText}
                  </span>
                  <svg className="model-row__mat-chevron" width="10" height="6" viewBox="0 0 10 6" fill="none" aria-hidden="true">
                    <path d="M1.5 1.5L5 4.5L8.5 1.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>

                {activePopoverId === entry.id && typeof document !== 'undefined'
                  ? createPortal(
                      <div
                        className="model-row__floating-card"
                        style={{
                          position: 'fixed',
                          left: `${popoverPos.left}px`,
                          top: `${popoverPos.top}px`,
                          width: '275px',
                          zIndex: 1000,
                        }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <div className="floating-card__header">
                          <span className="floating-card__title">{displayName}</span>
                          <button
                            type="button"
                            className="floating-card__close"
                            aria-label="Close component card"
                            onClick={() => setActivePopoverId(null)}
                          >
                            ✕
                          </button>
                        </div>

                        <div className="floating-card__section">
                          <span className="floating-card__label">Mouse Part Role</span>
                          <div className="floating-card__chip-grid">
                            {COMPONENT_ROLES.map((r) => {
                              const isSelected = assignedRole === r.value;
                              return (
                                <button
                                  key={r.value}
                                  type="button"
                                  className={`floating-chip${isSelected ? ' is-selected' : ''}`}
                                  onClick={() => {
                                    dispatch({
                                      type: 'SET_OBJECT_CLASSIFICATION',
                                      objectId: entry.id,
                                      role: r.value,
                                    });
                                  }}
                                >
                                  {r.label.split(' / ')[0]}
                                </button>
                              );
                            })}
                          </div>
                        </div>

                        <div className="floating-card__section">
                          <span className="floating-card__label">Material</span>
                          <div className="floating-card__chip-grid">
                            <button
                              type="button"
                              className={`floating-chip${!assignedMatKey ? ' is-selected' : ''}`}
                              onClick={() => {
                                dispatch({
                                  type: 'SET_OBJECT_MATERIAL',
                                  objectId: entry.id,
                                  materialKey: null,
                                });
                              }}
                            >
                              Default
                            </button>
                            {(state.materials ?? []).map((m) => {
                              const isSelected = assignedMatKey === m.key;
                              return (
                                <button
                                  key={m.key}
                                  type="button"
                                  className={`floating-chip${isSelected ? ' is-selected' : ''}`}
                                  onClick={() => {
                                    dispatch({
                                      type: 'SET_OBJECT_MATERIAL',
                                      objectId: entry.id,
                                      materialKey: m.key,
                                    });
                                  }}
                                >
                                  {m.key}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      </div>,
                      document.body,
                    )
                  : null}
              </div>
            </div>
            );
          })}
        </div>
      </div>
    );
});
