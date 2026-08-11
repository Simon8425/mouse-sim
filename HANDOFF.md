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

---

## 12. DEFECT-RESOLUTION PHASE (independent audit follow-up)

This section documents the audit-driven hardening phase. An independent adversarial
audit found defects that could produce confidently wrong shell results; all were fixed
and verified by separate adversarial verification agents.

### FIXED (with verification evidence)

1. **Self-intersection detection** (`mouse_sim/geometry.py`): the pair sweep previously
   skipped ALL shared-vertex pairs (folded shells and twisted prisms escaped with
   confident wrong mass). Now: relation-aware strict interior acceptance for every pair,
   2D coplanar edge-crossing detection, orientation-independent coplanar containment,
   duplicate-face detection, and an AABB-overlap-center interpenetration probe with
   strict (all-probes) vertex containment. A closed twisted prism is flagged
   `self_intersecting`, mass blocked. Dense adversarial sweeps: 216/216 overlapping box
   configs detected, 291/291-config sweep zero failures (incl. partial face-to-face
   touching unions and epsilon gaps that previously false-flagged).
2. **Geometry holes / STEP import integrity** (`mouse_sim/importers.py`): faces with
   inner bounds (holes) and `BREP_WITH_VOIDS` shells are now BLOCKED with an explicit
   `step_topology_unsupported` diagnostic — imported volume is never silently certified
   as solid. Regression tests with analytically known plate-with-hole and void geometry.
3. **Measured-drop correlation gates** (`mouse_sim/qualification.py`, `mouse_sim/pipeline.py`):
   fail-closed. Every statistic is recomputed from measured/predicted pairs; user
   `r_squared`/`bias`/`relative_error` are ignored; >= 3 distinct conditions on the
   (height, surface, orientation) TRIPLE (drop_id is a label, not independence); >= 3
   distinct measured values; R^2 in [0,1] and >= 0.8; |bias| <= 0.10; zero variance,
   zero/negative values, duplicates, and empty datasets all fail. `required=True` with
   no records fails. No adversarial dataset can unlock `physical_model_confidence =
   high` (verified: r^2=5/-5/NaN, 3 identical points, 2 points, zero measurements,
   outliers, same-height repeats, two-height+repeat, negative measured — all blocked).
4. **Engine hash / cache identity** (`mouse_sim/pipeline.py`): `_ENGINE_BEHAVIOR_MODULES`
   expanded 17 -> 23 with every result-affecting dependency (importers, canonical,
   model, schema, step_kernel, freecad_step_worker). A change in any of them changes the
   run_id; stale cache hits are impossible (verified by temp-dir hash tests and
   execution-skip cache tests).
5. **Lifecycle fatigue** (`mouse_sim/lifecycle.py`): event-wise Miner accumulation
   `D = sum 1/N(E_i)` with `N(E) = 1e6*(0.5/E)^2.5`, overflow-safe log-space
   computation; monotone non-decreasing, split/merge and reorder consistent (below the
   1e15 saturation cap), zero-energy events contribute 0. The aggregate
   (count, energy) path is explicitly disclosed as the uniform-event approximation
   (lower bound for heterogeneous histories); supply `prior_drop_energies_j` for the
   exact path. `prior_drops=1e300` no longer crashes; NaN/Inf/negative are explicit
   invalid input (never silently converted); huge finite values clamp to documented
   maxima with disclosure; `next_usage` chains event energies at full precision.
6. **`not_evaluated -> ok`** (`mouse_sim/population.py`): explicit outcome vocabulary
   (pass/fail/warn/unsupported/insufficient_evidence/invalid_input/not_evaluated).
   Units with an unknown outcome (unevaluated and not failed) are excluded from the
   failure-rate denominator (never counted as successes), disclosed via
   `units_unevaluated`/`evaluated_units`/`analysis_incomplete`; FAILED units always
   count even when another component was unevaluated; survival curve shares the same
   denominator. Worst-case verdict "unevaluated" when incomplete.
7. **NaN/Infinity everywhere**: `tolerance_scale=NaN` no longer clamps to 2.0 (rejected);
   negative `sample_count`/`base_seed` rejected; `workers` bounded; lifecycle NaN/Inf
   rejected at every field; component specs with non-finite values report
   `not_evaluated`, never pass.
8. **Impact safety-factor material** (`mouse_sim/pipeline.py`): the allowable now comes
   from the SHELL's resolved material (structural section's material — including a
   catalog miss, which correctly yields no allowable — else the first object's
   material), disclosed in `result["impact"]["material"]`. Two-material regression
   test with deliberately different strengths.
9. **Validation ↔ confidence propagation**: `self_intersection_unverified` (>5000
   triangles) now produces a validation finding, blocks `safe` classification and caps
   `physical_model_confidence` below high; microscopic (1e-9 m) geometry is labeled
   `OUTSIDE_SUPPORTED_PHYSICAL_SCALE` (not "invalid geometry"); collision clearance no
   longer certifies unsafe meshes.
10. **UI stale + worst-case wiring** (`web/src/state/projectStore.ts`, MissionControl,
    ResultsRail): every input-affecting mutation sets stale synchronously; re-running
    the SAME request no longer false-labels the result stale (request-key comparison);
    "Run deterministic worst-case" now actually dispatches the backend
    `population.worst_case` spec (schema-validated, physically-worst edge directions);
    Monte Carlo button labeled "10k units"; worst-case rows render status "ok".
