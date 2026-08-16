import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { FeaHud, formatStress, computeMaxDamage, countZoneVertices } from '../components/FeaHud';
import type { FeaResult } from '../api/contracts';
import type { LiveDropData } from '../scene/SceneViewport';

const fea: FeaResult = {
  computed: true,
  peak: {
    object_id: 'shell',
    vertex_index: 0,
    location_model_m: [0, 0, 0],
    damage: 0.00025,
    stress_pa: 5325.46,
    stress_mpa: 0.005325,
  },
  yield_stress_pa: 2e7,
  safety_factor: 3755.54,
  impact_window_s: 0.05,
  dent_threshold: 0.7,
  tear_threshold: 0.92,
  objects: [
    {
      object_id: 'shell',
      vertex_count: 4,
      damage: [0.00025, 0.0001, 0.8, 0.95],
      displacement: [],
      stress_pa: [],
    },
  ],
  procedural: [],
  assumptions: [],
  flags: ['FEA_STRUCTURAL_VALIDITY_INCONCLUSIVE', 'FEA_TEST_FLAG'],
};

describe('formatStress', () => {
  it('switches to kPa below 0.1 MPa', () => {
    expect(formatStress(0.005325)).toBe('5.3 kPa');
    expect(formatStress(0.05)).toBe('50.0 kPa');
    expect(formatStress(0.123)).toBe('0.12 MPa');
    expect(formatStress(17.435)).toBe('17.4 MPa');
    expect(formatStress(120)).toBe('120 MPa');
    expect(formatStress(Number.NaN)).toBe('—');
  });
});

describe('computeMaxDamage', () => {
  it('finds the field maximum across objects and procedural peaks', () => {
    expect(computeMaxDamage(fea)).toBeCloseTo(0.95);
    expect(computeMaxDamage(null)).toBe(0);
  });

  it('includes the peak record damage (shader-normalization parity)', () => {
    // The shader's feaFieldMaxDamage includes fea.peak.damage; the HUD
    // must use the same field maximum so the legend matches the contour.
    const sparse: FeaResult = {
      ...fea,
      peak: {
        object_id: 'shell',
        vertex_index: 0,
        location_model_m: [0, 0, 0],
        damage: 0.85,
        stress_pa: 4e7,
        stress_mpa: 40,
      },
      objects: [
        {
          object_id: 'shell',
          vertex_count: 2,
          damage: [0.1, 0.2],
          displacement: [],
          stress_pa: [],
        },
      ],
      procedural: [],
    };
    expect(computeMaxDamage(sparse)).toBeCloseTo(0.85, 6);
  });
});

describe('countZoneVertices', () => {
  it('counts dent (>=) and tear (>) vertices like the backend thresholds', () => {
    expect(countZoneVertices(fea, fea.dent_threshold, false)).toBe(2);
    expect(countZoneVertices(fea, fea.tear_threshold, true)).toBe(1);
    expect(countZoneVertices({ ...fea, objects: [] }, fea.dent_threshold, false)).toBe(0);
  });
});

describe('FeaHud', () => {
  it('renders the professional readout with legend and true values', () => {
    render(<FeaHud mode="fea" fea={fea} />);

    expect(screen.getByText('FEA STRESS ANALYSIS')).toBeInTheDocument();
    expect(screen.getByText('HEATMAP')).toBeInTheDocument();
    expect(screen.getByText('Peak stress')).toBeInTheDocument();
    expect(screen.getAllByText('5.3 kPa').length).toBeGreaterThan(0);
    expect(screen.getByText('Yield stress')).toBeInTheDocument();
    expect(screen.getByText('20.0 MPa')).toBeInTheDocument();
    expect(screen.getByText('0.0003')).toBeInTheDocument();
    expect(screen.getByText('3755.54')).toBeInTheDocument();
    // Known flags are translated to human-readable disclosure lines...
    expect(
      screen.getByText(
        'Structural solve is inconclusive — the contour is an approximation',
      ),
    ).toBeInTheDocument();
    // ...unknown flags stay raw.
    expect(screen.getByText('FEA_TEST_FLAG')).toBeInTheDocument();
    // Zone counts come from the per-vertex fields (0.8 >= 0.7, 0.95 > 0.92).
    expect(screen.getByText('Plastic zone vertices')).toBeInTheDocument();
    expect(screen.getByText('Tear zone vertices')).toBeInTheDocument();
    expect(document.querySelector('.fea-hud__legend-bar')).not.toBeNull();
  });

  it('labels the yield shader mode', () => {
    render(<FeaHud mode="yield" fea={fea} />);
    expect(screen.getByText('YIELD SHADER')).toBeInTheDocument();
  });

  it('renders the live impact telemetry rows when drop data is provided', () => {
    const live: LiveDropData = {
      activeDropIndex: 0,
      totalDrops: 1,
      dropTime: 0.42,
      activeDrop: {
        index: 0,
        start_s: 0,
        end_s: 0.6,
        settled_s: 0.5,
        impact_count: 1,
        peak_impact_speed_m_s: 3,
        peak_kinetic_energy_j: 0.5,
        orientation: 'flat',
      },
      liveFrame: null,
      speedMps: 0,
      kineticEnergyJ: 0,
      isPlaying: true,
      status: 'impact',
    };
    render(<FeaHud mode="fea" fea={fea} liveDropData={live} />);

    expect(screen.getByText('Drop time')).toBeInTheDocument();
    expect(screen.getByText('0.42s')).toBeInTheDocument();
    // Impact window progress: (0.42 - 0.6*0.38) / 0.05 = 3.84 -> clamped 100%.
    expect(screen.getByText('Impact window')).toBeInTheDocument();
    expect(screen.getByText('100%')).toBeInTheDocument();
    expect(screen.getByText('Impact pulse')).toBeInTheDocument();
  });

  it('hides the live telemetry rows without drop data', () => {
    render(<FeaHud mode="fea" fea={fea} />);
    expect(screen.queryByText('Drop time')).not.toBeInTheDocument();
    expect(screen.queryByText('Impact pulse')).not.toBeInTheDocument();
  });

  it('renders nothing in default mode or without computed fea', () => {
    const { container } = render(<FeaHud mode="default" fea={fea} />);
    expect(container).toBeEmptyDOMElement();

    const { container: container2 } = render(<FeaHud mode="fea" fea={null} />);
    expect(container2).toBeEmptyDOMElement();
  });
});
