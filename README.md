# mouse_sim — Gaming Mouse Simulation Engine (MVP)

**Version 0.1.0** · Python 3.9 stdlib only · no third-party dependencies · fully deterministic

## What it is

`mouse_sim` is a headless, stdlib-only, deterministic engineering-surrogate pipeline for gaming-mouse mechanical design. Given a JSON project document (objects, materials, load case, impact scenario, qualification inputs), it:

- imports geometry (analytic primitives, OBJ/STL meshes, and kernel-tessellated STEP assemblies) and computes mass properties;
- conservatively classifies components and runs DFM-lite validation (wall thickness, geometry health, material approval, PCB clearance, classification);
- screens loads with closed-form surrogates (Navier thin-plate and Euler-Bernoulli beam) and estimates drop-impact response by energy balance;
- evaluates an 18-gate qualification readiness model (12 readiness gates + 6 analysis-integrity gates) with a hard exploration/qualification separation;
- emits byte-deterministic JSON, HTML, and manifest artifacts plus a digest-verified content-addressed cache.

Every artifact is a pure function of the inputs: no timestamps, no random state — identical inputs produce byte-identical outputs.

## Scope boundaries

- **Exploration vs. qualification is enforced, not suggested.** Exploration output is always `exploration_only` and unqualified. Qualification output reaches at most `qualification_pending_review`, and only when all gates pass; automatic promotion to accepted evidence is never performed.
- **No legal certification.** Results are engineering screening estimates (surrogate closed-form and energy methods), not certified FEA, and each analysis section carries an explicit unsupported-failure-mode list.
- Headless and stdlib-only by design for the analysis core; advanced STEP display uses an isolated optional FreeCAD/OCCT worker so the normal Python server remains dependency-free. Arbitrary STEP is never silently reduced to the handwritten approximation.

## Capability boundary — what is and isn't modeled

`mouse_sim` is a **deterministic exploratory screening engine**, not a general-purpose FEA product. Every structural result carries solver metadata identifying it as `screening_surrogate_v1` (`model_family: closed_form_screening`, `backend: surrogate_closed_form`); impact results are `energy_quasi_static_v1`. The metadata description is explicit: *"closed-form surrogate solver; screening-quality estimates, not validated FEA."*

**Modeled (closed-form + energy, always recorded with assumptions):**

- **Mass properties** — analytic primitive/mesh volume × density, measured-mass overrides, centroid and full inertia-tensor aggregation in the project frame.
- **Structural screening** — Navier simply-supported thin-plate response (`shell_navier_v1`) and elementary Euler-Bernoulli beam response (`beam_closed_form_v1`), linear elastic, small deflection, SI units.
- **Impact screening** — energy/momentum-balance estimate of peak force, peak acceleration, contact duration, and optional load-path stress, with translation/rotation energy partition when inertia is supplied.
- **DFM-lite validation** — wall thickness, geometry health, material approval/provenance, classification, and tolerance-aware PCB clearance.

**Not modeled (never hidden — always listed as unsupported failure modes):**

- Plasticity and nonlinear material behavior; explicit (transient) dynamics.
- Impact-specific hazards: battery crush, PCB/component shock, fracture, delamination, screw pull-out.
- Shell: buckling, yield localization, crack propagation, snap-through, vibration fatigue. Beam: buckling, fatigue crack, joint failure, torsion buckling.

**Accuracy claims require physical calibration.** No accuracy percentage is claimed for uncalibrated screening output — a "95–98% physical accuracy" statement is only possible after instrumented physical calibration of the model against measured data. Correlation is enforced in two ways: (1) `correlation_records` are administrative evidence (approved records with self-reported error fractions) — they are reviewed but are NOT verified against simulated output; (2) a `correlation.measured_drops` campaign compares the simulator's predicted peak chassis acceleration and settle time against user-supplied instrumented drop measurements (ASTM D3332-style), and the `CORRELATION_MEASURED` integrity gate fails when the per-condition error exceeds 25%, R² < 0.8, or the signed bias exceeds 10%. Until measured-drop correlation exists, results are screening estimates labeled `exploration_only` or, at most, `qualification_pending_review`.

