# Handoff Prompt — Fix the Unrealistic/Buggy Drop Animation (for the next AI agent)

You are taking over a gaming-mouse drop-simulation platform. The user is
dissatisfied: **the 3D drop animation is still buggy and unrealistic**, and
previous fix attempts did not fully resolve it. Your job is to diagnose and
fix the remaining issues. **Read this entire document first, then inspect the
code yourself. Do not trust memory. Do not rewrite the simulator.**

---

## 1. The user's exact complaints (verbatim intent)

1. "When the mouse falls to the ground, **how it's interpreted / how it
   bounces** is not realistic."
2. "**When it falls onto the top shell it starts bugging**" — the
   on-the-back / dome-down landing visibly misbehaves (jitter, weird
   rocking, or a non-physical settle).
3. "**The next drop should start when the mouse stops moving, and after that
   wait 0.5 s.**" — the inter-drop pacing must be tied to actual motion
   stopping + a 0.5 s pause, not to the current arbitrary schedule.
4. The 3D preview must be a **1:1 representation of the backend trajectory** —
   no frontend embellishment, no artificial smoothing, no snapping.

---

## 2. Repo facts (verify, don't trust)

- Repo: `/Users/macbook/Downloads/mousetesting` — deterministic,
  **stdlib-only Python 3.9** backend; React/Vite/Three.js frontend in `web/`.
- Backend simulator: `mouse_sim/drop_sim.py` (`_simulate_drop`, `simulate`).
- Playback: `web/src/scene/sceneRuntime.ts` (`applyDropTransform`,
  `resolveDropSample`, lerp/slerp, floor lift), `web/src/components/SceneViewport.tsx`,
  `web/src/state/projectStore.ts`, `web/src/App.tsx`.
- Servers (currently running; **restart the backend after any backend
  change** — Python binds imports at startup):
  - Backend: `PYTHONPATH=. python3 -m mouse_sim serve --project-root .` on **:8000**
  - Frontend: `cd web && npx vite` on **:5173** (proxies `/api` → :8000)
- Tests:
  - Backend: `PYTHONPATH=. python3 -m pytest -q tests/` — currently
    **1136 passed, 2 skipped, 3 failed** (the 3 failures are
    `tests/test_shell_baseline.py` engine-hash/digest/trace — they are
    PRE-EXISTING from earlier uncommitted rounds, NOT caused by the drop
    changes; do not "fix" them by re-freezing baselines without a
    deliberate, verified physics change).
  - Frontend: `cd web && NODE_ENV=test npx vitest run` — **202/202 passed**
    (note: without `NODE_ENV=test` the React prod build breaks the
    testing-library `act()` — always use the env var).

---

## 3. THE CRITICAL REPRODUCTION PATH (verified this session)

The browser does NOT send inline geometry. It sends
`geometry_asset_id` + per-part metadata only; the server resolves the
**46-part G3 assembly** (`G3-20260320.stp`). Single-mesh scratch tests do
NOT reproduce the user's case — the real mass model differs:
`mass_kg = 0.28867`, `com_offset_m = [0.000143, -0.003114, 0.017019]`,
14 support points.

The G3 asset id: `6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404`
(registered in the temp asset dir; `register_existing_assets(...)` loads it).

