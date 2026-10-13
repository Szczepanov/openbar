# Portable per-rep and lift-phase metrics (design contract)

Issue: #180 (child design of #80). Decision record: [ADR-0012](../adr/0012-portable-rep-and-phase-metrics-contract.md).

## Status

**Design only.** Nothing here is implemented in `openbar-core`, the CLI or any mobile app. M1 is locked
by [ADR-0008](../adr/0008-m0-go-no-go-and-m1-entry.md); this document does not unlock it. It changes no
`analysis-v1` field, schema version, golden file or measurement output, and it does not touch the frozen
#79 agreement study or its pinned consumer.

It extends [`KINEMATIC_METRICS.md`](KINEMATIC_METRICS.md), which stays authoritative for the interval
primitives, and it is the contract a future Rust implementation, Android binding and presentation layer
are reviewed against. Everything it calls "reference" is a synthetic engineering expectation, not
accuracy evidence.

## Governing constraints

- Raw observations stay untouched; rep results are a derived layer with provenance (ADR-0003).
- Lost or unsupported data produces an explicit "not measurable" result. It never produces a
  plausible number, a zero, or an interpolated boundary.
- Decoded timestamps are authoritative. Rep boundaries are sampled timestamps, so they satisfy the
  `MetricInterval` rule that boundaries match sample timestamps exactly.
- Measurement logic lives in `openbar-core` (ADR-0001). The Android layer formats and navigates; it
  does not compute.
- Same Analysis + same rule version + same parameters ⇒ byte-identical result.

## 1. Inventory of existing definitions

Everything below was read from the code or documents named; nothing is inferred from the product
descriptions.

| # | Definition | Where | Units | Axis and sign | Interval | Method / identity |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Backward-difference velocity | `openbar-core` `kinematics::derive_velocity` | m/s | image-plane X right, Y up | segment ending at sample `i` | `backward-difference@1`, `max_gap_s`, `min_confidence` |
| 2 | Axis displacement, ROM | `axis_displacement`, `range_of_motion` | m | named axis, Y up | caller series | ROM is `max − min` over the series |
| 3 | Mean axis velocity | `mean_axis_velocity` | m/s | named axis, Y up | explicit `MetricInterval`, boundaries on sample timestamps | endpoint displacement ÷ interval duration (time-weighted) |
| 4 | Peak axis velocity | `peak_axis_velocity` | m/s | named axis, signed, Y up | derivative segments wholly inside the interval | maximum signed component; reports end timestamp of the winning segment |
| 5 | Concentric segmentation | pinned consumer `concentricSegmentation.ts` (`segmentConcentricReps`) | s, m/s, cm | Y up; descent only feeds completeness | maximal runs of `velocityMps > 0` | `concentric-segmentation-v1` / `-v2`, constants below |
| 6 | OpenBar adapter | pinned consumer `openBarAnalysis.ts` (`parseOpenBarAnalysis`) | as above | reads `vy_mps`, `y_m`, converts to cm | usable frames | `openbar-analysis-v1/v2`; adds gap breaks and `exclusion` |
| 7 | Session preview reps | `research/vbt-workflow/session_report.py` `preview_reps` | m, m/s | Y up | from the sample *before* the first `vy_mps > 0` sample to the last positive one; rise ≥ 0.10 m | labelled non-authoritative; mean is rise ÷ elapsed (time-weighted) |
| 8 | Hand-check arithmetic | `research/vbt-workflow/agreement79_handcheck.py` | as #5 | as #5 | as #5 | re-states #5 to verify the study; not a second rule |

Items 5 and 6 are the only rep and phase definitions with a version identity, a golden corpus and a
frozen role in a study. They live in the sibling `adaptive-training-recommender` repository (MIT, like
OpenBar) and are pinned by the #79 preregistration at commit
`a74cb9dca24864af9f348bf9853fb6fe4f475521`. Item 7 is a convenience preview and is not a candidate,
but its interval choice (start at the turnaround sample) is the one that makes a time-weighted mean
cover the same segments the consumer averages; Section 2 adopts that interval.