### Qualification integrity gates

In addition to the 12 readiness gates (approved method, geometry, materials, pinned load case, reviewed fixtures, tolerance profile, solver capability, convergence, force balance, correlation, active requirement, no blocking issues), qualification runs evaluate six **integrity gates** that hard-block on invalid or unsubstantiated analysis:

| Gate | Meaning |
|---|---|
| `ANALYSIS_VALIDITY` | Underlying analysis is valid and complete (no invalid/inconclusive/failed response) |
| `IMPACT_VALIDITY` | Impact result is not qualification-blocked and carries no unsupported failure modes |
| `CORRELATION_ERROR` | Correlation error fractions are within the policy `maximum_error_fraction` |
| `REQUIREMENT_EVALUATION` | Structured requirement targets measure to pass |
| `CONVERGENCE_EVIDENCE` | Claimed convergence/force-balance evidence is substantiated by a valid response |

**Requirement evaluation semantics:** a requirement is evaluated only when it carries structured targets — `{"metric": ..., "max": ...}` and/or `{"min": ...}` bounds — resolved against pipeline results (`mass_kg`, `max_displacement_m`, `max_stress_pa`, `safety_factor`, `peak_force_n`, or dotted paths). Each structured target is scored `pass` / `fail` / `not_available`; requirements without any structured metric target are reported as `not_evaluated` and do not fail the `REQUIREMENT_EVALUATION` gate (they also never pretend to pass).

## Quickstart

```bash
# Full analysis of the bundled example (exploration):
python3 -S -m mouse_sim run --input examples/mouse_baseline.json --output reports --emit json,html

# Version:
python3 -S -m mouse_sim --version

# Validate a material catalog JSON:
python3 -S -m mouse_sim material validate --input path/to/material_catalog.json

# Normalize geometry to JSON (auto/json/obj/stl/ascii/step/stp; advanced STEP uses
# the isolated FreeCAD/OCCT tessellator when available):
python3 -S -m mouse_sim import --input part.obj --format obj --units mm --out part.json
```

`run` writes `reports/report.json`, `reports/report.html`, and `reports/manifest.json`; stdout prints a `mode=... decision=... run_id=...` summary. `python3 -S` runs the package against the standard library only.

### STEP import backend (optional)

- Simple STEP (faceted / AP242), OBJ, STL, and JSON imports need nothing — they are handled entirely by the stdlib.
- Advanced **BREP STEP** uses the optional FreeCAD/OCCT backend, auto-detected on Windows (`C:\Program Files\FreeCAD*\bin\freecadcmd.exe`), macOS (`/Applications/FreeCAD.app`), and via `PATH`.
- Override detection with `MOUSE_SIM_FREECADCMD=/path/to/freecadcmd`.

Check the backend first:

```bash
python scripts/find_freecad.py
```

Then import (the backend is used only when the file actually needs it):

```bash
python -m mouse_sim import --input part.step --format step --units mm --out part.json
```

When FreeCAD is absent, advanced STEP uploads fail with a clear `FreeCADCmd is unavailable` error instead of fabricating geometry — fail-closed by design.

### Run the web console

```bash
# Terminal 1 — backend API + built web dashboard (single process):
python3 -m mouse_sim serve --project-root . --web-dist web/dist

# Terminal 2 — frontend dev server (hot reload, proxies /api to the backend):
cd web
npm run dev
```

Open `http://127.0.0.1:8000/` (single-process serve) or `http://localhost:5173/` (Vite dev server).

### Run the test suites

```bash
# Python backend suite (926 tests, stdlib only):
python3 -m unittest discover -s tests -q

# Frontend unit tests (Vitest, 178 tests):
cd web && npm test

# End-to-end Playwright matrix (68 tests: Chromium desktop/tablet/mobile + Firefox):
cd web && npm run e2e
```

## CLI reference

