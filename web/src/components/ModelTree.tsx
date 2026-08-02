import * as React from 'react';
import { useProjectStore } from '../state/projectStore';
import {
  selectObjectEntries,
  selectFindingSeverities,
  selectFindingsFor,
  selectWarningsCount,
} from '../state/selectors';
import { severityTone, severityLabel } from '../lib/status';
import { StatusBadge } from './StatusBadge';

export function ModelTree(): React.ReactElement {
  const { state, dispatch } = useProjectStore();
  const entries = selectObjectEntries(state);
  const severities = selectFindingSeverities(state);

  const [search, setSearch] = React.useState('');
  const deferredSearch = React.useDeferredValue(search);
  const [filterSeverity, setFilterSeverity] = React.useState<string | null>(null);

  // Compute severity chip counts
  const counts = React.useMemo(() => {
    let blocker = 0, error = 0, warning = 0;
    for (const sev of severities.values()) {
      if (sev === 'blocker') blocker++;
      else if (sev === 'error') error++;
      else if (sev === 'warning') warning++;
    }
    return { blocker, error, warning, all: severities.size };
  }, [severities]);

  const filteredEntries = React.useMemo(() => {
    return entries.filter((entry) => {
      if (
        deferredSearch &&
        !entry.id.toLowerCase().includes(deferredSearch.toLowerCase()) &&
        !(entry.className && entry.className.toLowerCase().includes(deferredSearch.toLowerCase()))
      ) {
        return false;
      }
      if (filterSeverity) {
        const sev = severities.get(entry.id);
        if (sev !== filterSeverity) return false;
      }
      return true;
    });
  }, [entries, deferredSearch, filterSeverity, severities]);

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
    }
  };

  return (
    <div className="model-tree">
      <div className="model-tree__search">
        <input
          type="text"
          placeholder="Filter models..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Filter models"
        />
      </div>

      <div className="model-tree__chips" role="group" aria-label="Filter by severity">
        <button
          type="button"
          className={`chip${filterSeverity === null ? ' chip--active' : ''}`}
          onClick={() => setFilterSeverity(null)}
        >
          All ({entries.length})
        </button>
        {counts.blocker > 0 && (
          <button
            type="button"
            className={`chip${filterSeverity === 'blocker' ? ' chip--active' : ''}`}
            onClick={() => setFilterSeverity(filterSeverity === 'blocker' ? null : 'blocker')}
          >
            Blocker ({counts.blocker})
          </button>
        )}
        {counts.error > 0 && (
          <button
            type="button"
            className={`chip${filterSeverity === 'error' ? ' chip--active' : ''}`}
            onClick={() => setFilterSeverity(filterSeverity === 'error' ? null : 'error')}
          >
            Error ({counts.error})
          </button>
        )}
        {counts.warning > 0 && (
          <button
            type="button"
            className={`chip${filterSeverity === 'warning' ? ' chip--active' : ''}`}
            onClick={() => setFilterSeverity(filterSeverity === 'warning' ? null : 'warning')}
          >
            Warning ({counts.warning})
          </button>
        )}
      </div>

      {state.isolatedId ? (
        <div className="model-tree__isolate-bar">
          <span>Isolated: {state.isolatedId}</span>
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => dispatch({ type: 'CLEAR_ISOLATION' })}
          >
            Clear
          </button>
        </div>
      ) : null}

      {filteredEntries.length === 0 ? (
        <p className="model-tree__empty muted">No matching objects found.</p>
      ) : (
        <div className="model-tree__list" role="tree" aria-label="Model Hierarchy">
          {filteredEntries.map((entry, index) => {
            const isSelected = state.selectedId === entry.id;
            const isVisible = state.visibility[entry.id] ?? true;
            const isIsolated = state.isolatedId === entry.id;
            const worstSeverity = severities.get(entry.id);
            const warningsCount = selectWarningsCount(state, entry.id);
            const findings = selectFindingsFor(state, entry.id);
            const findingTitle = findings.map((f) => f.message).join('\n');

            return (
              <div
                key={entry.id}
                role="treeitem"
                tabIndex={index === focusedIndex ? 0 : -1}
                aria-selected={isSelected}
                className={`model-row${!isVisible ? ' model-row--hidden' : ''}`}
                onClick={() => dispatch({ type: 'SELECT', id: entry.id })}
                onKeyDown={(e) => handleKeyDown(e, index, entry.id)}
              >
                <button
                  type="button"
                  className="model-row__visibility btn btn--ghost"
                  aria-pressed={isVisible}
                  aria-label={isVisible ? 'Hide object' : 'Show object'}
                  onClick={(e) => {
                    e.stopPropagation();
                    dispatch({ type: 'TOGGLE_VISIBILITY', id: entry.id });
                  }}
                >
                  {isVisible ? '👁' : '🙈'}
                </button>

                <span className="model-row__name">{entry.id}</span>
                <span className="model-row__type muted">
                  {entry.className ?? entry.geometry.type}
                </span>

                {worstSeverity ? (
                  <StatusBadge tone={severityTone(worstSeverity)} title={findingTitle}>
                    {severityLabel(worstSeverity)}
                  </StatusBadge>
                ) : null}

                {warningsCount > 0 ? (
                  <span className="model-row__warnings badge badge--warn">
                    {warningsCount}
                  </span>
                ) : null}

                <button
                  type="button"
                  className="model-row__isolate btn btn--ghost"
                  aria-pressed={isIsolated}
                  aria-label={isIsolated ? 'Clear isolation' : 'Isolate object'}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (isIsolated) {
                      dispatch({ type: 'CLEAR_ISOLATION' });
                    } else {
                      dispatch({ type: 'ISOLATE', id: entry.id });
                    }
                  }}
                >
                  {isIsolated ? '🎯' : '⭕'}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