### Pinned segmentation constants (`concentric-segmentation-v2`)

| Constant | Value | Role |
| --- | ---: | --- |
| `CONCENTRIC_VELOCITY_THRESHOLD_MPS` | 0 | a frame is concentric when `velocity > 0` |
| `MIN_REP_RISE_CM` | 10 | minimum run rise |
| `MIN_REP_RISE_FRACTION` | 0.5 | run rise must also reach this fraction of the largest run rise |
| `COMPLETE_DESCENT_FRACTION` | 0.85 | run rise ÷ preceding descent for `complete` |
| `BOUNDARY_VELOCITY_FLOOR_MPS` | 0.05 | v2 edge trim floor |
| `BOUNDARY_MAX_TRIM_RISE_CM` | 1 | v2 trims an edge only if the omitted rise is at most this |
| `OPENBAR_GAP_INTERVAL_FACTOR` | 1.5 | adapter flags an interval above this × the median interval |

The 0.05 m/s, 1 cm, 10 cm, 0.5 and 0.85 values are engineering choices recorded by the consumer. This
repository holds no evidence that derives them; they are adopted for continuity and are re-examined
by evidence, not by assertion.

### Where the definitions disagree

These are real differences between items 3–4 (core primitives, which need an explicit interval) and
5–6 (consumer). The consumer does not state an interval; it reports a window of frames.

| Quantity | Core primitives over an explicit interval | Pinned consumer (`concentric-segmentation-v2`) |
| --- | --- | --- |
| Segments covered | segments wholly inside the interval | the segment ending at each window frame, which includes the segment ending at the window's *first* frame |
| Mean velocity | `(y_end − y_start) ÷ (t_end − t_start)`; time-weighted | unweighted mean of `vy_mps` over the window's frames |
| Peak velocity | maximum over the segments inside the interval | maximum over the window's frames |
| ROM and duration | from the interval's own endpoints | from the first window frame to the last, so the first rising segment is omitted |
| Mean × duration vs ROM | consistent by construction | not consistent: the mean averages one more segment than the ROM and duration cover |
| Irregular timestamps | handled by time weighting | each frame counts equally |
| Gap rule | derivative unavailable when `dt > max_gap_s` | additionally flags a rep when any interval exceeds 1.5 × the median interval |
| Rounding | none (values are `f64`) | time and velocity to 3 dp, ROM to 2 dp (ECMAScript `Math.round`) |

Because the consumer's window starts one frame after the turnaround, a core interval that starts at
the window's first frame would exclude a segment the consumer averages. Evaluated that way, the two
means differ even on uniform sampling (by roughly 0.01–0.10 m/s on the 6–16 sample synthetic windows
in the reference cases, up to twice the provisional 0.05 m/s mean-velocity gate). Evaluated over the interval that starts at the sample before
the window's first frame (Section 2), core covers exactly the consumer's segments, and:

- the peak is identical by construction;
- the mean is identical on uniform sampling and differs only where sample spacing varies, because core
  time-weights and the consumer does not;
- duration and ROM are larger than the consumer's by one segment, which is the consumer's internal
  inconsistency, not a measurement disagreement.

The magnitude of the variable-spacing difference on real recordings is **unmeasured**. It must be
measured on definition-matched pairs before anyone claims the two are interchangeable. The #79
preregistration forbids replacing the consumer's sample mean for the study; this contract does not
touch that.

Other findings from the inventory:

- Detection is concentric-only. The descent is never reported and is only the denominator of the
  completeness test. There is no eccentric, pause or lockout phase.
- The completeness test is vacuously true whenever no descent is observed before the run
  (`descent <= 0`): at the series edge, but equally when a clip opens with the bar at rest on the floor
  or with the lifter paused at the bottom. Tracking jitter while the bar rests gives a small positive
  descent, which the 0.85 test then also passes. `complete` alone therefore does not mean a full rep was
  observed (`deadlift-from-rest-no-prior-descent`, `deadlift-from-rest-with-jitter`).
