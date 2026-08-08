# Capability Boundary — Deterministic Exploratory Screening Engine

This document records the honest, investor-presentable boundary of what `mouse_sim`
models and what it explicitly does not. Every claim here is enforced in code:
solver metadata, validity states, unsupported-failure-mode lists, and the
qualification integrity gates.

## Identity

`mouse_sim` is a **deterministic exploratory screening engine** for gaming-mouse
mechanical design. It is not a certified FEA product and makes no certified-engineering
claims.

- Structural solver metadata: `screening_surrogate_v1`
  (`model_family: closed_form_screening`, `backend: surrogate_closed_form`) —
  *"closed-form surrogate solver; screening-quality estimates, not validated FEA."*
- Impact solver metadata: `energy_quasi_static_v1`
  (`model_family: energy_quasi_static_screening`).
- Determinism: identical inputs produce byte-identical artifacts (canonical JSON,
  sha256 content addressing, no timestamps, no random state).

## What is modeled

| Area | Method | Notes |
|---|---|---|
| Mass properties | Analytic volume × density; mesh divergence-theorem solids | Per-object calculated mass, measured-mass overrides, centroid + inertia tensor aggregation in the project frame |
| Structural screening | Navier simply-supported thin plate (`shell_navier_v1`); Euler-Bernoulli beam (`beam_closed_form_v1`) | Linear elastic, small deflection, SI values, closed-form series |
| Impact screening | Energy/momentum-balance (`energy_quasi_static_v1`) | Peak force, peak acceleration, contact duration, optional load-path stress, translation/rotation energy partition when inertia is supplied |
| DFM-lite validation | Wall thickness, geometry health, material approval/provenance, classification, tolerance-aware PCB clearance | Structured findings with `evidence_blocking` severity |
| Qualification readiness | 17-gate model: 12 readiness gates + 6 integrity gates | Hard exploration/qualification separation; promotion to accepted evidence never performed |

Every result records its assumptions and an explicit unsupported-failure-mode list;
results carry validity states (`valid | approximate | inconclusive | failed`, impact
`valid | no_impact | failed`) and evidence dispositions that never exceed
`qualification_pending_review`.

## What is not modeled

These failure modes and methods are **never hidden inside a safety factor** — each is
carried as a machine-readable unsupported-failure-mode code on the result:

- Plasticity / nonlinear material behavior
- Explicit (transient) dynamics
- Impact-specific: battery crush (`UNSUPPORTED_BATTERY_CRUSH`), PCB/component shock
  (`UNSUPPORTED_PCB_SHOCK`), fracture (`UNSUPPORTED_FRACTURE`), delamination
  (`UNSUPPORTED_DELAMINATION`), screw pull-out (`UNSUPPORTED_SCREW_PULLOUT`)
- Shell: buckling, yield localization, crack propagation, snap-through, vibration fatigue
- Beam: buckling, fatigue crack, joint failure, torsion buckling
- Environmental temperature: results are 20 °C (293.15 K) material data unless
  `environment_temperature_k` is supplied, in which case only the documented
  linear modulus/allowable derating of ABS/PC/POM/PC-ABS/FR-4 is applied to the
  structural solver (never to drop dynamics); creep and time-dependent aging are
  not modeled (`lifecycle.age_days` is recorded but has no mechanical effect)
- Anisotropy: anisotropic materials (FR-4 laminate, flow-oriented polymers) are
  flagged `approximate`; the orthotropic plate solver is used when directional
  data exist, but weld-line and first-ply failures are not resolvable
  - STEP B-rep display uses the isolated FreeCAD/OCCT worker for arbitrary assemblies; the worker preserves native placements, colors, holes, voids, and curved surfaces in a deterministic GLB tessellation. The original STEP remains the CAD source of truth; the display mesh is not CAD-exact and is not automatically safe for mass properties. The stdlib parser remains limited to small faceted fixtures, while kernel-unavailable/failed advanced files block instead of degrading silently.
- Point loads (flagged as singularities; responses marked approximate)

## Accuracy claims require physical calibration

No accuracy percentage is claimed for uncalibrated screening output. A
95–98%-class physical accuracy statement is **only possible after instrumented
physical calibration** — comparing engine predictions against measured mass, CoM,
load-deflection, and drop-test data. That requirement is enforced in two ways:

- **Administrative correlation records** (`correlation_records`) compare
  predicted vs. measured metrics (`mass_kg`, `max_displacement_m`,
  `max_stress_pa`, `safety_factor`, `peak_force_n`). These are reviewed
  evidence with self-reported error fractions; they are **not** verified
  against simulated output, and the `CORRELATION_ERROR` gate fails when a
  recorded error fraction exceeds the configured policy
  `maximum_error_fraction`.
- **Measured-drop correlation** (`correlation.measured_drops`): a campaign of
  instrumented drop conditions (height, surface, orientation) with measured
  peak chassis acceleration (g) and settle time. The pipeline re-runs the
  simulator under each condition and compares predicted vs measured per
  condition (25% relative-error bound, R² ≥ 0.8, |bias| ≤ 10%, minimum 3
  conditions) via the `CORRELATION_MEASURED` integrity gate. This is the only
  path in which correlation is a genuine comparison of simulated output
  against experimental data (ASTM D3332-style instrumented drop practice).

Until measured-drop correlation exists, results are screening estimates labeled
`exploration_only` (exploration mode) or at most `qualification_pending_review`
(qualification mode). Uncalibrated screening is ranked-trend material, not
certification evidence.

## Qualification integrity gates

Six integrity gates hard-block qualification whenever the underlying analysis is
invalid, incomplete, or unsubstantiated:

| Gate | Fails when |
|---|---|
| `ANALYSIS_VALIDITY` | Underlying analysis is not valid and complete (invalid/inconclusive/failed structural response, failed validation report) |
| `IMPACT_VALIDITY` | Impact result is qualification-blocked or carries unsupported failure modes |
| `CORRELATION_ERROR` | Correlation error fractions exceed the policy `maximum_error_fraction` (self-reported records) |
| `CORRELATION_MEASURED` | A measured-drop campaign is supplied but per-condition error exceeds 25%, R² < 0.8, |bias| > 10%, or fewer than 3 conditions were evaluated (absent campaign: non-blocking, non-evaluable) |
| `REQUIREMENT_EVALUATION` | Structured requirement targets fail or cannot be measured |
| `CONVERGENCE_EVIDENCE` | Claimed convergence/force-balance evidence is not substantiated by a valid structural response |

## Requirement evaluation semantics

- A requirement is evaluated only when it carries **structured targets**:
  `{"metric": ..., "max": ...}` and/or `{"min": ...}` bounds, resolved against
  pipeline results (`mass_kg`, `max_displacement_m`, `max_stress_pa`,
  `safety_factor`, `peak_force_n`, or dotted paths).
- Each structured target is scored `pass` / `fail` / `not_available` (missing
  measured value or non-numeric bound).
- Requirements **without** a structured metric target are reported
  `not_evaluated` with reason "requirement carries no structured metric target".
  They never pretend to pass, and they do not fail the gate.
- `REQUIREMENT_EVALUATION` passes when all *evaluated* requirements pass.

## What this means for customers and investors

- Screening output is fast, deterministic, reproducible, and honest about its
  fidelity: closed-form and energy methods with full assumption transparency.
- Qualification claims are gated end-to-end: approved inputs, reviewed methods,
  correlation enforcement, and integrity checks — with no path to automatic
  acceptance.
- Physical-accuracy figures can be earned through instrumented calibration
  projects, after which correlation records with enforced error bounds support
  the claim. No metric is invented before that point.
