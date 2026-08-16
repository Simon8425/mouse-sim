import type {
  PipelineRequest,
  ValidationFinding,
  MassObjectResult,
} from '../api/contracts';
import {
  type ProjectState,
  type ObjectEntry,
  createAnalysisRequest,
  computeObjectEntries,
} from './projectStore';
import { isRecord } from '../api/contracts';

const SEVERITY_RANK: Record<string, number> = {
  blocker: 0,
  error: 1,
  warning: 2,
  info: 3,
};

export function selectAnalysisRequest(state: ProjectState): PipelineRequest | null {
  return createAnalysisRequest(state);
}

export function selectObjectEntries(state: ProjectState): ObjectEntry[] {
  return computeObjectEntries(state);
}

export function selectObjectById(state: ProjectState, id: string | null): ObjectEntry | null {
  if (id === null) return null;
  const entries = selectObjectEntries(state);
  return entries.find((e) => e.id === id) ?? null;
}

export function selectIsVisible(state: ProjectState, id: string): boolean {
  return state.visibility[id] ?? true;
}

export function selectIsIsolated(state: ProjectState, id: string): boolean {
  return state.isolatedId === id;
}

export function selectFindingSeverities(state: ProjectState): Map<string, string> {
  const map = new Map<string, string>();
  const findings = state.lastResult?.validation?.findings;
  if (!Array.isArray(findings)) return map;

  for (const finding of findings) {
    const sev = finding.severity;
    const rank = SEVERITY_RANK[sev] ?? 99;
    for (const affectedId of finding.affected_ids) {
      const existingSev = map.get(affectedId);
      const existingRank = existingSev ? (SEVERITY_RANK[existingSev] ?? 99) : 99;
      if (rank < existingRank) {
        map.set(affectedId, sev);
      }
    }
  }
  return map;
}

export function selectFindingsFor(state: ProjectState, id: string | null): ValidationFinding[] {
  if (id === null) return [];
  const findings = state.lastResult?.validation?.findings;
  if (!Array.isArray(findings)) return [];

  const matched = findings.filter((f) => f.affected_ids.includes(id));
  return matched.sort((a, b) => {
    const rankA = SEVERITY_RANK[a.severity] ?? 99;
    const rankB = SEVERITY_RANK[b.severity] ?? 99;
    if (rankA !== rankB) return rankA - rankB;
    return a.code.localeCompare(b.code);
  });
}

export function selectObjectFindingCodes(state: ProjectState, id: string | null): Set<string> {
  return new Set(selectFindingsFor(state, id).map((f) => f.code));
}

export function selectWarningsCount(state: ProjectState, id: string | null): number {
  const findings = selectFindingsFor(state, id);
  return findings.filter(
    (f) => f.severity === 'warning' || f.severity === 'error' || f.severity === 'blocker',
  ).length;
}

export function selectUnsupportedModes(state: ProjectState): string[] {
  const result = state.lastResult;
  if (!result) return [];
  const set = new Set<string>();

  if (Array.isArray(result.validity?.unsupported_failure_modes)) {
    for (const m of result.validity.unsupported_failure_modes) set.add(m);
  }
  if (Array.isArray(result.structural?.response?.unsupported_failure_modes)) {
    for (const m of result.structural.response.unsupported_failure_modes) set.add(m);
  }
  if (Array.isArray(result.impact?.unsupported_failure_modes)) {
    for (const m of result.impact.unsupported_failure_modes) set.add(m);
  }

  return Array.from(set).sort();
}

export function selectAssumptions(state: ProjectState): string[] {
  return state.lastResult?.validity?.assumptions ?? [];
}

export function selectHasStaleResult(state: ProjectState): boolean {
  return state.stale && state.lastResult !== null;
}

export function selectMassObject(state: ProjectState, id: string | null): MassObjectResult | null {
  if (id === null || !state.lastResult?.mass?.objects) return null;
  const entry = selectObjectById(state, id);
  const targetId = entry?.id ?? id;
  const targetName = entry?.name ?? null;
  return (
    state.lastResult.mass.objects.find(
      (o) =>
        o.object_id === targetId ||
        o.object_id === id ||
        (targetName !== null && o.object_id === targetName) ||
        (targetName !== null && (o as { name?: string }).name === targetName) ||
        (o as { name?: string }).name === id,
    ) ?? null
  );
}

export function selectSourceLabel(state: ProjectState): string {
  if (state.preview) {
    const sourceName = state.preview.source_name ?? 'upload';
    const projectName = state.projectName && state.projectName !== 'no project'
      ? state.projectName
      : null;
    return projectName ? `${projectName} + ${sourceName}` : sourceName;
  }
  if (state.tempPreview) {
    return `${state.projectName || 'Project'} + ${state.tempPreview.name}`;
  }
  return state.projectName || 'mouse_baseline';
}

export function selectRunStatusLabel(state: ProjectState): {
  text: 'Idle' | 'Running…' | 'Complete' | 'Failed';
  live: boolean;
} {
  switch (state.runStatus) {
    case 'loading':
    case 'running':
      return { text: 'Running…', live: true };
    case 'success':
      return { text: 'Complete', live: false };
    case 'error':
      return { text: 'Failed', live: false };
    default:
      return { text: 'Idle', live: false };
  }
}

export function selectEvidenceCount(state: ProjectState): number {
  const qualification = state.lastResult?.qualification;
  if (!qualification) return 0;
  return qualification.gates.length + (qualification.integrity_gates?.length ?? 0);
}

export function selectSolverModelBadge(state: ProjectState): string | null {
  const result = state.lastResult;
  if (!result) return null;
  const metadata: unknown[] = [];
  if (result.impact?.result && isRecord(result.impact.result)) {
    metadata.push(result.impact.result.solver_metadata);
  }
  if (result.structural?.response && isRecord(result.structural.response)) {
    metadata.push(result.structural.response.solver_metadata);
  }
  for (const meta of metadata) {
    if (isRecord(meta) && typeof meta.model_id === 'string' && meta.model_id.trim() !== '') {
      return meta.model_id;
    }
  }
  return null;
}
