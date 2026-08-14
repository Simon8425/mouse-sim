/**
 * Drop Physics Debug Overlay
 *
 * A visible, detailed physics readout rendered on top of the 3D viewport
 * during drop playback.  It derives every value from the BACKEND trajectory
 * samples and drop records (1:1 with what the scene renders — nothing is
 * guessed or re-simulated client-side):
 *
 *   - per-frame: time, position (x,y,z), horizontal speed, vertical speed,
 *     total speed, rotation rate (quaternion delta / dt), frame-to-frame
 *     pose delta
 *   - per-drop: start/end/settle times, motion-stop time (last sample that
 *     differs from the frozen tail), settled flag, impact count, peak
 *     impact speed, peak energy, restitution/friction, mass/CoM/inertia
 *   - impact list: each impact's time, speed, energy, contact location
 *   - live velocity/rotation readout of the CURRENT playback frame
 *
 * The overlay is intentionally information-dense (monospace, tabular) so a
 * screen recording captures the real physics numbers for diagnosis.
 */
import * as React from 'react';
import type {
  DropSimulationResult,
  DropSimulationImpact,
  DropTrajectorySample,
} from '../api/contracts';
import { TRAJECTORY_HZ } from '../scene/sceneRuntime';

interface LiveFrame {
  t: number;
  pos: [number, number, number];
  hSpeed: number;
  vSpeed: number;
  speed: number;
  rotRate: number;
  dq: number;
  settled: boolean;
}

function quatDist(a: number[], b: number[]): number {
  let d = 0;
  for (let i = 0; i < 4; i += 1) {
    const diff = a[i] - b[i];
    d += diff * diff;
  }
  return Math.sqrt(d);
}

export function deriveLiveFrame(
  samples: DropTrajectorySample[],
  t: number,
): LiveFrame | null {
  if (samples.length === 0) return null;
  // Find the bracketing samples (same logic as the scene playback).
  let low = 0;
  let high = samples.length - 1;
  if (t <= samples[0][0]) {
    low = 0;
    high = 0;
  } else {
    while (low < high) {
      const mid = (low + high + 1) >> 1;
      if (samples[mid][0] <= t) low = mid;
      else high = mid - 1;
    }
  }
  const a = samples[low];
  const b = low < samples.length - 1 ? samples[low + 1] : a;
  // Same inter-drop gap rule as the scene playback (resolveDropSample):
  // a gap larger than 2/60 s means the NEXT DROP's teleport (or a frozen
  // tail gap) — the model holds the current sample, it does NOT move
  // between them.  Interpolating across the gap would fabricate a huge
  // velocity/rotation-rate spike (the rest pose to the next drop's
  // release pose is not motion).  Hold the pose and report zero motion.
  if (b[0] - a[0] > 2 / TRAJECTORY_HZ) {
    return {
      t,
      pos: [a[1], a[2], a[3]],
      hSpeed: 0,
      vSpeed: 0,
      speed: 0,
      rotRate: 0,
      dq: 0,
      settled: true,
    };
  }
  const dt = Math.max(1e-9, b[0] - a[0]);
  const alpha = Math.min(1, Math.max(0, (t - a[0]) / dt));
  const pos: [number, number, number] = [
    a[1] + (b[1] - a[1]) * alpha,
    a[2] + (b[2] - a[2]) * alpha,
    a[3] + (b[3] - a[3]) * alpha,
  ];
  // Per-sample velocity (finite difference over the 60 Hz frame).
  const hSpeed = Math.hypot(b[1] - a[1], b[2] - a[2]) / dt;
  const vSpeed = (b[3] - a[3]) / dt;
  const speed = Math.hypot(b[1] - a[1], b[2] - a[2], b[3] - a[3]) / dt;
  const dq = quatDist(a.slice(4), b.slice(4));
  const rotRate = dq / dt;
  return { t, pos, hSpeed, vSpeed, speed, rotRate, dq, settled: false };
}

function fmt(v: number, digits = 3): string {
  if (!Number.isFinite(v)) return '—';
  return v.toFixed(digits);
}

