# HANDOFF — Gaming-Mouse Shell Engineering Validation Platform

Session handoff for the next AI/developer session. Read this FIRST, then independently
inspect the implementation before changing anything. Do not rely on this file alone.

---

## 1. PROJECT OBJECTIVE

This project is a **gaming-mouse shell engineering validation platform**: a
deterministic, stdlib-only (Python 3.9) CAE screening simulator plus a React/Three.js
frontend.

**The mouse SHELL is the primary engineering target and the authoritative result.** The
engine determines how a shell behaves under drops, impacts, repeated impacts, material
variation, manufacturing tolerances, deformation, stress, fatigue, and long-term usage.

**Internal components (PCB, battery, switches, encoder, screws, clips, mounts, adhesives)
are SECONDARY.** They exist to provide realistic physical context (mass, center of mass,
inertia, mounting constraints) and simplified screening observations. They must NEVER
silently contaminate the shell result, and the platform must NOT simulate every component
with the fidelity of the shell.

The goal is trustworthiness, not feature count: verified/simple physics beats theoretical
complexity; simplified models are preferable to unsupported complexity; false precision is
forbidden; real-world validation ultimately determines how trustworthy the model is.

---

## 2. CURRENT ARCHITECTURE

Verified data flow (the actual implemented pipeline — `mouse_sim/pipeline.py` `_execute`):

```
CAD geometry (STEP via FreeCAD worker | stdlib faceted parser | analytic shapes)
  → geometry validation (topology: closed/manifold/degenerate/self-intersecting/nested;
    relative thresholds)                    mouse_sim/geometry.py, mouse_sim/validation.py
  → material resolution (catalog, default-material fallback)
                                            mouse_sim/materials.py, mouse_sim/model.py
  → volume → mass → CoM → inertia           mouse_sim/mass.py
  → manufacturing variation                 mouse_sim/drop_sim.py (_unit_variation)
  → drop conditions (height/surface/orientation/seed/unit_seed)
  → rigid-body drop dynamics (sequential-contact windows, gyroscopic coupling,
    energy-conserving contact)              mouse_sim/drop_sim.py
  → impact loading (energy-capped handoff)  mouse_sim/impact.py
  → shell structural response (closed-form plate/beam, orthotropic Navier,
    temperature derating, K_f feature stress) mouse_sim/physics.py
  → SHELL RESULT (authoritative)            pipeline.py `_assemble_shell_result`
       status/classification, peak stress, deformation, min safety factor,
       critical region + stability probe, physical-model + statistical confidence
  → secondary component screening           mouse_sim/components_elec.py,
                                            mouse_sim/components_mech.py
  → population analysis (Monte Carlo + deterministic worst-case)
                                            mouse_sim/population.py
  → usage profiles / lifecycle degradation  mouse_sim/profiles.py, mouse_sim/lifecycle.py
  → qualification gates                     mouse_sim/qualification.py
  → HTTP API                                mouse_sim/web_api.py, mouse_sim/cli.py
  → React frontend                          web/src/App.tsx, components/, scene/, state/
```

Key architecture invariant (tested): the shell physics (structural, mass, drop trajectory)
is computed BEFORE the component/population sections run, and those sections write only
their own result keys. A component threshold can never change a shell output.

---

## 3. WHAT HAS BEEN IMPLEMENTED

### Backend (`mouse_sim/`)

- **Drop simulation** (`drop_sim.py`): rigid body, CoM-integrated position, body-frame
  angular velocity with substep-subdivided torque-free gyroscopic term, sequential-contact
  windows with analytic plane-crossing + high-spin swept substeps, contact manifold at the
  centroid of unique support points, tangential-effective-mass Coulomb friction,
  quadratic low-speed restitution roll-off, resting-contact clamp, honest settle
  (rest criterion, `settled` flag), per-drop energy ledger + physics checks
  (ENERGY_CREATION/DRIFT/REBOUND_OVERSPEED/EXCESSIVE_PENETRATION/DID_NOT_SETTLE),
  seeded per-drop jitter, `unit_seed` manufacturing variation (SplitMix64-mixed LCG),
  `unit_scale`/`mass_scale`/`friction_scale`/`restitution_scale` overrides (validated —
  invalid scales rejected), `com_offset_m` CoM frame, strict input validation
  (positive-definite inertia, height 0.02–2 m, drop_count 1–20, spin tumble-only,
  tumble default 6 rps, NaN/Inf rejected).