- Runs that fail the rise rules (10 cm, or half of the largest rise anywhere in the clip) are never
  reported as reps and leave no trace in the consumer's output. Which reps exist therefore depends on
  the rest of the clip.
- A single zero-velocity frame splits one ascent into two runs; the second fragment can be dropped by
  the rise rules, so the reported rep covers only the first fragment.
- `min_confidence` reaches the consumer only through core's rule that a below-threshold endpoint
  yields a null velocity. The consumer records it as provenance and applies no confidence rule of its
  own.
- `KINEMATIC_METRICS.md` says product labels such as "mean concentric velocity" must not be used until
  a separately validated process defines the concentric interval boundaries. The #79 preregistration
  uses the consumer rule for exactly that, under an owner-authorised, separately tracked study. This
  contract keeps the two consistent by withholding the "concentric" label for the app until validation
  (Section 2) and showing "upward" values meanwhile.

## 2. Displayed values

Each value a future Android screen may show, with its definition.

Two terms are used. The **window** is what the segmentation rule reports: the first and last
concentric frame, as sample indices. The **metric interval** is `[t(window first − 1), t(window last)]`:
it starts at the sample before the window's first frame. That is the turnaround sample, unless the v2
window trimmed leading sub-floor frames (at most 1 cm of rise), in which case it is the last trimmed
frame (`leading-creep-trimmed-window`; the consumer's omitted rise, which must not exceed 1 cm,
includes the first above-floor step). Its boundaries are sampled timestamps, so the core primitives
accept it without interpolation, and every upward segment the window counted lies wholly inside it.

| Displayed value | Units | Axis, sign | Interval | Method |
| --- | --- | --- | --- | --- |
| Rep number | – | – | order of detected runs | segmentation rule |
| Upward start, end | s, decoded presentation timestamps | – | metric interval | sample before the window's first frame; last concentric frame |
| Duration | s | – | metric interval | `t_end − t_start` |
| Upward ROM | m (UI may present cm) | vertical, positive up | metric interval | core `range_of_motion` over the interval samples |
| Mean upward velocity | m/s | vertical, positive up | metric interval | core `mean_axis_velocity` |
| Peak upward velocity | m/s | vertical, positive up | segments inside the metric interval | core `peak_axis_velocity`, with its timestamp |
| Completeness | `complete` / `incomplete` / `undetermined` | – | whole run vs preceding descent | Section 4 |
| Availability | `reported` / `withheld` plus reasons | – | metric interval | Section 4 |
| Data confidence | 0–1, not a probability | – | metric interval | minimum sample confidence in the interval |

Rules for every row:

- Values are image-plane measurements under the scalar plate-diameter calibration. A numeric value
  does not override calibration warnings or the recording-support policy.
- The word "concentric" in a product label (mean or peak concentric velocity, concentric ROM) is **not
  used** until the boundary rule has been validated by #79 / #58 evidence and the change that records
  that validation amends `KINEMATIC_METRICS.md`, which currently forbids the label. Until then the
  values are shown as "upward", with the rule id and version and an unvalidated marker, and only for a
  lift whose profile is supported (Section 3).
- Peak velocity is the maximum signed vertical component; ties keep the earliest segment (core
  `peak_axis_velocity`). Euclidean peak speed, acceleration, power and force are not displayed. They are not validated
  metrics (`KINEMATIC_METRICS.md`), and barbell-derived values must not be presented as whole-body
  output.
- Display rounding happens once, at presentation. Stored and exported values are unrounded `f64`.
  The legacy precision (3 dp time and velocity, 2 dp cm) belongs to the consumer-parity profile only.
- The consumer's unweighted sample mean is **not** a displayed value. If it is ever shown it must carry
  a different name, and any comparison against it must disclose the variable-spacing difference above.
