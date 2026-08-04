# Gaming Mouse Simulation Engine — Technical Development Plan

## 1. Vision & Guiding Principles

A desktop simulation engine for gaming-mouse mechanical design. It imports CAD geometry, assigns materials, computes mass properties, runs lightweight mechanical/impact analyses, validates manufacturability, suggests optimizations, and produces an engineering report after every run.

Guiding principles (in priority order):

1. **Practical usefulness with explicit fidelity levels** — exploration needs fast trends, comparisons, and red flags; customer qualification needs approved methods, verified solver behavior, traceable inputs, and physical correlation rather than unqualified heuristics.
2. **Simplicity over completeness** — every feature must earn its complexity budget. If a heuristic gives 90% of the value at 10% of the cost, use the heuristic.
3. **Modularity with stable interfaces** — each module is independently replaceable (e.g., swap the MVP beam-solver for a real FEM solver later without touching the viewer or reports).
4. **Everything is data** — geometry, materials, loads, results, and reports are serializable to disk so sessions are reproducible and AI-assisted iteration is possible.

### Critical product boundary

The selected direction is **customer qualification support**, which requires a stricter track than a design-exploration tool. The software itself cannot issue a legal product certification; it can produce controlled, reviewable qualification evidence mapped to customer requirements and acceptance criteria. A separate qualified engineer, customer reviewer, test laboratory, certification body, or regulatory process remains responsible for the final claim.

The product should therefore have two explicit result modes:

- **Exploration mode:** fast heuristics and approximations are permitted, but results are labeled estimates and cannot be used as qualification evidence.
- **Qualification mode:** only approved geometry/material inputs, verified solver capabilities, controlled load cases, valid fixtures, traceable data, convergence evidence, applicable tolerances, and required physical correlation can produce an evidence-bearing report. Unsupported or approximate methods must hard-fail this mode rather than merely display a warning.

Every result must carry a validity state (`valid`, `approximate`, `inconclusive`, or `failed`) and an explanation of the assumptions that produced it. Every qualification report must also identify the governing requirement, acceptance criterion, reviewer/approval state, evidence gaps, and whether the result is suitable for release review.

The original plan is directionally correct, but the following items are implementation gates rather than optional enhancements:

- Define a versioned project and analysis schema before building modules.
- Define a coordinate-system, unit, tolerance, and geometry-quality policy.
- Treat component separation from a fused solid as assisted segmentation, not reliable automatic reconstruction.
- Resolve thin-shell modeling and contact/fixture behavior before committing to a solver implementation.
- Add mesh-quality, convergence, numerical-singularity, and material-data validity checks.
- Add tolerance-aware clearance and manufacturing-process profiles.
- Add physical correlation, calibration, independent review, and requirements traceability before presenting safety factors as qualification evidence.
- Add a headless command-line/API path, immutable run manifests, and deterministic result caching.
- Define the applicable product/customer standards and the quality-management evidence required for a qualification report.
- Define how customer requirements, confidential geometry, test data, and review comments are imported, versioned, redacted, and exported without requiring a cloud collaboration system.

---

## 2. Overall Architecture

Layered, unidirectional data flow. No module talks "sideways" except through the Core/Project layer.

```
┌─────────────────────────────────────────────────────────────┐
│                    Application Shell                        │
│        (UI framework, 3D Viewer, project management)        │
├─────────────────────────────────────────────────────────────┤
│                  Analysis Orchestrator                      │
│   (runs pipelines: import → classify → mesh → solve →       │
│    post-process → report; job queue, caching, progress)     │
├───────────┬───────────┬───────────┬───────────┬─────────────┤
│ CAD       │ Geometry  │ Material  │ Physics / │ Collision   │
│ Import    │ Engine    │ System    │ FEA Solver│ Engine      │
├───────────┼───────────┼───────────┼───────────┼─────────────┤
│ Impact    │ Mass      │ Optimiz.  │ Report    │ Validation  │
│ Solver    │ Calculator│ Module    │ Generator │ (DFM)       │
├───────────┴───────────┴───────────┴───────────┴─────────────┤
│                       Core Layer                            │
│  (Project model, units, geometry kernel wrapper, logging,   │
│   serialization, plugin interface)                          │
└─────────────────────────────────────────────────────────────┘
```

**Key architectural decisions:**

- **Single versioned project document** (JSON metadata + binary geometry/result sidecars). Define and version entities for `Project`, `Component`, `GeometryAsset`, `MaterialAssignment`, `ReferenceFrame`, `LoadCase`, `Fixture`, `Mesh`, `AnalysisRun`, `ValidationIssue`, `OptimizationSuggestion`, and `Report`. Add schema migrations and reject unsupported future versions safely.
- **Immutable source assets and immutable run manifests.** Keep the original imported file unchanged, store derived/healed geometry separately, and snapshot every input used by a run. A report must be reproducible from the run manifest even after the project is edited.
- **Boundary representation (B-rep) is the source of truth for STEP; the imported triangle mesh is the source of truth for STL/OBJ.** Do not claim exact volume, thickness, or solid mass for an open or non-manifold mesh. Mark repaired geometry and estimated mass explicitly.
- **Canonical coordinate frames and unit policy.** Store a project frame, mouse axes, sensor frame, gravity vector, and each component transform. Normalize internally to SI units while preserving source units and displaying them explicitly. Define handedness, axis orientation, angle convention, and absolute/relative tolerances once.
- **Solver interface abstraction** (`IStructuralSolver`) must expose capabilities and validity metadata, not only displacement/stress arrays. It should support load cases, fixtures, contacts/connectors, mesh statistics, convergence evidence, warnings, and result provenance so a later FEM backend can replace the MVP without changing reports or the UI.
- **All analyses are jobs** run by the Orchestrator with progress callbacks, cancellation, failure states, and checkpointed intermediate artifacts. The UI never blocks.
- **Headless execution is a first-class interface.** Provide a CLI or local API for import, material assignment, analysis, batch studies, and JSON reports. This is required for regression tests, AI-assisted iteration, and reproducible automation; it does not require cloud infrastructure.
- **Content-addressed caching.** Cache geometry, tessellation, meshes, factorizations, and results by normalized input hashes plus solver version/configuration. Never reuse a result when the material, frame, fixture, load, tolerance, or software version changed.

---

## 3. Module Responsibilities

