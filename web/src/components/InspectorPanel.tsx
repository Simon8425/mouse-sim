import { useProjectStore, COMPONENT_ROLES } from '../state/projectStore';
import {
  selectObjectById,
  selectFindingsFor,
  selectMassObject,
  selectWarningsCount,
} from '../state/selectors';
import {
  worldBounds,
  localBounds,
  boundsSize,
  boundsCenter,
} from '../lib/geometryBounds';
import { formatVector3, formatMatrix3, formatNumber } from '../lib/format';
import { formatMass, formatVolume } from '../lib/units';
import { severityTone, dispositionLabel } from '../lib/status';
import { StatusBadge } from './StatusBadge';
import { isRecord } from '../api/contracts';
import type { GeometryJson } from '../api/contracts';

/** Project store state shape, derived from the object selector's signature. */
type ProjectState = Parameters<typeof selectObjectById>[0];

/** Three-component vector as consumed by the formatting helpers. */
type Vector3 = [number, number, number];

/** Badge tone palette, mirrored from the severity mapper. */
type Tone = ReturnType<typeof severityTone>;

/** Axis-aligned bounds with min/max corners. */
interface Bounds {
  min: Vector3;
  max: Vector3;
}

/** Reads a finite number from an unknown value. */
function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** Reads a string from an unknown value. */
function readString(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

/** Formats a raw value for display, preferring formatNumber for numerics. */
function displayText(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === 'number') return formatNumber(value);
  if (typeof value === 'string') return value;
  return String(value);
}

/** Reads a 3-component vector from an unknown value. */
function vec(value: unknown): Vector3 | null {
  if (!Array.isArray(value) || value.length !== 3) return null;
  const items: readonly unknown[] = value;
  const x = num(items[0]);
  const y = num(items[1]);
  const z = num(items[2]);
  if (x === null || y === null || z === null) return null;
  return [x, y, z];
}

/** Formats a vector as text, yielding '—' when absent. */
function vecText(value: unknown): string {
  const vector = vec(value);
  return vector !== null ? formatVector3(vector) : '—';
}

/** Formats a nested 3×3 matrix as text, yielding '—' when absent. */
function matrixText(value: unknown): string {
  if (!Array.isArray(value) || value.length !== 3) return '—';
  for (const row of value) {
    if (!Array.isArray(row) || row.length !== 3) return '—';
    for (const cell of row) if (typeof cell !== 'number' || !Number.isFinite(cell)) return '—';
  }
  return formatMatrix3(value as unknown as readonly (readonly number[])[]);
}

/** Reads min/max corners from a bounds object. */
function readBounds(bounds: unknown): Bounds | null {
  if (!isRecord(bounds)) return null;
  const min = vec(bounds.min);
  const max = vec(bounds.max);
  if (min === null || max === null) return null;
  return { min, max };
}

/** Human-readable dimension summary for each supported geometry type. */
function dimensionLine(geometry: GeometryJson): string {
  const g = geometry as unknown as Record<string, unknown>;
  switch (geometry.type) {
    case 'box':
      return `size ${vecText(g.size)}`;
    case 'sphere': {
      const radius = num(g.radius);
      return radius !== null ? `radius ${formatNumber(radius)}` : '—';
    }
    case 'cylinder': {
      const radius = num(g.radius);
      const height = num(g.height);
      return radius !== null && height !== null
        ? `radius ${formatNumber(radius)}, height ${formatNumber(height)}`
        : '—';
    }
    case 'cone': {
      const base = num(g.base_radius);
      const height = num(g.height);
      return base !== null && height !== null
        ? `base radius ${formatNumber(base)}, height ${formatNumber(height)}`
        : '—';
    }
    case 'frustum': {
      const bottom = num(g.bottom_radius);
      const top = num(g.top_radius);
      const height = num(g.height);
      return bottom !== null && top !== null && height !== null
        ? `bottom ${formatNumber(bottom)}, top ${formatNumber(top)}, height ${formatNumber(height)}`
        : '—';
    }
    case 'mesh':
      return Array.isArray(g.vertices) && Array.isArray(g.triangles)
        ? `${g.vertices.length} vertices · ${g.triangles.length} triangles`
        : '—';
    case 'compound':
      return Array.isArray(g.children) ? `${g.children.length} children` : '—';
    default:
      return '—';
  }
}