| Command | Purpose | Key flags |
|---|---|---|
| `run` | Run the analysis pipeline over a project document | `--input PATH` (required), `--output DIR` (default `reports`), `--emit json,html`, `--stdout json\|summary\|none`, `--mode exploration\|qualification`, `--cache-dir PATH`, `--no-cache`, `--strict`, `--debug`, `--error-format text\|json` |
| `import` | Normalize geometry to JSON | `--input PATH`, `--format auto\|json\|obj\|stl\|ascii\|step\|stp`, `--units mm\|cm\|m\|in`, `--out PATH` |
| `material validate` | Validate a material catalog JSON | `--input PATH` |
| `validate` | Validate a full project document | `--input PATH`, `--emit json`, `--debug`, `--error-format` |
| `--version` | Print `mouse-sim 0.1.0` | — |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | success (exploration completed, or qualification pending review) |
| 10 | qualification run not qualified (`EXIT_NOT_QUALIFIED`) |
| 20 | invalid input / validation failure / parse error (`EXIT_INVALID_INPUT`) |
| 30 | unsupported geometry format (`EXIT_UNSUPPORTED_FORMAT`) |
| 40 | internal error (`EXIT_INTERNAL`) |
| 64 | usage error (`EXIT_USAGE`) |

## Architecture map

Layered and acyclic — foundations never import analysis layers, and each layer consumes only lower layers.

| Layer | Module | Responsibility |
|---|---|---|
| Foundations | `units.py` | SI unit specs, normalization, conversion, dimension checks |
| | `canonical.py` | Deterministic JSON + sha256 content addressing and cache keys |
| | `errors.py` | Typed exception hierarchy |
| | `model.py` | Versioned, serializable data models (project, materials, enums) |
| | `schema.py` | Schema loading and dependency-free document validation |
| Geometry & mass | `geometry.py` | Analytic primitives, transforms, mesh diagnostics, solid properties |
| | `importers.py` / `step_kernel.py` | OBJ/STL/JSON import plus FreeCAD/OCCT-backed advanced STEP tessellation |
| | `mass.py` | Mass properties, measured overrides, aggregation |
| Materials & objects | `materials.py` | Builtin catalog, tolerant loading, assignment, approval checks |
| | `classification.py` | Name-synonym and topology-based conservative classification |
| Screening | `collision.py` | AABB clearance with tolerances and pair rules |
| | `validation.py` | DFM-lite structured findings (thickness, health, PCB, material) |
| | `qualification.py` | 18-gate readiness + integrity model with hard mode separation |
| Physics | `physics.py` | Navier shell / Euler-Bernoulli beam surrogates, load templates, preflight |
| | `impact.py` | Energy-based impact estimate, fatigue screening, desk-edge helper |
| Orchestration | `pipeline.py` | Deterministic run orchestration, run manifests, cache integration |
| | `cache.py` | Content-addressed artifact cache with digest verification |
| Output | `reports.py` | Deterministic JSON/HTML/evidence rendering |
| | `web_api.py` | Deterministic HTTP API server for the web console |
| | `cli.py` | argparse CLI, exit-code contract |

Data flow: foundations → geometry/mass → materials/classification → collision/validation/qualification → physics/impact → pipeline/cache → reports/cli. The graph is acyclic.

## Key concepts

- **Run manifests** (`gms.run-manifest/1`): canonical snapshot of every request input plus per-key sha256 hashes and a manifest hash; `pipeline.reproduce_from_manifest` replays a run and verifies the hash.
- **Content-addressed cache**: one JSON artifact per sha256 key (engine-versioned), each carrying a self-verifying `_digest`; corrupt or tampered artifacts are treated as cache misses.
- **Canonical hashing**: stable under dict insertion order, int/float equivalence (1 == 1.0), and entity identity exclusion.
- **Validity states**: lifecycle `completed|failed`; validation validity state with confidence; solver validity `valid|approximate|inconclusive`; impact validity `valid|no_impact|failed`.
- **Evidence dispositions**: `exploration_only` → `qualification_blocked` → `qualification_pending_review`; accepted evidence is never produced.
- **Unsupported failure modes**: always listed, never silent — shell (buckling, yield localization, crack propagation, snap-through, vibration fatigue), beam (buckling, fatigue crack, joint failure, torsion buckling), impact (battery crush, PCB shock, fracture, delamination, screw pull-out).

## Example output summary

