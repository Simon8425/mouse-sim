import type { DropSimulationConfig, DropOrientation, DropSurface, DropTestKind } from '../api/contracts';

export interface DropTestDefinition {
  id: string;
  title: string;
  test: DropTestKind;
  description: string;
  defaults: {
    height_m: number;
    surface: DropSurface;
    drop_count: number;
    orientation: DropOrientation;
    spin_rps: number;
  };
}

export const DROP_SURFACES: { value: DropSurface; label: string }[] = [
  { value: 'concrete', label: 'Concrete' },
  { value: 'wood', label: 'Hardwood' },
  { value: 'foam', label: 'Foam mat' },
  { value: 'steel', label: 'Steel plate' },
];

export const DROP_ORIENTATIONS: { value: DropOrientation; label: string }[] = [
  { value: 'flat', label: 'Flat (bottom down)' },
  { value: 'edge', label: 'Edge (long side)' },
  { value: 'corner', label: 'Corner' },
  { value: 'random', label: 'Random (deterministic)' },
];

export const PAUSE_BETWEEN_DROPS_OPTIONS: { value: number; label: string }[] = [
  { value: 1.0, label: '1.0s (Recommended)' },
  { value: 0.5, label: '0.5s' },
  { value: 0.1, label: '0.1s (Fast)' },
  { value: 2.0, label: '2.0s (Extended)' },
];

export const DROP_TESTS: DropTestDefinition[] = [
  {
    id: 'drop-test',
    title: 'Drop Test',
    test: 'drop',
    description:
      'Free-fall rigid-body simulation: the model drops from the configured height, bounces, and settles. Reports impact speeds, energies, and peak force.',
    defaults: {
      height_m: 0.75,
      surface: 'concrete',
      drop_count: 3,
      orientation: 'flat',
      spin_rps: 0,
    },
  },
  {
    id: 'impact-test',
    title: 'Impact Test',
    test: 'impact',
    description:
      'Corner-first impact drop: the model strikes on its corner for the harshest first hit, then settles. Repeats the configured number of drops.',
    defaults: {
      height_m: 1.0,
      surface: 'concrete',
      drop_count: 1,
      orientation: 'corner',
      spin_rps: 0,
    },
  },
  {
    id: 'tumble-test',
    title: 'Tumble Test',
    test: 'tumble',
    description:
      'Drops with an initial spin about a horizontal axis: the model tumbles through the air and impacts multiple times before settling.',
    defaults: {
      height_m: 0.75,
      surface: 'concrete',
      drop_count: 2,
      orientation: 'random',
      spin_rps: 4,
    },
  },
  {
    id: 'population-test',
    title: 'Population Analysis (10k)',
    test: 'population',
    description:
      '10,000-unit Monte Carlo population drop simulation across manufacturing tolerance variations.',
    defaults: {
      height_m: 0.75,
      surface: 'concrete',
      drop_count: 1,
      orientation: 'flat',
      spin_rps: 0,
    },
  },
];

export interface DropTestConfigState extends Omit<DropSimulationConfig, 'test'> {
  mass_kg: number | null;
  seed?: number | null;
  pause_between_drops_s?: number;
}

export function configForTest(definition: DropTestDefinition, overrides: Partial<DropTestConfigState> = {}): DropTestConfigState {
  return {
    height_m: overrides.height_m ?? definition.defaults.height_m,
    surface: overrides.surface ?? definition.defaults.surface,
    drop_count: overrides.drop_count ?? definition.defaults.drop_count,
    orientation: overrides.orientation ?? definition.defaults.orientation,
    spin_rps: overrides.spin_rps ?? definition.defaults.spin_rps,
    mass_kg: overrides.mass_kg ?? null,
    seed: overrides.seed ?? null,
    pause_between_drops_s: overrides.pause_between_drops_s ?? 1.0,
  };
}

// Per-test config persists across Mission Control reopenings so the user does
// not have to re-enter settings for every run.
const PERSISTED_CONFIGS = new Map<string, DropTestConfigState>();

export function persistedConfigForTest(definition: DropTestDefinition): DropTestConfigState {
  const existing = PERSISTED_CONFIGS.get(definition.id);
  if (existing) return { ...existing };
  return configForTest(definition);
}

export function persistConfigForTest(definition: DropTestDefinition, config: DropTestConfigState): void {
  PERSISTED_CONFIGS.set(definition.id, { ...config });
}

export function clampDropConfig(config: DropTestConfigState): DropTestConfigState {
  const height = Number.isFinite(config.height_m)
    ? Math.min(2, Math.max(0.02, config.height_m))
    : 0.75;
  const count = Number.isFinite(config.drop_count)
    ? Math.min(20, Math.max(1, Math.round(config.drop_count)))
    : 1;
  const spin = Number.isFinite(config.spin_rps ?? 0)
    ? Math.min(20, Math.max(0, config.spin_rps ?? 0))
    : 0;
  const mass =
    config.mass_kg !== null && config.mass_kg !== undefined && Number.isFinite(config.mass_kg)
      ? Math.min(10, Math.max(0.01, config.mass_kg))
      : null;
  return {
    ...config,
    height_m: height,
    drop_count: count,
    spin_rps: spin,
    mass_kg: mass,
  };
}