### 3.1 CAD Import
- Parse STEP (B-rep, via Open CASCADE), STL and OBJ (meshes).
- STEP: preserve solids/faces, names, colors, assembly hierarchy, instance transforms, and supported metadata if present. Record the STEP application protocol (for example AP203/AP214/AP242) and importer warnings.
- STL/OBJ: wrap as mesh-only "dumb solid". STL normally has no trustworthy unit, material, assembly, or part-boundary metadata; OBJ units and object/group semantics are not guaranteed. Require an import confirmation when metadata is absent.
- Unit detection/normalization (STEP carries units; STL/OBJ require explicit assumption or user confirmation). Show a scale sanity check against expected mouse dimensions before analysis.
- Healing must never silently change engineering geometry. Store original and healed versions, list every repair (weld, normal reversal, hole closure, self-intersection fix), and mark affected results as approximate until reviewed.
- Import diagnostics: solid count, open/non-manifold mesh count, duplicate geometry, zero-area faces, self-intersections, extreme dimensions, invalid transforms, missing names, and unsupported entities.
- Output: immutable source asset plus derived bodies, transforms, diagnostics, and a user-review status in the Geometry Engine.

### 3.2 Geometry Engine
- Wraps Open CASCADE (OCCT) as the geometry kernel.
- Operations: boolean union/subtract/intersect, face/solid queries, bounding boxes, tessellation (B-rep → triangle mesh with deflection tolerance), sectioning, wall-thickness probing (ray-cast or medial-axis approximation), signed distance/minimum-distance queries, and local coordinate-frame construction.
- Maintain a geometry health report and a single numerical tolerance policy for vertex welding, contact coincidence, boolean operations, clearance, and mesh validation. Distinguish intentional mating contact from unacceptable interference.
- Support named or geometry-derived regions for loads, fixtures, screw bosses, PCB mounts, button hinges, skate contact patches, battery supports, and sensor location. Face indices alone are not stable after CAD re-import, so selections need persistent geometric signatures and a manual rebind workflow.
- **Component separation/classification**: see §4 — this is the highest-risk feature.
- Meshing for analysis: prepare shell, solid, rigid, and excluded regions explicitly. Surface mesh → volumetric tetrahedralization (TetGen/Gmsh) is appropriate for thick solids, but thin shells need a shell formulation or a validated thickness-aware alternative; do not send poorly shaped thin tets directly to the MVP solver.

### 3.3 Material System
- Material library: built-in database (ABS, PC, PC/ABS, POM/Delrin, nylon, TPU, FR4, LiPo cell, steel screws, PTFE skates, magnesium/aluminum alloys) + user-defined materials.
- Properties per material: density ρ, Young's modulus E, Poisson's ratio ν, yield strength σy, ultimate strength σu, elongation-at-break, friction coefficient (for skates/grip), cost per kg, and allowable temperature/use range. Distinguish tensile, compressive, shear, and fatigue allowables where data exists; "strength" is not a single universal value.
- Every property needs units, source/provenance, test condition, temperature, moisture/conditioning state, strain-rate applicability, confidence, and valid range. A default catalog value is an estimate, not a certified supplier value.
- Support isotropic materials in the MVP, but model anisotropy/orientation as a planned requirement for injection-molded polymers, glass-filled nylon, molded fiber, and FR4. Do not imply that isotropic ABS data predicts every molding condition.
- Separate material definition from structural behavior: PCB, battery, rubber pads, adhesive, foam, switch, and electronics may be modeled as rigid bodies, elastic solids, connectors, or excluded mass. The chosen abstraction must be visible in the report.
- Component → material/behavior assignment stored in the project document; supports overrides per region only after the base assignment is valid.
- Add material-data validation: positive density/modulus, plausible Poisson ratio, nonzero allowable, consistent units, and warnings for extrapolation or missing properties.
- Cost is an objective/input for optimization, not a mechanical property. Add process, fixed tooling, and supplier-cost fields only when cost optimization is actually introduced.

### 3.4 Mass Calculator
- Per component: volume (exact from B-rep, or divergence theorem on closed mesh) × density → mass, centroid, inertia tensor.
- Aggregates: total weight, overall center of mass, full inertia tensor in the project frame, per-component contribution table, CoM offset from geometric center and from sensor position (gaming-relevant: CoM vs. sensor axis), and the effect of each unknown/overridden mass.
- Support measured-mass overrides for batteries, PCBs, switches, cables, magnets, and other parts whose simplified geometry does not represent their actual mass. Show calculated, measured, and unknown mass separately.
- Report mass uncertainty caused by estimated density, open meshes, excluded components, and user overrides. A nominal CoM without an uncertainty or completeness indicator can be misleading.
- Runs on-demand and cached; invalidated on geometry/material/transform/mass-override change. Nearly free — always keep it live in the UI.

### 3.5 Physics / FEA Solver (static structural)
Test cases required: shell flex, side grip pressure, button press deformation, torsion, localized pressure.

- **Required solver decision gate:** run a representative thin-shell benchmark before implementation is locked. Very thin mouse shells modeled with poorly shaped linear tets can be excessively stiff, mesh-sensitive, or numerically ill-conditioned. The recommended architecture is shell elements for thin shells, solid tets for thick parts, rigid-body abstractions for internal components, and connector elements for screws/bosses/mounts. If the MVP cannot support that combination, use a validated external backend or explicitly restrict the MVP to thickness-qualified regions rather than silently producing misleading results.
- **MVP analysis model:** linear elastic, small-strain, isotropic materials; bonded interfaces and rigid internal parts where contact is not modeled. Every simplification is recorded per component and interface.
- **Load definitions:** distributed pressure patches, force vectors, enforced displacement, torque couples, gravity, remote loads, and prescribed acceleration. Avoid point loads in production cases because they create mathematical stress singularities; allow them only for verification tests or with explicit hotspot exclusion rules.
- **Fixture definitions:** fixed, pinned, elastic support, screw/bolt connector, rigid mount, symmetry, and contact boundaries. Fixtures must be selected from named semantic regions such as screw bosses and feet, not arbitrary entire faces by default.
- **Assembly behavior:** define bonded, sliding-contact, clearance-contact, rigid, and excluded interfaces. For MVP, support bonded interfaces and a documented rigid/contact approximation; do not call a fully fixed shell face a realistic grip or PCB mounting condition.
- **Mouse-specific load-case templates:** each template contains force/pressure/torque range, application patch, direction, fixture assumptions, component scope, acceptance thresholds, and optional load combinations. The user can edit the values; the engine must not hide arbitrary defaults.
  - Shell flex: bottom shell/feet or mount fixtures, distributed palm/side load, report global displacement and local wall strain.
  - Side grip pressure: opposing side patches with selectable pressure distribution and grip direction.
  - Button press: button force and travel or force-displacement curve, switch/hinge/support regions, and displacement at the actuation point.
  - Torsion: defined hand/support regions plus a torque or opposing forces, with a clear rotation reference axis.
  - Localized pressure: pad shape, contact area, force, and surface normal.