11. **Component knife-edge thresholds** (`components_elec.py`, `components_mech.py`):
    unified marginal band — ratio < 1.0 pass, [1.0, 1.2) warn with explicit
    uncertainty-band message, >= 1.2 fail (margin-equivalent for screw) — no more
    1-ulp flip at exactly 1.0.
12. **Windows / cross-platform STEP** (`step_kernel.py`, `web_api.py`): `os.getuid`/
    `geteuid`/`preexec_fn` replaced with platform-neutral equivalents; `/api/health`
    returns 200 with structured FreeCAD availability; STEP kernel errors map to
    structured 422s, never raw 500s. Full suite now runs on Windows.
13. **Regression tests committed**: Navier plate tolerance 2% -> 0.05% (measured error
    ~0.003%); first-impact speed vs sqrt(2gh) bounded by the documented bias (1.5% at
    0.1 m, 0.5% at 0.75 m, never above free-fall); gravity scaling sqrt-law ±1%;
    96-drop energy sweep (4 surfaces x 4 orientations x heights x seeds: no energy
    checks fire, raw KE <= 1.001 x release, drift < 0.2%); worst-case edge math
    `SF_wc = SF_nom * s * t^2`; population subprocess determinism across
    PYTHONHASHSEED; geometry integrity matrix; correlation adversarial matrix;
    lifecycle math-property suite.

### STILL APPROXIMATE (unchanged, disclosed)

All component screening (validity "approximate"); the 0.5 J / 1e6 / 2.5 fatigue law
constants; restitution/friction derate amplitudes; skate wear rates; the 130 N battery
crush class with 0.5 transmission; contact stiffness k = 1e5 N/m; the uniform-event
aggregate lifecycle path; orthotropic plate closed-form shell model; AABB clearance.

### UNSUPPORTED (explicitly not claimed)

Buckling, crack propagation, yield localization, snap-through, vibration fatigue,
weld-line/first-ply failure, multi-point contact manifolds, environmental aging beyond
the documented temperature derate, detailed solder/PCB/battery-chemistry models.

### REMAINING RISKS (the shell result can still be misleading via)

- k = 1e5 N/m uncalibrated contact stiffness (verified: battery rate flips 0% -> 100%
  at k=5e5); peak accelerations possibly ~3x low.
- The 17.23% clip-side-button population headline is knife-edge on the unvalidated
  creep constant (0.50 -> 17.3%, 0.55 -> 0%).
- Self-intersection beyond the 5000-triangle sweep limit remains unverified (now
  disclosed in validation and confidence — mass computed, safe/high blocked).
- Thin-penetration blind zone (~diagonal x 1e-9) in interpenetration screening.
- STEP hole/void topology is blocked, but curved-edge chord approximation remains a
  disclosed warning.
- The correlation gate validates internal consistency, not the physical truth of the
  measured data itself.
- Same-height repeats are rejected as non-independent, which may be stricter than a
  repeatability experiment requires.

### PHYSICAL VALIDATION

Per the phase instructions, instrumented drop testing is NOT yet started. The defects
above are closed; reassess whether the instrumented drop tower is the next
highest-value step after reviewing this report.

### TEST STATUS (this phase)

- Backend: 806 tests (804 passed, 2 FreeCAD-gated skips) under Python 3.12.10 on
  Windows; the suite also runs on POSIX (os.getuid fix is platform-neutral).
- Frontend: 129 tests / 17 files; typecheck clean; lint clean.
- E2E: not runnable on this machine (no backend server, no Playwright browsers,
  no web/dist build); config unchanged (4 projects / 18 specs).
- Engine hash: `9923c2bc9988e4b246a3fa1d3107981ed1874b8858ff8d0971ac6c4a85751b47`
  (full sha256 over 23 modules).

---

## 13. SHELL VALIDATION PREPARATION (physical-testing readiness)

This phase prepared the engine to be compared against real instrumented drop
tests. The shell physics is now feature-frozen unless physical measurements
demonstrate a specific model error.

### Added (mouse_sim/shell_validation.py + pipeline wiring)

1. **Shell Validation mode** (`mode: "validation"` + a `validation` section):
   the shell chain is pinned explicitly — CAD revision (geometry),
   material (catalog key only; unknown/inline keys FAIL CLOSED), drop
   (height, orientation as mode OR explicit quaternion, surface, gravity,
   initial velocity [must be zero], initial angular velocity [must be zero]),
   contact (stiffness required, restitution/friction/timestep/substeps
   optional, pins reach the INTEGRATOR as scales), structural model record.
   Nothing is inherited from unrelated settings; incomplete sections are
   rejected (`VALIDATION_CONFIG_INVALID`). Qualification gates are skipped
   in validation mode.
2. **Contact stiffness as a first-class parameter**: fixed pin or
   `contact_stiffness_sweep_n_per_m` [1e5, 2e5, 5e5, 1e6]-style list; the
   sweep reports peak force/acceleration/duration/compression per k with the
   explicit note that NO value is claimed correct without measurements.
   Uncertainty bands (low/high/nominal at the PINNED k) are derived from the
   sweep — never invented.
