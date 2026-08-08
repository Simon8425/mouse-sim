import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { useEffect } from 'react';
import { ResultsRail } from '../components/ResultsRail';
import { ProjectProvider, useProjectStore, type ProjectAction } from '../state/projectStore';
import type { ProjectState } from '../state/projectStore';
import type { PipelineResult } from '../api/contracts';

function DispatchHelper({ action }: { action: ProjectAction }) {
  const { dispatch } = useProjectStore();
  useEffect(() => {
    dispatch(action);
  }, [dispatch, action]);
  return null;
}

function StateProbe({ onState }: { onState: (state: ProjectState) => void }) {
  const { state } = useProjectStore();
  useEffect(() => {
    onState(state);
  });
  return null;
}

function renderRail(
  result: PipelineResult | null,
  probe?: (state: ProjectState) => void,
  extraActions: ProjectAction[] = [],
) {
  const actions: ProjectAction[] = [];
  if (result !== null) {
    actions.push({ type: 'ANALYZE_START', version: 1 });
    actions.push({ type: 'ANALYZE_OK', version: 1, result });
  }
  actions.push(...extraActions);
  return render(
    <ProjectProvider>
      {actions.map((action, index) => (
        <DispatchHelper key={index} action={action} />
      ))}
      {probe ? <StateProbe onState={probe} /> : null}
      <ResultsRail open onToggleOpen={vi.fn()} />
    </ProjectProvider>,
  );
}

function baseResult(): PipelineResult {
  return {
    schema_id: 'gms.pipeline-result/1',
    engine_version: '1.0.0',
    run_id: 'run-pop-1',
    mode: 'exploration',
    lifecycle_state: 'completed',
    validity: {
      state: 'valid',
      reasons: [],
      assumptions: [],
      unsupported_failure_modes: [],
      confidence: 'high',
    },
    issues: [],
    geometry_summary: { objects: [], parse_errors: [] },
    mass: null,
    validation: null,
    structural: null,
    impact: null,
    drop_simulation: null,
    qualification: null,
    components: null,
    component_screening: null,
    shell: null,
    population: null,
    manifest: null,
    errors: [],
  };
}

function populationResult(): PipelineResult {
  return {
    ...baseResult(),
    components: {
      components: [
        {
          component_id: 'pcb-main',
          type: 'pcb',
          status: 'fail',
          validity: 'valid',
          metrics: {
            max_deflection_m: 0.0008,
            flex_stress_pa: 42000000,
            thermal_damage: 0.42,
          },
          findings: [
            { code: 'PCB_FLEX', severity: 'warning', message: 'Flex stress exceeds the derated limit' },
          ],
          assumptions: [],
          flags: [],
        },
        {
          component_id: 'batt-01',
          type: 'battery',
          status: 'warn',
          validity: 'valid',
          metrics: { transmitted_force_n: 85, shock_g: 460, crush_margin: 1.2 },
          findings: [],
          assumptions: [],
          flags: [],
        },
      ],
      summary: {
        fail_count: 1,
        warn_count: 1,
        weakest: { component_id: 'pcb-main', type: 'pcb', status: 'fail' },
      },
    },
    population: {
      sample_count: 10000,
      profile: 'esports_fps',
      lifespan_days: 730,
      units_failed: 1234,
      failure_rate: 0.1234,
      wilson_ci: { low: 0.117, high: 0.13 },
      component_failure_rates: [
        {
          component_id: 'pcb-main',
          type: 'pcb',
          failures: 800,
          rate: 0.08,
          wilson_ci: { low: 0.075, high: 0.085 },
          rank: 1,
        },
        {
          component_id: 'batt-01',
          type: 'battery',
          failures: 434,
          rate: 0.0434,
          wilson_ci: { low: 0.04, high: 0.047 },
          rank: 2,
        },
      ],
      weakest_components: [
        { component_id: 'batt-01', type: 'battery', rate: 0.067, rank: 1 },
      ],
      sensitivity: [
        { parameter: 'usage_damage_scale', correlation: 0.31, mean_value: 1, std_value: 0.1 },
        { parameter: 'tolerance_scale', correlation: -0.12, mean_value: 1, std_value: 0.05 },
        { parameter: 'drop_height', correlation: 0.08, mean_value: 0.75, std_value: 0.02 },
        { parameter: 'material_grade', correlation: 0.04, mean_value: 1, std_value: 0.01 },
        { parameter: 'surface_friction', correlation: 0.02, mean_value: 0.3, std_value: 0.05 },
        { parameter: 'ignored_extra', correlation: 0.01, mean_value: 0, std_value: 0 },
      ],
      survival: [
        { usage_fraction: 0, survival_rate: 1 },
        { usage_fraction: 0.5, survival_rate: 0.94 },
        { usage_fraction: 1, survival_rate: 0.88 },
      ],
    },
  };
}

