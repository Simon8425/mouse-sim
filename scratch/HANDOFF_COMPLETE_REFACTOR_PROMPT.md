# HANDOFF — Mouse Drop Test Still Appears Broken to the User: Full-State Refactor Prompt

You are taking over a gaming-mouse drop-simulation web app. The user is
furious: despite multiple fix rounds, they say "absolute fucking nothing has
been fixed" and the drop animation still looks broken in their browser. Your
job is to find out WHY the user's browser shows the old behavior when the code
has been verified fixed, and do whatever complete refactor is needed to make
the drop test genuinely work — every drop falls from 0.75 m, bounces, settles,
0.5 s pacing, no ground-spawn, no jitter, no DID_NOT_SETTLE.

**Read this entire document first. Then inspect the code and RUN the app
yourself in a browser. Do not trust memory. Do not assume the code you see is
what the user's browser runs.**

---

## 0. The user's exact complaint (verbatim intent)

- "Absolute fucking nothing has been fixed" — the browser STILL shows the old
  broken behavior: drops spawning on the ground, teleporting, no fall
  animation, model stuck in tilted/ground poses.
- They want a COMPLETE REFACTOR if that's what it takes — but the code has
  been verified working in tests, so the #1 suspect is that the user's browser
  is running STALE code.

---

## 1. THE CRITICAL FIRST CHECK: is the user's browser running current code?

The code is verified (all tests pass, live API returns correct data), so a
"nothing changed" report almost always means the browser is not running it.

**Do these in order:**

1. **Browser hard-reload with cache disabled.** The vite dev server serves
   modules with hashed URLs, but the browser can cache aggressively. Have the
   user (or you, via a headless browser) do:
   - `Cmd+Shift+R` (hard reload) in the browser.
   - Or open DevTools → Network → "Disable cache" → reload.
   - Or open in an Incognito window.
2. **Verify the served JS is current.** In the browser DevTools → Sources,
   check that `/src/scene/rapierDropSim.ts` exists and contains the Rapier
   code. If the browser shows an old bundle WITHOUT rapier, it's a cache/URL
   issue.
3. **Check WHICH server the browser hits.** The app may be opened via:
   - `http://localhost:5173` (vite dev, proxies /api → 127.0.0.1:8000) — CORRECT
   - `http://127.0.0.1:5173` (same, also correct)
   - `http://localhost:8898` or another port (an OLD single-process serve that
     has a STALE `web/dist` — this is the classic "nothing changed" trap!)
   - Any other URL → the user is looking at a stale build.
   The README's "production single-process serve" uses `python3 -m mouse_sim
   serve --web-dist web/dist` on port 8898 — if a STALE server from weeks ago
   is still running on 8898 (or the user has it bookmarked), they will ALWAYS
   see old behavior. Kill every server and run ONLY the current ones.
4. **Confirm the API returns the fixed trajectory.** `curl
   http://127.0.0.1:8000/api/health` and the 10-drop analyze request (below)
   must show `support_model: convex_hull`, `support_point_count: 156`, all
   drops `settled=True`, non-overlapping `start_s`.

**If the browser IS running current code and still shows broken behavior,
proceed to section 5 (the Rapier runtime fallback) — a silent runtime error in
the Rapier path falls back to sample playback, which can look broken.**

---

## 2. Repo facts (verify, don't trust)

- Repo: `/Users/macbook/Downloads/mousetesting` — deterministic stdlib-only
  Python 3.9 backend (`mouse_sim/`), React 18 + Vite + Three.js frontend
  (`web/`).
- The G3 mouse assembly (`G3-20260320.stp`, 46 parts) is the real test case.
  Asset id: `6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404`.
- Servers (the ONLY ones that should run):
  - Backend: `PYTHONPATH=. python3 -m mouse_sim serve --project-root . --host 127.0.0.1 --port 8000 --cache-dir .web-cache`
  - Frontend: `cd web && npx vite --host 127.0.0.1 --port 5173`
- Tests (all GREEN as of the last verified state — re-run to confirm):
  - Backend: `PYTHONPATH=. python3 -m pytest tests/ -q` → 1146 passed, 2 skipped.
  - Frontend: `cd web && NODE_ENV=test npx vitest run` → 209 passed.
  - Typecheck: `cd web && npm run typecheck` → clean. Build: `npm run build` → clean.