3. **Measured-test workflow**: `validation.measured_tests` accepts test_id,
   CAD revision, material, prototype ID, surface, height, orientation,
   environment, sensor, measured values AND measurement uncertainty; each
   test is re-simulated under its exact condition (gravity/scales/CoM pins
   propagate into the correlation re-sim), producing MEASURED vs SIMULATED
   with absolute/relative error, per-test uncertainty bounds, aggregate
   bias/RMSE, correlation (the hardened gates), and k-sensitivity of the
   comparison (per-test scaled, a ~ sqrt(k)). Measured data NEVER modifies
   the physics.
4. **Parameter sensitivity** (shell-only, end-to-end re-runs): mass, CoM,
   inertia, E, thickness, strength, k, restitution, friction, timestep at
   +/-10%, reporting relative response of peak accel/force/stress/
   displacement/SF/settle/impact speed. Reference-case top parameters:
   timestep, thickness, youngs_modulus (E), strength, mass (corner drop).
   Zero-effect parameters are reported as 0 with a note — never silently
   omitted.
5. **Traceability**: `shell.inputs_trace` records one authoritative value
   per quantity (geometry digest, material + properties, mass, CoM, inertia,
   drop height/gravity/orientation quaternion/velocity/spin/surface/
   restitution/friction/dt, k + estimate inputs, structural model/SF
   derivation, engine version/hash, seed). Every value was verified to equal
   the section that computed it.
6. **Model status**: `shell.model_status` = "unvalidated" | "correlated";
   "correlated" requires a PASSED measured correlation with >= 3 independent
   conditions (a single-condition lax pass is explicitly NOT physical
   validation). `shell.physical_validation` and
   `shell.invalidating_assumptions` ("what would invalidate this result?")
   are emitted for every shell result; the UI renders them.
7. **Reproducible orientations**: every drop records
   orientation_quaternion_wxyz, gravity_vector_body, initial_angular_
   velocity, initial_velocity, starting_pose; explicit quaternion input is
   supported and replays to numerical precision (1e-15). Coordinate
   convention documented (world z-up, gravity -z, body = CAD frame at rest).
8. **Audit fixes folded in** (from the two adversarial verifications):
   drop-derived estimate now uses the integrator's EFFECTIVE mass and
   degraded restitution (was base values); the energy budget honors pinned
   gravity; mass sensitivity perturbs a physically consistent body (mass AND
   inertia together); shell_validation.py added to the engine hash; the
   impact section carries a cross_reference to the drop-derived force;
   population context discloses mass-assumed/inertia-approximated flags;
   unpinned structures resolve the FIRST OBJECT's material (one material
   across mass/structural/impact).
9. **Nominal reference case**: `reference/shell_validation_reference.json`
   + `tests/test_shell_reference.py` — the permanent physics regression
   baseline (mass, CoM, inertia, structural response, drop dynamics,
   k-sweep forces, top sensitivity parameters) with 0.5-1% bands.
10. **Independent analytic verification**: `tests/test_analytic_verification.py`
    (21 tests) — mass vs analytic geometry, CoM of asymmetric bodies,
    inertia vs composites, drop energy vs mgh, impact velocity vs sqrt(2gh),
    contact impulse/peak force/duration vs the linear-spring closed forms
    (F = v*sqrt(mk), J = m(1+e)v, t = (pi/2)*sqrt(m/k)), plate/beam
    deflection and stress vs closed forms.

### TEST STATUS (this phase)

- Backend: 890 tests (888 passed, 2 FreeCAD-gated skips).
- Frontend: 141 tests / 17 files; typecheck + lint clean.
- Engine hash: `0a67197689a8ff0cea6bbf13760aeda23b757cd733371f4f5079b008eea12402`
  (full sha256 over 24 modules, incl. shell_validation).
- E2E: not runnable on this machine (no backend server / Playwright / web-dist).

### READY / NOT READY (physical comparison)

READY: validation mode, k sweep, sensitivity, measured workflow, trace,
model status, reference case, deterministic replay, analytic verification.
NOT READY (workflow, not engine): the web UI can carry a validation baseline
document (mode passthrough added) but has no editor for the validation
section — use the API or a baseline document; e2e suite not runnable here;
FreeCAD STEP path unverified on this machine.

---

## 14. FINAL PRE-TEST FREEZE (shell-only)

This phase closed the workflow and honesty gaps before physical testing and
FROZE the shell physics. The only validated engineering target is the mouse
shell; internals are mass/CoM/inertia context only.

### Added

1. **Physical Validation card (UI)** — `web/src/components/ValidationConfigCard.tsx`:
   enter CAD revision, material, prototype measured mass/CoM/inertia,
   drop height/orientation/surface/gravity, contact k/restitution/friction/
   timestep, sweep, sensitivity, and measured tests (test ID, prototype ID,
   environment, sensor definition, measured peak g ± uncertainty, settle);
   atomic commit: UPDATE_DRAFT {validation} + SET_MODE validation + RUN_STUDY;
   baseline-config loader; completeness check. ResultsRail renders the
   four-state model status, the tracks, the prototype mass disclosure, and
   the per-row equivalence/revision/uncertainty flags.
