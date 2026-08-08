import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { useEffect } from 'react';
import { MissionControl } from '../components/MissionControl';
import { ProjectProvider, useProjectStore, type ProjectAction } from '../state/projectStore';
import type { ProjectState } from '../state/projectStore';
import type {
  WebHealth,
  PipelineResult,
  PipelineRequest,
  StructuralResponse,
  ImpactEstimate,
} from '../api/contracts';
import { IDENTITY_TRANSFORM } from '../api/contracts';

vi.mock('../api/client', () => ({
  createClient: () => ({
    getHealth: vi.fn(),
    getMaterials: vi.fn(),
    normalizeGeometry: vi.fn(),
    analyze: vi.fn(),
  }),
}));

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

function renderPanel(
  actions: ProjectAction[] = [],
  onClose = vi.fn(),
  probe?: (state: ProjectState) => void,
) {
  const utils = render(
    <ProjectProvider>
      {actions.map((action, index) => (
        <DispatchHelper key={index} action={action} />
      ))}
      {probe ? <StateProbe onState={probe} /> : null}
      <MissionControl onClose={onClose} />
    </ProjectProvider>,
  );
  return { ...utils, onClose };
}

const mockHealth: WebHealth = {
  schema_id: 'gms.web-health/1',
  engine_version: '0.1.0',
  api_version: '2.0.0',
  supported_formats: ['json', 'obj', 'stl', 'step'],
  solver_capabilities: ['shell_navier_v1', 'energy_quasi_static_v1'],
  cache_active: true,
  max_json_bytes: 1000000,
  max_geometry_bytes: 5000000,
  deterministic: true,
};

const mockBaselineProject: PipelineRequest = {
  schema_id: 'gms.project/1',
  mode: 'exploration',
  units: 'mm',
  objects: [
    {
      id: 'shell_top',
      geometry: { type: 'box', size: [110, 65, 2.5], units: 'mm', transform: IDENTITY_TRANSFORM },
      material: 'ABS',
    },
  ],
};

const mockResult: PipelineResult = {
  schema_id: 'gms.pipeline-result/1',
  engine_version: '0.1.0',
  run_id: 'run-1',
  mode: 'exploration',
  lifecycle_state: 'completed',
  validity: {
    state: 'inconclusive',
    reasons: [],
    assumptions: [],
    unsupported_failure_modes: ['fatigue', 'creep'],
    confidence: 'low',
  },
  issues: [],
  geometry_summary: { objects: [], parse_errors: [] },
  mass: {
    mass_kg: 0.0696,
    mass_status: 'mixed',
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
  validation: null,
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
      solver_metadata: { model_id: 'screening_surrogate_v1' },
    } as unknown as StructuralResponse,
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
      assumptions: [],      unsupported_failure_modes: ['fatigue'],
      validity: 'valid',
      load_path_stress_pa: null,
      safety_factor: 'not_available',
      qualification_blocked: true,
      solver_metadata: { model_id: 'screening_surrogate_v1' },
    } as unknown as ImpactEstimate,
    reason: null,
    unsupported_failure_modes: ['fatigue', 'creep'],
  },
  drop_simulation: null,
  qualification: {
    mode: 'exploration',
    qualified: false,
    evidence_disposition: 'exploration_only',
    gates: [
      { key: 'CONVERGENCE', label: 'Solver convergence evidence', passed: false, evaluable: false, blocker: true, explanation: 'no analysis method provided' },
      { key: 'CORRELATION', label: 'Required correlation records', passed: false, evaluable: false, blocker: true, explanation: 'none' },
      { key: 'FIXTURES_REVIEWED', label: 'All fixtures reviewed', passed: false, evaluable: false, blocker: true, explanation: 'none' },
    ],
    blocking_keys: ['CONVERGENCE'],
    summary: 'screening only',
  },
  manifest: null,
  errors: [],
};

