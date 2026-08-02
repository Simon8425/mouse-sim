# mouse_sim — Implementation Walkthrough

Python 3.9 stdlib-only, dependency-free, deterministic. This document covers the build order, final
architecture, module APIs, defects found and fixed during deep verification, exact verification
results, and known limitations.

## Build order (per the /goal operational plan)

Built bottom-up so each layer rests on verified foundations:

1. **Foundations** — `errors.py`, `units.py`, `canonical.py`, `model.py`, `schema.py` (+
  `schemas/mouse_sim.schema.json`): typed exceptions, SI conversion, deterministic hashing, versioned
  dataclass models, and dependency-free document validation. `__init__.py` exports every public name.
2. **Geometry & mass** — `geometry.py` (analytic primitives, transforms, mesh diagnostics,
  closed-mesh solid properties), `importers.py` (JSON/OBJ/STL loading; STEP rejected with a
  diagnostic), `mass.py` (calculated mass, measured overrides, aggregate inertia).
3. **Materials** — `materials.py`: builtin catalog (ABS, PC/ABS, FR4, LiPo, POM, PTFE, steel),
  tolerant JSON catalog loading, assignment resolution, approval/provenance helpers.
4. **Collision / validation / qualification** — `collision.py` (AABB clearance, tolerances, pair
  rules), `validation.py` (DFM-lite findings), `qualification.py` (12-gate readiness model, hard
  exploration/qualification separation).
5. **Physics & impact** — `physics.py` (Navier shell, Euler-Bernoulli beam, load templates,
  preflight, solver capabilities), `impact.py` (energy-based estimate, Miner fatigue screening,
  desk-edge helper).
6. **Pipeline & cache** — `pipeline.py` (deterministic orchestration, run manifests; never raises —
  errors are collected), `cache.py` (content-addressed, digest-verified artifacts).
7. **Reports & CLI** — `reports.py` (byte-deterministic JSON/HTML/evidence rendering), `cli.py`
  (argparse, exit codes 0/10/20/30/40/64).
8. **Example** — `examples/mouse_baseline.json`, a realistic 9-object exploration project (thin-wall
  shells 2.5/1.2 mm) used for E2E verification.
9. **Deep verification** — three passes (numerical, core, e2e) against analytical references with
  permanent regression tests; see the defects section.

## Final architecture

Single package, acyclic layered graph: foundations (`errors`, `units`, `canonical`, `model`,
`schema`) → geometry/mass (`geometry`, `importers`, `mass`) → materials/objects (`materials`,
`classification`) → screening (`collision`, `validation`, `qualification`) → physics (`physics`,
`impact`) → orchestration (`pipeline`, `cache`) → output (`reports`, `cli`). Every module is a pure
function of its inputs; all outputs are JSON-serializable; identical inputs give byte-identical
artifacts.

## Module reference (key APIs)

- `errors.py` — `MouseSimError`, `ValidationError`, `DocumentValidationError`, `ReferenceError`,
  `UnitError`, `SerializationError`, `CanonicalizationError`, `UnsupportedVersionError`
- `units.py` — `UNIT_SPECS`, `UnitSpec`, `to_si`, `from_si`, `convert`, `normalize_value`,
  `normalize_unit`, `unit_dimension`, `si_unit_for_dimension`, `unit_spec`
- `canonical.py` — `canonical_json`, `canonical_bytes`, `canonical_value`, `sha256_bytes`,
  `sha256_file`, `content_hash`, `entity_content_hash`, `hashed_entity`, `manifest_hash`, `cache_key`,
  `cache_key_for_manifest`, `make_cache_key`, `without_identity`
- `model.py` — `ProjectDocument`, `MaterialDefinition`, `MaterialProperties`, `Quantity`,
  `Component`, `MassOverride`, `Provenance`; enums `ResultMode`, `EvidenceDisposition`,
  `ApprovalState`, `ReviewState`, `RequirementStatus`
- `schema.py` — `SCHEMA_PATH`, `load_schema`, `validate_document`, `document_validation_errors`,
  `validate_references`, `serialize`/`deserialize`, `document_from_dict`/`document_to_dict`