export function DropPhysicsDebug({
  simulation,
  dropTime,
}: {
  simulation: DropSimulationResult | null;
  dropTime: number;
}): React.ReactElement | null {
  const [show, setShow] = React.useState(true);
  if (!simulation) return null;

  const samples = simulation.trajectory;
  const live = deriveLiveFrame(samples, dropTime);

  // Active drop (largest start_s <= t).
  let activeDrop: (typeof simulation.drops)[number] | null = null;
  for (const d of simulation.drops) {
    if (d.start_s <= dropTime) activeDrop = d;
    else break;
  }
  activeDrop = activeDrop ?? simulation.drops[0] ?? null;

  // Motion-stop per drop: last sample that differs from the frozen tail.
  const motionStopFor = (dropIndex: number): number => {
    const start = simulation.drops[dropIndex]?.start_s ?? 0;
    const end = simulation.drops[dropIndex]?.end_s ?? Infinity;
    const seg = samples.filter((s) => s[0] >= start - 1e-6 && s[0] <= end + 1e-6);
    if (seg.length < 2) return start;
    let lastChange = 0;
    for (let i = 1; i < seg.length; i += 1) {
      if (
        quatDist(seg[i - 1].slice(4), seg[i].slice(4)) > 1e-9 ||
        Math.hypot(
          seg[i][1] - seg[i - 1][1],
          seg[i][2] - seg[i - 1][2],
          seg[i][3] - seg[i - 1][3],
        ) > 1e-9
      ) {
        lastChange = i;
      }
    }
    return seg[lastChange][0];
  };

  const impacts = simulation.impacts.filter(
    (im) => activeDrop && im.drop === activeDrop.index,
  );

  const model = simulation.model;

  return (
    <div className="drop-debug" role="region" aria-label="Drop physics debug overlay">
      <div className="drop-debug__header">
        <span className="drop-debug__title">PHYSICS DEBUG</span>
        <button
          type="button"
          className="drop-debug__toggle"
          aria-label={show ? 'Hide physics debug overlay' : 'Show physics debug overlay'}
          onClick={() => setShow((s) => !s)}
        >
          {show ? 'HIDE' : 'SHOW'}
        </button>
      </div>
      {show ? (
        <div className="drop-debug__body">
          {/* LIVE frame readout */}
          <div className="drop-debug__section">
            <div className="drop-debug__section-title">LIVE FRAME</div>
            <table className="drop-debug__table">
              <tbody>
                <tr>
                  <td>t</td>
                  <td>{fmt(dropTime, 3)} s</td>
                </tr>
                <tr>
                  <td>pos (x,y,z)</td>
                  <td>
                    {live ? `${fmt(live.pos[0])}, ${fmt(live.pos[1])}, ${fmt(live.pos[2])}` : '—'}
                  </td>
                </tr>
                <tr>
                  <td>v_horiz</td>
                  <td>{live ? `${fmt(live.hSpeed)} m/s` : '—'}</td>
                </tr>
                <tr>
                  <td>v_vert</td>
                  <td>{live ? `${fmt(live.vSpeed)} m/s` : '—'}</td>
                </tr>
                <tr>
                  <td>v_total</td>
                  <td>{live ? `${fmt(live.speed)} m/s` : '—'}</td>
                </tr>
                <tr>
                  <td>rot rate</td>
                  <td>{live ? `${fmt(live.rotRate)} rad/s` : '—'}</td>
                </tr>
                <tr>
                  <td>Δq/frame</td>
                  <td>{live ? fmt(live.dq, 5) : '—'}</td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* Active drop summary */}
          {activeDrop ? (
            <div className="drop-debug__section">
              <div className="drop-debug__section-title">
                DROP {activeDrop.index + 1}/{simulation.drops.length} —{' '}
                {activeDrop.settled ? 'SETTLED' : 'NOT SETTLED'}
              </div>
              <table className="drop-debug__table">
                <tbody>
                  <tr>
                    <td>start</td>
                    <td>{fmt(activeDrop.start_s, 3)} s</td>
                  </tr>
                  <tr>
                    <td>end</td>
                    <td>{fmt(activeDrop.end_s, 3)} s</td>
                  </tr>
                  <tr>
                    <td>settled_s</td>
                    <td>{fmt(activeDrop.settled_s, 3)} s</td>
                  </tr>
                  <tr>
                    <td>motion stop</td>
                    <td>{fmt(motionStopFor(activeDrop.index), 3)} s</td>
                  </tr>
                  <tr>
                    <td>impacts</td>
                    <td>{activeDrop.impact_count}</td>
                  </tr>
                  <tr>
                    <td>peak impact</td>
                    <td>{fmt(activeDrop.peak_impact_speed_m_s)} m/s</td>
                  </tr>
                  <tr>
                    <td>peak energy</td>
                    <td>{fmt(activeDrop.peak_kinetic_energy_j, 4)} J</td>
                  </tr>
                  <tr>
                    <td>release E</td>
                    <td>{activeDrop.energy?.release_j != null ? fmt(activeDrop.energy.release_j, 4) : '—'} J</td>
                  </tr>
                  <tr>
                    <td>settled E</td>
                    <td>{activeDrop.energy?.settled_j != null ? fmt(activeDrop.energy.settled_j, 4) : '—'} J</td>
                  </tr>
                  <tr>
                    <td>drift</td>
                    <td>{activeDrop.energy?.drift_pct != null ? `${fmt(activeDrop.energy.drift_pct, 3)}%` : '—'}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          ) : null}

          {/* Model / surface */}
          <div className="drop-debug__section">
            <div className="drop-debug__section-title">MODEL</div>
            <table className="drop-debug__table">
              <tbody>
                <tr>
                  <td>mass</td>
                  <td>{fmt(model.mass_kg, 4)} kg</td>
                </tr>
                <tr>
                  <td>CoM</td>
                  <td>
                    {model.com_offset_m
                      ? model.com_offset_m.map((v) => fmt(v)).join(', ')
                      : '—'}
                  </td>
                </tr>
                <tr>
                  <td>surface</td>
                  <td>{model.surface}</td>
                </tr>
                <tr>
                  <td>restitution</td>
                  <td>{model.restitution != null ? fmt(model.restitution) : '—'}</td>
                </tr>
                <tr>
                  <td>friction</td>
                  <td>{model.friction != null ? fmt(model.friction) : '—'}</td>
                </tr>
                <tr>
                  <td>inertia Ixx</td>
                  <td>
                    {model.inertia_kg_m2?.[0]?.[0] != null
                      ? fmt(model.inertia_kg_m2[0][0], 6)
                      : '—'}{' '}
                    kg·m²
                  </td>
                </tr>
                <tr>
                  <td>support pts</td>
                  <td>{model.support_point_count}</td>
                </tr>
                <tr>
                  <td>integrator</td>
                  <td>{model.integrator} @ {fmt(model.timestep_s, 5)} s</td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* Impacts */}
          <div className="drop-debug__section">
            <div className="drop-debug__section-title">IMPACTS ({impacts.length})</div>
            <table className="drop-debug__table">
              <thead>
                <tr>
                  <th>t</th>
                  <th>v</th>
                  <th>E</th>
                  <th>loc</th>
                </tr>
              </thead>
              <tbody>
                {impacts.length === 0 ? (
                  <tr>
                    <td colSpan={4}>—</td>
                  </tr>
                ) : (
                  impacts.map((im: DropSimulationImpact, i: number) => (
                    <tr key={i}>
                      <td>{fmt(im.t_s, 3)}</td>
                      <td>{fmt(im.impact_speed_m_s)}</td>
                      <td>{fmt(im.kinetic_energy_j, 4)}</td>
                      <td>
                        {im.contact_location
                          ? im.contact_location.map((v) => fmt(v)).join(', ')
                          : '—'}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {/* Checks */}
          {activeDrop?.checks && activeDrop.checks.length > 0 ? (
            <div className="drop-debug__section">
              <div className="drop-debug__section-title">CHECKS</div>
              <ul className="drop-debug__checks">
                {activeDrop.checks.map((c, i) => (
                  <li key={i} className={`drop-debug__check drop-debug__check--${c.severity}`}>
                    {c.code}: {c.message}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