`examples/mouse_baseline.json` is a 9-object exploration project (shells, PCB, LiPo battery, scroll wheel, skates, screws). Running the pipeline produces:

- **Mass**: total ≈ **69.6 g** with per-object calculated masses, mass overrides for the screws, and centroid/inertia aggregation.
- **Structural**: `shell_flex` 5 kPa panel → Navier response (`shell_navier_v1`): max displacement ≈ 0.45 mm, max stress ≈ 2.5 MPa, safety factor ≈ 10.3 vs. ABS allowable (pass), with thin-shell and equilibrium assumptions recorded.
- **Impact**: 0.75 m free fall, restitution 0.3, contact stiffness 1e5 N/m → energy-based peak force ≈ 320 N, peak acceleration, and contact duration (load-path stress is not set here — it requires an explicit `load_path_area_m2`); all five unsupported impact failure modes listed.
- **Disposition**: exploration → `exploration_only`, decision `not_qualified` (by convention — see `docs/walkthrough.md`), exit 0.

## Testing

```bash
python3 -S -m compileall -q mouse_sim
python3 -S -m unittest discover -s tests -p 'test_*.py'
```

Full suite: **926 tests, all green** (33 test modules, no third-party dependencies).

See [`docs/testing.md`](docs/testing.md) for the test groups, engineering purpose of each group,
expected validity semantics, and the complete validation command matrix.

## Roadmap (deferred)

- Real FEM / explicit dynamics (current: closed-form and energy surrogates)
- Topology optimization
- Cloud / service deployment
- Optional FreeCAD/OCCT worker for advanced STEP tessellation is now implemented; the original STEP remains the CAD source of truth and the GLB is a display tessellation.

## Web console

`mouse_sim` includes a web-based 3D engineering console in the `web/` directory built with React 18, TypeScript, and Three.js. The workflow is intentionally simple: **upload a model → pick a material → run a test → read the results**. Three 3D rigid-body drop tests (Drop, Impact, Tumble) simulate the model falling, bouncing, and settling from real physics data, with configurable height, surface, drop count, orientation, spin, and mass — and play back the trajectory in the viewport. It connects to the Python API (`mouse_sim/web_api.py`) to deliver interactive model navigation, physical property inspection, and real-time visualization of analysis findings and structural overlays.

### Results

After a run finishes, the results rail opens automatically (desktop) with a single clean panel: an overall **PASS / WARN / FAIL verdict**, the key numbers (mass, impact force, peak acceleration, safety factor, max stress, max deformation), the test configuration and material used, and a short list of actionable issues only. The engineering disclosure data (assumptions, solver metadata, validation preparation) stays in the backend artifacts and the inspector; the results panel shows what matters.

### Baseline boot flow

On startup the dashboard opens an empty workspace. No baseline or demo geometry is loaded; the user explicitly uploads the geometry to display and analyze.

### Upload lifecycle

1. The user drops a geometry file; OBJ/STL parsing runs in a **Web Worker** (`geometry.worker.ts`) off the UI thread — text/binary parsing with unit conversion, triangulation, and bounded warning collection. JSON and STEP/STP files skip the worker and are sent straight to the server. Advanced STEP is imported by the isolated FreeCAD/OCCT worker and its native GLB is used for display.
2. The parsed preview is posted to `POST /api/geometry/normalize?format=...&units=...`, which runs the Python importer and returns a `gms.geometry-preview/1` envelope.
3. Missing CAD-kernel support, kernel failures, unsupported formats, and parse failures return **422 envelopes with diagnostics** (e.g. `step_kernel_unavailable`, `step_kernel_failed`, `parse_failed`); the client deliberately accepts 422 as a valid envelope carrying the preview error diagnostics.

### Scene hardening

- **Geometry guards** (`scene/geometryFactory.ts`): runtime validation stricter than the public TypeScript contract — every vertex must be finite and float32-representable, triangle indices must be integers within bounds, dimensions must be non-negative/positive, and transforms finite — so malformed API payloads can never poison Three.js buffers.
- **Overlay scaling** (`scene/overlays.ts`): overlay geometry (load arrows, fixture octahedra, displacement pins, contact plane) is scaled from a guarded plane radius that is re-derived from the current model bounds; non-finite or degenerate radii fall back to a safe default, and radius changes re-apply the overlay spec.