**Exact browser request to reproduce (POST http://localhost:8000/api/analyze):**

```json
{
  "schema_id": "gms.web-analysis-request/1",
  "request": {
    "schema_id": "gms.project/1",
    "geometry_asset_id": "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404",
    "drop_simulation": {
      "test": "drop", "height_m": 0.75, "surface": "concrete",
      "drop_count": 3, "orientation": "flat"
    },
    "options": {"display_tessellation": true}
  },
  "options": {"strict": false, "use_cache": false}
}
```

Local equivalent: resolve the asset objects via
`mouse_sim.web_api._load_registered_asset_objects(asset_id)` (after
`register_existing_assets(<temp asset dir>)`) and pass them as
`request["objects"]` to `mouse_sim.pipeline.run_pipeline`.

Default browser drop test config (`web/src/lib/studies.ts` + MissionControl):
**flat, 0.75 m, concrete, drop_count 3, spin 0**.

Measured baseline of the default flat drop (current code):
- Drop 0: settle 2.296 s; body visually STOPS at ~0.683 s (rest capture)
  but the drop "ends" at 2.296 s (see issue below); 97 frozen samples.
- Drops 1–2: settle 1.887 s; frozen tails 48/49 samples.
- No quaternion snaps remain (a previous fix made the base-leveling a
  smooth 0.4 s ramp), but the **long frozen-tail dead time + the delayed
  settle** are still wrong.

---

## 4. Current code state (uncommitted changes on top of earlier rounds)

All changes are uncommitted in the working tree. The two drop-related
changes made this session (both in `mouse_sim/drop_sim.py`):

1. **Quiet stand-down gate rework** (in `_simulate_drop`):
   the quiet-pin condition is now
   `up_world[2] < 0.7 and |v| < 0.15 and |w| < 2.0 and pose_drift < 0.02`
   (with a `quiet_anchor_up` anchor), NOT gated on the instantaneous
   `acceptance_rejected` (which toggles frame-to-frame on the curved
   surfaces and defeated the pin). It pins a rocking-in-place metastable
   rest honestly (frozen `DROP_SIM_DID_NOT_SETTLE` tail).
2. **Smooth base-leveling ramp**: the rest-pose base-leveling (the
   "flat-drop tilt" correction) is now interpolated over
   `LEVEL_RAMP_S = 0.4 s` via a new `_slerp` helper
   (`level_start` / `level_from_origin` / `level_from_quaternion` state),
   instead of an instantaneous teleport at rest capture.

The escape torque (gravity torque about the contact, persisted axis,
seeded exact-balance lever, per-frame `alpha*dt` velocity step) is the
previous rounds' design — do NOT remove it without proof.

---

## 5. Known suspects / investigation guidance (in order)

### A. The drop-to-drop timing (user requirement #3) — highest priority, concrete

In `mouse_sim/drop_sim.py`, `simulate()` (multi-drop loop):
- `drop_interval_s = 0.35` — the gap between the reported `settled` time
  and the next drop's `start_s` (`t_offset += settled + drop_interval_s`).
- **Problem 1**: `settled` for the flat drop is 2.296 s even though the
  body visually stops at ~0.683 s (rest capture). The extra ~1.6 s is the
  near-base rest-attempt loop: the settle attempt runs
  `rest_acceptance()` on the LIVE (unleveled, tilted) quaternion; the
  hull test rejects the tilted rocker-keel rest, so `rest_time` keeps
  resetting (`up >= 0.7` branch) until the acceptance finally passes at
  2.296 s. The rest pose is already captured and frozen — the settle
  should fire at/near the rest capture, not 1.6 s later. Investigate why
  the acceptance can't accept the captured/leveled pose and fix the
  settle-attempt to use the captured rest pose (or accept the leveled
  pose), so `settled ≈ rest-capture time`.
- **Problem 2**: the user wants the next drop to start **when the mouse
  stops moving + 0.5 s**. Define "stops moving" = the rest-capture moment
  (last sample with actual motion). The current `0.35 s` gap plus the
  delayed settle produces ~2 s of frozen dead time before the next drop —
  perceived as broken. Fix: next drop `start_s = motion_stop_time + 0.5`.
  Check the frontend too: `web/src/scene/sceneRuntime.ts`
  `resolveDropSample` holds the previous sample during gaps
  (`b[0]-a[0] > 2/TRAJECTORY_HZ`), and `dropTrajectoryBounds`/playback
  clock use the last sample's timestamp — the trajectory timestamps
  fully control the pacing, so this is primarily a BACKEND timing fix.

### B. The bounce realism (user complaint #1)

The flat drop's post-impact motion: impact at ~0.383 s (3.80 m/s),
rebound (restitution 0.30 → ~6.8 cm apex), then a **~0.3 s rocking tail**
on the curved rocker keel (single-point manifold at `[-0.0035, 0.049]`)
before rest capture at 0.683 s. The user says the bounce/landing is
unrealistic. Investigate:
- The 60 Hz samples of the bounce: check the sample-to-sample z profile
  during the bounce (is the apex resolved? is the rebound shape
  parabolic? does the contact look like an instant "snap" at 60 Hz?).
- The rocking tail: a real mouse dropped with ≤6° jitter tilt lands and
  settles in 1–2 small bounces; the model rocks ~0.3 s on the keel
  (the near-upright face gate `up>0.9 and v_z<-0.05` zeroes the lever at
  the FIRST impact, but the subsequent keel contacts at `up<0.9` keep a
  lever and rock the body). Consider whether the near-upright gate should
  apply to ALL near-upright single-point contacts (not only `v_z<-0.05`),
  or whether the base-leveling should start at first rest-contact rather
  than at rest capture. **Measure before changing**: log the per-frame
  pose/velocity in `_simulate_drop` during 0.38–0.7 s and the exact
  sample profile the frontend renders.

### C. The on-the-back / top-shell case (user complaint #2)

"When it falls onto the top shell it starts bugging" = landing on the
rounded back/dome. Reproduce with the REAL assembly and an explicit
180°-about-X pose (`{"quaternion_wxyz": [0,1,0,0]}` drops the dome onto
the floor), plus 6° and 12° tilts, and random drops that end on the back.
Check:
- Does the body rock on the dome apex (escape torque limit cycle)?
- Does the quiet stand-down pin it with a genuinely frozen tail, or does
  it rock to the 8 s budget?
- Is there residual visible motion in the last 1 s of samples
  (quaternion spread > ~1e-3)?
- The honest outcomes per earlier rounds: (a) gravity tips it to a face
  and settles, or (b) `DROP_SIM_DID_NOT_SETTLE` with the body genuinely
  STILL — never a jittering frozen pose. The escape torque's per-frame
  `alpha*dt` step (~0.5 rad/s) on the dome is the known rocking engine;
  the quiet stand-down is the known pin. Verify both work on the REAL
  assembly (the previous session only verified a scratch box model).

### D. Frontend playback (verify, do not assume)

- `applyDropTransform` renders exactly the trajectory samples (lerp
  position, slerp quaternion, `-0.001` floor tolerance). Confirm the
  browser shows exactly the backend samples — no extra smoothing, no
  snap, no floor-lift fighting the rest pose.
- The floor lift (`floorCorrectionForModel`) must be 0 at rest (the
  leveled pose sits at z=0.0007, above the floor) — verify per drop.
- The `dropTrajectoryBounds` camera framing and the playback wall-clock
  advance must not distort timing.

---

## 6. Hard constraints

- **Smallest technically justified changes only.** Prove the root cause
  with numbers before editing. If an experiment makes things worse,
  revert it — do not stack compensating hacks.
- **Real physics, no calibration-to-look-good.** No arbitrary torque
  clamps, no velocity snapping to make the preview "look nicer".
- Do NOT remove the quiet stand-down or the gravity-torque tipping
  without proof; they fixed earlier "artificial kick" complaints.
- Preserve determinism (byte-identical outputs for identical inputs),
  the exact ledger closure, and the API/data formats.
- Do NOT regenerate `reference/*.json` unless the change is intentional
  and verified. The 3 pre-existing `test_shell_baseline` failures are
  unrelated to the drop animation — leave them alone.
- Do NOT touch the frontend to hide backend artifacts — the backend
  trajectory must be correct; the frontend renders it 1:1.

---

## 7. Verification required

- Test matrix after every meaningful change (backend trajectory + live
  `/api/analyze` + browser playback):
  flat, front/rear pitch, left/right roll, corner, edge, inverted
  (180°-about-X), and several random orientations — using the REAL
  assembly request from section 3.
- The on-the-back case must show: natural impact, decaying motion (or a
  clean gravity tip to a face), and a genuinely motionless final state —
  never visible jitter.
- The inter-drop pacing must be: motion stops → 0.5 s pause → next drop
  starts. Measure the actual sample timestamps.
- Full backend suite green (`PYTHONPATH=. python3 -m pytest -q tests/`),
  frontend suite green (`cd web && NODE_ENV=test npx vitest run`),
  baselines consistent.
- Restart the backend server and confirm `/api/analyze` matches the local
  pipeline; refresh the browser and confirm the preview matches the
  trajectory frame-by-frame (log the sample the browser renders vs the
  backend sample at the same t).

---

## 8. Deliverable

Report: root cause (backend or frontend, with file:line and measured
evidence), every change made and why it is physically correct, the
test-matrix results, live verification, and any remaining risks.
No commits unless asked.
