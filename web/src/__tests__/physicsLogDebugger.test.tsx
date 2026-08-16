/**
 * PhysicsLogDebugger component test — open/close wiring, tab switching,
 * and live telemetry frame streaming from the viewport runtime.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { RefObject } from 'react';
import type { SceneViewportHandle } from '../scene/SceneViewport';
import { ProjectProvider, useProjectStore } from '../state/projectStore';
import { PhysicsLogDebugger } from '../components/PhysicsLogDebugger';
import type { TelemetryFrame, TelemetryEvent } from '../api/telemetryDebuggerContracts';

function frame(index: number, status: TelemetryFrame['status'] = 'free_fall'): TelemetryFrame {
  return {
    index,
    t_s: index * 0.02,
    status,
    position_m: [0, 0, 0.75 - index * 0.01],
    quaternion_wxyz: [1, 0, 0, 0],
    velocity_m_s: [0, 0, -index * 0.1],
    angular_velocity_rad_s: [0, 0, 0],
    acceleration_g: index === 0 ? 1 : 0,
    energy_j: {
      kinetic_trans: 0.001 * index,
      kinetic_rot: 0,
      potential: 0.169 - 0.001 * index,
      total: 0.169,
      dissipated: 0.001 * index,
    },
    contact: { active: index > 0 },
  };
}

const EVENTS: TelemetryEvent[] = [
  { level: 'EVENT', code: 'DROP_START', t_s: 0, message: 'Drop initiated at 0.75 m' },
  { level: 'ANOMALY', code: 'IMPACT_PEAK', t_s: 0.4, message: 'Peak deceleration 42.1 g' },
];

/** The debugger mounted as ViewportToolbar mounts it: a toolbar popover child. */
function DebuggerFixture({ viewportRef }: { viewportRef: RefObject<SceneViewportHandle | null> }) {
  return (
    <ProjectProvider>
      <OpenDebuggerHelper />
      <div className="viewport-toolbar__log is-open" role="group" aria-label="Telemetry debugger">
        <PhysicsLogDebugger viewportRef={viewportRef} />
      </div>
    </ProjectProvider>
  );
}

/** Flips debuggerOpen to true on mount, mirroring the Log button's dispatch. */
function OpenDebuggerHelper() {
  const { dispatch } = useProjectStore();
  return (
    <button type="button" onClick={() => dispatch({ type: 'SET_DEBUGGER_OPEN', open: true })}>
      open-debugger
    </button>
  );
}

/** Store probe sharing the fixture's provider; reports debuggerOpen state. */
function DebuggerStateProbe() {
  const { state } = useProjectStore();
  return <span>{`debuggerOpen: ${state.debuggerOpen}`}</span>;
}

function makeViewportRef() {
  const frames = [frame(0), frame(1), frame(2)];
  const seekDropTime = vi.fn();
  const ref: RefObject<SceneViewportHandle | null> = {
    current: {
      getTelemetryFrames: () => frames,
      getTelemetryEvents: () => EVENTS,
      seekDropTime,
    } as unknown as SceneViewportHandle,
  };
  return { ref, frames, seekDropTime };
}

describe('PhysicsLogDebugger', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('renders all five tabs and the model specs on the overview tab', () => {
    const { ref } = makeViewportRef();
    render(<DebuggerFixture viewportRef={ref} />);
    fireEvent.click(screen.getByRole('button', { name: 'open-debugger' }));
    for (const label of ['Overview', 'Stream', 'Charts', 'Events', 'Export']) {
      expect(screen.getByRole('tab', { name: label })).toBeTruthy();
    }
    expect(screen.getByText('Model & Mass Properties')).toBeTruthy();
    expect(screen.getByText(/Material/)).toBeTruthy();
  });

  it('switches tabs: stream empty state, events empty state, and disabled export actions', () => {
    const emptyRef: RefObject<SceneViewportHandle | null> = { current: null };
    render(<DebuggerFixture viewportRef={emptyRef} />);
    fireEvent.click(screen.getByRole('button', { name: 'open-debugger' }));

    fireEvent.click(screen.getByRole('tab', { name: 'Stream' }));
    expect(screen.getByText(/No frames recorded yet/)).toBeTruthy();

    fireEvent.click(screen.getByRole('tab', { name: 'Events' }));
    expect(screen.getByText('No diagnostic events recorded.')).toBeTruthy();

    fireEvent.click(screen.getByRole('tab', { name: 'Export' }));
    const jsonBtn = screen.getByRole('button', { name: 'Export JSON' });
    expect(jsonBtn).toBeTruthy();
    expect(jsonBtn.hasAttribute('disabled')).toBe(true);
  });

  it('streams telemetry frames from the viewport runtime into the table', async () => {
    const { ref } = makeViewportRef();
    render(<DebuggerFixture viewportRef={ref} />);
    fireEvent.click(screen.getByRole('button', { name: 'open-debugger' }));

    // The mount poll already fed the runtime frames in; the table streams them.
    fireEvent.click(screen.getByRole('tab', { name: 'Stream' }));
    expect(screen.getByText('3 frames · click a row to scrub')).toBeTruthy();

    // Frame 1's Z height (0.73 m) renders as 730.0 mm in the table.
    expect(screen.getByText('730.0')).toBeTruthy();
  });

  it('streams diagnostic events from the runtime into the events tab', () => {
    const { ref } = makeViewportRef();
    render(<DebuggerFixture viewportRef={ref} />);
    fireEvent.click(screen.getByRole('button', { name: 'open-debugger' }));

    fireEvent.click(screen.getByRole('tab', { name: 'Events' }));
    expect(screen.getByText('Drop initiated at 0.75 m')).toBeTruthy();
    expect(screen.getByText('Peak deceleration 42.1 g')).toBeTruthy();
  });

  it('scrubbing a frame seeks the 3D viewport to that time', () => {
    const { ref, seekDropTime, frames } = makeViewportRef();
    render(<DebuggerFixture viewportRef={ref} />);
    fireEvent.click(screen.getByRole('button', { name: 'open-debugger' }));

    fireEvent.click(screen.getByRole('tab', { name: 'Stream' }));
    // Frame 1 (t=0.02 s) has Z = 0.74 m → 740.0 mm; click its row to scrub.
    const row = screen.getByText('740.0').closest('tr');
    expect(row).not.toBeNull();
    fireEvent.click(row as HTMLElement);

    expect(seekDropTime).toHaveBeenCalledTimes(1);
    expect(seekDropTime).toHaveBeenCalledWith(frames[1].t_s);
  });

  it('closes the popover via the close button (store flips debuggerOpen)', () => {
    const { ref } = makeViewportRef();
    render(
      <ProjectProvider>
        <OpenDebuggerHelper />
        <DebuggerStateProbe />
        <div className="viewport-toolbar__log is-open" role="group" aria-label="Telemetry debugger">
          <PhysicsLogDebugger viewportRef={ref} />
        </div>
      </ProjectProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'open-debugger' }));
    expect(screen.getByText('debuggerOpen: true')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Close log'));
    expect(screen.getByText('debuggerOpen: false')).toBeTruthy();
  });

  it('closes the popover when clicking outside of it', () => {
    const { ref } = makeViewportRef();
    render(
      <ProjectProvider>
        <OpenDebuggerHelper />
        <DebuggerStateProbe />
        <div className="viewport-toolbar__log is-open" role="group" aria-label="Telemetry debugger">
          <PhysicsLogDebugger viewportRef={ref} />
        </div>
      </ProjectProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'open-debugger' }));
    fireEvent.pointerDown(document.body);
    expect(screen.getByText('debuggerOpen: false')).toBeTruthy();
  });
});
