# Testing guide

The test suite is divided by the engineering question it answers. A green test run means the implementation behaves as specified by the surrogate model; it does **not** mean the mouse is physically certified. Physical strength still requires instrumented calibration and real drop/force testing.

## Test groups

| Group | Location | What it protects |
|---|---|---|
| Foundations and data contracts | `tests/test_core.py`, `tests/test_cache.py`, `tests/test_importers.py` | Units, schema validation, deterministic hashes, geometry import, cache integrity, and malformed-input handling. |
| Geometry and mass | `tests/test_geometry.py`, `tests/test_mass.py`, `tests/test_classification.py` | Primitive/mesh measurements, inertia and center of mass, mass overrides, and conservative component classification. |
| Materials and DFM validation | `tests/test_materials.py`, `tests/test_validation.py`, `tests/test_collision.py` | Material provenance, wall thickness, geometry health, classification findings, PCB clearance, tolerance margins, and interference. |
| Static strength screening | `tests/test_physics.py` | Beam and thin-panel formulas, load units, fixtures, point-load limitations, convergence flags, Poisson-ratio guards, and structural boundary cases. |
| Impact and abuse screening | `tests/test_impact.py` | Drop/slam kinematics, contact force models, desk-edge/Hertz assumptions, force-model completeness, repeated impacts, invalid inputs, and unsupported battery/PCB/fracture hazards. |
| Qualification integrity | `tests/test_qualification.py` | Exploration/qualification separation, all readiness gates, integrity gates, correlation error, requirements, and evidence claims that cannot be substantiated. |
| Pipeline and reports | `tests/test_pipeline.py`, `tests/test_reports.py`, `tests/test_cli.py` | End-to-end orchestration, advanced impact input forwarding, deterministic reports, exit codes, manifests, and cache reuse. |
| HTTP API | `tests/test_web_api.py` | Request envelopes, body limits, diagnostics, CORS, static serving, and API-to-pipeline parity. |
| Frontend unit and accessibility | `web/src/__tests__/` | Reducer race guards, study presets, API contracts, scene safety, result rendering, gate-count reporting, keyboard access, and polite run-status announcements. |
| Browser workflow | `web/e2e/` | Baseline boot, geometry upload (OBJ/STEP/JSON normalization, kernel-backed STEP diagnostics, malformed-input diagnostics), model selection, result tabs, mode switching, responsive layout, single-origin serving. |

## Commands

```bash
# Backend: all model and API groups
python3 -S -m unittest discover -s tests -p 'test_*.py' -v

# Frontend: unit, reducer, rendering, and accessibility groups
cd web && npm test

# Frontend type contract check
cd web && npm run typecheck

# Browser matrix: Chromium desktop/tablet/mobile and Firefox
cd web && npm run e2e
```

The browser tests use semantic/product selectors such as `.run-status__value`; they should not depend on an obsolete styling class. Test names should describe the user-visible behavior and the engineering purpose, for example `negative_drop_inputs_fail_instead_of_becoming_no_impact` and `missing_force_model_is_inconclusive_not_valid_zero_force`.

## Interpreting important outcomes

- `validity=valid` means the selected surrogate calculation completed, not that fracture, plasticity, battery crush, PCB shock, or fatigue was solved.
- `validity=inconclusive` means the result is not safe to use for a decision, even if some intermediate quantities were calculated.
- A missing contact stiffness, stopping distance, or contact duration cannot produce a valid peak-force result.
- Qualification can reach only `qualification_pending_review`; a human review and physical evidence are still required.
- The five integrity gates are shown separately from the twelve readiness gates in the results rail, so reviewers can see why evidence is blocked.
