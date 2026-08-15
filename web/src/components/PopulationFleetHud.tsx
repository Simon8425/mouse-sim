import * as React from 'react';
import type { PopulationResult } from '../api/contracts';
import type { FleetUnitData } from '../scene/populationFleetScene';

export interface PopulationFleetHudProps {
  population: PopulationResult;
  playing: boolean;
  onTogglePlay: () => void;
  selectedUnit: FleetUnitData | null;
  onCloseSelectedUnit: () => void;
}

export function PopulationFleetHud({
  population,
  playing,
  onTogglePlay,
  selectedUnit,
  onCloseSelectedUnit,
}: PopulationFleetHudProps): React.ReactElement {
  const failureRate = population.failure_rate ?? 0;
  const failurePct = (failureRate * 100).toFixed(1);
  const sampleCount = population.sample_count ?? 10000;
  const unitsFailed = population.units_failed ?? Math.round(sampleCount * failureRate);
  const verdict = population.verdict ?? (failureRate > 0.15 ? 'fail' : failureRate > 0.03 ? 'warn' : 'pass');

  return (
    <div className="population-hud" aria-label="Population Fleet Analytics">
      {/* Top Banner Control Strip */}
      <div className="population-hud__toolbar">
        <button
          type="button"
          className="population-hud__action-btn"
          onClick={onTogglePlay}
          aria-label={playing ? 'Pause fleet simulation' : 'Play fleet simulation'}
        >
          {playing ? 'Pause' : 'Play'}
        </button>

        <div className="population-hud__divider" />

        <div className="population-hud__stat">
          <span className="population-hud__stat-label">Fleet:</span>
          <span className="population-hud__stat-val">
            {sampleCount.toLocaleString()} units
          </span>
        </div>

        <div className="population-hud__divider" />

        <div className="population-hud__stat" title={`${(sampleCount - unitsFailed).toLocaleString()} of ${sampleCount.toLocaleString()} units passed`}>
          <span className="population-hud__stat-label">Yield:</span>
          <span className="population-hud__stat-val" style={{ color: '#22c55e' }}>
            {((1 - failureRate) * 100).toFixed(1)}%
          </span>
        </div>

        <div className="population-hud__divider" />

        <div
          className="population-hud__stat"
          title={`${unitsFailed.toLocaleString()} of ${sampleCount.toLocaleString()} units failed`}
        >
          <span className="population-hud__stat-label">Failure:</span>
          <span className={`population-hud__stat-val population-hud__stat-val--${verdict}`}>
            {failurePct}%
          </span>
        </div>
      </div>

      {/* Selected Unit 3D Inspector Card */}
      {selectedUnit ? (
        <div className="population-hud__unit-card" role="region" aria-label="Selected fleet unit inspection">
          <div className="unit-card__header">
            <div className="unit-card__title-wrap">
              <span className="unit-card__badge unit-card__badge--id">Unit #{selectedUnit.id}</span>
              <span className={`unit-card__badge unit-card__badge--${selectedUnit.verdict}`}>
                {selectedUnit.verdict.toUpperCase()}
              </span>
            </div>
            <button
              type="button"
              className="unit-card__close-btn"
              aria-label="Close unit inspection"
              onClick={onCloseSelectedUnit}
            >
              ✕
            </button>
          </div>

          <div className="unit-card__grid">
            <div className="unit-card__item">
              <span className="unit-card__item-lbl">Peak stress</span>
              <span className="unit-card__item-val">{selectedUnit.stressMpa} MPa</span>
            </div>
            <div className="unit-card__item">
              <span className="unit-card__item-lbl">Wall delta</span>
              <span className="unit-card__item-val">{selectedUnit.wallThicknessDeltaMm > 0 ? `+${selectedUnit.wallThicknessDeltaMm}` : selectedUnit.wallThicknessDeltaMm} mm</span>
            </div>
            <div className="unit-card__item">
              <span className="unit-card__item-lbl">Mass offset</span>
              <span className="unit-card__item-val">{selectedUnit.massOffsetG > 0 ? `+${selectedUnit.massOffsetG}` : selectedUnit.massOffsetG} g</span>
            </div>
            <div className="unit-card__item">
              <span className="unit-card__item-lbl">Drop angle</span>
              <span className="unit-card__item-val">{selectedUnit.dropAngleDeg}°</span>
            </div>
          </div>

          {selectedUnit.failureMode ? (
            <div className="unit-card__failure-note">
              <span>{selectedUnit.failureMode}</span>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