- `geometry.py` — `Box`, `Cylinder`, `Sphere`, `Cone`, `Frustum`, `Compound`, `TriangleMesh`,
  `RigidTransform`, `Bounds`/`AABB`, `geometric_properties`, `closed_mesh_diagnostics`,
  `geometry_from_dict`
- `importers.py` — `load_geometry(path, fmt, units)`, `GeometryLoadResult`, `ImportDiagnostic`
- `mass.py` — `mass_properties`, `MassPropertiesResult`, `ObjectMassProperties`
- `materials.py` — `builtin_materials`, `load_material_catalog`, `MaterialCatalog`,
  `TRACEABLE_SOURCE_TYPES`, `STRUCTURAL_BEHAVIORS`
- `classification.py` — `classify_objects`, `ClassificationResult`, `ObjectClassification`
  (NAME_SYNONYMS, confidence 0.95)
- `collision.py` — `clearance_between`, `pair_clearance_matrix`, `ClearanceResult`, `STATUS_*`
  constants, `clamp`, `sign`
- `validation.py` — `run_validation`, `ValidationReport`, `ValidationFinding`,
  `check_wall_thickness`, `check_geometry_health`, `check_material`, `check_classification`,
  `check_pcb_clearance`
- `qualification.py` — `evaluate_qualification`, `GATE_SPECS` (12 gates), `QualificationResult`,
  `QualificationGate`, `impact_qualification_status`, `method_supports`
- `physics.py` — `solve_load_case`, `shell_panel_response`, `beam_response`,
  `preflight_structural_case`, `MOUSE_LOAD_TEMPLATES`, `SOLVER_CAPABILITIES`, `StructuralResponse`
- `impact.py` — `estimate_impact`, `ImpactResult`, `desk_edge_impact`, `repeat_impact_cycles`,
  `impact_qualification_status`, `IMPACT_UNSUPPORTED_FAILURE_MODES`
- `pipeline.py` — `run_pipeline(request, cache, use_cache)`, `reproduce_from_manifest`,
  `ENGINE_VERSION` 0.1.0, `RESULT_SCHEMA_ID`, `MANIFEST_SCHEMA_ID`
- `cache.py` — `ArtifactCache` (`store`/`load`/`contains`/`key_for`/`path_for`),
  `cache_key_for_inputs`
- `reports.py` — `render_json_report`, `render_html_report`, `render_evidence_package`,
  `REPORT_SCHEMA_ID`, `EVIDENCE_SCHEMA_ID`
- `cli.py` — `main`, `build_parser`, `PROGRAM` "mouse-sim", `VERSION` 0.1.0,
  `EXIT_OK`/`EXIT_NOT_QUALIFIED`/`EXIT_INVALID_INPUT`/`EXIT_UNSUPPORTED_FORMAT`/`EXIT_INTERNAL`/`EXIT_USAGE`
- `__main__.py` — `python -m mouse_sim` entry point

## Defects found and fixed (three deep-verification passes)

### Pass 1 — numerical (vs. closed-form analytical references)
- **Frustum inertia integral** (`geometry.py`): `Frustum._integral` divided by the wrong height
  power, producing garbage inertias (Ixx = −6263.08 for a radius-2/height-4 frustum vs. the cone limit
  20.1062); the exponent was corrected so the frustum matches its cone/cylinder limits.
- **Shell series off-by-one** (`physics.py`): the Navier odd-term range `range(1, order, 2)`
  excluded the top odd term, so `series_order=1` produced an empty series and a fabricated 0.0
  deflection; the stop is now `order + 2` so m, n = 1, 3, 5, … always include the top term.
- **Grid max bias** (`physics.py`): `_grid_max` seeded at −1.0 and picked the maximum positive
  value, so negative (suction) pressure returned a fabricated 0.0 m deflection at the corner instead
  of ≈ −9.3 mm at the center; it now scans by max absolute value while preserving sign.