- The consumer's own duration and ROM (one segment shorter) are likewise not displayed. A comparison
  of ROM or duration against the recommender must account for that offset.

## 3. Phase vocabulary and lift matrix

### Phase vocabulary

| Phase id | Meaning | Status |
| --- | --- | --- |
| `concentric` | maximal run of upward vertical velocity, qualified by the rise rules | defined by this contract (rule `concentric-reps@1`) |
| `descent` | downward run preceding a concentric run | used only for the completeness test; no metrics displayed |
| `lift_specific` | named phases of a multi-stage lift (for example first pull, catch, dip, drive) | **reserved, undefined**; requires a lift profile with its own rule, evidence and decision |

The concentric rule does not recognise which lift is being performed. Exercise or pose recognition is
out of scope. The lift is declared by the user at import (the manifest already carries an exercise
label), and rep metrics are produced only for a declared lift that has a supported profile.

### Staged lift matrix

| Stage | Lift | Why this stage | Gate to enter |
| --- | --- | --- | --- |
| 1 | Back squat | One descent and one ascent per rep; the pinned study has a squat slot; the bar rises monotonically during the concentric phase | M1 entry (ADR-0008) and the #79 / #58 outcomes for the rule |
| 1, conditional | Snatch; clean without jerk | In the #79 study design with the same generic rule, but a single positive run per lift is **not established**: the bar rises during the pull, can drop briefly while the lifter moves under it, and rises again on recovery | #79 rep-count and pairing evidence per slot; otherwise moves to Stage 3 |
| 2 | Deadlift; bench press | Different phase semantics: a deadlift rep can begin with the concentric phase, so no descent precedes it and completeness is `undetermined` (and the first rep is withheld as well when the clip starts mid-pull); a bench press has an unrack and chest pause. Same rule may suffice, but this is not demonstrated | separate evidence and a decision per lift |
| 3 | Clean and jerk; jerk; other multi-stage lifts | One lift contains several rises separated by eccentric or hold phases | a lift-specific phase model, its own ADR and evidence |

Clean and jerk must not be treated as one generic positive-velocity run. Two reference cases pin two
failure modes of a constructed pull, catch, recovery, dip and drive:

- `clean-and-jerk-shaped`: the generic rule reports the pull and the drive as two reps and drops the
  recovery because its rise is under the 10 cm minimum;
- `clean-and-jerk-drive-below-half-pull`: with a drive under half of the pull's rise, the generic rule
  reports the pull alone and rejects both the recovery and the drive.

Which of these a real clean and jerk produces depends on the relative rises of its phases, so the result
is unpredictable without a lift-specific model. These cases are constructed shapes, not claims about
real data. The 2026-10-02 clean-and-jerk trial recorded in `VBT_WORKFLOW_PLAN.md` ran one set of four
lifts and both sources found 4 reps. That shows the two sources agreed under one rule, not that the rule
isolates the intended phase; the trial was also excluded from the formal study and re-implemented the
rule for the check.

For a declared lift without a supported profile the product shows the bar path and no rep or phase
metrics, with an explicit "not supported for this lift" state.

## 4. Gap, lost, low-confidence, incomplete and rejected behaviour

Every run that passes the rise rules is a rep and is always listed. Its metrics are `reported` or
`withheld`. A withheld rep shows its window and reasons and no numbers. It is never counted as a
complete rep and never shown as zero. In the reference file, `core_primitive_values` are intermediate
values for checking the definitions; they are shown to a user only when `availability` is `reported`.

| Reason | Condition | Source rule |
| --- | --- | --- |
| `unsupported_segment` | any sample in the metric interval is below `min_confidence`, or any interval exceeds `max_gap_s` | core `series_supported` |
| `spans_gap` | a continuity break lies inside or at the edge of the rep's run: a null velocity, or an interval above 1.5 × the median of all consecutive sample intervals in the Analysis's kinematic samples (an even count averages the two middle values) | consumer adapter break rule, adopted |
| `touches_series_edge` | the run begins at the first usable frame or ends at the last | consumer adapter, adopted |