- **Outputs:** displacement components and magnitude, reactions, strain, principal stresses, von Mises stress where appropriate, maximum values and locations, stress/strain energy, safety factor or margin of safety, constrained/singular regions, and load-case pass/warn/fail status.
- **Post-processing rules:** use element-averaged or integration-point stress for decision metrics, identify mesh/fixture singularities, and report both global peak and filtered engineering hotspot values. A raw maximum at a fixed edge must not be presented as a physical failure prediction.
- **Mesh controls:** element type, target size, local refinement zones, quality metrics (aspect ratio, Jacobian, minimum angle, inverted elements), and adaptive refinement around valid hotspots. Require a convergence study for a result to receive a high-confidence status.
- **Post-MVP:** validated contact, geometric nonlinearity/large displacement, nonlinear polymer behavior, connector preload, modal analysis, and a higher-fidelity backend. Explicitly defer plasticity, creep, thermal coupling, and full transient structural dynamics until the validation framework supports them.

### 3.6 Collision Engine
- Purpose: (a) inter-part interference detection, (b) clearance checking PCB↔shell, (c) broad/narrow phase for impact pre-positioning.
- Implementation: BVH (bounding volume hierarchy) over triangle meshes + exact B-rep boolean check for confirmed interference volume where valid B-reps exist. Mesh-only inputs use a tolerance-aware mesh test and are labeled lower confidence.
- Clearance: minimum distance queries between component pairs with configurable thresholds, including nominal, deformed, and tolerance-expanded geometry. For example, PCB-to-shell clearance must be evaluated as nominal clearance minus shell displacement minus the relevant manufacturing/assembly tolerance, not just as a nominal distance.
- Distinguish intended contact, interference, clearance, overlap caused by simplified envelopes, and unknown due to invalid geometry. Component-pair rules define whether contact is allowed and what minimum clearance applies.
- Support swept-volume checks for button travel, scroll-wheel rotation envelope, cable paths, battery installation, screw insertion, and other moving or assembled parts. Full mechanism simulation remains out of scope.

### 3.7 Impact Solver
Scenarios: drop (specified height/orientation/surface), desk edge impact, repeated impacts (fatigue-ish).

- **MVP approach — energy-based estimation, not explicit dynamics:**
  1. Drop energy E = m·g·h.
  2. Define impact orientation, contact point/edge/face, target surface geometry/material, coefficient of restitution or contact stiffness, friction, and whether the mouse is free-falling or constrained.
  3. Include rigid-body translation and rotation from the total mass and inertia tensor before transferring the impact event to the deformable model.
  4. Estimate impact duration and peak force from a validated contact model or from an explicitly documented spring-contact assumption.
  5. Apply the resulting impulse or quasi-static equivalent load at the physical contact patch via the Physics solver → stress/deformation/failure map.
- The energy model is useful for ranking designs but cannot by itself predict fracture, battery damage, screw pull-out, delamination, or high-frequency PCB response. Report those as unsupported failure modes rather than hiding them inside a safety factor.
- **Desk impacts:** model desk face, edge, and corner separately. A desk edge is a changing contact geometry, not merely a point force; use contact-radius assumptions and show the contact patch used.
- **Repeated impacts:** Miner's-rule style cumulative-damage estimate using per-event stress cycles and material S–N/strain-life data only where data exists. For polymers and impact damage, this is a coarse screening estimate and must not be called a fatigue prediction without calibration.
- Add impact outputs: peak acceleration/force/impulse, contact duration estimate, orientation, contact location, energy partition assumptions, local and global deformation, and unsupported hazard warnings (especially battery crush/penetration and PCB/component shock).
- **Post-MVP:** explicit dynamics (central difference time integration) for true transient response — large effort, defer.
- Orientation sweep: run N canonical orientations (6 faces + corners/edges), report worst case.
- Qualification mode rule: the energy/quasi-static method can generate exploratory results only until it has been verified against the applicable customer test method and physical drop data. A customer evidence package must hard-fail if it depends on an unvalidated impact approximation.

### 3.8 Validation Module (DFM-lite)
- **Wall thickness:** sample-based probing; flag regions below material- and process-specific minimums and above sink-mark risk limits. Avoid universal values such as "ABS is always 0.8–1.0 mm"; thickness depends on grade, flow direction, tooling, process, geometry, and supplier guidance. Report probe direction, sample density, and uncertainty.
- **Part interference:** from Collision Engine, with volume quantification.
- **Geometry issues:** non-manifold edges, open shells, degenerate faces, slivers, self-intersections.
- **PCB validation:** collision/clearance under nominal, tolerance-expanded, and deformed shapes; keep-out envelopes for components, buttons, wheel, battery, connectors, and cables. **Stress coupling** — transfer shell deformation to PCB mounts only when mount correspondence and boundary conditions are defined; otherwise report a clearance result without inventing PCB stress.
- **Assembly validation:** screw/insert/boss alignment, fastener access, snap-fit travel, tool clearance, cable bend radius, battery installation/removal envelope, and intentional contact pairs. Keep these separate from structural stress validation.
- **Tolerance model:** support bilateral dimensional tolerances and simple worst-case clearance stack-ups in the MVP. Statistical tolerance analysis is later, but nominal-only clearance is insufficient for design decisions.
- Later versions: draft-angle analysis with a selected pull direction, undercut detection, uniform-thickness scoring, sink/warp risk, rib-to-wall rules, minimum fillets, parting-line/ejector constraints, and process-specific injection-molding checks.

### 3.9 Optimization Module
Heuristic, explainable suggestions (not black-box topology optimization for MVP):
- **Design-variable registry:** explicitly declare editable variables such as wall thickness, rib height/thickness, boss diameter, component position, battery location, and material choice. A generic imported STEP has no safe parametric edit history, so MVP suggestions must not silently modify CAD geometry.
- **Material removal candidates:** low-stress regions from all relevant load cases, with adequate stiffness margin, buckling/geometry checks, minimum wall/fillet/clearance rules, and no protected regions such as bosses, mounts, snap fits, PCB keep-outs, or battery barriers. Suggest a bounded edit and re-evaluate it before claiming a benefit.
- **Rib suggestions:** high-deflection regions + flat-wall detection → suggest rib placement/normal direction with estimated stiffness gain, but treat the plate-bending estimate as a ranking heuristic. Re-run structural and DFM validation for every accepted change.
- **Weight vs. stiffness/cost/CoM trade-off:** rank suggestions by a visible Pareto score such as mass, compliance, stress margin, cost, and CoM error. Do not collapse these into an unexplained single score.
- **CoM improvement:** sensitivity table — "moving battery Δx along Y shifts CoM by …" using Mass Calculator only; suggest placements aligning CoM with sensor axis.
- **Robustness:** later optimization must evaluate tolerance, material, and load uncertainty rather than optimizing a single nominal model. Preserve the baseline and support accept/reject/rollback for every suggestion.
- Later: real topology optimization (SIMP) on the tet mesh.

