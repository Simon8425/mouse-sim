# mouse_sim — Gaming Mouse Simulation Engine (MVP)

**Version 0.1.0** · Python 3.9 stdlib only · no third-party dependencies · fully deterministic

## What it is

`mouse_sim` is a headless, stdlib-only, deterministic engineering-surrogate pipeline for gaming-mouse mechanical design. Given a JSON project document (objects, materials, load case, impact scenario, qualification inputs), it:

- imports geometry (analytic primitives, OBJ/STL meshes) and computes mass properties;
- conservatively classifies components and runs DFM-lite validation (wall thickness, geometry health, material approval, PCB clearance, classification);
- screens loads with closed-form surrogates (Navier thin-plate and Euler-Bernoulli beam) and estimates drop-impact response by energy balance;
- evaluates a 12-gate qualification readiness model with a hard exploration/qualification separation;
- emits byte-deterministic JSON, HTML, and manifest artifacts plus a digest-verified content-addressed cache.

Every artifact is a pure function of the inputs: no timestamps, no random state — identical inputs produce byte-identical outputs.

## Scope boundaries

- **Exploration vs. qualification is enforced, not suggested.** Exploration output is always `exploration_only` and unqualified. Qualification output reaches at most `qualification_pending_review`, and only when all gates pass; automatic promotion to accepted evidence is never performed.
- **No legal certification.** Results are engineering screening estimates (surrogate closed-form and energy methods), not certified FEA, and each analysis section carries an explicit unsupported-failure-mode list.
- Headless and stdlib-only by design: no UI, no cloud, no STEP/OCCT kernel.

## Quickstart

```bash
# Full analysis of the bundled example (exploration):
python3 -S -m mouse_sim run --input examples/mouse_baseline.json --output reports --emit json,html

# Version:
python3 -S -m mouse_sim --version

# Validate a material catalog JSON:
python3 -S -m mouse_sim material validate --input path/to/material_catalog.json

# Normalize geometry to JSON (auto/json/obj/stl/ascii; STEP is rejected with a diagnostic):
python3 -S -m mouse_sim import --input part.obj --format obj --units mm --out part.json
```

`run` writes `reports/report.json`, `reports/report.html`, and `reports/manifest.json`; stdout prints a `mode=... decision=... run_id=...` summary. `python3 -S` runs the package against the standard library only.

## CLI reference

| Command | Purpose | Key flags |
|---|---|---|
| `run` | Run the analysis pipeline over a project document | `--input PATH` (required), `--output DIR` (default `reports`), `--emit json,html`, `--stdout json\|summary\|none`, `--mode exploration\|qualification`, `--cache-dir PATH`, `--no-cache`, `--strict`, `--debug`, `--error-format text\|json` |
| `import` | Normalize geometry to JSON | `--input PATH`, `--format auto\|json\|obj\|stl\|ascii`, `--units mm\|cm\|m\|in`, `--out PATH` |
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
| | `importers.py` | OBJ/STL/JSON geometry import; STEP rejected with a diagnostic |
| | `mass.py` | Mass properties, measured overrides, aggregation |
| Materials & objects | `materials.py` | Builtin catalog, tolerant loading, assignment, approval checks |
| | `classification.py` | Name-synonym and topology-based conservative classification |
| Screening | `collision.py` | AABB clearance with tolerances and pair rules |
| | `validation.py` | DFM-lite structured findings (thickness, health, PCB, material) |
| | `qualification.py` | 12-gate readiness model with hard mode separation |
| Physics | `physics.py` | Navier shell / Euler-Bernoulli beam surrogates, load templates, preflight |
| | `impact.py` | Energy-based impact estimate, fatigue screening, desk-edge helper |
| Orchestration | `pipeline.py` | Deterministic run orchestration, run manifests, cache integration |
| | `cache.py` | Content-addressed artifact cache with digest verification |
| Output | `reports.py` | Deterministic JSON/HTML/evidence rendering |
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
- **Impact**: 0.75 m free fall, restitution 0.3, contact stiffness 1e5 N/m → energy-based peak force ≈ 320 N, peak acceleration, contact duration, and load-path stress; all five unsupported impact failure modes listed.
- **Disposition**: exploration → `exploration_only`, decision `not_qualified` (by convention — see `docs/walkthrough.md`), exit 0.

## Testing

```bash
python3 -S -m compileall -q mouse_sim
python3 -S -m unittest discover -s tests -p 'test_*.py'
```

Full suite: **208 tests, all green** (15 test modules, no third-party dependencies).

## Roadmap (deferred)

- Real FEM / explicit dynamics (current: closed-form and energy surrogates)
- Topology optimization
- Cloud / service deployment
- STEP/OCCT import kernel (currently rejected with a structured diagnostic)

## Web viewer and analysis console

`mouse_sim` includes a web-based 3D engineering console in the `web/` directory built with React 18, TypeScript, and Three.js. It connects to the Python API (`mouse_sim/web_api.py`) to deliver interactive model navigation, physical property inspection, and real-time visualization of analysis findings and structural overlays.

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
| `--host HOST` | Bind host IP address | `127.0.0.1` (`GMS_WEB_HOST`) |
| `--port PORT` | Bind port number | `8000` (`GMS_WEB_PORT`) |
| `--web-dist PATH` | Path to built SPA dist directory | `None` (`GMS_WEB_DIST`) |
| `--project-root PATH` | Base directory for file resolution | Current working directory |
| `--cache-dir PATH` | Cache storage directory | `.web-cache` |
| `--cors-origin ORIGIN` | Allowed CORS origins (exact matches only) | `*` in dev, exact in prod |
| `--max-json-bytes BYTES` | Max payload size for JSON requests | `8388608` (8 MiB) |
| `--max-geometry-bytes BYTES` | Max upload size for geometry files | `67108864` (64 MiB) |
| `--quiet` | Suppress HTTP access logging | `false` |

### Web API routes

| Endpoint | Method | Schema / Response | Description |
|---|---|---|---|
| `/api/health` | GET | `gms.web-health/1` | Server status, version, format capabilities, cache status |
| `/api/baseline` | GET | `gms.web-baseline/1` | Bundled baseline project request |
| `/api/materials` | GET | `gms.web-material-catalog/1` | Material catalog with density, modulus, and approval state |
| `/api/geometry/normalize` | POST | `gms.geometry-preview/1` | Normalizes uploaded OBJ/STL/JSON geometry; returns 422 for unsupported formats |
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
# Python Web API integration test suite (233 tests):
python3 -B -S -m unittest discover -s tests -p 'test_*.py'

# Web frontend typecheck, lint, unit tests, and build:
cd web
npm run typecheck
npm run lint
npm test -- --run
npm run build

# End-to-end Playwright test suite (Chromium, Firefox, Mobile):
npx playwright install chromium
npm run e2e
```