`unsupported_segment` may combine with either of the other two. `spans_gap` and `touches_series_edge`
do not combine in rule version 1: the consumer reports one exclusion per rep and `spans_gap` takes
precedence, which `series-edge-run-with-gap` pins (a run that touches the start edge and contains a
break). Reasons are listed in the order above.

The relative-gap reason is intentionally stricter than core: a 0.125 s interval is inside
`max_gap_s = 0.2 s`, so core can still derive a velocity, but the rep is withheld because a peak or
onset inside an under-sampled stretch could be missed (`relative-gap-inside-rep` records both facts).
Lost observations appear to this rule only as timestamp gaps. A loss of a few frames at 30 fps stays
within `max_gap_s`, so it is caught by `spans_gap` and not by `unsupported_segment`.

### Completeness

Completeness compares the run's rise with the descent observed before it (the highest displacement
since the previous rep's run ended, minus the displacement at the run's start). It has three states:

| State | Condition | Treatment |
| --- | --- | --- |
| `complete` | descent observed and rise ≥ 0.85 × descent | counted |
| `incomplete` | descent observed and rise < 0.85 × descent | reported, flagged partial, excluded from set summaries and complete-rep counts by default; counted separately |
| `undetermined` | the preceding descent is below the minimum rep rise, 10 cm (`MIN_REP_RISE_CM`), including no descent at all | reported, flagged, treated like `incomplete` for summaries; the consumer reports a run with no descent as `complete` and judges a tiny one by the 0.85 rule |

`undetermined` covers the series edge, a clip that opens with the bar at rest, a first rep that starts
from a pause, and tracking jitter before a pull (`deadlift-from-rest-with-jitter`: a 0.78 cm descent
that a `descent > 0` test would accept). A descent smaller than the minimum rep rise cannot be a
reference for a full rep, and the floor reuses an existing constant rather than adding one. It is
independent of `touches_series_edge`. A rep that is withheld is shown as withheld whatever its
completeness.

### Rejected runs

A positive-velocity run that fails the rise rules is not a rep: its rise is under 10 cm
(`below_min_rise`, which wins when both rules reject the run), or under 0.5 × the largest run rise
anywhere in the clip (`below_relative_rise`). A clip may therefore contain rejected runs and no rep at
all (`no-rep-only-a-rejected-run`).
The consumer drops these silently. The rule returns them as `rejected_runs` (run window, rise, reason)
so that a failed attempt, a fragment or a dropped phase is visible to the caller:

- the relative threshold depends on the whole clip, so trimming or concatenating clips changes which
  reps exist (`rejected-runs-depend-on-clip`, `rejected-run-alone-is-accepted`);
- a zero-velocity frame at a sticking point splits an ascent, and a small second fragment is rejected,
  so the reported rep covers only the first fragment (`zero-velocity-frame-inside-ascent`);
- a clean and jerk can lose its recovery or drive the same way (Section 3).

`rejected_runs` is a diagnostic, not a displayed value: it lists every positive-velocity run that was
not accepted, including noise-sized ones, and the caller decides what to surface. Whether a rejected run
adjacent to a rep should mark that rep ambiguous is not decided here; it needs evidence on real clips.

No rep boundary is interpolated, and no value is produced across an unsupported span.

## 5. Segmentation rule identity

The first portable rule is named `concentric-reps`, version `1`. Its detection, completeness and
window behaviour are specified to match `concentric-segmentation-v2` at the pinned commit, using the
constants in Section 1, with these differences and no others:

- it consumes core samples (decoded timestamps, `y_m`, `vy_mps`, confidence) and produces sampled
  timestamps, rather than the consumer's cm frames and ordinals;
- availability is decided by Section 4 and core `series_supported`, not left to a caller;
- metric values are the core definitions in Section 2 over the metric interval, not the consumer's
  sample mean, peak and endpoint ROM over its window;