2. **Prototype measured overrides** — `validation.prototype` {mass_kg, com_m,
   inertia_kg_m2 (symmetric), thickness_m, material, cad_revision}: absolute
   overrides reach the integrator (mass_kg, com_override_m,
   inertia_override_kg_m2 with `inertia_source` recorded); the model-vs-
   measured mass delta is disclosed (`prototype_mass_disclosure`). The
   validation section MERGES onto user drop_simulation (no silent
   discarding). Off-body CoM overrides are rejected (they made the integrator
   numerically explosive); asymmetric inertia tensors are rejected.
3. **Track separation** — `shell.validation.tracks`: DROP DYNAMICS (peak
   acceleration, impact duration, settle, rebound; validates contact
   stiffness, restitution, friction, rigid-body dynamics, mass/CoM/inertia)
   vs STRUCTURAL (deformation/stress/SF; requires a physical structural test
   with a known applied load; "the drop force does NOT feed it"). The shell
   statement and the UI carry the separation explicitly.
4. **Four-state model status** — UNVALIDATED (no tests) / PARTIALLY VALIDATED
   (compared, not accepted) / CORRELATED (>= 3 independent conditions pass) /
   OUTSIDE VALIDATED DOMAIN (correlated, current drop condition outside the
   validated set; `physical_validation.validated_domain` records the
   conditions). Top-level `validity.confidence` is capped below "high"
   without a passed correlation; shell confidence never rises from solver
   tests alone.
5. **Measurement definition** — measured tests carry a structured sensor
   definition (model, location_body_m, sampling_rate_hz, filter, quantity
   resultant_peak_g|axis_peak_g, axis, sync) and surface definition (type +
   thickness/hardness/mounting). The comparison discloses the SIMULATED
   QUANTITY (CoM-frame quasi-static linear-spring peak, rotation-free), the
   settle criterion (0.4 s dwell, |v|<0.05 m/s, |ω|<0.5 rad/s), the surface
   table parameters, and a per-row EQUIVALENCE flag: a surface-mounted
   sensor reading body-frame acceleration includes rotational terms (factor
   ~2-3 at corner/edge) — comparisons are marked NOT directly equivalent
   unless flat + sensor at CoM + resultant. Impact duration is now compared
   (spring contact duration). Each condition echoes the exact simulated
   quaternion/pose/gravity-body/angular velocity.
6. **Failure-mode hardening** — duplicate test IDs rejected; negative and
   >10000 g measurements rejected; missing uncertainty flagged per metric;
   test CAD revision vs validation revision mismatch flagged; validation-mode
   NaN/Inf classified as VALIDATION_CONFIG_INVALID (pre-canonical check);
   sensitivity runs ONLY when explicitly requested; repeat conditions no
   longer contaminate comparison rows.
7. **BASELINE / UNCALIBRATED snapshot** — `reference/shell_baseline_uncalibrated.json`
   (engine hash, geometry/structure/load_case/validation/request digests,
   run_id, full request, trace, full outputs incl. sweep rows and sensitivity)
   + `tests/test_shell_baseline.py` (verifier: engine hash HARD-FAILS on
   drift, digests recompute, outputs within bands; `--regenerate` rewrites).
   The first physical comparison MUST be against this untouched baseline.
8. **Physical test matrix** — `reference/shell_drop_test_matrix.md`: the
   12-drop drop-dynamics campaign (0.5/1.0/1.5 m; flat/edge/corner/top/
   tumble; concrete/hard pad/foam), 2.0 m EXCLUDED (documented
   excessive-penetration warning at k=1e5), no parameter fitting to
   individual tests, first comparison against the baseline.
9. **Reference case labeling** — `reference/shell_validation_reference.json`
   now carries a `scope` block (engineering target = shell; internals =
   context only; drop tests validate drop dynamics only).

### Final adversarial verification (item 13)

Two independent verifiers attacked the physical-validation path (wrong
mass/CoM/inertia/revision/material/orientation/surface/height, missing
uncertainty, duplicate test IDs/conditions, NaN/Inf, negative/impossible
measurements, missing measurements, stale results, config changes after
measurement, cached old simulations, wrong prototype linkage). All findings
were fixed and regression-tested (quaternion re-sim, revision flag, symmetric
inertia, off-body CoM, NaN classification, no-always-on sensitivity, repeat-
condition contamination, settle/duration uncertainty flags).

### TEST STATUS

- Backend: 926 tests (924 passed, 2 FreeCAD-gated skips).
- Frontend: 156 tests / 17 files; typecheck + lint clean.
- Engine hash (frozen at that phase): `a88917e4f96b14d77068f2210438d15e02ee9e33bbb76ed50527bdffe805ddfc`
  (superseded by later freezes; see §15).
- E2E: not runnable on this machine (no backend server / Playwright / web-dist).

---

## 15. ORCHESTRATED ADVERSARIAL GATE (50 agents + 5 fresh verifiers)

A 50-agent orchestrated audit (7 waves + 4 synthesis leads) plus 5 fresh
verification agents re-attacked the physical-validation path after the fixes.
The gate found and closed the following GENUINE blockers (each with a
regression test in tests/test_audit_blockers.py / test_shell_validation.py /
test_shell_integrity.py / missionControl.test.tsx):

