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
import { isRecord } from '../api/contracts';

type ConfidenceLevel = 'high' | 'medium' | 'low' | 'unknown';

function objectConfidence(
  state: ReturnType<typeof useProjectStore>['state'],
  id: string,
): { label: string; level: ConfidenceLevel } {
  const objects = state.project?.objects;
  const raw = Array.isArray(objects)
    ? objects.find((item) => isRecord(item) && (item.id === id || item.name === id))
    : isRecord(objects)
      ? objects[id]
      : null;
  if (!isRecord(raw) || !isRecord(raw.classification)) {
    return { label: 'UNRATED', level: 'unknown' };
  }

  const confidence = raw.classification.confidence;
  if (typeof confidence === 'number' && Number.isFinite(confidence)) {
    return { label: confidence.toString(), level: 'unknown' };
  }
  if (typeof confidence === 'string' && confidence.trim().length > 0) {
    const normalized = confidence.toLowerCase();
    const level: ConfidenceLevel =
      normalized === 'high' || normalized === 'medium' || normalized === 'low' ? normalized : 'unknown';
    return { label: confidence.toUpperCase(), level };
  }
  return { label: 'UNRATED', level: 'unknown' };
}

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
      <div className="model-tree__header">
        <div>
          <span className="panel-eyebrow">Assembly navigator</span>
          <h2 className="model-tree__title">Model tree</h2>
        </div>
        <span className="model-tree__count">{entries.length.toString().padStart(2, '0')} ITEMS</span>
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
            const worstSeverity = severities.get(entry.id);
            const warningsCount = selectWarningsCount(state, entry.id);
            const findings = selectFindingsFor(state, entry.id);
            const findingTitle = findings.map((f) => f.message).join('\n');
            const confidence = objectConfidence(state, entry.id);
            const displayType =
              entry.className && entry.className.toLowerCase() !== entry.id.toLowerCase()
                ? entry.className
                : entry.geometry.type;

            return (
              <div
                key={entry.id}
                role="treeitem"
                tabIndex={index === focusedIndex ? 0 : -1}
                aria-selected={isSelected}
                className={`model-row${!isVisible ? ' model-row--hidden' : ''}${isSelected ? ' is-selected' : ''}`}
                onClick={() => dispatch({ type: 'SELECT', id: entry.id })}
                onKeyDown={(e) => handleKeyDown(e, index, entry.id)}
              >

                <div className="model-row__center">
                  <div className="model-row__title-line">
                    <span className="model-row__name">{entry.id}</span>
                  </div>
                  <div className="model-row__sub-line">
                    {displayType ? <span className="model-row__type muted">{displayType}</span> : null}
                    {confidence.label !== 'UNRATED' ? (
                      <span className={`model-row__confidence model-row__confidence--${confidence.level}`}>
                        CONF {confidence.label}
                      </span>
                    ) : null}
                    {worstSeverity ? (
                      <StatusBadge tone={severityTone(worstSeverity)} title={findingTitle}>
                        {severityLabel(worstSeverity)}
                      </StatusBadge>
                    ) : (
                      <span className="model-row__confidence">
                        {state.lastResult ? 'NO FINDINGS' : 'UNASSESSED'}
                      </span>
                    )}
                    {warningsCount > 0 ? (
                      <span className="model-row__warnings badge badge--warn" title={`${warningsCount} finding(s)`}>
                        {warningsCount}
                      </span>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