- results carry the rule id, version, every constant above and the kinematics provenance;
- it returns `rejected_runs` (Section 4) and the three-state completeness.

The rule is defined on the Analysis's recorded kinematics layer. Its input layer, filter identity and
`backward-difference@1` parameters are part of the result's provenance, and a result is valid only for
the provenance it carries. This contract chooses no filter: that belongs to #57 and #79. The input
matters. The 2026-10-02 trial recorded that raw frame differencing delayed rep onset by 0.08–0.15 s on
three of four reps, and any `vy <= 0` frame mid-ascent splits a run (Section 4).

Any change to a constant, to the window logic or to the metric definitions is a new version. Version 1
readers fail closed on other versions.

## 6. Reference cases and parity

The executable expectations are in
[`validation/reference-cases/rep-metrics-v1.json`](../../validation/reference-cases/rep-metrics-v1.json),
validated by [`rep-metrics-reference-cases-v1.schema.json`](../../validation/schema/rep-metrics-reference-cases-v1.schema.json)
and `python validation/tools/schema_check.py`.

Inputs are dyadic: sample times and velocities are exact binary fractions, so every expected value is
exactly reproducible in `f64` across Rust, Kotlin, Dart and Python. Each case records the pinned
consumer's output (`consumer_reps` with its run indices, `consumer_breaks`), the core primitive values
over the metric interval (`core_primitive_values`, with `interval_start_sample_index`), the consumer's
unrounded values, the explicit mean difference, the preceding descent and completeness state, the
availability and withheld reasons, and the `rejected_runs`.

| Case | What it pins |
| --- | --- |
| `single-rep-constant-velocity` | the simplest rep; mean and peak coincide |
| `three-rep-sticking-point-and-incomplete` | rep count of 3, the v2 trailing trim, a sticking point, an incomplete third rep; mean and peak coincide on uniform sampling |
| `leading-creep-trimmed-window` | a real v2 leading trim (omitted rise 0.49 cm): the metric interval starts at the last trimmed frame, and a test asserts the window starts after the run start |
| `irregular-timestamps-ascent` | variable frame spacing: time-weighted core mean 0.75 m/s vs consumer sample mean 0.643 m/s, same peak |
| `relative-gap-inside-rep` | core still derives; policy withholds (`spans_gap`) |
| `long-gap-inside-rep` | `unsupported_segment` and `spans_gap` together |
| `low-confidence-inside-rep` | below-threshold sample: both adjacent derivatives unsupported |
| `series-edge-truncated-pulls` | `touches_series_edge`; `undetermined` completeness at the start |
| `series-edge-run-with-gap` | a run that touches the edge and contains a break: `spans_gap` takes precedence |
| `deadlift-from-rest-no-prior-descent` | `undetermined` completeness without touching the edge; the consumer says `complete` |
| `deadlift-from-rest-with-jitter` | a 0.78 cm jitter descent stays `undetermined` under the 10 cm floor |
| `no-rep-only-a-rejected-run` | no rep and one rejected run |
| `rejected-runs-depend-on-clip` | `below_min_rise` and `below_relative_rise` rejected runs |
| `rejected-run-alone-is-accepted` | the same small rep accepted when the large rep is absent |
| `zero-velocity-frame-inside-ascent` | a split ascent: the second fragment is rejected |
| `clean-and-jerk-shaped` | `lift_specific_required`: generic rule yields two reps and drops the recovery |
| `clean-and-jerk-drive-below-half-pull` | `lift_specific_required`: generic rule yields the pull alone |

### Checks and tolerances

| Check | Command | What it proves |
| --- | --- | --- |
| Case file consistency | `python -m unittest discover -v -s validation/tests -p 'test_rep_metrics_reference.py'` | expected values follow the `KINEMATIC_METRICS.md` definitions and the consumer arithmetic; policy fields follow Section 4 |
| Schema | `python validation/tools/schema_check.py` | the case file is well-formed |
| Pinned-consumer parity | `node --experimental-strip-types research/vbt-workflow/rep_reference_cases_consumer.mjs check <consumer-app-dir> validation/reference-cases/rep-metrics-v1.json` with the consumer checkout at the pinned commit | the recorded consumer output is what the pin produces. Run by hand: it needs the sibling checkout and is not part of CI, so the consumer boundaries in the file are taken as given by the Python test |