- **Impact screening** (`impact.py`): energy-based quasi-static model (closing velocity,
  effective mass, impulse, peak force/acceleration, Hertz branch, per-material fatigue
  law N = 1e6·(σ_ref/σ)^k with generic fallback flagged), energy cap at the drop budget,
  plausibility flags.
- **Structural screening** (`physics.py`): simply supported plate (Navier series, aspect-
  interpolated coefficients, orthotropic D11/D12/D66 form that reduces exactly to
  isotropic), cantilever beam, point-load (critical region = load point, disclosed slow
  series), thin-shell ratio + SMALL_DEFLECTION_VIOLATED + series-convergence flags,
  temperature derating (per-material linear coefficients), anisotropy honesty
  (UNSUPPORTED_ANISOTROPY), weld-line derated allowable, feature stress concentration K_f,
  equilibrium residuals, preflight checks.
- **Material system** (`materials.py`): builtin catalog (ABS, PC, PC/ABS, POM, nylon, TPU,
  FR-4 with anisotropy quartet, LiPo, steel, PTFE, magnesium/aluminum, `default`),
  full SI properties incl. fatigue constants, anisotropy, weld-line factor,
  continuous-use temperature ranges; validation incl. plausibility bounds
  (density ≤ 3e4, E ≤ 1e13, E2/E3 within 100× E1, Poisson bands, fatigue pair);
  `ensure_default_material` preserves the catalog type (case-insensitive lookup);
  inline material dicts validated (no TypeError).
- **Default material**: any object without a valid explicit material deterministically
  falls back to the built-in `default` (generic polymer) — never fails, always disclosed
  (`DEFAULT_MATERIAL_ASSIGNED` warning + `material_assignments` log).
- **Geometry validation** (`geometry.py`): relative degenerate/volume thresholds
  (scale-invariant), shell-connectivity via union-find components, self-intersection
  detection (exact pair sweep ≤ 5000 tris + per-component vertex containment;
  `self_intersection_unverified` disclosed above), nested/cavity detection
  (jittered ray parity), multi-component disclosure; open meshes never silently produce
  certifiable mass.
- **Fatigue/lifecycle** (`lifecycle.py`): event-wise Miner accumulation (D = n/N(ē),
  linear in count — the old lumped law was 33,000× inflated), per-event energy law
  (0.5 J @ 1e6, slope 2.5), restitution derate (≤7%), skate wear (cloth/hard pad rates),
  switch-type ratings (mechanical 20M / optical 60M), scroll encoder
  (25k revolutions, 24 detents/rev conversion), actuation flags, age-driven disclosures,
  `next_usage` chaining; non-finite inputs clamped (OverflowError caught).
- **Usage profiles** (`profiles.py`): esports_fps / esports_moba / productivity / general
  with daily clicks/slide/scroll/drop rates, lifespan projection, `combine_usage`.
- **Component screening** (`components_elec.py`, `components_mech.py`): pcb (plate flex
  with board self-mass + solder shock shear + Coffin-Manson thermal fatigue), battery
  (crush 130 N class with 0.5 transmission + 500 g shock + temperature), switch
  (usage rating + stalk fatigue with K_f), encoder (steps→revolutions conversion),
  screw (boss pull-out π·d·Le·0.2·S_y + vibration/impact + Junker loosening),
  clip (cantilever snap-fit + release-ramp retention + ABS creep),
  mount (eccentric compression 0.6 derate + Euler slenderness-gated buckling),
  adhesive (thermal-mismatch + impact shear, SRSS, exposed-only aging).
  All: validity "approximate", marginal bands (warn 1.0–1.2×), full assumption
  disclosure, never raise.
