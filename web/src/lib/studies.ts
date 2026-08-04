import type { PipelineRequest } from '../api/contracts';

export interface StudyPreset {
  id: string;
  title: string;
  method: string;
  description: string;
  patch: Partial<PipelineRequest>;
}

const SLAM_IMPACT = {
  fall_height_m: 0.75,
  restitution: 0.3,
  contact_stiffness_n_per_m: 1e5,
};

const DOWNFORCE_LOAD_CASE = {
  name: 'shell_flex',
  kind: 'pressure',
  magnitude: { value: 5, unit: 'kPa' },
};

const DOWNFORCE_STRUCTURE = {
  type: 'shell_panel',
  a_m: 0.11,
  b_m: 0.065,
  t_m: 0.002,
  material: 'ABS',
};

const DROP_SUITE_IMPACT = {
  ...SLAM_IMPACT,
  orientation: 'face',
  contact_normal: [0, 0, 1],
};

function studyPatch(patch: Record<string, unknown>): Partial<PipelineRequest> {
  return patch as unknown as Partial<PipelineRequest>;
}

export const STUDY_PRESETS: StudyPreset[] = [
  {
    id: 'slam-impact',
    title: 'Slam Impact',
    method: 'impact · energy_quasi_static_v1',
    description: 'Screen a mouse drop or slam-to-table event as free-fall contact; estimates force and acceleration, not fracture or battery damage.',
    patch: studyPatch({ impact: SLAM_IMPACT, load_case: null, structure: null }),
  },
  {
    id: 'downforce',
    title: 'Downforce',
    method: 'load_case · shell_navier_v1',
    description: 'Measure shell flex under a distributed hand-pressure load; reports displacement and stress screening.',
    patch: studyPatch({
      load_case: DOWNFORCE_LOAD_CASE,
      structure: DOWNFORCE_STRUCTURE,
      impact: null,
    }),
  },
  {
    id: 'drop-suite',
    title: 'Drop Suite',
    method: 'impact · orientation',
    description: 'Sweep a defined drop orientation; resolves contact-normal energy while disclosing unsupported damage modes.',
    patch: studyPatch({
      impact: DROP_SUITE_IMPACT,
      load_case: null,
      structure: null,
    }),
  },
];