function computeGeometryVolume(geometry: GeometryJson): number | null {
  const g = geometry as unknown as Record<string, unknown>;
  if (geometry.type === 'mesh' && Array.isArray(g.vertices) && Array.isArray(g.triangles)) {
    const verts = g.vertices as number[][];
    const tris = g.triangles as number[][];
    let totalSignedVolume = 0;
    for (const tri of tris) {
      const i0 = tri[0];
      const i1 = tri[1];
      const i2 = tri[2];
      const v0 = verts[i0];
      const v1 = verts[i1];
      const v2 = verts[i2];
      if (!v0 || !v1 || !v2) continue;
      const crossX = v1[1] * v2[2] - v1[2] * v2[1];
      const crossY = v1[2] * v2[0] - v1[0] * v2[2];
      const crossZ = v1[0] * v2[1] - v1[1] * v2[0];
      totalSignedVolume += v0[0] * crossX + v0[1] * crossY + v0[2] * crossZ;
    }
    const vol = Math.abs(totalSignedVolume / 6);
    return vol > 1e-15 ? vol : null;
  }
  if (geometry.type === 'box' && Array.isArray(g.size)) {
    const s = g.size as number[];
    if (s.length === 3 && s.every((v) => typeof v === 'number' && v > 0)) {
      return s[0] * s[1] * s[2];
    }
  }
  if (geometry.type === 'cylinder') {
    const radius = num(g.radius);
    const height = num(g.height);
    if (radius !== null && height !== null && radius > 0 && height > 0) {
      return Math.PI * radius * radius * height;
    }
  }
  if (geometry.type === 'sphere') {
    const radius = num(g.radius);
    if (radius !== null && radius > 0) {
      return (4 / 3) * Math.PI * Math.pow(radius, 3);
    }
  }
  return null;
}

/**
 * Finds the material name attached to the object with the given id, searching
 * the project's objects list or id-keyed mapping.
 */
function findMaterial(state: ProjectState, id: string): string | null {
  if (!state.project?.objects) return null;
  const objects = state.project.objects;
  if (Array.isArray(objects)) {
    for (const item of objects) {
      if (!isRecord(item)) continue;
      if (item.id === id || item.name === id) {
        return typeof item.material === 'string' ? item.material : null;
      }
    }
    return null;
  }
  if (isRecord(objects)) {
    const direct = objects[id];
    if (isRecord(direct)) {
      return typeof direct.material === 'string' ? direct.material : null;
    }
    for (const item of Object.values(objects)) {
      if (isRecord(item) && item.name === id) {
        return typeof item.material === 'string' ? item.material : null;
      }
    }
  }
  return null;
}

/** Maps an approval state to a badge tone; unknown states render neutral. */
function approvalTone(approval: string): Tone {
  switch (approval) {
    case 'draft':
      return 'warn';
    case 'approved':
      return 'ok';
    default:
      return 'neutral';
  }
}

/**
 * Side panel inspecting the currently selected object: geometry, material,
 * mass properties and validation findings.
 */
function issueSeverityLabel(severity: string): string {
  switch (severity.toLowerCase()) {
    case 'warning':
    case 'warn':
      return 'WARN';
    case 'error':
      return 'Error';
    case 'blocker':
      return 'Blocker';
    case 'info':
      return 'Info';
    default:
      return severity;
  }
}