function shellResult(): PipelineResult {
  return {
    ...baseResult(),
    shell: {
      status: 'warn',
      classification: 'marginal',
      peak_stress_pa: 32000000,
      max_displacement_m: 0.00045,
      min_safety_factor: 1.12,
      critical_region: [12.4, 3.1, 5.2],
      failure_mode: 'buckling',
      physical_model_confidence: 'medium',
      statistical_confidence: { kind: 'single_run' },
      assumptions: ['1 mm shell mesh', 'fixed fixtures at screw bosses'],
      limitations: ['no fatigue', 'linear elastic material'],
      loading: { drop_peak_speed_m_s: 3.83, drop_peak_energy_j: 0.51, drop_peak_force_n: 340 },
    },
  };
}

function safeShellResult(): PipelineResult {
  return {
    ...baseResult(),
    shell: {
      status: 'pass',
      classification: 'safe',
      peak_stress_pa: 12000000,
      max_displacement_m: 0.0002,
      min_safety_factor: 2.4,
      physical_model_confidence: 'high',
      statistical_confidence: { kind: 'single_run' },
    },
  };
}

function unstableShellResult(): PipelineResult {
  return {
    ...baseResult(),
    shell: {
      status: 'warn',
      classification: 'marginal',
      physical_model_confidence: 'low',
      statistical_confidence: { kind: 'single_run' },
      critical_region_stability: {
        stable: false,
        probe_solves: 6,
        max_location_shift_m: 0.0009,
        tolerance_m: 0.0002,
        statement: 'Critical region shifts across probes — treat location as approximate.',
      },
    },
  };
}

function worstCaseResult(): PipelineResult {
  return {
    ...baseResult(),
    population: {
      mode: 'deterministic_worst_case',
      verdict: 'fail',
      shell: {
        safety_factor: 0.84,
        peak_stress_pa: 61000000,
        max_displacement_m: 0.0011,
        verdict: 'fail',
      },
      drop: {
        drop_height_m: 1.5,
        surface: 'concrete',
        orientation: 'corner',
        peak_impact_speed_m_s: 5.42,
        impact_energy_j: 1.02,
        peak_acceleration_g: 620,
      },
      components: [
        { component_id: 'pcb-main', type: 'pcb', status: 'fail', usage_ratio: 1.35 },
        { component_id: 'batt-01', type: 'battery', status: 'warn', usage_ratio: 0.62 },
      ],
      assumptions: ['worst-case corner drop at max spec height', 'peak stress at nominal wall thickness'],
    },
  };
}

function screeningResult(): PipelineResult {
  return {
    ...baseResult(),
    component_screening: {
      confidence: 'low-medium',
      note: 'surrogate models, order-of-magnitude accuracy',
      components: [
        {
          component_id: 'batt-01',
          type: 'battery',
          status: 'warn',
          validity: 'approximate',
          metrics: { transmitted_force_n: 85, shock_g: 460, crush_margin: 1.2 },
          findings: [],
          assumptions: [],
          flags: [],
          usage_ratio: 0.62,
        },
      ],
      summary: { fail_count: 0, warn_count: 1, weakest: null },
    },
  };
}

