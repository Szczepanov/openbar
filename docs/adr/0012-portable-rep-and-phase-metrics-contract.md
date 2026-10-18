# ADR-0012: Portable per-rep and lift-phase metrics contract

- Status: Accepted (owner decision 2026-10-18)
- Date: 2026-10-13
- Issue: #180 (child design of #80)
- Related: ADR-0001, ADR-0003, ADR-0007, ADR-0008, #79, #58, #181, #179
- Detail: [`docs/validation/REP_PHASE_METRICS.md`](../validation/REP_PHASE_METRICS.md)

## Context

The planned Android app shows mean and peak velocity, range of motion and duration per rep or lift
phase. Two things already define parts of that:

- `openbar-core` provides timestamped kinematics and explicit-interval primitives
  (`mean_axis_velocity`, `peak_axis_velocity`, `range_of_motion`). It deliberately provides **no**
  rep or phase segmentation, and `KINEMATIC_METRICS.md` forbids the label "mean concentric velocity"
  until concentric boundaries are defined and validated.
- The sibling `adaptive-training-recommender` (MIT) ships a versioned, source-neutral segmentation
  (`concentric-segmentation-v1/v2`) and an OpenBar importer. The #79 agreement study pins that
  consumer at commit `a74cb9dca24864af9f348bf9853fb6fe4f475521` and forbids changing its rule.

An inventory (see the detail document) found that these two do not mean the same thing by "mean
velocity", "ROM" or "duration" for the same rep, that the consumer segments only the concentric phase
and has no lift-specific model, that its completeness test is vacuously true whenever no descent is
observed before a rep, and that runs failing its rise rules vanish without a trace.
A new app that reused either one by name would silently change the meaning of a displayed value.

M1 is locked (ADR-0008, NO-GO / REWORK). Real-recording accuracy of ROM, mean and peak velocity is
not established, and no recording condition is fully supported. This ADR is a design decision. It
authorises no implementation.

## Decision

### 1. Ownership: one Rust implementation in `openbar-core`

Rep detection, the concentric window rule and the per-rep metric values are authoritative in
`openbar-core`, built on the existing `MetricPositionSample` and interval primitives (ADR-0001). The
Android layer is presentation only: it formats units, localises, selects a rep, and seeks the video to
a rep's timestamps. It computes nothing.

No Kotlin, Dart or TypeScript port of the algorithm is authorised. The recommender's segmenter stays an
independent consumer and a **parity reference**; OpenBar does not import the recommender app and does
not vendor its code. If code is later copied, the source commit and MIT notice travel with it.

The binding technology and UI toolkit are not decided here. ADR-0001 prefers Flutter; this contract is
toolkit-neutral.

### 2. Rule identity: `concentric-reps@1`

The first portable rule matches the detection and window behaviour of
`concentric-segmentation-v2` at the pinned commit, with the pinned constants (10 cm minimum rise, 0.5 ×
largest rise, 0.85 completeness fraction, 0.05 m/s boundary floor, 1 cm maximum trim, 1.5 × median
interval gap factor). Those constants are engineering choices recorded by the consumer; this repository
has no evidence that derives them, and they are re-examined by evidence, not asserted.

It differs from the consumer in exactly five ways: it consumes core samples and returns sampled
timestamps; availability is decided by the policy in section 4; metric values are the core definitions
in section 3; completeness has a third state and rejected runs are returned (section 4); and every
result carries the rule id, version, constants and kinematics provenance. Any change to a constant, the
window logic or a metric definition is a new version. Readers fail closed on other versions.

