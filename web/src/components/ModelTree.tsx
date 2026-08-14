import * as React from 'react';
import { useProjectStore, COMPONENT_ROLES } from '../state/projectStore';
import { selectObjectEntries } from '../state/selectors';

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

  const [search, setSearch] = React.useState('');
  const deferredSearch = React.useDeferredValue(search);
  const [activePopoverId, setActivePopoverId] = React.useState<string | null>(null);
  const [popoverTop, setPopoverTop] = React.useState<number>(100);

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

  const filteredEntries = React.useMemo(() => {
    return entries.filter((entry) => {
      if (
        deferredSearch &&
        !entry.id.toLowerCase().includes(deferredSearch.toLowerCase()) &&
        !(entry.className && entry.className.toLowerCase().includes(deferredSearch.toLowerCase()))
      ) {
        return false;
      }
      return true;
    });
  }, [entries, deferredSearch]);

  const listRef = React.useRef<HTMLDivElement | null>(null);
  const rowRefs = React.useRef(new Map<string, HTMLDivElement | null>());

  // Selecting a component from outside the tree is an explicit request to
  // show it: clear any filters that would hide the row, then scroll it into
  // view once it renders.
  React.useEffect(() => {
    const selectedId = state.selectedId;
    if (!selectedId) return;
    if (!filteredEntries.some((entry) => entry.id === selectedId)) {
      setSearch('');
    }
  }, [state.selectedId, filteredEntries]);

  React.useEffect(() => {
    const selectedId = state.selectedId;
    if (!selectedId) return;
    const row = rowRefs.current.get(selectedId);
    if (row && listRef.current?.contains(row)) {
      row.scrollIntoView({ block: 'nearest' });
    }
  }, [state.selectedId, filteredEntries]);

  // Roving tabindex state
  const [focusedIndex, setFocusedIndex] = React.useState(0);

  const handleKeyDown = (e: React.KeyboardEvent, index: number, id: string) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setFocusedIndex(Math.min(index + 1, filteredEntries.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setFocusedIndex(Math.max(index - 1, 0));
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      dispatch({ type: 'SELECT', id });
      // Match the mouse behavior: selecting a component is an explicit
      // request to inspect it, so open the inspector drawer alongside it.
      dispatch({ type: 'SET_INSPECTOR_OPEN', open: true });
    }
  };

  if (entries.length === 0) {
    return (
      <div className="model-tree model-tree--empty">
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
      </div>
      <div className="model-tree__search">
        <input
          type="text"
          placeholder="Filter models..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Filter models"
        />
      </div>

      {filteredEntries.length === 0 ? (
        <p className="model-tree__empty muted">No matching objects found.</p>
      ) : (
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
          {filteredEntries.map((entry, index) => {
            const isSelected = state.selectedId === entry.id;
            const isVisible = state.visibility[entry.id] ?? true;
            const displayName = entry.name ?? entry.id;
            const rawType =
              entry.className && entry.className.toLowerCase() !== displayName.toLowerCase()
                ? entry.className
                : entry.geometry.type;
            const assignedMatKey = state.objectMaterials?.[entry.id];
            const assignedRole = state.objectClassifications?.[entry.id];
            const roleObj = COMPONENT_ROLES.find((r) => r.value === assignedRole);
            const displayChip = rawType !== 'mesh' ? rawType : null;
            const pillText = assignedMatKey
              ? assignedMatKey
              : roleObj
                ? roleObj.label.split(' / ')[0]
                : 'Default';
            return (
              <div
                key={entry.id}
                ref={(el) => {
                  rowRefs.current.set(entry.id, el);
                }}
                role="treeitem"
                tabIndex={index === focusedIndex ? 0 : -1}
                aria-selected={isSelected}
                title={displayName}
                className={`model-row model-row--child${!isVisible ? ' model-row--hidden' : ''}${isSelected ? ' is-selected' : ''}`}
                onClick={() => {
                  dispatch({ type: 'SELECT', id: entry.id });
                  dispatch({ type: 'SET_INSPECTOR_OPEN', open: true });
                }}
                onKeyDown={(e) => handleKeyDown(e, index, entry.id)}
              >
                <button
                  type="button"
                  className="model-row__eye"
                  aria-label={`Toggle visibility ${displayName}`}
                  aria-pressed={isVisible}
                  onClick={(e) => {
                    e.stopPropagation();
                    dispatch({ type: 'TOGGLE_VISIBILITY', id: entry.id });
                  }}
                >
                  <EyeIcon open={isVisible} />
                </button>
                <span className="model-row__name">{displayName}</span>
                <div className="model-row__right">
                  {displayChip ? <span className="model-row__chip">{displayChip}</span> : null}
                  <button
                    type="button"
                    className={`model-row__mat-pill${activePopoverId === entry.id ? ' is-active' : ''}`}
                    aria-label={`Configure component card for ${displayName}`}
                    title="Configure mouse part role & material"
                    onClick={(e) => {
                      e.stopPropagation();
                      dispatch({ type: 'SELECT', id: entry.id });
                      if (activePopoverId === entry.id) {
                        setActivePopoverId(null);
                      } else {
                        const rect = e.currentTarget.getBoundingClientRect();
                        setPopoverTop(Math.min(rect.top - 4, window.innerHeight - 160));
                        setActivePopoverId(entry.id);
                      }
                    }}
                  >
                    <span className="model-row__pill-text">
                      {pillText}
                    </span>
                    <svg className="model-row__mat-chevron" width="7" height="4" viewBox="0 0 7 4" fill="none" aria-hidden="true">
                      <path d="M1 0.5L3.5 3L6 0.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </button>

                  {activePopoverId === entry.id ? (
                    <div
                      className="model-row__floating-card"
                      style={{ position: 'fixed', left: '264px', top: `${popoverTop}px`, width: '275px' }}
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
                    </div>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
});