### 3.10 Report Generator
- Triggered after every completed analysis job; one report per job plus a project summary.
- Contents (per spec): total weight, per-component weights, center of mass (with sensor-axis delta), max deformation + location, max stress + location, safety factor estimate, critical areas list (top-N hotspots with screenshots), validation findings, applied loads/boundary conditions, solver metadata (mesh stats, assumptions, warnings).
- Add mandatory validity sections: input geometry health, material source/conditioning, coordinate frames, fixture/contact abstraction, mesh quality, convergence evidence, numerical singularity handling, tolerance assumptions, unsupported failure modes, confidence/validity status, and whether results are nominal or calibrated.
- Safety factor is `not available` rather than a guessed number when the allowable, stress measure, material condition, load case, or model validity is insufficient. In qualification mode, display the governing requirement and acceptance margin explicitly.
- Report both raw and engineering-filtered peaks where singularities or point loads exist. Include reactions and force/moment balance checks so an apparently plausible contour cannot hide an unconstrained or unbalanced model.
- Formats: HTML (self-contained, interactive screenshots) first; PDF via print; JSON export with a stable schema for downstream/AI consumption. Include machine-readable issue codes and pass/warn/fail statuses.
- Every number traceable: report embeds the project/run schema version, source asset hashes, project document hash, geometry-repair log, material IDs and sources, solver version, mesh configuration, load/fixture definitions, tolerance profile, and software/dependency versions.
- Support baseline-vs-candidate comparison reports with aligned units, changed inputs, changed assumptions, and delta metrics. Never compare results with different validity states without displaying the difference.

### 3.11 3D Viewer
- Render B-rep-tessellated meshes (per-component materials/colors, transparency, isolation).
- Result overlays: displacement (exaggerated), stress heatmaps, hotspot markers, wall-thickness maps, clearance violations.
- Result overlays must show the undeformed/deformed state, legend units/range, deformation scale, selected stress measure, excluded singular regions, and the coordinate frame used.
- Interaction: load-case setup by clicking faces (apply force/pressure/fixture), component selection → property panel, measurement tools, section/clipping planes, exploded view, visibility groups, and a way to inspect the exact geometry/element/load responsible for a warning.
- Import/classification workflow must make confidence, unresolved bodies, repaired geometry, missing materials, and excluded mass visually obvious. A user should be able to fix an issue without restarting the import.
- Keep the viewer dumb: it renders scene-graph state; analysis logic lives elsewhere.

### 3.12 Project and analysis workflow
- Import wizard: source file, units/scale confirmation, assembly/body review, geometry repair review, coordinate-frame setup, and component classification.
- Analysis setup: choose a named load-case template, assign components/material behavior, select fixtures and contact pairs, define tolerance/process profile, choose mesh/solver settings, and review a preflight checklist.
- Preflight must block only conditions that make a result impossible to interpret (for example, missing units, invalid mesh, no fixtures, or missing density for a requested mass result). Warnings should permit exploratory runs but lower validity status.
- Run lifecycle: `draft → preflight_failed/preflight_passed → queued → running → cancelled/failed/completed → reviewed`. Preserve logs and artifacts for every terminal state.
- Editing a geometry, material, fixture, load, tolerance, or solver version creates a new analysis snapshot; do not mutate the inputs of a completed run.
- Include autosave, crash recovery, undo/redo for project metadata, and a clear distinction between saved source geometry and derived analysis geometry.

### 3.13 Experimental Correlation & Calibration
- Store measured total/component mass, balance-derived center of mass, load-deflection curves, button force/travel data, clearance measurements, acceleration/force traces, and controlled drop-test observations as immutable test records.
- Record specimen identity, material batch, process condition, fixture geometry, sensor type/range, sampling rate, calibration state, uncertainty, and test environment. A measured number without test context is not a useful calibration target.
- Compare predicted and measured quantities by metric (mass, CoM, displacement, reaction, peak force, duration, or damage observation), including error bars and model discrepancy. Do not tune the model until it matches one test while hiding failures in another.
- Allow calibration parameters such as effective contact stiffness or support compliance to be versioned and scoped to a test family. Never overwrite raw measurements or silently alter a material catalog value.
- MVP: attach manual measurements and show comparison plots. Later: instrument import, parameter fitting, repeatability statistics, and qualification evidence packages.

### 3.14 Qualification & Evidence Control
- Maintain a lightweight customer requirements register: customer requirement ID and revision, source document/section, applicable product variant/SKU, load case/test method, acceptance threshold, evidence required, customer interpretation notes, owner, reviewer, and status. Support manual entry and structured import; do not assume a universal mouse standard.
- Define approved analysis methods and solver capabilities. A method record must specify geometry quality, element types, material model, fixture/contact assumptions, mesh/convergence rules, uncertainty treatment, and known limitations.
- Gate qualification runs on approved inputs: source geometry revision, material lot/data source, process condition, tolerance profile, load case revision, solver version, and calibration status. Exploratory results cannot be promoted automatically.
- Store review/sign-off state, reviewer identity, timestamp, comments, superseded reports, and immutable hashes. A simple append-only audit trail is sufficient for the MVP; enterprise document-management integration is not required.
- Qualification report status should be `draft`, `under_review`, `accepted`, `rejected`, or `superseded`. An accepted report is evidence for a defined requirement, not a universal safety certificate.
- Require a change-impact check when an input or solver changes. Re-run affected requirements instead of assuming an old report remains valid.
- Generate a customer evidence package containing a requirement-to-evidence matrix, approved input revisions, analysis reports, physical test records, raw/processed data references, assumptions, deviations, open issues, and review/sign-off state. Support redaction of internal-only notes and separate delivery copies from the working project.
- Keep confidential customer data local by default, disable telemetry by default, and provide project-level export controls. Do not add multi-user permissions or cloud workflow to the MVP unless a customer contract requires it.
- Keep regulatory/compliance evidence outside the mechanical solver boundary unless a specific standard is selected. Battery transport, battery safety, EMC, radio, RoHS/REACH, and charger requirements need specialized test plans and must not be implied by structural simulation.

---

## 4. Component Auto-Separation & Classification (highest-risk feature)

Real CAD files arrive as one fused solid, an unnamed solid soup, or a proper assembly. Plan for all three, degrade gracefully:

1. **Assembly preserved (best case):** use STEP assembly structure + names; map names to known component types via synonym dictionary ("wheel", "scroll", "pcb", "battery", "shell_top"...). User confirms mapping.
2. **Multiple solids, no names:** geometric heuristics —
   - Screws: small cylindrical, threaded-ish aspect ratio.
   - PCB: large flat box-shaped solid with aspect ratio thresholds, FR4 default.
   - Battery: rectangular prism with characteristic LiPo densities/sizes.
   - Scroll wheel: short cylinder between the buttons region.
   - Skates: thin flat pads on bottom face extremities.
   - Shells: thin-walled solids with dominant surface area; top vs. bottom split by centroid Z.
   - Buttons: thin-walled small solids adjacent to top shell front.
3. **Single fused solid:** region-growing segmentation by wall thickness and convexity may find candidate regions, but it cannot reliably recover semantic components that were modeled as one fused solid. Treat these as suggestions only. Offer manual split tools (cutting plane, paint-selection, boolean extraction, and component-envelope assignment) — **manual correction UI is mandatory, not optional.**

Output: each body tagged `{type, confidence, source, reviewStatus, structuralBehavior}`; user overrides persist in the project document. Track whether a component was imported separately, inferred, manually segmented, or represented by an envelope. Never block the workflow on classification — worst case everything is "generic part" and mass/sim still work, but do not claim per-component weight or interface stress where the geometry does not support it.

**Required classification rules:**

- Preserve an `unresolved` category; do not force a low-confidence body into PCB, battery, shell, or another safety-relevant type.
- Store protected regions and semantic interfaces separately from visual labels. A part named "cover" may still be a fixture or a structural shell.
- Allow multiple bodies to map to one logical component and one body to be split into logical regions only with an explicit user operation.
- Provide confidence explanations (name match, aspect ratio, location, adjacency, or manual choice) so the user can correct systematic errors.
- Classification does not create missing geometry. A fused shell cannot be separated into top/bottom components without a user-defined split or source assembly.

---

## 5. Suggested Technologies