- NOTE: `npm config get omit` returns `dev` on this machine — if you ever
  `npm install` without `--include=dev`, devDependencies get pruned and
  typecheck/tests break. Always use `npm install --include=dev`.

---

## 3. What has ALREADY been fixed and verified (do NOT regress these)

### 3a. Backend: convex-hull contact model (`mouse_sim/drop_sim.py`)
- `support_points()` now computes the TRUE 3D convex hull of the assembly
  vertex cloud (new `convex_hull_3d`, incremental stdlib-only; candidate
  reduction for speed). The old 14-direction sampling produced a degenerate
  tripod → tilted dome rests.
- Result: `support_model: "convex_hull"`, 156 support points for the G3.
- All G3 orientations settle (flat/edge/corner/inverted/random) with no
  `DROP_SIM_DID_NOT_SETTLE` (a "sustained-still rest certification" was added:
  a body genuinely still for 0.4 s certifies settled=True even on a non-base
  pose).

### 3b. Backend: multi-drop timeline (the "spawns on ground" root cause)
- `motion_stop_s` had a degenerate case returning 0.0 for frozen trajectories,
  collapsing the timeline and overlapping drops → the frontend gap-hold hid the
  release samples and the model appeared to "spawn on the ground."
- FIXED: motion_stop falls back to `settled`, and the timeline advance has a
  floor `max(motion_stop, sqrt(2h/g)) + 0.5`.
- Verified: 10-drop live run — all 10 drops start at z≈0.75, settle, 0.5 s
  gaps, no overlaps.

### 3c. Frontend: Rapier.js live physics animation (the "complete refactor")
- `@dimforge/rapier3d-compat` 0.20 added (WASM auto-embedded, lazy dynamic
  import — no vite config).
- New `web/src/scene/rapierDropSim.ts`: builds a Rapier world (Z-up gravity
  for scene parity), fixed ground collider (backend restitution/friction),
  convex-hull collider from display mesh vertices, dynamic body with backend
  mass/CoM/diagonal inertia, per-drop reset to `starting_pose_m` (z=0.75) +
  initial velocities, fixed-substep stepping (backend timestep 1/240).
- `web/src/scene/sceneRuntime.ts`: the render loop runs the Rapier scheduler
  (reset at each `start_s`, step, read body transform → objectsGroup); the
  OLD sample-playback path remains as a FALLBACK when Rapier fails to init.
- `web/src/components/DropPhysicsDebug.tsx`: LIVE FRAME table shows real
  Rapier body velocities when active.
- `web/src/scene/geometryFactory.ts`: added `worldVerticesForGeometryFull`
  (non-strided) for the collider.

---

## 4. The exact reproduction request (what the browser sends)

POST `http://127.0.0.1:8000/api/analyze`:
```json
{
  "schema_id": "gms.web-analysis-request/1",
  "request": {
    "schema_id": "gms.project/1",
    "geometry_asset_id": "6a737b1fa73c35865821f62612d472deff39b3eeafccc5d74dad48d5585a8404",
    "drop_simulation": {"test": "drop", "height_m": 0.75, "surface": "concrete", "drop_count": 10, "orientation": "flat"},
    "options": {"display_tessellation": true}
  },
  "options": {"strict": false, "use_cache": false}
}
```
Expected: `support_model: convex_hull`, `support_point_count: 156`, every
`drops[i].settled == True`, `drops[i+1].start_s - drops[i].end_s >= 0.5`,
first sample z ≈ 0.75 for every drop.

---

## 5. THE PRIME SUSPECT: the Rapier fallback is silently active

`sceneRuntime.setDropSimulation` builds the Rapier sim ASYNCHRONOUSLY:
```ts
buildRapierDropSim(simulation.model, currentEntries, simulation.drops).then((built) => { ... rapierSim = built; });
```
If `buildRapierDropSim` throws or returns null (WASM load failure, a Rapier
API misuse, a vertex-hull error), `rapierSim` stays null and the runtime
silently falls back to the OLD sample-playback path — which the user would
see as "nothing changed" (the same old trajectory lerp, including any visual
quirks). **Verify in the browser console:** open DevTools → Console during a
drop run. If there is a Rapier/WASM error, or if the drop uses the sample
path, that is the bug.