describe('Settings modal', () => {
  it('renders engine health and the empty state', () => {
    renderPanel([{ type: 'HEALTH_OK', health: mockHealth }]);

    expect(screen.getByRole('dialog', { name: 'Settings' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument();
    expect(screen.getByText('Engine health')).toBeInTheDocument();
    expect(screen.getByText('json')).toBeInTheDocument();
    expect(screen.getByText('shell_navier_v1')).toBeInTheDocument();
    expect(screen.getByText('on')).toBeInTheDocument();

    expect(screen.getAllByText('Idle').length).toBeGreaterThan(0);
  });

  it('closes on Escape and on the close button', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderPanel([], onClose);

    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: 'Close settings panel' }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('shows fidelity state, not-simulated chips, surrogate badge, and metrics from a result', () => {
    renderPanel([
      { type: 'HEALTH_OK', health: mockHealth },
      { type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' },
      { type: 'ANALYZE_START', version: 1 },
      { type: 'ANALYZE_OK', version: 1, result: mockResult },
    ]);

    expect(screen.getByText('Inconclusive')).toBeInTheDocument();
    expect(screen.getByText(/confidence/)).toBeInTheDocument();
    expect(screen.getByText('fatigue')).toBeInTheDocument();
    expect(screen.getByText('creep')).toBeInTheDocument();
    expect(screen.getByText('Exploration only')).toBeInTheDocument();
    expect(screen.getByText(/screening_surrogate_v1/)).toBeInTheDocument();
    expect(screen.getByText('69.6 g')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText(/gates evaluated/)).toBeInTheDocument();
  });

  it('renders the Model Quality control with four options', () => {
    renderPanel();

    const group = screen.getByRole('radiogroup', { name: 'Model quality' });
    expect(group).toBeInTheDocument();
    expect(screen.getByText('Model Quality')).toBeInTheDocument();

    const options = screen.getAllByRole('radio');
    expect(options).toHaveLength(4);
    expect(screen.getByRole('radio', { name: 'LOW' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'MEDIUM' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'HIGH' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'ULTRA' })).toBeInTheDocument();

    expect(
      screen.getByText(/rendering only/i),
    ).toBeInTheDocument();
  });

  it('defaults the active quality to the detected tier when qualityTier is null', () => {
    renderPanel([], vi.fn(), (state) => {
      expect(state.qualityTier).toBeNull();
    });

    const checked = screen.getAllByRole('radio').filter((option) =>
      option.getAttribute('aria-checked') === 'true',
    );
    expect(checked).toHaveLength(1);
  });

  it('selecting ULTRA dispatches SET_QUALITY_TIER with ultra', async () => {
    const user = userEvent.setup();
    const qualityTiers: (string | null)[] = [];
    renderPanel([], vi.fn(), (state) => {
      qualityTiers.push(state.qualityTier);
    });

    await user.click(screen.getByRole('radio', { name: 'ULTRA' }));

    expect(screen.getByRole('radio', { name: 'ULTRA' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(qualityTiers[qualityTiers.length - 1]).toBe('ultra');
  });

  it('renders the Default Material select and dispatches SET_DEFAULT_MATERIAL on change', async () => {
    const user = userEvent.setup();
    const defaultKeys: (string | null)[] = [];
    renderPanel(
      [
        {
          type: 'MATERIALS_OK',
          materials: [
            {
              key: 'default',
              name: 'Default',
              family: null,
              density_kg_m3: null,
              young_modulus_pa: null,
              approval_state: 'default',
              confidence: 'default',
              source_type: 'default',
            },
            {
              key: 'ABS',
              name: 'ABS',
              family: null,
              density_kg_m3: null,
              young_modulus_pa: null,
              approval_state: 'approved',
              confidence: 'high',
              source_type: 'library',
            },
          ],
        },
      ],
      vi.fn(),
      (state) => {
        defaultKeys.push(state.defaultMaterialKey);
      },
    );

    const select = screen.getByLabelText('Default Material');
    expect(select).toBeInTheDocument();
    expect(screen.getByText(/without an explicit material/i)).toBeInTheDocument();

    await user.selectOptions(select, 'ABS');

    expect(select).toHaveValue('ABS');
    expect(defaultKeys[defaultKeys.length - 1]).toBe('ABS');
  });

  it('dispatches RUN_POPULATION from the worst-case button when geometry is loaded', async () => {
    const user = userEvent.setup();
    let capturedDraft: Record<string, unknown> | null | undefined;
    let runNonce = -1;
    renderPanel(
      [{ type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' }],
      vi.fn(),
      (state) => {
        capturedDraft = state.draft;
        runNonce = state.runNonce;
      },
    );

    expect(screen.getByText('Worst-case population analysis')).toBeInTheDocument();
    expect(
      screen.getByText(/Simulates 10,000 manufactured units/i),
    ).toBeInTheDocument();

    const button = screen.getByRole('button', {
      name: 'Run worst-case population analysis',
    });
    expect(button).toBeEnabled();
    await user.click(button);

    await waitFor(() => {
      expect(runNonce).toBe(1);
    });
    expect(capturedDraft?.population).toEqual({
      sample_count: 10000,
      profile: 'esports_fps',
      lifespan_days: 730,
    });
  });

  it('disables the worst-case population button without geometry', () => {
    renderPanel();
    const button = screen.getByRole('button', {
      name: 'Run worst-case population analysis',
    });
    expect(button).toBeDisabled();
  });

  it('disables the worst-case run button and shows the running label while analysis is running', () => {
    renderPanel([
      { type: 'LOAD_BASELINE_OK', project: mockBaselineProject, name: 'mouse_baseline' },
      { type: 'ANALYZE_START', version: 1 },
    ]);

    const button = screen.getByRole('button', {
      name: 'Run worst-case population analysis',
    });
    expect(button).toBeDisabled();
    expect(screen.getByText('RUNNING — may take a few minutes')).toBeInTheDocument();
  });

});