| Concern | Recommendation | Alternatives |
|---|---|---|
| Language | **C++17/20** for engine core; **Python bindings (pybind11)** for scripting/AI-assist | C# + wrappers; pure Python too slow for solvers |
| Geometry kernel | **Open CASCADE (OCCT)** | CGAL (mesh-only, weaker B-rep) |
| Meshing | **Gmsh** for tetrahedral and surface meshes; OCCT tessellator for display; select shell/solid element strategy after the thin-shell benchmark | Netgen; TetGen subject to license review |
| Linear algebra | **Eigen** (+ SuiteSparse/CHOLMOD or Eigen's SparseLU for K solve) | PETSc (overkill for MVP) |
| MVP static solver | Verified solver spike first; recommended shell elements for thin shells + solid tets for thick parts, using a backend that supports both | In-house linear elements only if verification passes; CalculiX/Code_Aster subprocess integration |
| Post-MVP FEM backend | **CalculiX** (subprocess, GPL, .inp decks) or Code_Aster | MFEM/deal.II (library, heavier) |
| Collision/BVH | In-house BVH over trimesh, or `libigl` AABB / FCL | Embree (ray-centric) |
| Misc geometry algos | **libigl**, CGAL selective use | — |
| Viewer/UI | **Qt6 + OpenGL/Vulkan-capable rendering path** for a desktop application; alternative: Three.js frontend + local engine service | Unreal/Unity (rejected: licensing + weight) |
| Serialization | JSON (nlohmann) + binary blob sidecar (HDF5 or zip of .npy-like buffers) | SQLite project container |
| Report | HTML template engine (inja/mustache) → self-contained HTML; weasyprint/Chromium print for PDF | LaTeX (overkill) |
| Build/deps | CMake + vcpkg/conan; CI on Win/Linux/macOS; dependency license/SBOM tracking | — |
| Testing | Catch2/GoogleTest; golden-file regression for solver results | — |

**Mathematical models (concise):**
- Mass: m = ρ·∫dV; centroid via divergence theorem on closed meshes (exact for tets).
- Linear statics: K u = F, K assembled from the verified shell/solid/connector element set, with isotropic Hooke's law σ = C(E,ν)·ε where applicable.
- Stress: von Mises σ_VM = √(½[(σ1−σ2)²+(σ2−σ3)²+(σ3−σ1)²]); safety factor is `allowable / engineering stress metric` only when the material behavior, failure mode, conditioning, and load case justify that metric. Do not universally use σy/σ_VM,max for polymers, brittle parts, joints, fatigue, impact, or singular peaks.
- Impact (MVP): E = mgh; spring-contact estimate F_peak ≈ √(2·k_eff·E) with k_eff probed from static solver (unit-load deflection at impact point).
- Fatigue (coarse): Miner's sum D = Σ n_i/N_i(σ_i), fail at D ≥ 1.
- Wall thickness: ray-cast both directions from surface samples; median local thickness field.
- Optimization heuristics: stress percentile maps, plate stiffness ∝ t³, rib stiffness gain ∝ (h_rib/t)³ estimates.

**Technology decision gates:**

- Do not select the in-house tet solver solely because it is small. Verify thin-shell behavior, fixture behavior, stress recovery, and mesh convergence on representative mouse geometry first.
- Decide whether the distributed product can legally include or invoke Gmsh, TetGen, CalculiX, Code_Aster, OCCT, CGAL, and other dependencies. Keep a license matrix and isolate copyleft subprocesses where appropriate; do not assume a dependency's license permits every intended distribution model.
- Keep the core solver and project schema independent of Qt, Python, and any external solver file format. This preserves the option to add a CLI, scripting, or a different UI without rewriting analysis logic.

---

## 6. Module Dependencies

```
Core (project doc, units, logging)  ← everything depends on this
CAD Import        → Geometry Engine
Geometry Engine   → Core
Material System   → Core
Mass Calculator   → Geometry, Material
Collision Engine  → Geometry
Physics Solver    → Geometry (meshing), Material, Collision (fixtures/contacts)
Impact Solver     → Physics Solver, Mass Calculator, Material
Validation        → Geometry, Collision, Physics Solver (PCB coupling)
Optimization      → Physics Solver results, Mass Calculator, Validation (thickness)
Correlation       → Mass, Physics, Impact, Validation, and immutable experimental records
Qualification     → requirements register, approved methods, Correlation, all analysis manifests, and Report Generator
Report Generator  → all result producers (read-only)
Viewer            → Geometry (tessellation), results for overlays
Orchestrator      → all (scheduling/caching only)
```

Hard rule: no cycles; results flow through the project document, not direct module-to-module calls, except Orchestrator-invoked pipelines.

---

## 7. Development Phases

### Phase 0 — Foundations (MVP-0)
- Versioned core project model + serialization/migrations, coordinate frames, units, tolerances, logging, immutable source/run manifests.
- OCCT integration: STEP/STL/OBJ import, tessellation, import diagnostics, repair review, basic viewer (rotate/pan/zoom, component list, colors).
- Material library (hardcoded ~12 materials) + assignment UI, property provenance, measured-mass override, and material validation.
- Mass Calculator: weights, per-component table, CoM with visualization marker.
- Headless import/mass/report path and preflight checklist.
- Requirements register, approved-method placeholders, immutable review/audit records, and explicit exploration-versus-qualification mode gating.
- **Exit criteria:** import a mouse STEP, confirm scale and frames, review geometry diagnostics, assign materials, see correct weight/CoM with completeness and uncertainty status, and reproduce the same mass result from a saved run manifest.

### Phase 1 — MVP (usable v0.1)
- Component classification (heuristics + manual override UI), unresolved state, protected regions, and structural behavior assignment.
- Solver benchmark gate, then mesh/solver implementation appropriate for thin shells and thick parts; do not assume coarse linear tets are valid for thin walls.
- Load-case templates: pressure patch, distributed force, enforced displacement, gravity, torque, fixtures, bonded interfaces, and named semantic regions. Point forces are verification-only or explicitly filtered.
- Results: displacement/stress/strain fields, reactions, force balance, max and filtered engineering values + locations, safety factor/margin, mesh quality, and validity status with heatmap overlay in viewer.
- Wall-thickness validation + tolerance-aware interference/clearance checks, intended-contact rules, and deformed-clearance preview.
- **Report Generator v1** (HTML/JSON with all mandated fields, assumptions, warnings, provenance, and machine-readable statuses).
- Impact MVP: energy→quasi-static method, single orientation.
- Headless execution, cancellation, failed-run preservation, and deterministic cache invalidation.
- **Exit criteria:** full exploratory pipeline import→classify→assign→mesh→simulate→validate→report on a real mouse model, with at least one analytically verified load case and no high-severity preflight warnings hidden from the user. This phase must not generate accepted qualification evidence yet.

### Phase 2 — v0.5 (engineering credibility)
- Impact orientation sweep + repeated-impact (Miner's rule) estimates.
- PCB stress-from-shell-deformation coupling with explicit mount correspondence and PCB abstraction choice.
- Optimization Module v1 (thinning candidates, rib suggestions, CoM sensitivity).
- Improved classification (convexity/region-growing for fused solids).
- Mesh refinement controls + convergence check (auto refine at valid hotspots, compare; exclude singularities).
- Tolerance profiles, worst-case clearance stack-ups, assembly envelopes, and basic screw/boss/cable checks.
- Physical correlation workflow: measured mass/CoM, load-deflection test, and drop-test comparison data can be attached to a project and used to calibrate/qualify assumptions.
- JSON report export; project diff/compare (two design variants side-by-side) with validity-state comparison.
- First controlled customer-qualification pilot for a narrowly defined customer requirement, using approved inputs, a reviewed method, a physical correlation dataset, a requirement-to-evidence matrix, redacted export package, and customer/human acceptance decision.

### Phase 3 — v1.0 (robustness)
- Explicit-dynamics impact solver (or validated empirical model upgrade).
- CalculiX backend option for higher-fidelity cross-checks.
- Draft angle / undercut / injection-molding DFM checks.
- Modal analysis (rattle/resonance).
- SIMP topology optimization (optional, behind flag).
- Batch/parametric studies (sweep wall thickness, report table), robust optimization under uncertainty, and design-variable evaluation loops.
- Nonlinear/contact behavior, connector preload, and better PCB/laminate modeling only after correlation evidence supports them.
- Controlled release/evidence package workflow, independent solver review, change-impact reruns, and standard-specific qualification reports.

### Explicitly deferred / out of scope
For the MVP and early releases: nonlinear/plastic materials, thermal, creep, electronics simulation (signal integrity, battery thermal), statistical tolerance analysis, full motion/mechanism simulation (click kinematics beyond enforced displacement), and cloud/collaboration features. These can be reconsidered only after the core model is verified and physically correlated.

---

## 8. MVP Simplifications (explicit)

| Full feature | MVP simplification |
|---|---|
| Component recognition (ML/classification) | Geometry heuristics + confidence + mandatory manual override UI; fused-solid separation is never promised as automatic |
| Qualification input geometry | STEP assembly or reviewed, watertight derived geometry with revision traceability; STL/OBJ remain exploratory unless independently reviewed and verified |
| Material catalog | Catalog values are exploratory defaults; qualification requires approved supplier/test data with condition and batch traceability |
| Explicit impact dynamics | Energy + stiffness-probed quasi-static load for exploration only; qualification mode blocks this method until validated for the customer requirement |
| Real fatigue | Miner's rule with coarse S–N curves, labeled "estimate" |
| Topology optimization | Stress-threshold heuristics for thinning/ribs |
| Nonlinear/contact FEM | Linear elastic model with bonded/rigid/contact approximations selected per interface; small-strain assumption and unsupported contacts stated in every report |
| Injection-molding DFM | Wall thickness + interference + geometry health only |
| Curved high-order meshes | First-order shell/solid elements with local refinement only where verified; no high-order formulation in the MVP |
| Parametric CAD features | Dumb solids only; re-import to update |
| Tolerance analysis | Worst-case clearance expansion and simple stack-ups; no statistical tolerance distribution |
| Accepted qualification | Analytical verification and optional measured correlation in the MVP; no accepted customer-qualification evidence until reviewed methods, physical correlation, and customer criteria are present |
| PCB/electronics | Envelope/rigid-body clearance first; detailed laminate, component shock, and battery failure modeling deferred |
| Qualification evidence | MVP can generate draft evidence packages only; accepted qualification requires reviewed methods, physical correlation, acceptance criteria, and change-controlled sign-off |

---

## 9. Performance Bottlenecks & Mitigations

1. **Tet meshing of complex shells** (Gmsh can choke on dirty B-reps) → pre-healing pass, deflection-based coarsening of display mesh before meshing, cache meshes by geometry hash.
2. **Sparse solve time** (linear tets, 100k–1M DOF) → CHOLMOD/SparseLU; keep MVP meshes ≤ ~300k tets via adaptive refinement instead of global density.
3. **Wall-thickness probing** (O(samples × rays)) → BVH-accelerated ray casts, subsample + interpolate.
4. **Orientation-sweep impacts** (N solves) → share K factorization when only load vector changes (direct solver reuse); parallelize independent orientations across threads.
5. **Viewer overlay rendering** on big meshes → decimated overlay mesh, vertex-attribute heatmaps, LOD.
6. **Repeated full-pipeline reruns** during optimization iteration → Orchestrator caches every stage keyed by input hashes; only invalidated stages re-run.
7. **Memory pressure from geometry plus field results** → stream large result fields, keep analysis mesh separate from display mesh, compress immutable sidecars, and expose a resource estimate before running.
8. **Non-deterministic parallel results** → deterministic mesh/ordering seeds for regression mode, controlled floating-point tolerances, and a clear distinction between exact reproducibility and numerically equivalent parallel output.
9. **External solver/process failures** → bounded temporary directories, captured stdout/stderr, timeout/cancellation handling, exit-code mapping, and retention of the failed input deck/log for diagnosis.

---

## 10. Risks & Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Auto-classification/separation unreliable on real files | High | Ship manual override and unresolved state from day 1; never imply that a fused solid has been semantically separated |
| Thin-shell solver gives false stiffness or stress | Critical | Run a representative solver spike; use shell/solid/rigid/connector representations or a verified external backend; require mesh/convergence and analytical verification |
| Boundary conditions do not represent a real grip or assembly | Critical | Named fixture/load templates, interface definitions, reaction balance checks, physical load-deflection correlation, and explicit validity warnings |
| Material data is not representative of the molded part | High | Store provenance/conditioning/anisotropy metadata, expose uncertainty, support supplier data and correlation, and avoid universal catalog allowables |
| In-house FEM solver correctness | High | Validate against analytical cases (cantilever, plate with hole, thick cylinder) + independent solver cross-checks; golden-file regression tests |
| Nominal clearance passes but tolerance stack fails | High | Tolerance-expanded geometry and worst-case stack-up in MVP; distinguish intended contact from interference |
| Impact quasi-static approximation misleading | High | Always display assumptions and unsupported failure modes; calibrate against instrumented drops before increasing confidence |
| Reported maximum stress is a numerical singularity | High | Avoid point loads, use load patches, filter/label singularities, perform convergence checks, and report engineering hotspot metrics separately |
| PCB/battery abstractions hide critical failure modes | High | Use explicit rigid/envelope abstractions and warnings; defer battery crush, PCB component shock, and electrical/thermal claims |
| Customer requirement target is undefined | Critical | Import the customer requirement revision, test method, acceptance criteria, variants, deviations, and evidence ownership before accepting qualification reports |
| Qualification evidence is not auditable | Critical | Versioned inputs/methods, immutable run manifests, review state, change impact, independent review, and controlled release records |
| OCCT learning curve/integration pain | Medium | Thin wrapper layer; isolate all OCCT types behind Geometry Engine interface |
| Geometry repair changes the design silently | High | Preserve original asset, log every repair, require review, and lower result validity when repaired regions affect analysis |
| Dependency license blocks distribution | Medium | Maintain a license/SBOM matrix and choose subprocess or replacement strategy before implementation |
| Scope creep (user asks for CFD, electronics…) | Medium | Deferred list in §7 is the contract |

---

## 11. Validation Plan (for the engine itself)

- **Unit tests:** mass properties on primitives (sphere/box exact values), inertia/coordinate transforms, BVH correctness, tolerance expansion, contact classification, material DB integrity, schema migrations, and serialization round-trips.
- **Geometry verification:** known-good and intentionally damaged STEP/STL/OBJ fixtures for units, scale, open meshes, non-manifold edges, self-intersections, repair logs, tessellation, and stable semantic-region rebinding.
- **Solver verification:** cantilever beam (Euler–Bernoulli), simply supported plate, thick-cylinder pressure, shell patch, connector/fixture reactions, and rigid-body mode detection. Compare against analytical solutions and at least one independent solver where feasible. Do not use a blanket error target if the quantity is a singular peak; define separate displacement, reaction, energy, and filtered-stress tolerances.
- **Mesh/convergence verification:** refine representative meshes, compare engineering outputs, detect inverted/poor elements, and require a convergence status before labeling a hotspot high confidence.
- **Impact verification:** unit tests for energy/impulse bookkeeping, orientation transforms, inertia, contact patch assumptions, and comparison against instrumented drop data before treating peak force or damage estimates as calibrated.
- **Physical correlation:** attach measured component/total mass, balance-derived CoM, load-deflection curves for shell/button cases, clearance measurements, and controlled drop results. Store test fixture, force/position sensor data, specimen/material batch, and uncertainty. Use this to calibrate model parameters and record model discrepancy; do not overwrite raw evidence.
- **End-to-end golden tests:** 2–3 reference mouse-like models with pinned expected outputs (weight, CoM, displacement, reactions, and filtered stress within defined tolerances) plus negative cases that must fail preflight.
- **Report completeness check:** automated assertion that every mandated field (§3.10) is present, typed, unit-tagged, and accompanied by validity/provenance metadata.
- **Reproducibility tests:** same immutable run manifest produces the same result within numerical tolerance across supported platforms; cache keys change when any analysis-affecting input changes.
- **Qualification-process verification:** every accepted report maps to a requirement and approved method, includes acceptance criteria and uncertainty, records reviewer approval, and can be reconstructed from immutable inputs. Changes to geometry, materials, solver, or method must identify affected reports and trigger reruns.
- **Independent review:** solver equations, element formulations, load templates, material allowables, and report calculations receive review separate from the implementation author. Keep known-error and limitation records.
- **Release QA:** versioned builds, dependency/license inventory, test evidence, defect/change records, and controlled configuration. Do not call a build qualification-capable until these controls exist.

---

## 12. Future Expansion Possibilities

- Real topology optimization (SIMP/level-set) with manufacturing constraints.
- Explicit dynamics & drop-test animation playback.
- Click-feel simulation (button kinematics + switch force curves + snap-through).
- Ergonomics module (hand-size envelope fit, grip heatmaps from scanned hands).
- Injection-molding flow-lite (fill pattern, weld-line prediction via heuristic or Moldflow-link export).
- Material cost/CO₂ optimization objectives.
- AI-copilot loop: LLM agent reads JSON reports, proposes design edits, re-runs pipeline (architecture already supports this via Orchestrator + JSON artifacts).
- Cloud batch runs and a shared material/component library.

---

## 13. Build Order Summary (for the implementing agent)

1. Versioned core project model, coordinate frames, units/tolerances, immutable run manifests, schema migrations, and serialization.
2. OCCT/mesh wrapper + import (STEP/STL/OBJ), diagnostics/repair review, and viewer with component list.
3. Material system, behavior abstractions, measured-mass overrides, Mass Calculator, inertia, and CoM marker.
4. Classification heuristics, unresolved/confidence states, semantic regions, and manual override/segmentation UI.
5. Solver representation benchmark: compare shell, solid, rigid, and connector choices on analytical and representative mouse cases before selecting the MVP backend.
6. Selected mesh/solver integration + analytical verification tests, preflight, fixture/load-case definition, result overlays, mesh quality, and convergence status.
7. Wall-thickness + tolerance-aware interference/clearance/assembly validation.
8. Report Generator (HTML/JSON) with provenance, assumptions, validity status, and machine-readable issues.
9. Experimental data attachment/correlation workflow, then Impact MVP (energy method) with explicit limitations.

Each step is independently demoable and testable; do not proceed to production load-case work before the solver representation benchmark, analytical tests, fixture reaction checks, and preflight rules pass.

## 14. Audit Conclusion & Remaining Decision

### Coverage of the requested scope

- CAD import: STEP, STL, OBJ, units, assemblies, transforms, repair diagnostics.
- Component separation: assisted classification with confidence, manual segmentation, unresolved state, and explicit limits for fused solids.
- Materials: density, stiffness, strength, elasticity-related properties, friction, cost, provenance, conditioning, and uncertainty.
- Mass properties: total/component mass, center of mass, inertia, sensor-frame offset, measured overrides, and completeness status.
- Mechanical simulation: shell flex, side grip, button press, torsion, localized pressure, fixtures, interfaces, deformation, stress, reactions, safety/margin, and critical areas.
- Impact: drop, desk face/edge/corner, orientation, repeated-impact estimate, impulse/contact assumptions, and unsupported battery/PCB failure warnings.
- Internal validation: collision, nominal/deformed/tolerance-aware clearance, PCB coupling where correspondence exists, moving envelopes, cable/battery/button checks.
- Manufacturing validation: wall thickness, interference, geometry health, tolerance stack-ups, assembly checks, and later process-specific injection-molding analysis.
- Optimization: explainable material-removal/rib/CoM suggestions, declared design variables, Pareto trade-offs, re-evaluation, and rollback.
- Reporting: required metrics plus provenance, assumptions, mesh/convergence evidence, validity status, issue codes, and baseline comparison.

### Important additions made during audit

The plan now includes the missing foundations that would otherwise make the requested outputs unreliable: versioned data contracts, coordinate frames, tolerance policy, import healing provenance, solver-representation selection, realistic fixtures and contacts, singularity/convergence handling, material-data provenance, physical correlation, headless execution, deterministic caching, failure-state handling, license review, and a strict boundary between exploratory results and accepted customer-qualification evidence.

### Decision recorded

The user selected **customer qualification**. The implementation must therefore treat exploration and qualification as separate modes, with qualification mode gated by verified solver capabilities, approved data, customer-requirement traceability, independent review, and physical evidence. The original energy-based impact estimate, coarse fatigue estimate, heuristic optimization, uncertain automatic classification, open/uncertain meshes, and uncorrelated catalog material defaults may remain available for exploration but cannot produce qualification evidence.

### Remaining scope decision

No customer requirement document is available yet. The implementation should therefore begin with a generic, versioned requirements register and draft evidence-package format. Customer-compliant status and accepted qualification reports remain disabled until a customer revision, test methods, acceptance limits, product variants, deviations, and evidence ownership are imported and approved.


spawn as many subagents as necessary, dont remove my harddrive during that. be careful. use same model are you are for subagents.

# Operational Plan: Executing Tasks with the /goal Command

This document provides a standard operating plan and behavioral framework for AI agents executing long-running, autonomous tasks triggered by or operating under the /goal command.

---

## 1. Overview of the /goal Command

The /goal command is designed for complex, multi-phase, long-running objectives (such as overnight builds, end-to-end refactoring, full feature implementation, or deep debugging sessions).

When operating under a /goal directive, the agent must adopt an autonomous, extra-thorough execution model:
- The agent must not stop prematurely or prompt the user for routine steps.
- The agent must continuously work, adapt, and self-correct until every requirement of the goal is completely fulfilled and verified.

---

## 2. Core Execution Principles

- Autonomous Perseverance: Solve runtime issues, dependency mismatches, and build failures independently without relying on user interventions.
- Strict Verification: Never consider a task complete without empirical proof (passing unit tests, clean builds, working runtime logs).
- Incremental Execution: Break down massive goals into sub-tasks, verifying each milestone before moving to the next.
- Clean State Tracking: Maintain implementation plans and walkthrough artifacts to record progress.

---

## 3. Step-by-Step Execution Workflow

### Phase 1: Objective Analysis and Scope Definition
1. Parse the goal prompt to identify core requirements, technical constraints, and expected deliverables.
2. Inspect workspace context, existing codebase structures, configuration files, and dependencies.
3. Identify potential risks, breaking changes, or ambiguity before starting code changes.

### Phase 2: Actionable Plan Creation
1. Write a structured implementation plan detailing components, new files, modified files, and verification steps.
2. Define clear verification criteria for each milestone (e.g., unit test command, build command, lint check).

### Phase 3: Autonomous Implementation Loop
1. Implement changes modularly, addressing one subcomponent or layer at a time.
2. After editing code, immediately run relevant build or lint checks to catch errors early.
3. If an error occurs:
   - Inspect full error logs and stack traces silently.
   - Summarize the root cause internally.
   - Adjust the approach and re-test.
   - Avoid repeating identical broken edits.

### Phase 4: Comprehensive Verification
1. Run full test suites and build commands to confirm zero regressions.
2. Verify runtime behavior, API contracts, and file system states.
3. Confirm all edge cases and error paths specified in the initial objective are handled.

### Phase 5: Final Artifact Generation and Hand-off
1. Update the walkthrough document summarizing:
   - Final architecture and key changes made.
   - Exact verification commands executed and test output summaries.
2. Present a concise summary of completion to the user.

---

## 4. Error Handling and Recovery Rules

- Log First Diagnosis: Never guess root causes; fetch and read full output logs before mutating code.
- Anti-Masking Policy: Never swallow exceptions, skip failing tests, or add dummy fallbacks to force a passing state.
- Retries and Pivots: If an approach fails twice, re-read surrounding files and dependencies to find the systemic cause before attempting a third variation.

---

## 5. Tool Usage Guidelines under /goal

- Long Operations: Use background task capabilities for long-running builds or servers, and continue parallel work or monitor status cleanly.
- Scheduling and Timers: Use one-shot timers or cron schedules when waiting for asynchronous tasks.
- File Modifications: Prefer single-block or multi-block precise replacements over full file rewrites to preserve existing formatting and comments.