The rule is defined on the Analysis's recorded kinematics layer. Its input layer, filter identity and
`backward-difference@1` parameters are part of the result's provenance and a result is valid only for
the provenance it carries. This ADR chooses no filter (that is #57 and #79), but the choice matters:
raw frame differencing delayed rep onset by 0.08–0.15 s in the 2026-10-02 trial, and any frame with
`vy <= 0` inside an ascent splits a run.

### 3. Displayed values use the core primitives over an explicit metric interval

The **window** is the first and last concentric frame the rule reports. The **metric interval** is the
sample before the window's first frame through its last frame. Its boundaries are sampled timestamps,
so core accepts it without interpolation, and every upward segment the rule counted lies inside it.

| Value | Definition |
| --- | --- |
| Duration | `t_end − t_start` of the metric interval |
| Upward ROM | core `range_of_motion` over the interval samples (vertical, +Y up) |
| Mean upward velocity | core `mean_axis_velocity` over the interval |
| Peak upward velocity | core `peak_axis_velocity` over the interval, with timestamp |
| Completeness, availability, confidence | section 4 |

Over this interval core covers exactly the segments the consumer averages, so the peak equals the
consumer's by construction and the mean equals it on uniformly sampled data. They differ only when
sample spacing varies, because core time-weights and the consumer does not. The interval starts at the
turnaround sample, or at the last trimmed frame when the v2 window trims leading sub-floor frames.
Duration and ROM are one segment longer than the consumer's, which fixes the consumer's own
mean × duration ≠ ROM inconsistency. The consumer's unweighted sample mean is **not** a displayed value; if shown anywhere it
is named differently and every comparison discloses the difference. Euclidean peak speed, acceleration,
power and force are not displayed.

The word "concentric" in a product label (mean or peak concentric velocity, concentric ROM) is **not
used** until #79 / #58 evidence validates the boundary rule and the change that records that validation
amends `KINEMATIC_METRICS.md`, which currently forbids the label. Until then the values are shown as
"upward" with the rule id, version and an unvalidated marker, for a supported lift only.

### 4. Gaps, lost data, low confidence, incomplete reps and rejected runs

Every run that passes the rise rules is a rep and is always listed. Its metrics are `reported` or
`withheld`; a withheld rep shows its window and reasons and no number, never zero.

- `unsupported_segment`: a sample below `min_confidence` or an interval above `max_gap_s` inside the
  metric interval (core `series_supported`).
- `spans_gap`: a null velocity or an interval above 1.5 × the median of all consecutive sample intervals
  inside or at the edge of the rep's run. This is stricter than core, deliberately, so a peak inside an
  under-sampled stretch cannot be missed.
- `touches_series_edge`: the run begins at the first or ends at the last usable frame.

`spans_gap` and `touches_series_edge` do not combine in version 1 (one consumer exclusion per rep;
`spans_gap` takes precedence, as the `series-edge-run-with-gap` case pins).

Completeness has three states. `complete` and `incomplete` compare the rise with 0.85 × the preceding
descent. `undetermined` applies when that descent is below the minimum rep rise of 10 cm (an existing
constant), which includes no descent at all. That happens at the series edge, but also when a clip opens
with the bar at rest or paused, and under tracking jitter before a pull. The consumer reports a run with
no descent as `complete`, so the edge reason alone does not cover these reps. `incomplete` and
`undetermined` reps are reported and flagged, are excluded from set summaries and complete-rep counts by
default, and are counted separately.

A positive-velocity run that fails the rise rules (under 10 cm, or under half of the largest rise
anywhere in the clip; the first wins when both apply) is not a rep. The consumer drops it silently. The rule returns these as
`rejected_runs` with their window, rise and reason, so a failed attempt, a fragment of a split ascent or
a dropped phase is visible. Because the relative threshold depends on the whole clip, trimming or
concatenating clips changes which reps exist; the reference cases pin this. Whether a rejected run next
to a rep should mark it ambiguous needs evidence on real clips and is not decided here.

No boundary or value is interpolated across an unsupported span.

### 5. Staged lift matrix

The lift is declared by the user. Automatic exercise or pose recognition is out of scope.

1. **Back squat.** Committed first stage, subject to M1 entry.
2. **Snatch and clean without jerk.** Candidates only. They are in the #79 design, but a single upward
   run per lift is not established: the bar can drop briefly while the lifter moves under it and rises
   again on recovery. They join stage 1 only on #79 rep-count and pairing evidence.
3. **Deadlift and bench press.** Separate evidence and decision per lift. The same rule may suffice;
   that is not demonstrated.
4. **Clean and jerk, jerk, other multi-stage lifts.** A lift-specific phase model, its own ADR and
   evidence. The generic rule must not be used: the `clean-and-jerk-shaped` reference case shows it
   reporting two reps for one lift and dropping the recovery, and `clean-and-jerk-drive-below-half-pull`
   shows it reporting the pull alone. These are constructed shapes; which one a real lift produces
   depends on the relative rises of its phases.

A declared lift with no supported profile shows the bar path and no rep or phase metrics, with an
explicit "not supported for this lift" state. Phase names beyond `concentric` and `descent` are
reserved and undefined.

### 6. Parity and tolerances

Executable expectations live in `validation/reference-cases/rep-metrics-v1.json`, schema
`rep-metrics-reference-cases-v1`. Inputs are dyadic, so expected values are bit-reproducible. The
checks are:

- boundaries, rep count, completeness, withheld reasons, and rejected-run windows and reasons: exact;
- core values: absolute difference at most `1e-9` on the dyadic cases, the existing narrow canonical
  tolerance on real data;
- consumer-parity profile, if built for #79 comparison: equal after the consumer's legacy rounding;
- Android binding versus native CLI: byte-identical serialized results for every case;
- the pin's 200-case `concentricSegmentation.golden.json` replays through the Rust segmentation
  primitive with exact boundary and flag agreement, so the primitive takes the consumer's frame shape
  internally with an adapter from core samples. The golden holds outputs only; its inputs come from a
  seeded generator in the consumer's tests, so the implementing task exports them through a bridge or
  ports the generator under the MIT notice.

The consumer-parity check (`rep_reference_cases_consumer.mjs check`) is run by hand against a sibling
checkout and is not part of CI; the Python test takes the recorded consumer boundaries as given.

These cases are synthetic engineering expectations. They are not accuracy evidence and do not
substitute for the #58 reference study.

### 7. Persistence and export

- `analysis-v1` is **unchanged**: no field, schema version, version constant, golden or fixture. Rep
  results are a derived, recomputable view of a validated Analysis, a declared lift profile and a rule
  version, in line with `ANALYSIS_SCHEMA.md`, which recommends typed and discriminated protocol results
  rather than an untyped map.
- An app may cache a result keyed by the Analysis digest, rule id and version, and parameters, and must
  recompute on any mismatch.
- An exported rep-metrics file, if wanted, is a new versioned sidecar that references the Analysis by
  source digest and kinematics provenance. It needs its own schema, golden and review in a follow-up
  before any implementation. CSV is an adapter, never a source of truth.
- The #79 pin, its method identities and its frozen artifacts are not modified.

## Alternatives considered

- **Port the recommender segmenter into the Android app.** Rejected. A second unversioned
  implementation of measurement logic, against ADR-0001, and parity could only be tested, never
  guaranteed.
- **Embed the recommender app or its module as a runtime dependency.** Rejected. It couples OpenBar to
  an app's release cycle and storage, and the issue excludes importing the whole app.
- **Adopt the consumer's reported values as displayed.** Rejected. Its mean, duration and ROM cover
  different segment sets, so the displayed numbers would be inconsistent with each other and with the
  core primitives that M0 validated.
- **Evaluate core over the consumer's window instead of the metric interval.** Rejected. It drops a
  segment the consumer averages, so mean parity fails even on uniform sampling (roughly 0.01–0.10 m/s on
  the short synthetic windows), for no benefit.
- **Persist reps inside `analysis-v1`.** Rejected. It would change a frozen contract and golden, and
  rep structure is lift- and rule-specific rather than part of every Analysis.
- **Treat clean and jerk as one positive-velocity run, or support all lifts by assertion.** Rejected;
  excluded by the issue and shown wrong on the reference case.

## Consequences

Positive:

- one definition per displayed value, identical across platforms because it is the same code;
- the recommender comparison is explicit: the peak and (uniform) mean agree, the offsets are named;
- loss, gaps and partial reps are visible instead of producing plausible numbers;
- nothing frozen moves: `analysis-v1`, #79 and the pinned consumer are untouched.

Costs and risks:

- a new core module, golden corpus replay and binding are needed before any app work;
- the displayed mean is not the number the recommender shows for variable-rate video; the real size of
  that difference is unmeasured and must be measured before the two are called interchangeable;
- the adopted constants have no derivation in this repository;
- stage 1 is a single lift until #79 and #58 provide evidence;
- the stricter relative-gap rule may withhold reps that core alone would report on irregular footage;
- reps can be fragmented or dropped by the rise rules, and the rejected-run diagnostic only surfaces
  that; resolving it needs real-clip evidence.

## Owner decisions (recorded 2026-10-18)

1. **Displayed mean.** Decided: core time-weighted mean over the metric interval, as specified in
   Section 3. This keeps one metric meaning and matches the consumer on uniformly sampled video.
2. **Recommender alignment.** Decided: deferred outside the #79 freeze and outside this ADR.
3. **Snatch and clean in stage 1.** Decided: back squat only in Stage 1; snatch and clean join
   only after #79 rep-count and pairing evidence.
4. **Label.** Decided: keep "mean upward velocity" (and "upward ROM" / "peak upward velocity")
   until validation, then amend `KINEMATIC_METRICS.md` in the validating change.

## Revisit when

- #79 or #58 show the interval or constants are wrong for real recordings;
- M1 entry is recorded GO or CONDITIONAL GO and the binding technology is chosen;
- a stage 2 or stage 3 lift is taken up;
- a sidecar export is required.