function shellPopulationResult(): PipelineResult {
  const base = populationResult();
  return {
    ...base,
    population: {
      ...base.population,
      shell: {
        nominal: {
          safety_factor: 1.5,
          peak_stress_pa: 21000000,
          max_displacement_m: 0.0004,
          wall_thickness_m: 0.0016,
        },
        failures: 137,
        failure_rate: 0.0137,
        wilson_ci: { low: 0.0116, high: 0.0161 },
        sensitivity: [
          { parameter: 'wall_thickness', correlation: -0.62, mean_value: 1.6, std_value: 0.05, level: 'HIGH' },
          { parameter: 'drop_height', correlation: 0.41, mean_value: 0.75, std_value: 0.02, level: 'MEDIUM' },
          { parameter: 'material_grade', correlation: 0.22, mean_value: 1, std_value: 0.01, level: 'LOW' },
          { parameter: 'com_offset', correlation: 0.05, mean_value: 0, std_value: 0.1, level: 'NOT_OBSERVED' },
          { parameter: 'friction', correlation: 0.03, mean_value: 0.3, std_value: 0.05, level: 'LOW' },
          { parameter: 'ignored_param', correlation: 0.01, mean_value: 0, std_value: 0, level: 'NOT_OBSERVED' },
        ],
        assumptions: ['wall thickness sampled N(1.6, 0.05) mm'],
      },
    },
  };
}

function tinyRateResult(): PipelineResult {
  return {
    ...baseResult(),
    population: {
      sample_count: 10000,
      profile: 'esports_fps',
      lifespan_days: 730,
      units_failed: 1,
      failure_rate: 0.0001,
      wilson_ci: { low: 0.0001, high: 0.0002 },
      component_failure_rates: [
        {
          component_id: 'batt-01',
          type: 'battery',
          failures: 1,
          rate: 0.0001,
          wilson_ci: { low: 0.0001, high: 0.0002 },
          rank: 1,
        },
      ],
    },
  };
}