export function InspectorPanel(): JSX.Element | null {
  const { state, dispatch } = useProjectStore();
  // Material/role assignment is locked while a test is running or loading:
  // the analysis snapshot must reflect the model as it was submitted.
  const isRunning = state.runStatus === 'loading' || state.runStatus === 'running';

  const entry =
    state.selectedId === null ? null : selectObjectById(state, state.selectedId);

  if (entry === null) {
    return null;
  }

  const geometry = entry.geometry;
  const g = geometry as unknown as Record<string, unknown>;
  const local = readBounds(localBounds(geometry));
  const world = readBounds(worldBounds(geometry));
  const transform = isRecord(g.transform) ? g.transform : null;
  const units = readString(g.units) ?? 'm';

  const materialName = state.objectMaterials[entry.id] ?? findMaterial(state, entry.id);
  const displayName = entry.name ?? entry.id;
  const material =
    materialName !== null
      ? state.materials?.find((m) => m.key.toLowerCase() === materialName.toLowerCase()) ?? null
      : null;
  const aiSuggestion = state.aiClassifications?.[entry.id] ?? null;
  const aiTone =
    !aiSuggestion || aiSuggestion.confidence === undefined
      ? 'neutral'
      : aiSuggestion.confidence >= 0.85
        ? 'ok'
        : aiSuggestion.confidence >= 0.6
          ? 'warn'
          : 'error';
  const family = readString(material?.family);
  const density = num(material?.density_kg_m3);
  const youngModulus = num(material?.young_modulus_pa);
  const approval = readString(material?.approval_state);
  const confidence = displayText(material?.confidence);
  const hasNoMaterial =
    materialName === null && state.materials !== null && state.materials.length > 0;

  const massObj = selectMassObject(state, entry.id) ?? selectMassObject(state, state.selectedId);
  const massRecord = isRecord(massObj) ? massObj : null;
  const geomVolume = computeGeometryVolume(geometry);

  const rawMass = num(massRecord?.mass_kg);
  const rawVolume = num(massRecord?.volume_m3);
  const volume = rawVolume ?? geomVolume;
  const mass = rawMass ?? (volume !== null && density !== null && density > 0 ? volume * density : null);
  const massStatus = readString(massRecord?.mass_status) ?? (mass !== null ? 'computed' : volume !== null ? 'unassigned' : null);
  const sourceStatus = readString(massRecord?.source_status) ?? (materialName ? 'assigned' : 'default');
  const reviewStatus = readString(massRecord?.review_status) ?? (approval ?? 'draft');
  const completeness = num(massRecord?.completeness) ?? (mass !== null ? 1.0 : volume !== null ? 0.5 : null);

  const findings = selectFindingsFor(state, state.selectedId);
  const warnings = selectWarningsCount(state, state.selectedId);

  const geometryRows: ReadonlyArray<readonly [string, string]> = [
    ['ID', readString(entry.id) ?? '—'],
    ['Type', geometry.type],
    ['Units', units],
    ['Dimensions', dimensionLine(geometry)],
    ['Local min', vecText(local?.min)],
    ['Local max', vecText(local?.max)],
    ['World min', vecText(world?.min)],
    ['World max', vecText(world?.max)],
    ['Size', vecText(boundsSize(worldBounds(geometry)))],
    ['Center', vecText(boundsCenter(worldBounds(geometry)))],
    ['Rotation', matrixText(transform?.rotation)],
    ['Translation', vecText(transform?.translation)],
  ];

  return (
    <div className="inspector-panel">
      <div className="inspector-panel__header">
        <h2 className="inspector-panel__object">{displayName}</h2>
        <div className="inspector-panel__header-actions">
          {entry.className ? <StatusBadge tone="neutral">{entry.className}</StatusBadge> : null}
          <button
            type="button"
            className="inspector-panel__close-btn"
            onClick={() => dispatch({ type: 'SET_INSPECTOR_OPEN', open: false })}
            title="Close inspector"
            aria-label="Close inspector"
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>

      <div className="inspector-panel__body">
        <h3 className="section-title">Mouse Part Role</h3>
      <label className="inspector-material-select">
        <select
          aria-label="Component role classification"
          value={state.objectClassifications?.[entry.id] ?? ''}
          disabled={isRunning}
          onChange={(event) =>
            dispatch({
              type: 'SET_OBJECT_CLASSIFICATION',
              objectId: entry.id,
              role: event.target.value === '' ? null : event.target.value,
            })
          }
        >
          <option value="">Unclassified / General Surface</option>
          {COMPONENT_ROLES.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
      </label>
      {aiSuggestion && !state.objectClassifications?.[entry.id] ? (
        <div className="inspector-ai-suggestion">
          <span className={`badge badge--${aiTone}`}>AI {aiSuggestion.confidence !== undefined ? `${Math.round(aiSuggestion.confidence * 100)}%` : ''}</span>
          <span className="inspector-ai-suggestion__role">{aiSuggestion.component_type ?? 'unresolved'}</span>
          <button
            type="button"
            className="btn btn--small"
            onClick={() => dispatch({ type: 'CLASSIFY_APPLY_ONE', objectId: entry.id })}
          >
            Apply
          </button>
          <button
            type="button"
            className="btn btn--small"
            onClick={() => dispatch({ type: 'CLASSIFY_CLEAR', objectId: entry.id })}
          >
            Dismiss
          </button>
          {aiSuggestion.reasons && aiSuggestion.reasons.length > 0 ? (
            <span className="inspector-ai-suggestion__reasons" title={aiSuggestion.reasons.join(' · ')}>
              {aiSuggestion.reasons.join(' · ')}
            </span>
          ) : null}
        </div>
      ) : null}

      <h3 className="section-title section-title--no-border">Material</h3>
      {state.materials && state.materials.length > 0 ? (
        <label className="inspector-material-select">
          <select
            aria-label="Material assignment"
            value={materialName ?? ''}
            disabled={isRunning}
            onChange={(event) =>
              dispatch({
                type: 'SET_OBJECT_MATERIAL',
                objectId: entry.id,
                materialKey: event.target.value === '' ? null : event.target.value,
              })
            }
          >
            <option value="">Default (Project default)</option>
            {state.materials.map((m) => (
              <option key={m.key} value={m.key}>
                {m.key}
              </option>
            ))}
          </select>
          {hasNoMaterial ? (
            <p className="inspector-hint inspector-hint--material">
              No material assigned — this component uses Default material.
            </p>
          ) : null}
        </label>
      ) : null}
      <table className="dense-table">
        <tbody>
          <tr>
            <th scope="row">Material</th>
            <td>{materialName ?? '—'}</td>
          </tr>
          <tr>
            <th scope="row">Family</th>
            <td>{family ?? '—'}</td>
          </tr>
          <tr>
            <th scope="row">Density</th>
            <td>{density !== null ? `${formatNumber(density)} kg/m³` : '—'}</td>
          </tr>
          <tr>
            <th scope="row">Young modulus</th>
            <td>{youngModulus !== null ? `${formatNumber(youngModulus)} Pa` : '—'}</td>
          </tr>
          <tr>
            <th scope="row">Approval</th>
            <td>
              {approval !== null ? (
                <StatusBadge tone={approvalTone(approval)}>{approval}</StatusBadge>
              ) : (
                '—'
              )}
            </td>
          </tr>
          <tr>
            <th scope="row">Confidence</th>
            <td>{confidence ?? '—'}</td>
          </tr>
        </tbody>
      </table>

      <h3 className="section-title">
        Diagnostics{warnings > 0 ? ` · ${warnings} warning${warnings === 1 ? '' : 's'}` : ''}
      </h3>
      {findings.length === 0 ? (
        <p className="inspector-hint">No findings</p>
      ) : (
        <ul className="inspector-findings-list">
          {findings.map((finding, index) => {
            const severity = finding.severity || 'info';
            const message = displayText(finding.message) ?? '—';
            const code = displayText(finding.code);
            return (
              <li key={index} className="inspector-finding-item">
                <div className="inspector-finding-line">
                  <StatusBadge tone={severityTone(severity)}>
                    {issueSeverityLabel(severity)}
                  </StatusBadge>
                  <span className="inspector-finding-message">
                    {message}
                    {code !== null ? (
                      <code className="inspector-finding-code">{code}</code>
                    ) : null}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <h3 className="section-title">Geometry</h3>
      <table className="dense-table">
        <tbody>
          {geometryRows.map(([label, value]) => (
            <tr key={label}>
              <th scope="row">{label}</th>
              <td>{value}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 className="section-title">Mass properties</h3>
      <table className="dense-table">
        <tbody>
          <tr>
            <th scope="row">Mass</th>
            <td>{mass !== null ? formatMass(mass) : '—'}</td>
          </tr>
          <tr>
            <th scope="row">Volume</th>
            <td>{volume !== null ? formatVolume(volume) : '—'}</td>
          </tr>
          <tr>
            <th scope="row">Mass status</th>
            <td>{massStatus ?? '—'}</td>
          </tr>
          <tr>
            <th scope="row">Source</th>
            <td>{sourceStatus ?? '—'}</td>
          </tr>
          <tr>
            <th scope="row">Review</th>
            <td>{reviewStatus !== null ? dispositionLabel(reviewStatus) : '—'}</td>
          </tr>
          <tr>
            <th scope="row">Completeness</th>
            <td>{completeness !== null ? `${Math.round(completeness * 100)}%` : '—'}</td>
          </tr>
        </tbody>
      </table>
      </div>
    </div>
  );
}