### BLOCKERS FIXED THIS GATE

1. **Equivalence-blind verdict** — the correlation verdict, model_status and
   both confidence gates never considered sensor equivalence; corner/edge
   data with off-CoM/axis sensors (factor ~2-3 mismatch) drove correlated +
   high confidence while every row was flagged NOT EQUIVALENT; an absent
   sensor definition was silently treated as a CoM/resultant reading. FIXED:
   equivalence computed per condition (flat + DEFINED sensor at the actual
   CoM + resultant), non-equivalent conditions EXCLUDED from the verdict in
   ALL modes (exploration included — V1 follow-up), correlated requires
   >= 3 equivalent identity-consistent conditions with a peak-accel
   comparison, confidence high requires model_status == correlated.
2. **Orientation identity aliasing** — duplicate identity keyed on the
   orientation STRING (all explicit quaternions collapsed to "explicit";
   mode-vs-identical-quaternion counted as 2 conditions; q vs -q counted as
   2 poses). FIXED: identity keyed on the resolved quaternion, sign-
   canonicalized; validated-domain keys use the same canonical form.
3. **Comparison-table cross-wiring** — two explicit quaternions at one
   height/surface were paired last-wins against the wrong simulation.
   FIXED: rows paired by test_id.
4. **Prototype identity unbound** — tests from another CAD revision /
   material / prototype fed the verdict undetected. FIXED: identity
   cross-check (cad_revision, material, prototype_id) with mismatch rows
   excluded from the verdict and aggregate.
5. **Invalid measurements accepted** — negative/implausible duration and
   settle; unvalidated environment (NaN temperature destroyed the run as
   PIPELINE_INTERNAL); axis_peak_g without axis; unknown keys silently
   ignored (a tumble spin field silently compared against a spin-free
   re-sim). FIXED: bounds (duration (0,1] s, settle (0,60] s), environment
   validation (temperature/humidity ranges), axis required, unknown keys
   rejected.
6. **Confidence gate inconsistency** — high confidence with 1-2 conditions
   (user-lowered min_drop_conditions) while model_status stayed
   unvalidated. FIXED: one standard — high requires correlated.
7. **Correlation re-sim dropped dt/seed/unit_seed** — the compared
   simulation differed from the reported configuration (settle 8.0 s vs
   2.8 s at pinned dt). FIXED: pins propagated into the re-sim.
8. **Settle sentinel compared** — the 8.0 s DID_NOT_SETTLE sentinel was
   compared as a settle value. FIXED: settle metric marked not-applicable
   with the warning surfaced.
9. **Duration convention** — measured full pulse compared against the
   compression-phase model value (~30% systematic bias). FIXED: predicted
   duration = (1+e)*t (full contact) with the convention recorded.
10. **Materials-as-path cache gap** — run_id hashed the path string, not the
    file content; a changed catalog file served stale cached results. FIXED:
    content-hash for path-form materials (also verified unreachable for
    nested/dict forms; old manifests fail closed).
11. **Manifest certification gap** — manifest_hash omitted the engine hash;
    replay certified different physics as supported after an engine change.
    FIXED: manifest binds engine_hash + run_id; reproduce_from_manifest
    verifies both; cache.load returns None on any corruption (incl. NaN).
12. **Trace mislabels + inert pins** — trace claimed the Navier model for
    beam solves and the geometry mass while the integrator solved the
    prototype mass; thickness_m/substeps/structural.model pins were recorded
    as applied but did nothing. FIXED: trace reports the executed
    method_id and the effective drop mass; inert pins warn
    (VALIDATION_THICKNESS_PIN_NOT_APPLIED / VALIDATION_SUBSTEPS_PIN_INERT /
    VALIDATION_STRUCTURAL_MODEL_PIN_MISMATCH).
13. **Badge overclaim** — "PHYSICALLY VALIDATED — N" counted excluded rows.
    FIXED: independent_conditions = the verdict's evaluated count;
    compared_conditions reported separately.
14. **UI silent drops** — partial quaternion sent zeros (identity pose);
    dead surface-definition inputs; axis_peak_g without axis; sensor block
    omitted when only the quantity was selected; rows without measured
    values passed the client check; duplicate test_ids uncaught. FIXED:
    all-or-none quaternion, surface-definition inputs, axis select +
    required, sensor always emitted when touched, per-row client checks
    (measured value, duplicate ID, positive values, partial location),
    sweep-token validation; ResultsRail renders IDENTITY MISMATCH /
    NO SIMULATION chips and per-metric uncertainty notes.
15. **Campaign matrix vs software** — rows 9 (hard pad), 11 (top), 12
    (tumble) were unexecutable as documented. FIXED: matrix rewritten —
    row 9 = steel + recorded definition, row 11 = explicit quaternion
    [0,1,0,0], row 12 = EXPLORATION-ONLY (no measured comparison claim);
    rows 4-8/11 disclosed as not verdict-driving.
16. **Near-CoM equivalence vs the mesh origin** — the check compared the
    sensor location against the body origin, wrong for asymmetric
    prototypes. FIXED: compared against the drop's actual com_offset.

### VERIFIED NON-BLOCKING LIMITATIONS (disclosed, not fixed)