- **Population** (`population.py`): 10k-unit Monte Carlo (parallel, chunked, byte-
  deterministic across workers/hash), 18 tolerance parameters (14 component/manufacturing
  + 4 shell: wall thickness/modulus/strength/density), per-unit drop with mass-model CoM,
  lifecycle degradation, component analysis, shell failure rate via closed-form scaling
  laws (σ ∝ 1/t², SF ∝ strength·t², w ∝ 1/(E·t³)), Wilson 95% CI, point-biserial
  sensitivity with HIGH/MEDIUM/LOW/NOT_OBSERVED labels, survival curve
  (S(u) = P(u_f > u), S(1.0) = 1 − rate), **deterministic worst-case mode**
  (`worst_case` config: band-edge corners, single unit, no CI — distinguished from
  Monte Carlo), unknown config keys rejected, contact stiffness overridable + disclosed.
- **Confidence system**: shell `physical_model_confidence` (high requires valid response,
  no UNSUPPORTED flags, no assumed mass/inertia, no point-load, AND a passed
  measured-drop correlation — else capped at medium/low) + `statistical_confidence`
  (single_run or population CI); six-state classification
  (safe/marginal/failed/unsupported/invalid_input/insufficient_evidence) — unsupported
  and invalid inputs are never PASS; critical-region stability probe (7 solves,
  stable/unstable verdict).
- **Caching/determinism**: content-addressed run_id (engine_version + engine hash of 17
  modules + mode + per-key input hashes + options), verified cache hits, engine-hash
  invalidation on code changes.
- **Qualification** (`qualification.py`): 12 readiness + 6 integrity gates incl.
  CORRELATION_MEASURED (measured-drop comparison ±25%, R² ≥ 0.8, |bias| ≤ 10%),
  fail-closed correlation handling.
- **Web API** (`web_api.py`): /api/health, /api/materials, /api/geometry/normalize,
  /api/geometry/assets, /api/analyze (bounded semaphore), manifest-slimmed responses,
  400/413 connection close.

### Frontend (`web/`)

- Scene: Z-up engineering scene, floor/grid, drop trajectory playback with memoized
  playback controls, quality tiers (Low/Medium/High/Ultra rendering only), part picking
  with tree highlight/auto-scroll, GLB parts, default-material chips + banner.
- Test runner: RUN TEST menu → per-test config card (TestRunCard), RUN QUALIFICATION,
  Settings (engine health, Model Quality, Default Material, worst-case population trigger).
- Results rail: Overview (Shell Validation primary: status/classification badge,
  physical-model + statistical confidence rows, critical region + stability warning),
  Impact, Structural, Qualification, Issues, Components (screening banner,
  validity/usage_ratio), Population (Monte Carlo CI + sensitivity levels + survival, OR
  deterministic worst-case block); stale-result banner (marked synchronously on input
  changes).

---

## 4. WHAT WAS VERIFIED (this session — independent subagents, ~250 probes)

- **Physical invariants** (encoded in `tests/test_hardening_regressions.py`):
  density×2 → mass×2 exact; scale×2 → volume×8/area×4/mass×8/inertia×32 exact;
  gravity×0.5 → speed √0.5 (±0.2%); height×2 → energy×2 (±0.4%); density×2 → impact
  speed bit-identical; E×2 → deflection×0.5, mass unchanged; strength×2 → SF×2;
  t×0.5 → deflection×8, stress×4 (exact).
- **Analytical regression**: plate 0.061%, cantilever beam 0.094%, cube inertia exact,
  composite CoM exact, first-impact √(2gh) within the documented half-step bias
  (0.46%, conservative).
- **Energy**: 288-drop sweep (h × 4 surfaces × 4 orientations × seeds) — zero
  ENERGY_CREATION/DRIFT/REBOUND checks on legitimate runs; raw KE ≤ release everywhere
  (max 0.969×); settled ≤ release; drift < 0.12% (threshold 1%); no NaN.
  New energy assertions are non-vacuous (mutate-tested).
- **Determinism**: byte-identical across workers 1–8, PYTHONHASHSEED 0/12345, chunk
  sizes, repeated runs, worst-case mode; run_id differs on all 10 tested input
  dimensions; engine hash covers 17 modules.
