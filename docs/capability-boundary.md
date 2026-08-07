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
| Qualification readiness | 17-gate model: 12 readiness gates + 5 integrity gates | Hard exploration/qualification separation; promotion to accepted evidence never performed |

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
 - STEP B-rep display uses the isolated FreeCAD/OCCT worker for arbitrary assemblies; the worker preserves native placements, colors, holes, voids, and curved surfaces in a deterministic GLB tessellation. The original STEP remains the CAD source of truth; the display mesh is not CAD-exact and is not automatically safe for mass properties. The stdlib parser remains limited to small faceted fixtures, while kernel-unavailable/failed advanced files block instead of degrading silently.
- Point loads (flagged as singularities; responses marked approximate)

## Accuracy claims require physical calibration

No accuracy percentage is claimed for uncalibrated screening output. A
95–98%-class physical accuracy statement is **only possible after instrumented
physical calibration** — comparing engine predictions against measured mass, CoM,
load-deflection, and drop-test data. That requirement is enforced structurally:

- Correlation records compare predicted vs. measured metrics (`mass_kg`,
  `max_displacement_m`, `max_stress_pa`, `safety_factor`, `peak_force_n`).
- The `CORRELATION_ERROR` integrity gate fails when any recorded error fraction
  exceeds the configured policy `maximum_error_fraction`.
- The `CORRELATION` readiness gate requires required correlation records to exist
  and be reviewed when the approved method demands them.

Until such calibration exists, results are screening estimates labeled
`exploration_only` (exploration mode) or at most `qualification_pending_review`
(qualification mode). Uncalibrated screening is ranked-trend material, not
certification evidence.

## Qualification integrity gates

Five integrity gates hard-block qualification whenever the underlying analysis is
invalid, incomplete, or unsubstantiated:

| Gate | Fails when |
|---|---|
| `ANALYSIS_VALIDITY` | Underlying analysis is not valid and complete (invalid/inconclusive/failed structural response, failed validation report) |
| `IMPACT_VALIDITY` | Impact result is qualification-blocked or carries unsupported failure modes |
| `CORRELATION_ERROR` | Correlation error fractions exceed the policy `maximum_error_fraction` |
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
