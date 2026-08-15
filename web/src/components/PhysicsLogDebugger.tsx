/**
 * PhysicsLogDebugger — Telemetry & Tracking Log Debugger drawer.
 *
 * Five tabs: Overview (spec inspector), Live Stream (virtualized frame table
 * with scrub-to-frame), Charts (energy partition, kinematics, orientation),
 * Events (filterable diagnostics), and Export (JSON/CSV/clipboard).
 *
 * The drawer is a sibling of the inspector inside the workspace; its width is
 * accounted for in App's viewport insets so it never overlaps the 3D canvas.
 */
import * as React from 'react';
import { useProjectStore } from '../state/projectStore';
import type { SceneViewportHandle } from '../scene/SceneViewport';
import type {
  PhysicsFloorSpec,
  TelemetryEvent,
  TelemetryFrame,
  TelemetryLogSession,
} from '../api/telemetryDebuggerContracts';
import {
  MATERIAL_REFERENCE,
  FLOOR_REFERENCE,
  effectiveContactModulus,
  summarizeFrames,
  buildTelemetrySession,
} from '../lib/telemetrySessionBuilder';
import {
  exportFramesCsv,
  exportSessionJson,
  copyFrameToClipboard,
} from '../lib/telemetryExporter';

type TabId = 'overview' | 'stream' | 'charts' | 'events' | 'export';

const TABS: { id: TabId; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'stream', label: 'Stream' },
  { id: 'charts', label: 'Charts' },
  { id: 'events', label: 'Events' },
  { id: 'export', label: 'Export' },
];

export interface PhysicsLogDebuggerProps {
  viewportRef: React.RefObject<SceneViewportHandle | null>;
}

const STATUS_TONE: Record<TelemetryFrame['status'], string> = {
  free_fall: 'ok',
  impact: 'warn',
  rebound: 'info',
  rolling: 'info',
  settled: 'neutral',
};

const EVENT_TONE: Record<TelemetryEvent['level'], string> = {
  INFO: 'neutral',
  EVENT: 'info',
  WARNING: 'warn',
  ANOMALY: 'error',
};

const ROW_H = 26;
const VISIBLE_ROWS = 9;

function fmt(v: number | undefined | null, digits = 2): string {
  if (v === undefined || v === null || !Number.isFinite(v)) return '—';
  return v.toFixed(digits);
}

function statusLabel(status: TelemetryFrame['status']): string {
  switch (status) {
    case 'free_fall': return 'Freefall';
    case 'impact': return 'Impact';
    case 'rebound': return 'Rebound';
    case 'rolling': return 'Settling';
    case 'settled': return 'Rest';
  }
}