- measured=predicted fabrication passes (garbage-in; the gate validates
  internal consistency, disclosed since §12).
- Quantity spoofing (declared resultant on a single-axis sensor) — same
  trust class as the measured values; disclosed.
- Explicit identity quaternion [1,0,0,0] is non-equivalent (fail-closed;
  never inflates the verdict).
- Bias pool mixes dimensionless errors across quantities (g/s) — the
  binding gate is the per-metric 25% bound; disclosed.
- Materials-file TOCTOU and nested-path forms: latent, unreachable.
- 6dp quaternion identity boundary merges only <5.7e-5 deg pose
  differences — physically indistinguishable.
- Mixed-identity row flags are fail-closed false positives (whitespace in
  prototype_id/cad_revision) — deterministic and disclosed.

### TEST STATUS

- Backend: 973 tests (971 passed, 2 FreeCAD-gated skips).
- Frontend: 158 tests / 17 files; typecheck + lint clean.
- Engine hash (frozen): `1467e71f5679957d39070adbbc71d29927b3244c7df3e39e61b3936c966ccfd6`
  (24 modules + shell_validation; regenerated baseline and reference after
  the round-2 repair batches: W2-02E height-identity tolerance, W2-02F
  sampling resolution warning, W2-04 raw-correlation identity gate, W2-05D
  fail-closed inertia tensor + raw measured_drops validation, W2-06A
  all-excluded status, W2-07 executed-seed trace, W2-10D/F/G materials
  order + wrapper-root + manifest replay, W2-12A/C trace fields +
  boundary assumptions, W2-13 badge count, W2-16B/C Euclidean near-CoM +
  fail-closed fallback, W4-01 cache engine-hash cross-check, W4-03
  non-positive load rejection + thickness-limits verdict gate, W5-01
  exploration outside-domain parity, W5-03 identity-unchecked disclosure,
  W5-04 web manifest replayability, W8-02 non-finite correlation
  fail-closed + run_id collision closure, W9-02 UI identity disclosure +
  stale-validation mode strip, W10-01 excluded-diagnostic-rows no longer
  veto the verdict (matrix design), W11-02 correlated invalidating-count
  discipline, W12-01 cache inputs-snapshot binding + recursive trace
  verifier, W12-02 band-nominal closest-row + disclosure, SENIOR-01
  qualification-gate exclusion parity + trace string/bool verification,
  CERT-01 invalidating-assumptions model-status label, CERT-04 fatigue-law
  payload disclosure, SENIOR-04 hard-failure validity state; plus the
  W3-gap regression pins: acceptance gates, fatigue saturation/exhaustion
  boundary, quaternion sign canonicalization, and the W8-06 full-leaf
  baseline verifier).
- E2E: not runnable on this machine (no backend server / Playwright / web-dist).
- STEP/FreeCAD: `freecadcmd` is auto-detected for STEP tessellation (env
  override `MOUSE_SIM_FREECADCMD`; `scripts/find_freecad.py` diagnoses it).
  Optional — only advanced BREP STEP needs it; other formats need nothing.

---

## 16. OVERNIGHT RE-VERIFICATION & QUALITY PASS (independent multi-agent audit)

A full independent re-verification was performed (6 parallel audit agents:
UI/UX + AI-slop, functional interaction, test-engine lifecycle, 3D scene/
floor math, test-suite quality, typography). Findings were synthesized by the
lead; fixes below, each with its regression test.

### LIFECYCLE FIXES (highest priority)