Check these specifically in `rapierDropSim.ts`:
- `RAPIER.init()` resolves? (compat embeds the wasm; if the dynamic
  import fails under vite, catch returns null → fallback.)
- `RAPIER.ColliderDesc.convexHull(new Float32Array(all))` — if `all` is
  empty/too large or the hull computation fails, the try/catch falls back to
  a box — verify the hull is actually built for the G3 (156-ish vertices, not
  a box).
- `body.setAdditionalMassProperties(mass, com, principal, identityFrame,
  true)` — correct 0.20 signature? (mass, centerOfMass Vector, principal
  inertia Vector, angularInertiaLocalFrame Rotation, wakeUp bool).
- The `world.timestep = model.timestep_s` (1/240) with substep capping — if
  the world.step() is called with the 1/240 timestep but the render delta is
  ~1/60, the sim runs slow/fast. Verify the visual drop duration matches
  ~0.39 s free-fall.

**The user-visible symptom of the fallback**: the model lerps the backend
trajectory — if that looks fine now (post-fix backend), the user might still
perceive the teleport-up at each drop start as "spawn on ground" if the
timeline has the drop-0 frozen-tail overlap. The Rapier path should make every
drop visibly FALL from 0.75 m. If you see lerp-style motion, Rapier is not
running.

---

## 6. What to do (in priority order)

1. **Reproduce the user's exact view.** Run the app, hard-reload, run a
   10-drop test, watch the browser console. Determine: is Rapier running
   (live physics, real fall) or is the fallback active (lerp, teleport)?
2. **Fix the Rapier path if it's falling back.** Add visible diagnostics:
   log `rapierSim` status (built / null + reason) to the console; surface it
   in the debug HUD ("LIVE" vs "SAMPLE" playback mode). Ensure the WASM loads
   in the browser (it may fail on `file://` or blocked CSP — the dev server
   must be http://).
3. **If Rapier runs and the animation is still wrong**, check:
   - The ground collider z placement (top face must be z=0).
   - The body's initial translation (must be z≈0.75) and the convex hull
     collider's offset vs the display mesh (the mesh may need the same
     rotation.x=π flip the GLB loader applies — see `loadDisplayAsset` in
     sceneRuntime; if the collider is upside-down the mouse "rests" on its
     dome).
   - Mass properties: if `setAdditionalMassProperties` is rejected or the
     density-0 collider makes the body massless, Rapier may behave
     erratically (sink/float).
4. **Eliminate every stale-server path.** Kill ANY process on 8000/5173/8898/
   8899/5199 and start ONLY the two servers in section 2. Tell the user to use
   `http://localhost:5173` and hard-reload.
5. **If all else fails: complete refactor of the animation layer.** The
   current architecture (backend Python physics for numbers + Rapier for
   animation) is sound; if Rapier cannot be made to work in the browser
   (WASM issues on the user's machine), the alternative is: keep the verified
   backend trajectory (now correct) and make the SAMPLE-PLAYBACK path perfect
   — smooth lerp/slerp, explicit teleport-up at each drop start (not a
   gap-hold that can hide it), and a clean frozen rest tail. The backend data
   is correct, so a perfect sample player is a valid complete solution.

---

## 7. Hard constraints

- Do NOT regress the verified backend (convex hull, timeline, settle). The
  backend suite must stay 1146 passed / 2 skipped; the frontend 209 passed.
- Preserve determinism and the API/data formats. Do NOT re-freeze baselines
  without a deliberate verified change.
- The debug HUD must show the truth: label the playback mode (LIVE/SAMPLE)
  and the live physics values. No fake numbers.
- No "look-good" hacks: real physics or honest fallback, never a fake
  animation.

## 8. Deliverable

A report with:
1. Whether the user's browser was running stale code (cache/port) or current
   code, with evidence (screenshot of Sources tab / console).
2. Whether the Rapier path was active or silently falling back, with the
   console evidence and the exact error if any.
3. Every change made and why.
4. The test-matrix results (backend, frontend, build, live browser).
5. A clear statement of what the user should do to see the fixed behavior
   (URL, hard-reload instructions).