describe('ResultsRail population and component views', () => {
  it('renders the Components tab summary and a component row with a status badge', async () => {
    const user = userEvent.setup();
    renderRail(populationResult());

    await user.click(screen.getByRole('tab', { name: 'Components' }));

    expect(
      screen.getByText(/2 component\(s\), 1 failed, 1 warnings — weakest: pcb-main \(fail\)/),
    ).toBeInTheDocument();
    expect(screen.getByText('pcb-main')).toBeInTheDocument();
    expect(screen.getByText('fail')).toBeInTheDocument();
    expect(screen.getByText('warn')).toBeInTheDocument();
    expect(screen.getByText('0.8 mm')).toBeInTheDocument();
    expect(screen.getByText('42 MPa')).toBeInTheDocument();
    expect(screen.getByText('42.0%')).toBeInTheDocument();
    const finding = screen.getByText('PCB_FLEX');
    expect(finding).toHaveAttribute('title', 'Flex stress exceeds the derated limit');
  });

  it('renders the Population tab header, failure-rate ranks, weakest list, survival, and sensitivity', async () => {
    const user = userEvent.setup();
    renderRail(populationResult());

    await user.click(screen.getByRole('tab', { name: 'Population' }));

    expect(
      screen.getByText(/10,000 virtual units · esports_fps · 730 days — 1,234 failed \(12\.3%, 95% Wilson CI 11\.7%–13\.0%\)/),
    ).toBeInTheDocument();
    expect(screen.getByText('#1')).toBeInTheDocument();
    expect(screen.getByText('#2')).toBeInTheDocument();
    expect(screen.getAllByText('8.0%').length).toBeGreaterThan(0);
    expect(screen.getByText('4.3%')).toBeInTheDocument();
    expect(screen.getByText(/weakest components/i)).toBeInTheDocument();
    expect(screen.getByText(/6\.7% · rank 1/)).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Survival curve across usage' })).toBeInTheDocument();
    expect(screen.getByText('usage_damage_scale')).toBeInTheDocument();
    expect(screen.getByText('+0.31')).toBeInTheDocument();
    expect(screen.queryByText('ignored_extra')).not.toBeInTheDocument();
  });

  it('shows empty states when components and population are absent', async () => {
    const user = userEvent.setup();
    renderRail(baseResult());

    await user.click(screen.getByRole('tab', { name: 'Components' }));
    expect(screen.getByText('No component analysis was requested.')).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: 'Population' }));
    expect(
      screen.getByText(
        'No population analysis was requested — run the worst-case analysis from Settings.',
      ),
    ).toBeInTheDocument();
  });

  it('falls back to the overview tab for unknown persisted tab values', () => {
    renderRail(populationResult(), undefined, [{ type: 'SET_TAB', tab: 'bogus' }]);
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Components' })).not.toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('renders the Shell Validation section first on the Overview tab when a shell result exists', async () => {
    const user = userEvent.setup();
    renderRail(shellResult());

    expect(screen.getByRole('heading', { name: 'Shell Validation' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Summary' })).toBeInTheDocument();
    expect(screen.getByText('warn')).toBeInTheDocument();
    expect(screen.getByText('32 MPa')).toBeInTheDocument();
    expect(screen.getByText('0.45 mm')).toBeInTheDocument();
    expect(screen.getByText('1.12')).toBeInTheDocument();
    expect(screen.getByText('(12.4, 3.1, 5.2)')).toBeInTheDocument();
    expect(screen.getByText('buckling')).toBeInTheDocument();
    expect(screen.getByText('Medium')).toBeInTheDocument();
    expect(screen.getByText('Single deterministic run')).toBeInTheDocument();
    expect(screen.getByText('Marginal')).toBeInTheDocument();
    expect(screen.getByText('3.83 m/s')).toBeInTheDocument();
    expect(screen.getByText('510 mJ')).toBeInTheDocument();
    expect(screen.getByText('340 N')).toBeInTheDocument();

    await user.click(screen.getByText('Screening assumptions (4)'));
    expect(screen.getByText(/1 mm shell mesh/)).toBeInTheDocument();
    expect(screen.getByText(/no fatigue/)).toBeInTheDocument();
  });

  it('renders the classification badge with the safe→ok tone and both confidence rows', async () => {
    renderRail(safeShellResult());

    expect(screen.getByRole('heading', { name: 'Shell Validation' })).toBeInTheDocument();
    const badge = screen.getByText('Safe');
    expect(badge).toHaveClass('badge--ok');
    expect(screen.getByText('Physical-model confidence')).toBeInTheDocument();
    expect(screen.getByText('Statistical confidence')).toBeInTheDocument();
    const physicalRow = screen.getByText('Physical-model confidence').closest('tr');
    expect(physicalRow).not.toBeNull();
    expect(within(physicalRow as HTMLElement).getByText('High')).toBeInTheDocument();
    expect(screen.getByText('Single deterministic run')).toBeInTheDocument();
  });

  it('renders the critical-region stability statement as a warning line when unstable', async () => {
    renderRail(unstableShellResult());

    expect(
      screen.getByText('Critical region shifts across probes — treat location as approximate.'),
    ).toHaveClass('results-rail__stability-warning');
  });

  it('shows the screening banner, validity label, usage chip, and crush_margin metric on the Components tab', async () => {
    const user = userEvent.setup();
    renderRail(screeningResult());

    await user.click(screen.getByRole('tab', { name: 'Components' }));

    expect(
      screen.getByText(/SECONDARY COMPONENT SCREENING — surrogate models, order-of-magnitude accuracy \(low-medium confidence\)/),
    ).toBeInTheDocument();
    expect(screen.getByText('approximate')).toBeInTheDocument();
    expect(screen.getByText('62.0%')).toBeInTheDocument();
    expect(screen.getByText(/crush_margin:/)).toBeInTheDocument();
    expect(screen.getByText('1.2')).toBeInTheDocument();
  });

  it('renders the population shell-robustness line and its sensitivity list before the population summary', async () => {
    const user = userEvent.setup();
    renderRail(shellPopulationResult());

    await user.click(screen.getByRole('tab', { name: 'Population' }));

    expect(
      screen.getByText(/137 \/ 10,000 units below safety factor 1 \(1\.4%, 95% Wilson CI 1\.2%–1\.6%\)/),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Sensitivity (correlation with shell failure — correlation, not causation)'),
    ).toBeInTheDocument();
    expect(screen.getByText('wall_thickness')).toBeInTheDocument();
    expect(screen.queryByText('ignored_param')).not.toBeInTheDocument();
    expect(screen.getByText('Secondary component screening')).toBeInTheDocument();
  });

  it('renders sensitivity level chips next to the shell correlations', async () => {
    const user = userEvent.setup();
    renderRail(shellPopulationResult());

    await user.click(screen.getByRole('tab', { name: 'Population' }));

    const highChip = screen.getByText('HIGH');
    expect(highChip).toHaveClass('results-rail__sensitivity-level--error');
    expect(screen.getByText('MEDIUM')).toHaveClass('results-rail__sensitivity-level--warn');
    expect(screen.getByText('NOT_OBSERVED')).toBeInTheDocument();
  });

  it('renders the deterministic worst-case block with verdict and no Monte Carlo CI', async () => {
    const user = userEvent.setup();
    renderRail(worstCaseResult());

    await user.click(screen.getByRole('tab', { name: 'Population' }));

    expect(screen.getByText(/DETERMINISTIC WORST CASE — worst-case corner, not a Monte Carlo tail/)).toBeInTheDocument();
    const worstCaseSection = screen
      .getByRole('heading', { name: 'Deterministic Worst Case' })
      .closest('section');
    expect(worstCaseSection).not.toBeNull();
    expect(within(worstCaseSection as HTMLElement).getByText('fail')).toBeInTheDocument();
    expect(screen.getByText('0.84')).toBeInTheDocument();
    expect(screen.getByText('61 MPa')).toBeInTheDocument();
    expect(screen.getByText('1.1 mm')).toBeInTheDocument();
    expect(screen.getByText('1.5 m')).toBeInTheDocument();
    expect(screen.getByText('corner')).toBeInTheDocument();
    expect(screen.getByText('620 g')).toBeInTheDocument();
    expect(screen.getByText('pcb-main')).toBeInTheDocument();
    expect(screen.getByText('135.0%')).toBeInTheDocument();
    expect(screen.getByText('62.0%')).toBeInTheDocument();
    expect(screen.queryByText(/95% Wilson CI/)).not.toBeInTheDocument();
    expect(screen.queryByText(/virtual units/)).not.toBeInTheDocument();

    await user.click(screen.getByText('Worst-case assumptions (2)'));
    expect(screen.getByText(/worst-case corner drop at max spec height/)).toBeInTheDocument();
  });

  it('formats small rates with two significant digits (1/10000 → 0.01%)', async () => {
    const user = userEvent.setup();
    renderRail(tinyRateResult());

    await user.click(screen.getByRole('tab', { name: 'Population' }));

    expect(
      screen.getByText(/1 failed \(0\.01%, 95% Wilson CI 0\.01%–0\.02%\)/),
    ).toBeInTheDocument();
    expect(screen.getByText('0.01%')).toBeInTheDocument();
  });

  it('adds the screening note to the Population empty state', async () => {
    const user = userEvent.setup();
    renderRail(baseResult());

    await user.click(screen.getByRole('tab', { name: 'Population' }));

    expect(
      screen.getByText(/SECONDARY COMPONENT SCREENING — simplified models, low-medium confidence; component verdicts do not affect the shell result/),
    ).toBeInTheDocument();
    expect(
      screen.getByText('No population analysis was requested — run the worst-case analysis from Settings.'),
    ).toBeInTheDocument();
  });
});