/** Minimal row-windowing virtualization: only the visible slice renders. */
function VirtualStreamTable(props: {
  frames: TelemetryFrame[];
  onScrub: (frame: TelemetryFrame) => void;
}) {
  const { frames, onScrub } = props;
  const [scrollTop, setScrollTop] = React.useState(0);
  const total = frames.length;
  const start = Math.max(0, Math.min(total - 1, Math.floor(scrollTop / ROW_H)));
  const end = Math.min(total, start + VISIBLE_ROWS + 1);
  const rows: React.ReactNode[] = [];

  for (let i = start; i < end; i += 1) {
    const f = frames[i];
    rows.push(
      <tr
        key={f.index}
        className="telemetry-table__row"
        onClick={() => onScrub(f)}
        title="Click to seek the 3D viewport to this frame"
      >
        <td className="telemetry-table__cell">{f.t_s.toFixed(3)}</td>
        <td className="telemetry-table__cell">
          <span className={`badge badge--${STATUS_TONE[f.status]}`}>{statusLabel(f.status)}</span>
        </td>
        <td className="telemetry-table__cell">{fmt(f.position_m[2] * 1000, 1)}</td>
        <td className="telemetry-table__cell">
          {fmt(f.velocity_m_s[0])} / {fmt(f.velocity_m_s[1])} / {fmt(f.velocity_m_s[2])}
        </td>
        <td className="telemetry-table__cell">{fmt(f.acceleration_g, 1)}</td>
        <td className="telemetry-table__cell">
          {fmt(f.angular_velocity_rad_s[0])} / {fmt(f.angular_velocity_rad_s[1])} / {fmt(f.angular_velocity_rad_s[2])}
        </td>
        <td className="telemetry-table__cell">
          {fmt(f.quaternion_wxyz[0], 3)} {fmt(f.quaternion_wxyz[1], 3)} {fmt(f.quaternion_wxyz[2], 3)} {fmt(f.quaternion_wxyz[3], 3)}
        </td>
        <td className="telemetry-table__cell">{fmt(f.energy_j.total * 1000, 1)}</td>
        <td className="telemetry-table__cell">{f.fea ? fmt(f.fea.peak_stress_pa / 1e6, 1) : '—'}</td>
        <td className="telemetry-table__cell">{f.fea ? fmt(f.fea.safety_factor, 1) : '—'}</td>
      </tr>,
    );
  }

  return (
    <div className="telemetry-table-wrap">
      <div
        className="telemetry-table-spacer"
        onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
      >
        <div style={{ height: total * ROW_H, position: 'relative' }}>
          <table
            className="telemetry-table"
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              transform: `translateY(${start * ROW_H}px)`,
            }}
          >
            <thead>
              <tr>
                <th>t [s]</th>
                <th>Status</th>
                <th>Z [mm]</th>
                <th>Vel [m/s]</th>
                <th>G</th>
                <th>AngVel</th>
                <th>Quat</th>
                <th>KE [mJ]</th>
                <th>σ [MPa]</th>
                <th>SF</th>
              </tr>
            </thead>
            <tbody>
              {rows}
            </tbody>
          </table>
        </div>
      </div>
      <div className="telemetry-table__meta">
        {total.toLocaleString()} frames · click a row to scrub
      </div>
    </div>
  );
}

/** SVG sparkline for one time series. */
function Sparkline(props: {
  data: number[];
  width?: number;
  height?: number;
  stroke?: string;
}) {
  const { data, width = 220, height = 40, stroke = 'var(--color-ok)' } = props;
  const pts: string[] = [];
  const n = data.length;
  if (n === 0) return <svg className="telemetry-chart" width={width} height={height} />;
  let min = Infinity;
  let max = -Infinity;
  for (const v of data) {
    if (Number.isFinite(v)) {
      if (v < min) min = v;
      if (v > max) max = v;
    }
  }
  const span = max - min || 1;
  for (let i = 0; i < n; i += 1) {
    const x = (i / (n - 1)) * width;
    const y = height - 3 - ((data[i] - min) / span) * (height - 6);
    pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  }
  return (
    <svg className="telemetry-chart" width={width} height={height} aria-hidden="true">
      <polyline points={pts.join(' ')} fill="none" stroke={stroke} strokeWidth={1.5} />
    </svg>
  );
}

function SpecRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="telemetry-spec-row">
      <span className="telemetry-spec-row__label">{label}</span>
      <span className="telemetry-spec-row__value">{value}</span>
    </div>
  );
}

