# Architecture — Web Console, API, and Scene Hardening

This document describes the runtime architecture of the web layer: the Mission
Control dashboard, the baseline boot flow, the geometry upload lifecycle, and the
scene-hardening guards that keep untrusted runtime JSON from corrupting the 3D
viewport.

## Mission Control dashboard

The web console (`web/`, React 18 + TypeScript + Three.js, served by the Python
`mouse_sim/web_api.py` HTTP server) acts as a **Mission Control dashboard**: a
centralized control panel combining:

- **System monitoring** — engine version, API version, cache status, and supported
  geometry formats surfaced from `GET /api/health` (`gms.web-health/1`), rendered
  in the top bar with a live status badge.
- **Study launcher** — upload a geometry
  part, or run an analysis (`POST /api/analyze`) from one screen; results stream
  into the model tree, inspector panel, and results rail.

The analysis core remains headless and stdlib-only; the dashboard is a separate
presentation layer that consumes the deterministic API.

## Baseline boot flow

1. On mount, the dashboard calls `GET /api/projects/baseline` via
   `client.getBaseline()`.
2. The server loads `examples/mouse_baseline.json` under `--project-root` and
   returns a `gms.web-baseline/1` envelope (`source`, `project`); a missing or
   invalid baseline yields a 404 `E_NOT_FOUND` web-error envelope.
3. The workspace boots from that server-owned project. The request is wrapped in
   an `AbortController` and aborted on unmount, so React StrictMode remounts can
   never commit stale source data.

## Geometry upload lifecycle

1. **Worker parse (UI-thread offload)** — dropping a file sends the bytes to a
   Web Worker (`web/src/workers/geometry.worker.ts`). The worker parses OBJ (line
   by line, with comment/NaN/out-of-range-face handling and fan triangulation) and
   STL (binary via length-exact triangle-count check, or ASCII), applies the
   requested unit factor, deduplicates vertices, and returns typed arrays plus a
   capped warnings list (max 50 warnings). Parse failures return a structured
   `ParseError` to the main thread. JSON and STEP/STP uploads skip the worker and
    normalize server-side immediately: simple faceted STEP can use the stdlib
    path, while advanced assembly STEP is sent through the isolated
    FreeCAD/OCCT worker and produces a native GLB display asset.
2. **Normalize** — the parsed preview (or the raw JSON/STEP bytes) is posted to
   `POST /api/geometry/normalize?format=...&units=...` (`gms.geometry-preview/1`).
    The Python importer (`importers.load_geometry`) runs again server-side.
    Advanced B-rep uses `step_kernel.py` and FreeCAD/OCCT so placements,
    colors, holes, and curved surfaces are tessellated by the CAD kernel.
3. **422 envelopes with diagnostics** — unsupported formats return
    `E_INVALID_FORMAT`; unavailable or failed FreeCAD/OCCT returns
    `step_kernel_unavailable`/`step_kernel_failed` blocker diagnostics;
    `UnitError` yields an
   `invalid_units` blocker diagnostic and `ValueError` a `parse_failed` error
   diagnostic, all wrapped in the `gms.geometry-preview/1` envelope with
   `supported: false` and the diagnostics attached. The client deliberately
   accepts 422 as a valid envelope carrying preview error diagnostics
   (`accept422` in `api/client.ts`), so failures render in the dashboard with
   their machine-readable reason instead of being treated as transport errors.

## Scene hardening

### Geometry guards (`web/src/scene/geometryFactory.ts`)

Scene geometry arrives as JSON at runtime, so the TypeScript union is not treated
as validation. `isSafeGeometry` / `isSafeMesh` enforce a guard stricter than the
public contract where Three.js buffers require it:

- Vertices must be finite and **float32-representable** (`Math.fround` check).
- Triangle indices must be integers within the vertex count.
- Dimensions must be non-negative (box) or positive (sphere/cylinder/cone/frustum
  radii and heights).
- Transforms must be finite rotation rows + finite translation; non-finite
  transforms fall back to identity.
- Bounds computation rejects non-finite matrices/corners and returns `null` on
  any invalid geometry, so malformed payloads are dropped per-object instead of
  producing `NaN` scene graphs.

### Overlay scaling (`web/src/scene/overlays.ts`)

All analysis overlays — load arrows, fixture octahedra, displacement pins, stress
badges, contact plane, severity markers, selection anchor — are sized from a
**guarded plane radius** (`safePlaneRadius`): non-finite, non-positive, or
overflowing radii fall back to a safe default. `setPlaneRadius` re-derives the
radius from the current model bounds and re-applies the last overlay spec so all
radius-dependent geometry and labels track the model without callers retaining
the spec. Overlay resources (geometries, owned materials, sprite textures) are
tracked and disposed on re-apply and teardown.

## Request/response summary

| Stage | Direction | Schema | Failure mode |
|---|---|---|---|
| Health | `GET /api/health` | `gms.web-health/1` | — |
| Baseline boot | `GET /api/projects/baseline` | `gms.web-baseline/1` | 404 `E_NOT_FOUND` |
| Materials | `GET /api/materials` | `gms.web-material-catalog/1` | — |
| Upload normalize | `POST /api/geometry/normalize` | `gms.geometry-preview/1` | 422 envelope + diagnostics (`E_INVALID_FORMAT`, `step_kernel_unavailable`, `step_kernel_failed`, `invalid_units`, `parse_failed`); advanced STEP returns a kernel mesh plus GLB asset |
| Analyze | `POST /api/analyze` | `gms.web-analysis-request/1` → `gms.web-analysis-response/1` | 422/500 web-error envelopes (`E_INVALID_ENVELOPE`, pipeline validation failures) |