- **Shell isolation**: A/B/C/D experiments — shell/structural/mass/drop byte-identical
  with realistic, extreme, and invalid component data (21-section contamination sweep
  clean); 19-case edge matrix in `tests/test_component_isolation.py`.
- **Statistics**: Wilson CI exact; point-biserial exact (analytic −0.654 vs measured
  −0.656); n=100 CI brackets n=10000; nested populations (unit i independent of N);
  worst-case verdict matches edge math SF_wc = SF_nom·s·t² exactly.
- **Accumulation**: D monotone in count; D(100×0.5 J) == D(100, 50 J) == 100·D(1, 0.5);
  chained next_usage == merged usage; flags flip exactly at ratings.
- **Geometry**: interpenetrating boxes detected, touching unions NOT flagged, concentric
  cavities detected, 1e-9 m closed meshes valid (relative thresholds), large meshes
  disclosed as unverified.
- **Materials**: density 1e12 / E 1e15 / E3=1000×E1 rejected; FR-4 and ABS valid;
  inline dicts handled; default-material fallback deterministic.
- **Population headline**: 10,000-unit esports 5-year run → 17.23% unit failures,
  sole driver clip_side_button (clip-thickness tolerance, analytic 17.2%),
  all failures in the final usage decile, shell failures 0 (nominal SF ~10).

---

## 5. TRUST LEVELS

**VERIFIED / STRONGLY SUPPORTED**
- Shell safety factor, peak stress, deformation, critical region (closed-form solver
  vs analytical cases ≤ 0.5%).
- Drop dynamics (energy-conserving, deterministic, invariant-consistent).
- Mass/CoM/inertia single-source consistency (frame/winding/order invariant).
- Monte Carlo statistics (CI, sensitivity, survival) and the deterministic worst-case
  arithmetic.
- Classification/confidence mechanics and the shell-isolation invariant.
- Switch/encoder life consumption vs manufacturer class ratings.

**APPROXIMATE / SCREENING (honestly labeled)**
- ALL component screening verdicts (validity "approximate", low-medium confidence,
  marginal bands, full assumptions).
- Peak acceleration magnitudes (contact stiffness k = 1e5 convention — see Risks).
- The 5-year clip failure prediction: knife-edge design margin, reported as
  marginal/verify, NOT a field rate.
- Lifecycle drop-fatigue channel (numerically inert at realistic rates, disclosed).
- Temperature derating coefficients (supplier-curve class, ±20% screening).

**UNSUPPORTED (deliberately not claimed — disclosed, never PASS)**
- Buckling, crack propagation, yield localization, snap-through, vibration fatigue,
  weld-line/first-ply failures, creep beyond the class derates, battery internal
  chemistry, detailed solder mechanics, multi-point contact manifolds, environmental
  aging beyond class derates.

---

## 6. CURRENT KNOWN RISKS

1. **Contact stiffness k = 1e5 N/m (largest uncertainty)**: screening convention for
   the component load chain; measured plastic-enclosure effective stiffness spans
   2e5–1e6 N/m, so peak accelerations may be under-predicted up to ~3×. Disclosed and
   overridable; component shock verdicts are screening-only.
2. **Self-intersection unverified above 5,000 triangles** (disclosed issue
   `self_intersection_unverified`; mass still computed, honestly flagged).
3. **Float NaN in any request field aborts the whole run** (PIPELINE_INTERNAL from
   canonical JSON; string "nan"/"inf" are guarded). Locked in by a test.
4. **First-impact integration bias**: semi-implicit Euler crossing-time estimate
   under-reports impact speed by ~g·dt/2 (0.3–3.2% depending on height) — conservative.
5. **Corner-orientation settle is non-monotonic in restitution** (chaotic bounce
   sequences; energetically clean, all drops settle or flag honestly).
6. **Pipeline `peak` metric** reports the max contact-point speed (lever-amplified)
   — physically meaningful, but not the closing speed.
7. **Battery crush channel** loads the cell by its own inertia with a 0.5 transmission
   factor; the chassis-level force path is not separately modeled (disclosed).
8. **Concentric-shell parity degeneracy** handled by jittered ray re-probing (majority
   of 3) — robust for the canonical cases, still a heuristic.