export function PhysicsLogDebugger({ viewportRef }: PhysicsLogDebuggerProps) {
  const { state, dispatch } = useProjectStore();
  const [tab, setTab] = React.useState<TabId>('overview');
  const [frames, setFrames] = React.useState<TelemetryFrame[]>([]);
  const [events, setEvents] = React.useState<TelemetryEvent[]>([]);
  const [session, setSession] = React.useState<TelemetryLogSession | null>(null);
  const [copied, setCopied] = React.useState(false);
  const pollRef = React.useRef<number | null>(null);

  // Poll the runtime telemetry while the drawer is open (20 Hz, cheap).
  React.useEffect(() => {
    if (!state.debuggerOpen) return;
    const tick = () => {
      const rt = viewportRef.current as {
        getTelemetryFrames?: () => TelemetryFrame[];
        getTelemetryEvents?: () => TelemetryEvent[];
      } | null;
      if (rt?.getTelemetryFrames) setFrames(rt.getTelemetryFrames());
      if (rt?.getTelemetryEvents) setEvents(rt.getTelemetryEvents());
    };
    tick();
    pollRef.current = window.setInterval(tick, 50);
    return () => {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [state.debuggerOpen, viewportRef]);

  // Rebuild the session snapshot when frames change (export tab uses it).
  React.useEffect(() => {
    const rt = viewportRef.current as { buildTelemetrySession?: () => TelemetryLogSession | null } | null;
    if (rt?.buildTelemetrySession) setSession(rt.buildTelemetrySession());
  }, [frames, viewportRef]);

  const handleScrub = React.useCallback(
    (frame: TelemetryFrame) => {
      viewportRef.current?.seekDropTime(frame.t_s);
    },
    [viewportRef],
  );

  const handleCopy = async (frame: TelemetryFrame) => {
    await copyFrameToClipboard(frame);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  // Close when clicking outside the log card
  const cardRef = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    const handlePointerDownOutside = (e: PointerEvent) => {
      const target = e.target as HTMLElement | null;
      if (
        target &&
        !target.closest('.viewport-toolbar__log') &&
        !target.closest('.telemetry-debugger')
      ) {
        dispatch({ type: 'SET_DEBUGGER_OPEN', open: false });
      }
    };
    window.addEventListener('pointerdown', handlePointerDownOutside);
    return () => {
      window.removeEventListener('pointerdown', handlePointerDownOutside);
    };
  }, [dispatch]);

  const derivedSession = React.useMemo(() => {
    if (session) return session;
    const lastSim = state.lastResult?.drop_simulation;
    if (lastSim) {
      return buildTelemetrySession(lastSim, frames);
    }
    const massKg = state.lastResult?.mass?.mass_kg ?? 0.28867;
    const com = state.lastResult?.mass?.center_of_mass_m ?? [0.000143, -0.003114, 0.017019];
    const inertia = state.lastResult?.mass?.inertia_tensor_kg_m2 ?? [
      [0.000343, 0, 0],
      [0, 0.000118, 0],
      [0, 0, 0.000405],
    ];
    const dims = { x: 0.125, y: 0.065, z: 0.040 };
    const mat = MATERIAL_REFERENCE.abs;
    const floorRef = FLOOR_REFERENCE.concrete;
    const surfaceKey = state.draft?.drop_simulation?.surface ?? 'concrete';
    return {
      session_id: 'pending',
      timestamp: new Date().toISOString(),
      model: {
        name: 'G3 Mouse Model',
        mass_kg: massKg,
        com_offset_m: [com[0], com[1], com[2]] as [number, number, number],
        inertia_tensor_kg_m2: inertia,
        dimensions_m: dims,
        material: {
          name: mat.name,
          density_kg_m3: mat.density_kg_m3,
          young_modulus_pa: mat.young_modulus_pa,
          poissons_ratio: mat.poissons_ratio,
          yield_strength_pa: mat.yield_strength_pa,
          ultimate_strength_pa: mat.ultimate_strength_pa,
          friction_coefficient: 0.60,
        },
      },
      floor: {
        surface_id: surfaceKey as PhysicsFloorSpec['surface_id'],
        young_modulus_pa: floorRef.young_modulus_pa,
        poissons_ratio: floorRef.poissons_ratio,
        restitution: 0.38,
        friction_static: 0.60,
        friction_dynamic: 0.48,
        effective_modulus_pa: effectiveContactModulus(
          mat.young_modulus_pa,
          mat.poissons_ratio,
          floorRef.young_modulus_pa,
          floorRef.poissons_ratio,
        ),
      },
      drop_config: {
        height_m: state.draft?.drop_simulation?.height_m ?? 0.75,
        orientation: state.draft?.drop_simulation?.orientation ?? 'flat',
        gravity_m_s2: 9.81,
        initial_velocity_m_s: [0, 0, 0],
        initial_spin_rad_s: 0,
      },
      summary: frames.length > 0 ? summarizeFrames(frames, 0.5 * massKg * 9.81 * 0.75) : null,
    } as unknown as TelemetryLogSession;
  }, [session, state.lastResult, state.draft, frames]);

  const model = derivedSession?.model;
  const floor = derivedSession?.floor;
  const drop = derivedSession?.drop_config;
  const summary = derivedSession?.summary;

  return (
    <div ref={cardRef} className="telemetry-debugger">
      <div className="telemetry-debugger__header">
        <h2 className="telemetry-debugger__title">Log</h2>
        <button
          type="button"
          className="telemetry-debugger__close"
          aria-label="Close log"
          onClick={() => dispatch({ type: 'SET_DEBUGGER_OPEN', open: false })}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>

      <div className="telemetry-segmented-bar" role="tablist" aria-label="Log tabs">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={`telemetry-segmented-item${tab === id ? ' is-active' : ''}`}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="telemetry-debugger__body">
        {tab === 'overview' ? (
          <div className="telemetry-overview">
            {!model ? (
              <p className="telemetry-empty">Run a drop test to populate the telemetry overview.</p>
            ) : (
              <>
                <section className="telemetry-section">
                  <h3>Model &amp; Mass Properties</h3>
                  <SpecRow label="Mass" value={`${fmt(model?.mass_kg, 3)} kg (${fmt((model?.mass_kg ?? 0) * 1000, 0)} g)`} />
                  <SpecRow
                    label="CoM offset"
                    value={`[${model?.com_offset_m?.map((v) => fmt(v * 1000, 2)).join(', ') ?? '—'}] mm`}
                  />
                  <SpecRow label="Dimensions" value={model?.dimensions_m ? `${fmt(model.dimensions_m.x * 1000, 1)} × ${fmt(model.dimensions_m.y * 1000, 1)} × ${fmt(model.dimensions_m.z * 1000, 1)} mm` : '—'} />
                  <SpecRow
                    label="Principal inertia"
                    value={model?.inertia_tensor_kg_m2
                      ? `${fmt(model.inertia_tensor_kg_m2[0]?.[0] * 1e6, 1)} / ${fmt(model.inertia_tensor_kg_m2[1]?.[1] * 1e6, 1)} / ${fmt(model.inertia_tensor_kg_m2[2]?.[2] * 1e6, 1)} g·m²`
                      : '—'}
                  />
                </section>
                <section className="telemetry-section">
                  <h3>Material</h3>
                  <SpecRow label="Family" value={model?.material.name ?? '—'} />
                  <SpecRow label="Density" value={model?.material ? `${fmt(model.material.density_kg_m3, 0)} kg/m³` : '—'} />
                  <SpecRow label="Young's modulus" value={model?.material ? `${fmt(model.material.young_modulus_pa / 1e9, 2)} GPa` : '—'} />
                  <SpecRow label="Yield / UTS" value={model?.material ? `${fmt(model.material.yield_strength_pa / 1e6, 0)} / ${fmt(model.material.ultimate_strength_pa / 1e6, 0)} MPa` : '—'} />
                </section>
                <section className="telemetry-section">
                  <h3>Floor &amp; Drop</h3>
                  <SpecRow label="Surface" value={floor?.surface_id ?? '—'} />
                  <SpecRow label="Effective modulus" value={floor ? `${fmt(floor.effective_modulus_pa / 1e9, 2)} GPa` : '—'} />
                  <SpecRow label="Restitution / friction" value={floor ? `${fmt(floor.restitution, 2)} / ${fmt(floor.friction_static, 2)}` : '—'} />
                  <SpecRow label="Drop height" value={drop ? `${fmt(drop.height_m * 100, 1)} cm` : '—'} />
                  <SpecRow label="Orientation" value={drop?.orientation ?? '—'} />
                  <SpecRow label="Impact velocity (2gh)" value={drop ? `${fmt(Math.sqrt(2 * drop.gravity_m_s2 * drop.height_m), 2)} m/s` : '—'} />
                  <SpecRow label="Peak G-force" value={summary ? `${fmt(summary.peak_g_force, 1)} g` : '— (awaiting run)'} />
                  <SpecRow label="Measured restitution" value={summary ? fmt(summary.restitution_measured, 3) : '— (awaiting run)'} />
                  <SpecRow label="Peak stress" value={summary ? `${fmt(summary.peak_stress_mpa, 1)} MPa` : '— (awaiting run)'} />
                  <SpecRow label="Min safety factor" value={summary ? fmt(summary.min_safety_factor, 2) : '— (awaiting run)'} />
                  <SpecRow label="Max energy drift" value={summary ? `${fmt(summary.energy_drift_max_pct, 3)} %` : '— (awaiting run)'} />
                </section>
              </>
            )}
          </div>
        ) : null}

        {tab === 'stream' ? (
          frames.length === 0 ? (
            <p className="telemetry-empty">No frames recorded yet — start drop playback to stream telemetry.</p>
          ) : (
            <VirtualStreamTable frames={frames} onScrub={handleScrub} />
          )
        ) : null}

        {tab === 'charts' ? (
          <div className="telemetry-charts">
            <section className="telemetry-section">
              <h3>Energy partition (mJ)</h3>
              <Sparkline data={frames.map((f) => f.energy_j.potential * 1000)} stroke="var(--color-info, #4aa3ff)" />
              <Sparkline data={frames.map((f) => f.energy_j.kinetic_trans * 1000)} stroke="var(--color-ok)" />
              <Sparkline data={frames.map((f) => f.energy_j.kinetic_rot * 1000)} stroke="var(--color-warn)" />
              <Sparkline data={frames.map((f) => f.energy_j.dissipated * 1000)} stroke="var(--color-error, #ff5c5c)" />
              <div className="telemetry-legend">
                <span className="telemetry-legend__item" style={{ color: 'var(--color-info, #4aa3ff)' }}>Potential</span>
                <span className="telemetry-legend__item" style={{ color: 'var(--color-ok)' }}>Kinetic trans</span>
                <span className="telemetry-legend__item" style={{ color: 'var(--color-warn)' }}>Kinetic rot</span>
                <span className="telemetry-legend__item" style={{ color: 'var(--color-error, #ff5c5c)' }}>Dissipated</span>
              </div>
            </section>
            <section className="telemetry-section">
              <h3>Kinematics</h3>
              <Sparkline data={frames.map((f) => f.position_m[2] * 1000)} stroke="var(--color-ok)" />
              <Sparkline data={frames.map((f) => f.acceleration_g)} stroke="var(--color-warn)" />
              <div className="telemetry-legend">
                <span className="telemetry-legend__item" style={{ color: 'var(--color-ok)' }}>Z-height [mm]</span>
                <span className="telemetry-legend__item" style={{ color: 'var(--color-warn)' }}>G-load</span>
              </div>
            </section>
          </div>
        ) : null}

        {tab === 'events' ? (
          events.length === 0 ? (
            <p className="telemetry-empty">No diagnostic events recorded.</p>
          ) : (
            <div className="telemetry-events">
              {events.map((e, i) => (
                <div key={i} className={`telemetry-event telemetry-event--${EVENT_TONE[e.level].toLowerCase()}`}>
                  <span className={`badge badge--${EVENT_TONE[e.level]}`}>{e.level}</span>
                  <span className="telemetry-event__time">{e.t_s.toFixed(3)}s</span>
                  <span className="telemetry-event__code">{e.code}</span>
                  <span className="telemetry-event__msg">{e.message}</span>
                </div>
              ))}
            </div>
          )
        ) : null}

        {tab === 'export' ? (
          <div className="telemetry-export">
            <p className="telemetry-export__hint">
              Export the full telemetry session ({frames.length.toLocaleString()} frames) for offline analysis.
            </p>
            <div className="telemetry-export__actions">
              <button
                type="button"
                className="btn"
                disabled={!session}
                onClick={() => session && exportSessionJson(session)}
              >
                Export JSON
              </button>
              <button
                type="button"
                className="btn"
                disabled={!session}
                onClick={() => session && exportFramesCsv(session)}
              >
                Export CSV
              </button>
              <button
                type="button"
                className="btn"
                disabled={frames.length === 0}
                onClick={() => frames.length > 0 && handleCopy(frames[frames.length - 1])}
              >
                {copied ? 'Copied!' : 'Copy last frame'}
              </button>
            </div>
            <div className="telemetry-export__meta">
              <SpecRow label="Session" value={session?.session_id ?? '—'} />
              <SpecRow label="Duration" value={summary ? `${fmt(summary.duration_s, 2)} s` : '—'} />
              <SpecRow label="Frames" value={summary ? String(summary.total_frames) : '—'} />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
