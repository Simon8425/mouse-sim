import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useEffect } from 'react';
import { MissionControl } from '../components/MissionControl';
import { ProjectProvider, useProjectStore, type ProjectAction } from '../state/projectStore';
import type { ProjectState } from '../state/projectStore';
import type {
  WebHealth,
  PipelineResult,
  PipelineRequest,
  WebBaselineResponse,
  StructuralResponse,
  ImpactEstimate,
} from '../api/contracts';
import { IDENTITY_TRANSFORM } from '../api/contracts';

const { getBaselineMock } = vi.hoisted(() => ({ getBaselineMock: vi.fn() }));

vi.mock('../api/client', () => ({
  createClient: () => ({
    getBaseline: getBaselineMock,
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
  onUpload = vi.fn(),
  probe?: (state: ProjectState) => void,
) {
  const utils = render(
    <ProjectProvider>
      {actions.map((action, index) => (
        <DispatchHelper key={index} action={action} />
      ))}
      {probe ? <StateProbe onState={probe} /> : null}
      <MissionControl onClose={onClose} onUpload={onUpload} />
    </ProjectProvider>,
  );
  return { ...utils, onClose, onUpload };
}

const mockHealth: WebHealth = {
  schema_id: 'gms.web-health/1',
  engine_version: '0.1.0',
  api_version: '2.0.0',
  supported_formats: ['json', 'obj', 'stl'],
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
      assumptions: [],
      unsupported_failure_modes: ['fatigue'],
      validity: 'valid',
      load_path_stress_pa: null,
      safety_factor: 'not_available',
      qualification_blocked: true,
      solver_metadata: { model_id: 'screening_surrogate_v1' },
    } as unknown as ImpactEstimate,
    reason: null,
    unsupported_failure_modes: ['fatigue', 'creep'],
  },
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

beforeEach(() => {
  getBaselineMock.mockReset();
});

describe('MissionControl dashboard', () => {
  it('renders engine health, study cards, and empty state', () => {
    renderPanel([{ type: 'HEALTH_OK', health: mockHealth }]);

    expect(screen.getByRole('dialog', { name: 'Mission control' })).toBeInTheDocument();
    expect(screen.getByText('Engine health')).toBeInTheDocument();
    expect(screen.getByText('json')).toBeInTheDocument();
    expect(screen.getByText('shell_navier_v1')).toBeInTheDocument();
    expect(screen.getByText('on')).toBeInTheDocument();

    expect(screen.getByRole('button', { name: 'Slam Impact study' })).toBeInTheDocument();
    expect(screen.getByText(/drop or slam-to-table event/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Downforce study' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Drop Suite study' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run study' })).toBeInTheDocument();

    expect(screen.getAllByText('Idle').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Load baseline' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Upload geometry' })).toBeInTheDocument();
  });

  it('prefills the draft with the selected study and closes the panel', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    let captured: ProjectState | undefined;

    renderPanel([], onClose, vi.fn(), (state) => {
      captured = state;
    });

    await user.click(screen.getByRole('button', { name: 'Downforce study' }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(captured?.draft?.load_case).toEqual({
      name: 'shell_flex',
      kind: 'pressure',
      magnitude: { value: 5, unit: 'kPa' },
    });
    expect(captured?.draft?.structure).toEqual({
      type: 'shell_panel',
      a_m: 0.11,
      b_m: 0.065,
      t_m: 0.002,
      material: 'ABS',
    });
    expect(captured?.draft?.impact).toBeNull();
  });

  it('closes on Escape and on the close button', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderPanel([], onClose);

    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: 'Close control panel' }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('dispatches RUN_STUDY from the Run study action', async () => {
    const user = userEvent.setup();
    let runNonce = -1;

    renderPanel([], vi.fn(), vi.fn(), (state) => {
      runNonce = state.runNonce;
    });
    expect(runNonce).toBe(0);

    await user.click(screen.getByRole('button', { name: 'Run study' }));
    expect(runNonce).toBe(1);
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

  it('loads the baseline from the client when the empty-state CTA is clicked', async () => {
    const user = userEvent.setup();
    const baseline: WebBaselineResponse = {
      schema_id: 'gms.web-baseline/1',
      source: 'mouse_baseline',
      project: mockBaselineProject,
    };
    getBaselineMock.mockResolvedValueOnce(baseline);

    renderPanel([]);
    await user.click(screen.getByRole('button', { name: 'Load baseline' }));

    await waitFor(() => expect(getBaselineMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText('Ready')).toBeInTheDocument());
  });
});
