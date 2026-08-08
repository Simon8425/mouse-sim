import * as React from 'react';
import { useProjectStore } from '../state/projectStore';
import {
  DROP_ORIENTATIONS,
  DROP_SURFACES,
  clampDropConfig,
  persistedConfigForTest,
  persistConfigForTest,
  type DropTestConfigState,
} from '../lib/studies';
import type { DropTestDefinition } from '../lib/studies';
import type { DropOrientation, DropSurface } from '../api/contracts';

export interface TestRunDialogProps {
  test: DropTestDefinition;
  onClose: () => void;
}

export function TestRunDialog({ test, onClose }: TestRunDialogProps): React.ReactElement {
  const { state, dispatch } = useProjectStore();
  const panelRef = React.useRef<HTMLDivElement | null>(null);
  const [config, setConfig] = React.useState<DropTestConfigState>(() =>
    persistedConfigForTest(test),
  );
  const hasGeometry = state.project !== null || state.preview !== null;

  React.useEffect(() => {
    panelRef.current?.focus();
  }, []);

  React.useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const update = (patch: Partial<DropTestConfigState>) => {
    const next = clampDropConfig({ ...config, ...patch });
    setConfig(next);
    persistConfigForTest(test, next);
  };

  const run = () => {
    const finalConfig = clampDropConfig(config);
    dispatch({
      type: 'RUN_DROP_TEST',
      test: test.test,
      config: {
        height_m: finalConfig.height_m,
        surface: finalConfig.surface,
        drop_count: finalConfig.drop_count,
        orientation: finalConfig.orientation,
        spin_rps: finalConfig.spin_rps,
        mass_kg: finalConfig.mass_kg,
      },
    });
    onClose();
  };

  return (
    <div className="mission-control">
      <div className="mission-control__backdrop" aria-hidden="true" onClick={onClose} />
      <div
        className="mission-control__panel"
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={test.title}
      >
        <header className="mission-control__header">
          <div className="mission-control__title">
            <h2>{test.title}</h2>
          </div>
          <button
            type="button"
            className="btn btn--ghost"
            aria-label="Close test card"
            onClick={onClose}
          >
            CLOSE
          </button>
        </header>

        <div className="mission-control__body mission-control__body--two-cards">
          <div className="mc-card">
            <div className="mc-card__content">
              <p className="mc-test-item__description">{test.description}</p>
              <div className="mc-test-item__controls">
                <label className="mc-field">
                  <span>Drop height (m)</span>
                  <input
                    type="number"
                    min={0.02}
                    max={2}
                    step={0.05}
                    value={config.height_m}
                    aria-label={`${test.title} height`}
                    onChange={(e) => update({ height_m: Number(e.target.value) })}
                  />
                </label>
                <label className="mc-field">
                  <span>Drops</span>
                  <input
                    type="number"
                    min={1}
                    max={20}
                    step={1}
                    value={config.drop_count}
                    aria-label={`${test.title} drop count`}
                    onChange={(e) => update({ drop_count: Number(e.target.value) })}
                  />
                </label>
                <label className="mc-field">
                  <span>Surface</span>
                  <select
                    value={config.surface}
                    aria-label={`${test.title} surface`}
                    onChange={(e) => update({ surface: e.target.value as DropSurface })}
                  >
                    {DROP_SURFACES.map((surface) => (
                      <option key={surface.value} value={surface.value}>
                        {surface.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="mc-field">
                  <span>Orientation</span>
                  <select
                    value={config.orientation}
                    aria-label={`${test.title} orientation`}
                    onChange={(e) => update({ orientation: e.target.value as DropOrientation })}
                  >
                    {DROP_ORIENTATIONS.map((orientation) => (
                      <option key={orientation.value} value={orientation.value}>
                        {orientation.label}
                      </option>
                    ))}
                  </select>
                </label>
                {test.test === 'tumble' ? (
                  <label className="mc-field">
                    <span>Spin (rev/s)</span>
                    <input
                      type="number"
                      min={0}
                      max={20}
                      step={0.5}
                      value={config.spin_rps}
                      aria-label={`${test.title} spin`}
                      onChange={(e) => update({ spin_rps: Number(e.target.value) })}
                    />
                  </label>
                ) : null}
                <label className="mc-field">
                  <span>Mass (kg, optional)</span>
                  <input
                    type="number"
                    min={0.01}
                    max={10}
                    step={0.01}
                    placeholder="auto"
                    value={config.mass_kg ?? ''}
                    aria-label={`${test.title} mass`}
                    onChange={(e) =>
                      update({ mass_kg: e.target.value === '' ? null : Number(e.target.value) })
                    }
                  />
                </label>
              </div>
              <div className="mc-actions-row">
                <button
                  type="button"
                  className="btn btn--primary btn--sm"
                  onClick={run}
                  disabled={!hasGeometry}
                  title={hasGeometry ? `Run ${test.title}` : 'Upload a model first'}
                >
                  Run {test.title}
                </button>
                <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>
                  CANCEL
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