9. **UI transient staleness window** (400 ms debounce) — self-healing; persistent
   paths now mark stale synchronously.
10. **Two gravity conventions** (9.81 integrator vs 9.80665 g-units) — 0.03% inert,
    now commented.

---

## 7. PHYSICAL VALIDATION — HIGHEST-VALUE REAL TEST

**Instrumented drop tower (ASTM D3332-style)**:
- **Measure**: peak chassis acceleration (g) + settle time + rebound behavior on the
  actual shell, over heights 0.5–2.0 m, the four surfaces (concrete/wood/foam/steel),
  and the standard orientations; plus cell-level accelerometer for the battery mount.
- **Why**: it directly calibrates the contact stiffness k (the largest uncertainty),
  grounds the battery 0.5 transmission factor, validates the surface restitution table,
  and supplies the measured-drop correlation records that unlock
  `physical_model_confidence = "high"`.
- **Secondary high-value tests**: clip pull-off retention + ISO 899-1 creep coupons at
  the actual beam stress; cell crush (UN 38.3); board-level drop (JEDEC JESD22-B111);
  switch/encoder endurance per manufacturer spec; manufacturer telemetry (clicks/
  distance/scroll per genre) for the profiles.

---

## 8. CURRENT TEST STATUS (exact)

- Backend: **713 tests, OK (2 skipped — FreeCAD-gated STEP integration)**. Run:
  `python3 -S -m unittest discover -s tests -p 'test_*.py' -q` (~160 s).
- Frontend: **112 tests / 17 files** (`npm test -- --run`); **typecheck**, **lint**,
  **build** all clean.
- E2E: **72 passed / 0 failed** across 4 projects (`npm run e2e`, ~2 min — first
  `lsof -ti :8899 -i :8898 -i :5199 | xargs kill` to free ports).
- STEP integration: `MOUSE_SIM_RUN_SLOW_STEP_INTEGRATION=1 python3 -S -m unittest
  tests.test_step_kernel_integration -q` — OK.
- Server: `python3 -S -m mouse_sim serve --project-root . --web-dist web/dist
  --port 8898` (running on http://127.0.0.1:8898/).
- Engine hash: `e8a010a408f4e17c…` (17 modules).

---

## 9. IMPORTANT DESIGN PRINCIPLES

1. The shell is the primary engineering target; shell results are authoritative.
2. Internal components are secondary context and screening.
3. Secondary models must never silently contaminate shell results.
4. Simplified models are preferable to unsupported complexity.
5. Never create false precision (significant figures, classification states, split
   confidence, "correlation, not causation").
6. Statistical confidence is NOT physical-model confidence (10,000 simulations ≠
   high physical confidence).
7. Monte Carlo is NOT the same as worst-case analysis (both exist, labeled).
8. Unsupported physics must never silently become PASS (classification `unsupported` /
   `insufficient_evidence` with the numeric SF still shown).
9. Prefer validated/simple physics over theoretical complexity.
10. Do not add physics merely to increase feature count.
11. Real-world validation ultimately determines trustworthiness.
12. Determinism is a hard requirement: same seed/config → byte-identical; no set/dict
    iteration in output paths; stdlib only; Python 3.9.

---

## 10. WHAT NOT TO DO NEXT

Do NOT immediately: add more component models; add unnecessary theoretical physics;
attempt full PCB FEA; attempt battery chemistry simulation; add detailed solder
mechanics; add crack propagation unless specifically justified; expand scope simply
because something is theoretically possible. This is a hardening/trust phase — the
platform is complete in scope.

---

## 11. CURRENT STATE / NEXT LOGICAL STEP

The engineering platform is complete and verified. The highest-value next phase is
**physical calibration**: instrumented drop testing (Section 7) and using the measured
data to calibrate k, the restitution table, and the battery transmission factor, then
feeding the results into the correlation section to raise physical-model confidence.
Secondary candidates (only after calibration): a multi-point contact manifold for
resting realism, and mesh-level (non-parametric) shell failure modes — both deferred
until the closed-form screening is experimentally grounded.

The next session should first read this file, then independently inspect the
implementation and run the full test matrix before making any changes.