### Dev quick start

1. Terminal 1 — Start the Python API server:
   ```bash
   python3 -S -m mouse_sim serve --host 127.0.0.1 --port 8000 --cache-dir .web-cache
   ```

2. Terminal 2 — Start the Vite development server:
   ```bash
   cd web
   npm install
   npm run dev
   ```

Open `http://localhost:5173/` in your browser.

### Production single-process serve

Build the web application production bundle and serve both the SPA frontend and `/api/*` endpoints from a single Python process:

```bash
cd web && npm run build && cd ..
python3 -S -m mouse_sim serve --web-dist web/dist --cache-dir .web-cache --port 8898
```

Open `http://127.0.0.1:8898/` in your browser.

### Serve command options

| Option | Description | Default / Environment Override |
|---|---|---|
| `--host HOST` | Bind host IP address | `127.0.0.1` |
| `--port PORT` | Bind port number | `8000` |
| `--web-dist PATH` | Path to built SPA dist directory | `None` (API only) |
| `--project-root PATH` | Base directory for file resolution | `None` — package parent directory (repo root when run from checkout) |
| `--cache-dir PATH` | Cache storage directory | `None` (cache disabled) |
| `--cors-origin ORIGIN` | Allowed CORS origins (exact matches only, repeatable) | None sent; dev relies on the Vite `/api` proxy |
| `--max-json-bytes BYTES` | Max payload size for JSON requests | `134217728` (128 MiB) (`MOUSE_SIM_MAX_JSON_BYTES`) |
| `--max-geometry-bytes BYTES` | Max upload size for geometry files | `67108864` (64 MiB) (`MOUSE_SIM_MAX_GEOMETRY_BYTES`) |
| `--quiet` | Suppress HTTP access logging | `false` |

### Web API routes

| Endpoint | Method | Schema / Response | Description |
|---|---|---|---|
| `/api/health` | GET | `gms.web-health/1` | Server status, version, format capabilities, cache status |
| `/api/projects/baseline` | GET | `gms.web-baseline/1` | Bundled baseline project request |
| `/api/materials` | GET | `gms.web-material-catalog/1` | Material catalog with density, modulus, and approval state |
| `/api/geometry/normalize` | POST | `gms.geometry-preview/1` | Normalizes uploaded OBJ/STL/JSON/STEP geometry; advanced STEP returns a kernel-tessellated mesh plus a registered GLB display asset, or a blocker if FreeCAD/OCCT is unavailable |
| `/api/analyze` | POST | `gms.web-analysis-response/1` | Executes pipeline over `gms.web-analysis-request/1`; returns full result payload |

### Browser support & fallbacks

- Supported browsers: Evergreen Chrome, Firefox, Edge, Safari.
- WebGL 2.0 / 1.0 support: Required for 3D viewport rendering. If WebGL is unavailable or fails to initialize, the application renders a fallback notice while keeping the model navigator, inspector, and results rail fully operational.

### Truthful visualization limits

- **Geometry**: Rendered true-scale undeformed at actual input dimensions.
- **Overlays**: Load vectors, displacement pins, and fixture locations are rendered strictly from Python API locations; no synthetic heatmaps or unverified interpolations are generated.
- **Contact Plane**: Displayed as a labeled assumption aid based on impact contact normal.
- **Exploded View**: Purely display-only offsets; never passed to the Python pipeline.
- **Dispositions**: Verbatim evidence disposition strings (`exploration_only`, `qualification_pending_review`) are displayed without automatic promotion or re-interpretation.

### Verification commands

```bash
# Full Python test suite (926 tests, including 31 web-API integration tests):
python3 -B -S -m unittest discover -s tests -p 'test_*.py'

# Web frontend typecheck, lint, unit tests, and build:
cd web
npm run typecheck
npm run lint
npm test -- --run        # 178 unit tests, 19 files
npm run build

# End-to-end Playwright matrix (68 tests across Chromium desktop, Chromium
# tablet, Chromium mobile, and Firefox desktop projects):
npx playwright install chromium
npm run e2e
```