### Pass 2 — core (integration, canonical, schema, classification)
- **Schema validation error handling** (`schema.py`): `document_validation_errors` raised on unknown
  root fields / undecodable documents instead of returning structured messages; it now reports
  structural errors first, then the decode error, and never raises (schema version errors still raise
  as `UnsupportedVersionError`).
- **Classification synonym mapping** (`classification.py`): added the name-based synonym table
  `NAME_SYNONYMS` (wheel/scroll, pcb/board, battery/lipo, shell_top, shell_bottom, skate, screw,
  button) with case/punctuation-insensitive matching and trailing-number tolerance, confidence 0.95,
  evaluated before the geometry fallback; fused objects remain unresolved and never claim semantic
  separation.

### Pass 3 — e2e (CLI, cache, reports)
- **CLI document wrapping** (`cli.py` `_cmd_run`): `run` sent the document under
  `request["document"]` while the pipeline reads top-level keys, so runs analyzed zero objects (null
  mass, empty geometry summary); the document is now passed as the request itself.
- **Cache-dir ignored** (`cli.py`): `--cache-dir` set an option but the cache was never constructed;
  `ArtifactCache(args.cache_dir)` is now passed to `run_pipeline`, and reuse is verified (same run_id,
  byte-identical outputs).
- **HTML script escaping case** (`reports.py` `render_html_report`): only lowercase `</script` was
  escaped in the embedded JSON blob; escaping is now case-insensitive and case-preserving.
- **Error code mapping** (`cli.py` `_exit_code_for_error_code`): `GEOMETRY_*` and
  `MATERIAL_CATALOG_INVALID` pipeline errors mapped to exit 40; they now map to 20 (invalid input),
  with `UNSUPPORTED*` → 30.
- **Mode key ignored** (`cli.py`): the document's `mode` field was ignored when `--mode` was absent;
  it is now honored (with `project.default_mode` fallback), so qualification documents actually run in
  qualification mode.

## Verification commands and final results

```bash
python3 -S -m compileall -q mouse_sim      # clean, exit 0
python3 -S -m unittest discover -s tests -p 'test_*.py'
```

- **Suite**: **208 tests, all green** (15 test modules). Progression across passes: 157 → 166
  (numerical) → 202 (core) → 208 (e2e, +6 regression tests).
- **E2E determinism**: consecutive runs produce byte-identical `report.json` / `report.html` (`cmp`
  clean); no timestamps anywhere.
- **Cache reuse**: a second run with the same `--cache-dir` returns the same `run_id` from the
  digest-verified cache with identical outputs.
- **Qualification blocked path**: a qualification-mode run with missing gates exits **10**
  (`EXIT_NOT_QUALIFIED`) with `evidence_disposition: qualification_blocked` and gate explanations.
- **Example run**: exploration `examples/mouse_baseline.json` exits 0; total mass ≈ 69.6 g;
  structural max stress ≈ 2.5 MPa (safety factor ≈ 10.3); impact peak force ≈ 320 N; all 18 report
  keys present; HTML self-contained and deterministic.

## Known limitations / remaining notes

- **`not_qualified` in exploration reports is by convention**: the report `decision` is
  `not_qualified` whenever a qualification section exists and `qualified` is false — including
  exploration (where the stdout summary says `completed`). This matches `reports._decision` and its
  regression test.
- **Impact energy method blocked for qualification**: impact results carry
  `qualification_blocked=True`; `impact_qualification_status` requires an approved method plus
  validated evidence.
- **STEP/OCCT and UI deferred**: STEP/STP input is rejected with a structured `unsupported_format`
  diagnostic; there is no geometry viewer or other UI; no cloud deployment.
- **Point loads flagged**: point loads set `POINT_LOAD_SINGULARITY` and mark the response
  approximate — local contact stress is not resolved.
- **Manifest has no `run_id`**: the run manifest intentionally omits it (top-level only), by design
  of `_build_manifest`.
- Closed-form surrogates remain screening tools; real FEM, explicit dynamics, and topology
  optimization are roadmap items, not MVP claims.