Tolerances a future implementation is held to:

- **Boundaries, rep count, completeness, withheld reasons, rejected-run windows and reasons:** exact.
- **`rise_cm` and `preceding_descent_cm`:** within `1e-9` on these inputs. They are in centimetres, in
  the consumer's arithmetic, so they can be compared with its golden; core-owned metric values are in
  metres.
- **Core values** (`duration_s`, `rom_m`, `mean_velocity_mps`, `peak_velocity_mps`, `peak_timestamp_s`):
  absolute difference at most `1e-9` on these dyadic inputs. Non-dyadic real recordings are compared
  with the existing narrow canonical floating-point tolerance, not a looser one.
- **Consumer-parity profile** (if implemented for #79 comparison): equal after the legacy rounding, 3 dp
  for time and velocity and 2 dp for cm, using ECMAScript `Math.round` semantics (ties toward +∞; the
  reference values are positive). Rounded values are compared; stored values are not rounded.
- **Cross-binding parity:** the Android binding and the native CLI return byte-identical serialized
  results for every case. Because both call the same Rust implementation, this is a binding test, not a
  second-implementation test.
- **Consumer golden corpus:** the pin's `concentricSegmentation.golden.json` (200 seeded synthetic
  series, 30 and 60 fps) should be replayed through the Rust segmentation primitive. The primitive
  therefore takes the same frame shape (time, velocity, displacement) internally, with an adapter
  from core samples. Boundaries and rep flags must match exactly. The golden file holds outputs only;
  its inputs come from a seeded generator in the consumer's test code. The implementing task must either
  export the inputs through a bridge like the one used here or port the generator, which is copied code
  and carries the MIT notice.

## 7. Ownership, portability and persistence

- **Authoritative implementation:** a future module in `openbar-core`, on core samples and the existing
  interval primitives. No Kotlin, Dart or TypeScript port of the algorithm is authorised. The Android
  layer receives results through whichever binding M1 selects. That binding choice and the UI toolkit
  are not decided here; ADR-0001 prefers Flutter, and this contract is toolkit-neutral.
- **The recommender is a consumer and a parity reference.** Its segmenter stays frozen at the pin for
  #79 and for its own use. OpenBar does not import the recommender app. The roughly 180-line pure module is
  specified and tested against, not vendored. If any of its code is later copied, the source commit and
  MIT licence notice travel with it.
- **Persistence:** rep results are a derived, recomputable view of a validated Analysis, a declared
  lift profile and a rule version. They are **not** added to `analysis-v1`: that schema, its version
  constants and its golden are unchanged, and `ANALYSIS_SCHEMA.md` recommends that protocol results be
  typed, discriminated and explicit about units and definitions rather than an untyped map. An app
  may cache a result keyed by the Analysis digest, rule id and version, and parameters, and must
  recompute on any mismatch.
- **Export:** a rep-metrics export, if wanted, is a new versioned sidecar document that references the
  Analysis by source digest and kinematics provenance and carries the rule id, version, parameters and
  lift profile. It needs its own schema, golden and review before implementation. CSV is an adapter
  around it, never a source of truth.

## 8. Not decided here

- Whether the displayed mean is the core interval mean (this contract's recommendation) or the
  consumer's sample mean, which is an owner decision recorded in ADR-0012.
- The binding technology, UI toolkit and any Android packaging.
- Acceptance thresholds for rep metrics on real recordings. Those come from the #58 reference study
  and #79, not from the synthetic cases.
- The lift-specific phase models for Stage 3 lifts.
- Anything about `analysis-v1`, #79 or M1 entry.