1. **Mid-run input-change stale clobber (HIGH)** — `ANALYZE_OK` unconditionally
   cleared `stale: false`. Editing inputs DURING a run (default material,
   object materials, draft patches) changed the draft without touching the
   in-flight token, so the completed result was presented as fresh against
   changed inputs. FIXED: `ANALYZE_OK` recomputes staleness by comparing
   `createAnalysisRequestKey(createAnalysisRequest(state))` against the
   completed run's key (`projectStore.ts`). Tests: `mid-run input changes keep
   result stale` / `re-running the same request clears stale`.
2. **No cancellation (HIGH)** — a running analysis could only be killed by
   launching something else (silent supersede) or reloading; a hung server
   left the UI stuck in "Running…" forever. FIXED: new `CANCEL_RUN` action
   (version bump drops late responses, `cancelNonce` triggers the App abort
   effect) + a Cancel button in `RunStatus` while busy. Test:
   `CANCEL_RUN cancels a running run and drops late responses`.
3. **Duplicate-launch protection (MED)** — double-clicking a Run button
   issued a second `/api/analyze` POST (first aborted client-side, orphaned
   server pipeline). FIXED at two layers: the reducer returns `state`
   unchanged when the identical request key is already in flight
   (`inflightRequestKey` set at `ANALYZE_START`), and the App effect repairs
   the transient 'loading' window of a double-click. Request keys are now
   canonical (object keys sorted) so draft rebuilds with different insertion
   order still dedup. Tests: `RUN_STUDY/RUN_DROP_TEST/RUN_POPULATION ... 
   deduplicated when the identical ... is already running`, `... from a fresh
   baseline deduplicates an identical in-flight relaunch`.
4. **Silent no-ops (MED)** — RUN_STUDY with no geometry or during a mesh
   parse did nothing with zero feedback. FIXED: reducer sets explicit
   `runError` feedback ('Load a model before running an analysis.' /
   'Model import in progress — wait for it to finish before running.') with
   status idle and no nonce bump; launch controls disabled while running or
   without geometry (Exploration, Run Qualification, test Run buttons, Run
   validation, population buttons). Tests: `RUN_STUDY with no geometry sets
   feedback and does not bump nonce`, `RUN_STUDY while mesh parse is in
   progress ...`, `disables the Run button while a run is in progress`.
5. **Error visibility (MED)** — `runError` lived only in an ERR tooltip.
   FIXED: `RunStatus` renders the message inline; the static 'TEST' label is
   now mode-aware (ANALYSIS/QUALIFICATION/VALIDATION); progress label
   'Loading test…' → 'Running…'; the redundant STALE marker in the top bar
   was removed (the results-rail banner remains).
6. **Keyboard parity** — issue-table expandable rows now toggle on Enter/
   Space (`ResultsRail`); model-tree keyboard selection opens the inspector
   like mouse selection.

### 3D SCENE / FLOOR MATH (dedicated investigation with G3-20260320.stp)

- Verified mathematically, not visually: Z-up scene; the display floor is
  **derived from the model bounds** (`floorZ = boundsUnion.min[2] − 0.001`,
  `sceneRuntime.ts syncGridAndGround`), so the static model always sits
  exactly 1 mm above the floor — no hardcoded floor height, no origin
  assumptions. G3 bounds (kernel import, metres): min (−0.03159, −0.05918,
  −0.00070), max (+0.03159, +0.06088, +0.03766); bottom 0.7 mm below the
  origin; static gap exactly 1 mm. No floor-alignment bug for the static
  scene.
- **Impact penetration (MED, fixed)** — the drop sim allows spring-contact
  penetration, so at peak impact (0.75 m, k=1e5, G3) the model's lowest
  point dips ~2–3 mm BELOW the display floor. The dead, never-wired
  `floorCorrectionForModel` was wired into `applyDropTransform`: the model is
  lifted so it never renders below the floor; correction is zero whenever it
  sits above. (Rest-pose float of `0.001 − minZ` ≈ 1.7 mm for G3 remains —
  invisible; see risks.)
- **GLB flip pivot (MED, fixed)** — the display flip (`rotation.x = π`) now
  also flips the GLB root translation (`x, −y, −z`), so display and
  bounds-baked analysis frames agree even for translated GLB roots (was
  identity for G3, latent otherwise).
- **OBJ/STL Y-up** — NOT auto-normalized (a Y-up-authored OBJ renders lying
  on its side). Deliberate: a Y-up→Z-up heuristic can corrupt legitimate
  files; documented risk, no silent change.

### UI/UX + AI-SLOP ELIMINATION

- Deleted: `GeometryGuideCard` (dead component + 53 dead CSS rulesets /
  396 lines, incl. decorative art, unused keyframes, duplicate
  `.model-row.is-selected`), `MetricStrip` (duplicated Overview Summary),
  MissionControl "Workspace Mode & Source" + "Fidelity & Results" echo
  groups (status now lives only in the rail + top bar), unlabeled
  formats/solver chip rows, duplicate TopBar Fit button (toolbar owns Fit),
  default-material banner (per-row DEFAULT chips remain), isolate bar
  (ISOLATE never dispatched), Render-tier telemetry row (always 'low'),
  `SET_QUALITY_TIER`/`qualityTier` dead state.
- Fixed: unstyled `study-card`/`section-title` CSS added (InspectorPanel
  load-case sections were raw HTML), base `.chip` style, undefined CSS
  variables (`--surface-raised`/`--border-subtle` → real tokens),
  `font-weight: 505` (invalid), modal close controls standardized, 'Display
  only' chip → plain muted text, viewport toolbar hidden when no scene
  (was: enabled no-op buttons), ViewportToolbar camera controls no longer
  inert in empty/WebGL-error states.
- Terminology: 'Downforce / load case' → 'Structural — load case', 'Slam /
  impact' → 'Impact' (InspectorPanel matches rail tabs); 'Loading test…' →
  'Running…'; button labels de-shouted ('Run population (10k units)',
  'Run worst-case', 'Run validation', 'Close').

### TYPOGRAPHY (one font, one weight language, one tracking)

- Removed the global `font-weight: 500 !important` flatten (it silently
  killed the 400/500/600/700 hierarchy). Headings = semibold by default.
- Single tracking for uppercase micro-labels: **0.05em** everywhere
  (was 0/0.02/0.03/0.04/0.06/0.08/0.18em); brand 0.1em and heading
  −0.01em kept.
- Size tiers normalized: no more 8/8.5/9/9.5/10.5/14.5px; badges 10px,
  buttons 10px, tabs 10px mono uppercase (was 11px Inter sentence case),
  table headers uppercase, `.format-tag` uppercase, ✕ close 300→400,
  `--font-mono, monospace` fallbacks → `var(--font-mono)`.
- One Inter Variable family + one mono stack throughout; no third font.

### TESTS

- Frontend: **202 tests / 19 files, all passing**; typecheck clean; lint
  clean; `npm run build` clean. Backend suite run separately (see §8 status
  refresh at the end of this session).
- New regression coverage: mid-run stale persistence, CANCEL_RUN semantics,
  all dedup paths, launch guards, RunStatus rendering (mode label, Cancel
  button, inline error, no STALE marker), disabled-while-running states,
  canonical request-key equivalence, issue-row keyboard expansion.
- E2E specs updated textually for renamed UI strings (not executable on
  this machine — no Playwright browsers/server).

### REMAINING RISKS (disclosed, unchanged)

- Drop-playback rest pose floats `0.001 − minZ` above the display floor for
  models whose origin is not near the bottom (G3: ~1.7 mm, invisible;
  centered-origin models: tens of mm). Static pose is always exact.
- OBJ/STL Y-up files render lying on their side (no auto-normalization).
- No fetch timeout on `/api/analyze`: a hung server shows Running… until the
  user clicks Cancel (cancel is now first-class).
- k=1e5 contact stiffness uncalibrated (previous phases); float32 precision
  for STEP with huge global offsets; `maxDistance` clamp for models > ~8 m.

### TEST STATUS (this phase)

- Frontend: 202 tests / 19 files; typecheck clean; lint clean; build clean.
- Backend: **974 tests, all pass, 2 FreeCAD-gated skips** (~10 min on this
  machine; population tests run serially on Windows — no fork).
- Baseline: deliberately re-frozen (`python tests/test_shell_baseline.py
  --regenerate`) at engine hash
  `729783dcfb7805f2026ffdf519b2a6fdd2b2a9f2c4e26c7f1fedafee84ed56e4`
  (the frozen hash had drifted because the previous session's
  self-intersection hardening in geometry.py landed after the last freeze).
- E2E: not runnable on this machine (no Playwright browsers / server);
  specs updated textually for renamed UI strings.
- Smoke: `python -S -m mouse_sim serve --web-dist web/dist` boots and serves
  the built SPA + `/api/health` + `/api/materials` correctly.

### POST-AUDIT USER-FEEDBACK FIXES (same session)

1. **Silent population runs (root cause fixed)** — a leftover one-shot
   `draft.population` spec survived aborted/superseded/cancelled population
   runs (the strip lives in ANALYZE_OK/ERROR, which never fire when a fetch
   is aborted) AND `RUN_STUDY` never stripped it — so an Exploration /
   qualification / validation click after an interrupted population run
   silently launched a fresh 10k-unit campaign ("population runs
   automatically"). FIXED: `RUN_STUDY` strips `draft.population` before
   launching; `CANCEL_RUN` strips it too. Regression tests:
   `RUN_STUDY strips a leftover population spec so plain runs stay plain`,
   `CANCEL_RUN strips the one-shot population spec from the draft`.
2. **Physical Shell Validation editor removed from Settings** — the
   validation-campaign card (measured tests, sensor/surface definitions) was
   a specialist workflow dominating the settings dialog. Removed:
   `ValidationConfigCard.tsx` deleted, Settings now has Engine status /
   Materials / Population analysis only. Validation mode remains fully
   available through the API or a baseline document (mode passthrough is
   untouched).
3. **Drop playback under-floor (display-layer fixes)** — verified
   mathematically with the real pipeline on G3-20260320.stp: the trajectory
   NEVER puts the model below the floor for any orientation/height (worst
   sampled gap +1.3 mm at 0.75 m corner). Two display-layer defects could
   still render below the floor and are now fixed:
   (a) the impact lift used the STATIC bottom, under-correcting rotated
   poses (corner/edge/tumble) whose true lowest point is the rotated corner;
   the lift is now pose-aware (`rotatedBoundsMinZ` — conservative AABB-under-
   rotation estimate; `applyDropTransform`); (b) a STALE result's trajectory
   was replayed against the current model after a model/input change; the
   scene now ignores `drop_simulation` while `stale` is true (App.tsx).
4. **Tests**: 197 frontend tests (19 files), typecheck/lint/build clean;
   validation-card tests removed with the card; new `rotatedBoundsMinZ`
   unit tests.

### WORLD-CLASS VISUAL / TYPOGRAPHY PASS (same session)

The remaining "AI-slop" signature was the terminal-console styling: ALL-CAPS
labels, mono type on every control, wide letter-spacing, 10px micro text.
Redesigned to a professional single-font system (Inter Variable everywhere;
mono reserved for `code`/IDs only):
- Sentence case throughout (buttons, tabs, badges, labels, top-bar meta
  labels "Project"/"Geometry", RunStatus mode label "Analysis"/…, rail
  toggle "Results"); uppercase remains only on the brand wordmark.
- All letter-spacing normalized to normal; headings keep -0.01em; brand
  keeps 0.08em.
- Size scale raised: buttons 12px/30px, labels 11px, titles 13px/600,
  tables/inputs 12px, base 13px (was 10-11px mono).
- Radii added consistently (buttons/inputs 6px, badges/chips 999px,
  cards 8-10px, modals 12px, HUD panels 8px) replacing the harsh
  0px corners; subtle shadows on floating panels; tabular-nums on all
  numeric cells; input/select styling unified.
- Verification: 197/197 tests, tsc/lint/build clean.
