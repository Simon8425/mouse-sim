import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useEffect } from 'react';
import { ResultsRail } from '../components/ResultsRail';
import { ProjectProvider, useProjectStore, type ProjectAction } from '../state/projectStore';
import type { PipelineResult } from '../api/contracts';

function DispatchHelper({ action }: { action: ProjectAction }) {
  const { dispatch } = useProjectStore();
  useEffect(() => {
    dispatch(action);
  }, [dispatch, action]);
  return null;
}

function renderRail(result: PipelineResult | null, extraActions: ProjectAction[] = []) {
  const actions: ProjectAction[] = [];
  if (result !== null) {
    actions.push({ type: 'ANALYZE_START', version: 1, requestKey: 'k1' });
    actions.push({ type: 'ANALYZE_OK', version: 1, requestKey: 'k1', result });
  }
  actions.push(...extraActions);
  return render(
    <ProjectProvider>
      {actions.map((action, index) => (
        <DispatchHelper key={index} action={action} />
      ))}
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

function warnShellResult(): PipelineResult {
  return {
    ...baseResult(),
    shell: {
      status: 'warn',
      classification: 'marginal',
      peak_stress_pa: 32000000,
      max_displacement_m: 0.00045,
      min_safety_factor: 1.12,
      physical_model_confidence: 'medium',
      statistical_confidence: { kind: 'single_run' },
      loading: { drop_peak_speed_m_s: 3.83, drop_peak_energy_j: 0.51, drop_peak_force_n: 340 },
    },
  };
}

function failShellResult(): PipelineResult {
  return {
    ...baseResult(),
    shell: {
      status: 'fail',
      classification: 'failed',
      peak_stress_pa: 61000000,
      max_displacement_m: 0.0011,
      min_safety_factor: 0.84,
      physical_model_confidence: 'low',
      statistical_confidence: { kind: 'single_run' },
    },
  };
}

function dropTestResult(): PipelineResult {
  return {
    ...baseResult(),
    mass: {
      mass_kg: 0.0696,
      mass_status: 'derived',
      center_of_mass_m: [0, 0, 0.0005],
      inertia_tensor_kg_m2: null,
      uncertainty_kg: 0,
      completeness: 1,
      objects: [],
      diagnostics: [],
      source_status: 'calculated',
      derived_status: 'calculated',
      review_status: 'unreviewed',
    },
    impact: {
      mass_kg: 0.0696,
      result: {
        impact_energy_j: 0.5,
        closing_velocity_m_s: 3.8,
        effective_mass_kg: 0.0696,
        impulse_n_s: 0.27,
        peak_force_n: 320,
        peak_acceleration_m_s2: 4571,
        contact_duration_s: 0.0017,
        contact_compression_m: 0.0003,
        method_id: 'energy_quasi_static_v1',
        flags: [],
        assumptions: [],
        unsupported_failure_modes: [],
        validity: 'valid',
        load_path_stress_pa: null,
        safety_factor: 1.9,
        qualification_blocked: false,
      },
      reason: null,
      unsupported_failure_modes: [],
    },
    drop_simulation: {
      config: {
        test: 'drop',
        height_m: 0.75,
        surface: 'concrete',
        drop_count: 3,
        orientation: 'flat',
      },
      model: {
        mass_kg: 0.0696,
        inertia_kg_m2: [[1e-4, 0, 0], [0, 1e-4, 0], [0, 0, 1e-4]],
        support_model: 'mesh_extreme_points',
        support_point_count: 14,
        integrator: 'semi_implicit_euler',
        timestep_s: 1 / 240,
        gravity_m_s2: 9.81,
        surface: 'concrete',
      },
      drops: [],
      impacts: [],
      peak: null,
      peak_force_estimate_n: 313,
      trajectory: [],
    },
    structural: {
      load_case: {},
      structure: {},
      material: 'ABS',
      fixtures: null,
      preflight: [],
      response: {
        method_id: 'shell_navier_v1',
        max_displacement_m: 0.00012,
        max_displacement_location: null,
        max_stress_pa: null,
        max_stress_filtered_pa: null,
        filtered_location: null,
        safety_factor: null,
        safety_factor_status: 'not_available',
        reactions: {},
        force_residual_n: null,
        moment_residual_n_m: null,
        flags: [],
        assumptions: [],
        unsupported_failure_modes: [],
        validity: 'valid',
      },
    },
  };
}

function issueResult(): PipelineResult {
  return {
    ...safeShellResult(),
    validation: {
      status: 'completed',
      validity_state: 'valid',
      findings: [
        {
          code: 'MATERIAL_FALLBACK',
          severity: 'warning',
          state: 'active',
          category: 'material',
          message: 'Default material fallback applied to 3 components.',
          affected_ids: ['a', 'b', 'c'],
          phase: 'material',
          evidence_blocking: false,
        },
        {
          code: 'INFO_NOTE',
          severity: 'info',
          state: 'active',
          category: 'geometry',
          message: 'Units normalized to mm.',
          affected_ids: [],
          phase: 'geometry',
          evidence_blocking: false,
        },
      ],
    },
    issues: [
      {
        code: 'THICKNESS',
        severity: 'warning',
        category: 'validation',
        message: 'Wall thickness below recommended minimum.',
        evidence_blocking: false,
      },
    ],
  };
}

describe('ResultsRail', () => {
  it('shows the empty state before the first run', () => {
    renderRail(null);

    expect(screen.getByText('No results yet')).toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
  });

  it('renders a PASS verdict and the key metrics for a safe shell result', () => {
    renderRail(safeShellResult());

    expect(screen.getByText('PASS')).toBeInTheDocument();
    expect(screen.getByText('All checks passed')).toBeInTheDocument();
    expect(screen.getByText('12 MPa')).toBeInTheDocument();
    expect(screen.getByText('0.2 mm')).toBeInTheDocument();
    expect(screen.getByText('2.4')).toBeInTheDocument();
    expect(screen.getByText('Max stress')).toBeInTheDocument();
    expect(screen.getByText('Safety factor')).toBeInTheDocument();
    expect(screen.getByText('Max deformation')).toBeInTheDocument();
  });

  it('renders a FAIL verdict when the shell fails', () => {
    renderRail(failShellResult());

    expect(screen.getByText('FAIL')).toBeInTheDocument();
    expect(screen.getByText('Failing checks need attention')).toBeInTheDocument();
    expect(screen.getByText('61 MPa')).toBeInTheDocument();
    expect(screen.getByText('0.84')).toBeInTheDocument();
  });

  it('renders a WARN verdict when the shell warns', () => {
    renderRail(warnShellResult());

    expect(screen.getByText('WARN')).toBeInTheDocument();
    expect(screen.getByText('Passed with warnings')).toBeInTheDocument();
    expect(screen.getByText('340 N')).toBeInTheDocument();
  });

  it('renders a FAIL verdict when the run reports errors', () => {
    renderRail({ ...baseResult(), errors: [{ code: 'EXPLOSION', message: 'kaboom' }] });

    expect(screen.getByText('FAIL')).toBeInTheDocument();
    expect(screen.getByText(/EXPLOSION: kaboom/)).toBeInTheDocument();
  });

  it('renders the drop-test configuration line and material', () => {
    renderRail(dropTestResult());

    expect(screen.getByText('Drop Test · 0.75 m · Concrete · Flat · 3 drops')).toBeInTheDocument();
    expect(screen.getByText('Material:')).toBeInTheDocument();
    expect(screen.getByText('ABS')).toBeInTheDocument();
  });

  it('renders impact metrics from the impact estimate', () => {
    renderRail(dropTestResult());

    expect(screen.getByText('69.6 g')).toBeInTheDocument();
    expect(screen.getByText('320 N')).toBeInTheDocument();
    // Peak acceleration is quoted in g (4571 m/s² ≈ 466 g).
    expect(screen.getByText('466 g')).toBeInTheDocument();
    expect(screen.getByText('1.9')).toBeInTheDocument();
  });

  it('lists only warnings and worse from findings, issues, and errors', () => {
    renderRail(issueResult());

    expect(
      screen.getByText('Default material fallback applied to 3 components.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Wall thickness below recommended minimum.')).toBeInTheDocument();
    expect(screen.queryByText('Units normalized to mm.')).not.toBeInTheDocument();
    expect(screen.queryByText('Info')).not.toBeInTheDocument();
  });

  it('shows no issues section when there is nothing actionable', () => {
    renderRail(safeShellResult());

    expect(screen.queryByRole('heading', { name: 'Issues' })).not.toBeInTheDocument();
  });

  it('shows the stale note when the inputs changed after the result', () => {
    renderRail(baseResult(), [{ type: 'UPDATE_DRAFT', patch: { units: 'mm' } }]);
    expect(screen.getByText('Inputs changed — rerun to refresh these results.')).toBeInTheDocument();
  });

  it('does not show the stale note for a fresh result', () => {
    renderRail(baseResult());
    expect(screen.queryByText(/Inputs changed/)).not.toBeInTheDocument();
  });

  it('renders the header with the test name when a drop simulation ran', () => {
    renderRail(dropTestResult());

    expect(screen.getByText('Results of Drop Test')).toBeInTheDocument();
  });

  it('renders the collapsed strip and the expand/collapse toggle', () => {
    render(
      <ProjectProvider>
        <ResultsRail open={false} onToggleOpen={vi.fn()} />
      </ProjectProvider>,
    );

    expect(screen.getByRole('button', { name: 'Show results rail' })).toBeInTheDocument();
    const rail = screen.getByRole('button', { name: 'Show results rail' }).closest('aside');
    expect(rail?.className).toContain('results-rail--collapsed');
  });
});
