# Shell Physical Drop-Test Matrix — Drop-Dynamics Validation Campaign

The FIRST physical campaign is a **drop-dynamics calibration/validation
campaign**, not a structural campaign. Per the freeze-phase rules:

- Heights limited to **0.5 / 1.0 / 1.5 m**. The 2.0 m / 10 mm shell / k=1e5
  case produces documented excessive-penetration warnings; 2.0 m is EXCLUDED
  from the initial campaign and the physics will NOT be changed to make it
  pass.
- Every test compares the untouched BASELINE/UNCALIBRATED simulation
  (reference/shell_baseline_uncalibrated.json) against the measurement —
  no parameter is fitted to individual tests.
- The comparison is the measured-vs-simulated table in
  `shell.validation.measured_comparison` (peak acceleration g, ±uncertainty,
  absolute/relative error, aggregate bias/RMSE) plus the correlation verdict
  (≥ 3 distinct EQUIVALENT conditions, r² ≥ 0.8, |bias| ≤ 10%, each error
  ≤ 25%). Conditions whose compared quantity is NOT equivalent to the
  simulated quantity (corner/edge/top impacts with a surface-mounted sensor,
  axis-peak readings, or missing sensor definitions) are EXCLUDED from the
  verdict — they are reported in the comparison table with their flags but
  cannot drive CORRELATED.
- Equivalence flags: the simulated peak is the CoM-frame quasi-static
  linear-spring deceleration; a row is `equivalent` only for FLAT impacts
  with a defined sensor at/near the CoM reading the RESULTANT peak.

## The 12-drop matrix

| # | Height | Surface | Orientation | Purpose |
|---|--------|---------|-------------|---------|
| 1 | 0.5 m | concrete (def. recorded) | flat bottom | energy/velocity baseline; restitution via settle |
| 2 | 1.0 m | concrete | flat bottom | energy scaling |
| 3 | 1.5 m | concrete | flat bottom | energy scaling; penetration watch |
| 4 | 0.5 m | concrete | edge | orientation mapping (90° about X) |
| 5 | 1.0 m | concrete | edge | lever amplification / energy cap |
| 6 | 1.5 m | concrete | edge | lever amplification |
| 7 | 1.0 m | concrete | corner | peak-load geometry (DID_NOT_SETTLE accepted, flagged) |
| 8 | 1.5 m | concrete | corner | corner scaling |
| 9 | 1.0 m | steel (hard pad; definition recorded) | flat | restitution/friction via SETTLE (peak-g is surface-independent by design) |
| 10 | 1.0 m | foam (def. recorded) | flat | restitution/friction via SETTLE (peak-g is surface-independent by design) |
| 11 | 1.0 m | concrete | top (explicit quaternion [0,1,0,0], 180° about X) | inverted body: CoM/inertia sensitivity via settle |
| 12 | 1.0 m | concrete | tumble 5 rps | EXPLORATION-ONLY — see row 12 note |

Row notes:

- **Row 9 "hard pad"**: the simulation surface table has keys concrete/wood/
  foam/steel only. The hard pad is entered as surface type **steel** (or the
  nearest class) with the FULL physical definition recorded in
  `surface_definition` (thickness/hardness/mounting/notes). The recorded
  definition is metadata — restitution/friction come from the class table
  and are disclosed per row (`surface_table_parameters`).
- **Row 11 "top"**: no "top" mode exists; enter the explicit quaternion
  [0, 1, 0, 0] (180° about X — body +z to world −z). The pose is recorded
  and re-simulated exactly. (180° about Y, [0,0,1,0], is a DIFFERENT pose —
  do not substitute.)
- **Row 12 "tumble (5 rps)"**: the validation comparison workflow does NOT
  support release spin (it is a zero-spin re-sim; an unsupported spin field
  is rejected, not ignored). Row 12 is therefore an EXPLORATION-ONLY
  condition: run it as a standalone `drop_simulation` (test: tumble,
  spin_rps: 5) and compare manually against the measured trace. It does NOT
  participate in the measured comparison or the verdict, and the matrix
  makes no validation claim for it.
- Rows 4-8 and 11 are reported as NOT EQUIVALENT for a surface-mounted
  sensor (rotational terms, factor ~2-3): they appear in the comparison
  table with the equivalence flag and notes, but do NOT contribute to the
  verdict. Their purpose is diagnostic (orientation/lever/CoM sensitivity),
  not verdict-driving.
- The primary verdict-driving rows are 1-3, 9-10 (settle channel) and any
  flat + CoM-sensor + resultant comparisons.

The matrix separates the error sources:

- **Contact-model error** — force/accel scaling across heights 1-3 (and the
  k sweep 1e5–1e6 bracket in every validation run);
- **Restitution error** — rebound/settle on 1-3, 9-10;
- **Friction error** — settle on 9-10 (steel vs foam);
- **Rigid-body / mass / CoM / inertia error** — 4-8 (orientation effects),
  11 (inverted body), against the measured prototype mass/CoM;
- **Structural-model error** — NOT separable from drop tests; requires a
  quasi-static structural test with a known applied load (separate track).

## Per-test record (what must be captured)

For every test (see the validation measured_tests schema):

- test_id (unique), prototype_id, cad_revision, material;
- drop height, surface TYPE + definition (thickness/hardness/mounting),
  orientation (mode or explicit quaternion);
- environment (temperature, humidity if available);
- sensor: model, location in body coordinates, quantity
  (resultant_peak_g or axis_peak_g), axis, sampling rate, filter, sync;
- measured peak acceleration (g) ± uncertainty, impact duration if
  instrumented, settle time;
- the simulated counterpart is produced automatically by the validation run
  (same height/surface/orientation re-simulated with the pinned prototype
  mass/CoM/inertia and the pinned dt/seed).

## Rules

1. NO parameter fitting to individual tests. Examine the full 12-test set
   first, then determine the systematic error.
2. The first comparison is against the untouched baseline
   (BASELINE/UNCALIBRATED, reference/shell_baseline_uncalibrated.json).
3. Any test whose measured condition lies outside the eventually-correlated
   domain is reported OUTSIDE VALIDATED DOMAIN.
4. The accelerometer quantity must match the simulated quantity: for
   corner/edge/top impacts the comparison is marked not directly equivalent
   unless the sensor is at the CoM (see the per-row equivalence flag).
5. Rows 9-12 do not drive the verdict as designed; rows 9-10 validate the
   settle channel only (the compared peak-g is surface-independent by
   construction of the quasi-static model).
