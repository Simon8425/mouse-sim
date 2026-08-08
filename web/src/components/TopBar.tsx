import * as React from 'react';
import { useProjectStore } from '../state/projectStore';
import { selectSourceLabel } from '../state/selectors';
import { DROP_TESTS } from '../lib/studies';
import type { DropTestDefinition } from '../lib/studies';
import { TestRunDialog } from './TestRunCard';

export interface TopBarProps {
  onOpenNav: () => void;
  onOpenInspector: () => void;
  onOpenControl: () => void;
  onFit: () => void;
}

export function TopBar(props: TopBarProps): React.ReactElement {
  const { state, dispatch } = useProjectStore();
  const sourceReadyLabel = selectSourceLabel(state);

  const [menuOpen, setMenuOpen] = React.useState(false);
  const [pendingTest, setPendingTest] = React.useState<DropTestDefinition | null>(null);
  const menuRef = React.useRef<HTMLDivElement | null>(null);
  const triggerRef = React.useRef<HTMLButtonElement | null>(null);

  let sourceLabel: string;
  switch (state.sourceStatus) {
    case 'loading':
      sourceLabel = 'Loading…';
      break;
    case 'ready':
      sourceLabel = sourceReadyLabel;
      break;
    case 'error':
      sourceLabel = 'Source error';
      break;
    default:
      sourceLabel = '—';
      break;
  }

  const openTestDialog = (test: DropTestDefinition) => {
    setMenuOpen(false);
    setPendingTest(test);
  };

  const runQualification = () => {
    dispatch({ type: 'SET_MODE', mode: 'qualification' });
    dispatch({ type: 'SET_TAB', tab: 'qualification' });
    dispatch({ type: 'RUN_STUDY' });
    setMenuOpen(false);
  };

  // Close the menu on outside clicks.
  React.useEffect(() => {
    if (!menuOpen) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [menuOpen]);

  // Move focus into the menu when it opens so keyboard users land on an item.
  React.useEffect(() => {
    if (!menuOpen) return;
    const firstItem = menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]');
    firstItem?.focus();
  }, [menuOpen]);

  const handleMenuKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      setMenuOpen(false);
      triggerRef.current?.focus();
      return;
    }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp' && event.key !== 'Home' && event.key !== 'End') {
      return;
    }
    event.preventDefault();
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [],
    );
    if (items.length === 0) return;
    const currentIndex = items.indexOf(document.activeElement as HTMLButtonElement);
    let nextIndex = currentIndex;
    if (event.key === 'ArrowDown') nextIndex = (currentIndex + 1) % items.length;
    else if (event.key === 'ArrowUp') nextIndex = (currentIndex - 1 + items.length) % items.length;
    else if (event.key === 'Home') nextIndex = 0;
    else if (event.key === 'End') nextIndex = items.length - 1;
    items[nextIndex]?.focus();
  };

  return (
    <header className="top-bar">
      <div className="top-bar__identity">
        <div className="top-bar__project">
          <span className="top-bar__eyebrow">Project</span>
          <h1 className="top-bar__project-name">mouse_sim / {state.projectName || 'no project'}</h1>
        </div>
        <div className="top-bar__source">
          <span className="top-bar__source-label">Source</span>
          <span className="top-bar__source-value" title={sourceLabel}>
            {sourceLabel}
          </span>
        </div>
      </div>
      <div className="top-bar__actions">
        <button
          type="button"
          className={`btn btn--ghost${state.mode === 'exploration' ? ' is-active' : ''}`}
          onClick={() => {
            dispatch({ type: 'SET_MODE', mode: 'exploration' });
            dispatch({ type: 'SET_TAB', tab: 'overview' });
          }}
        >
          EXPLORATION
        </button>
        <div className="top-bar__menu" ref={menuRef}>
          <button
            ref={triggerRef}
            type="button"
            className={`btn btn--primary top-bar__menu-trigger${state.mode === 'qualification' ? ' is-active' : ''}`}
            aria-label="Run test menu"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            RUN TEST
            <span className="top-bar__menu-caret" aria-hidden="true">▾</span>
          </button>
          {menuOpen ? (
            <div
              className="top-bar__menu-popup"
              role="menu"
              aria-label="Run test"
              onKeyDown={handleMenuKeyDown}
            >
              {DROP_TESTS.map((test) => (
                <button
                  key={test.id}
                  type="button"
                  role="menuitem"
                  className="top-bar__menu-item"
                  onClick={() => openTestDialog(test)}
                >
                  {test.title}
                </button>
              ))}
              <div className="top-bar__menu-separator" role="separator" />
              <button
                type="button"
                role="menuitem"
                className="top-bar__menu-item"
                onClick={runQualification}
              >
                Run Qualification
              </button>
            </div>
          ) : null}
        </div>
        <button
          type="button"
          className="btn btn--ghost top-bar__nav-toggle"
          aria-label="Toggle model navigator"
          aria-expanded={state.navOpen}
          onClick={props.onOpenNav}
        >
          MODEL
        </button>
        <button type="button" className="btn btn--ghost" onClick={props.onFit} aria-label="Fit view">
          FIT
        </button>
        <button
          type="button"
          className="btn btn--ghost top-bar__control-toggle"
          aria-label="Control panel"
          aria-expanded={state.controlOpen}
          onClick={props.onOpenControl}
        >
          SETTINGS
        </button>
        <button
          type="button"
          className="btn btn--ghost top-bar__inspector-toggle"
          aria-label="Toggle inspector"
          aria-expanded={state.inspectorOpen}
          onClick={props.onOpenInspector}
        >
          INFO
        </button>
      </div>
      {pendingTest ? (
        <TestRunDialog test={pendingTest} onClose={() => setPendingTest(null)} />
      ) : null}
    </header>
  );
}
