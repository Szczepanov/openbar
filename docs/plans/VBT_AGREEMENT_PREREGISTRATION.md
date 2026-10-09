# Personal OpenBar / WL Analysis agreement study (#79)

Study identity: `owner-vbt-agreement-79-v1`.
Design authorized on 2026-10-09: the owner answered “go with recommendations” to
the threshold/criterion, new sample plan and protocol questions. The choices below
exercise that authorization before any formal paired outcomes are calculated.
Status: finalized prospective preregistration; execution pending new recordings,
human confirmation and the private collection lock.
No formal comparison has been run. This document is a preregistration, not a result.

## Readiness and governing evidence

OpenBar baseline: `742225dc21d0d947db0ddf472218cd4dc442dc24` (current main at
inspection). PR #165 is merged; its last fix is
`bb8f93e6d9398f101bacff8ec76e759100221724`. All six checks passed, including
Windows. #78/#84/#85/#86/#87 and coordination #110's #111–#114 are closed.
Consumer #981/#982/#984 and PRs #987/#988/#989 are delivered. Consumer main at
inspection is `ea5e020792551e6c4f5f748cdee92fe50e5d677c`.

Technical delivery does not establish evidence eligibility. Inspected consumer main's
`parseOpenBarAnalysis` rejects nonempty tracker parameters; its agreement CLI also
includes per-video prediction hashes and tracking time bounds in a cohort method
fingerprint. The separate consumer compatibility change fixes these seams while
retaining full provenance and using the existing parser and segmenter. The study
pins consumer commit **`a74cb9dca24864af9f348bf9853fb6fe4f475521`**, based on
the main commit above. It preserves full scalar tracker signatures and separates
only `prediction_sha256`, `seed_timestamp_s` and `end_s` from method identity;
parameterized imports get v2 identities, with persisted rules checked in parity.
Main/browser acceptance requires integrating that change. Formal reporting uses
the explicit pinned commit, not an unreviewed moving main. No OpenBar serialization
change is needed.

Compatibility validation: 134 focused TS tests, 31 focused CLI/hash tests, and
the full consumer `make verify` equivalent passed (8,244 unit tests, 1,294 Python
tests, 422 emulator tests, 49 browser tests, hygiene/static/build/simulation and
latency gates). Independent diff review approved. OpenBar's 211 Python validation
tests, schema catalogue (13 documents), release CLI build and public synthetic
analyze smoke passed. These synthetic checks do not enroll study videos or produce
formal paired outcomes. The preregistration review's failed-input subset ambiguity
was corrected in the execution instructions below.

This harness worktree has no personal manifest, new formal recordings, research
venv or built CLI at initial preflight. FFmpeg/ffprobe are available. Collection,
human confirmation, retained runtime provenance and a locked private input inventory
are required before execution; old worktree data are not a replacement cohort.
Preparation subsequently built `target/release/openbar-cli.exe` with
`cargo build --locked --release -p openbar-cli` and confirmed the existing main
checkout's research interpreter has OpenCV 4.12.0, NumPy 2.2.6 and a CSRT factory.
No environment installation or personal-data copying was needed. These checks
establish executable availability, not a completed session or agreement result.

Governed by [VBT_WORKFLOW_PLAN.md](VBT_WORKFLOW_PLAN.md),
[VBT_LOCAL_HANDOFF.md](../validation/VBT_LOCAL_HANDOFF.md),
[VBT_CLIP_ASSESSMENT.md](../validation/VBT_CLIP_ASSESSMENT.md),
[SCALE_REFERENCE.md](../validation/SCALE_REFERENCE.md), ADR-0001/0003/0005/0006/0007/0008,
and [clean-room boundaries](../clean-room/COMPETITOR_BOUNDARIES.md).
ADR-0006 is labelled Proposed, but its external-process boundary is the implemented
headless contract; this study does not promote it to a mobile architecture.

## Prospective cohort and enrollment

One lifter: the owner. Three entirely new sessions after this preregistration is
committed, on distinct days, using the same camera/plate/reference setup.
Two videos per lift per session, three attempted reps per video:

| Lift / manifest exercise | Load (including bar) | Videos | Planned reps |
| --- | ---: | ---: | ---: |
| Back squat / `back_squat` | 40 kg | 6 | 18 |
| Snatch / `snatch` | 30 kg | 6 | 18 |
| Clean without jerk / `clean` | 40 kg | 6 | 18 |
| Total | Three fixed submaximal loads | 18 | 54 |

These loads reuse load levels described in owner comments, not a prescription to
attempt a new maximum. A session that cannot perform this protocol is recorded as
incomplete; stop rather than silently changing its load. This is a feasibility
decision for this owner/setup/load set. It is not a powered population validation,
a load-velocity profile or evidence for other lifters, loads or recording modes.

Predeclared public slot IDs are `S{1,2,3}-{SQ,SN,CL}-{1,2}`. For each session,
record SQ1, SQ2, SN1, SN2, CL1, CL2, in that order. Enroll the first recording
attempt for each slot, before inspecting either source's numerical outputs. Keep
every enrolled slot even if the lift, recording, tracking, reference or export fails.
No replacement videos, optional stopping, extra reps, cherry-picked successful
reruns or development clips enter this study.

Exclude all previously inspected development/trial clips, including the 2026-10-02
three-lift trial, 2026-10-03 two-snatch comparison and retained suggestion-evaluation
clips. Never access #57 held-out fixtures for collection, development or this study.
Verify novelty against retained development video hashes locally without running
new comparisons. A duplicate is retained as a protocol failure, not re-enrolled.

## Recording, seed and scale protocol

- Record the same original video in both sources: 1080p, nominal 60 fps, fixed
  side view, whole plate and movement visible. Preserve original bytes; no trim,
  transcode, stabilization or timestamp reconstruction. PTS remain authoritative.
- Use one fixed phone/lens/orientation/zoom/focus mode across all sessions. Record
  model/mode, camera height and plate-plane distance locally; target about 1.0 m
  height and 2.0 m distance. These are setup targets, not a validated envelope.
  Recheck framing before recording; a geometry change is a protocol failure.
- Measure the plate's physical diameter; use 0.45 m only if the actual plate is
  0.45 m. Lock the measured value before the first session. A different value
  requires a documented pre-collection amendment, never a post-result correction.
- Film an upright rigid stick in the plate face/sleeve plane for at least 2 s
  before rep 1 in **every** video. Use the existing two-marker arrangement with
  a physically verified 1.30 m separation; record the measurement resolution and
  repeated length check. Camera and zoom must remain fixed through the lift.
- Select a decoded seed frame during that initial still interval, before rep 1,
  with plate and markers visible. Human-confirm or adjust centre/rim and both
  markers on the existing session page. Retain `accepted`/`adjusted`/`manual`
  statuses as emitted. Seed time and an independently viewed first-rep start
  time must demonstrate that the seed precedes the rep.
- Keep a still interval before and after the three reps; for squats capture the
  initial standing position and first descent. Cleans contain no jerk. Record
  attempted rep times from video before comparing source values.
- Use legitimately obtained WL Analysis and its user-facing full per-frame CSV
  export only. Record app version/build if visible, otherwise `not_visible`;
  retain export settings and plate-scale choice. Export the same full original
  clip with no manually chosen rep windows or comparison-guided edits.

Primary OpenBar calibration stays plate-derived and unchanged. The existing
`scale_reference.py` report supplies stick/plate scale ratio separately. Invalid
stick plane, tilt, endpoints or movement stays a reference failure; do not repair
canonical calibration to match WL. No stick-corrected velocity enters PASS/FAIL.
The report's geometric OpenBar/WL velocity ratio is not a physical scale estimate.
Keep those two ratios distinct; proportional/constant association is descriptive
and cannot by itself identify scale or tracking as the cause.

## Frozen analysis and segmentation

Primary tracker: research `opencv-csrt`, invoked as `analyze_lift.py --tracker csrt`
or session `--tracker-policy csrt-all-v1`. There is no production/default tracker
decision. SAM 2 is not part of this study and cannot rescue an unfavorable CSRT
result; it requires a separately preregistered study.

Use preset `vbt-sg-0.15s-v1`, expanded and retained as:

```text
--filter savitzky-golay --filter-window-s 0.15
--filter-polynomial-order 2 --filter-max-gap-s 0.2
--kinematics-max-gap-s 0.2 --kinematics-min-confidence 0
```

Record resolved filter sample count and full method configuration. At regular
60 fps this is nine samples. All inputs must resolve to the same method cohort;
an unsupported timestamp pattern/filter rejection is a recorded failure, not a
reason to change the filter. Confidence 0 is an explicit inclusion floor and does
not make confidence a probability or establish correct object tracking.

Both sources use the consumer's one shared `concentric-segmentation-v2` via
`segmentConcentricReps`; WL parser `wl-analysis-csv-v2` and the existing OpenBar
parser extended as `openbar-analysis-v2` for parameterized trackers. Its legacy
unparameterized v1 identity remains distinct; canonical input stays `analysis-v1`.
Pin their code commit before execution. Detection uses maximal
positive-velocity runs and rise at least `max(10 cm, 0.5 * largest rise)`.
v2 trims edges below 0.05 m/s only when each omitted rise is at most 1 cm;
completeness uses the full run and 0.85 descent fraction. No manual boundaries,
new segmenter, Python port or tuning is allowed. Applying the same rule to each
source does not force numerically identical boundaries.

Primary: reported mean concentric velocity (arithmetic sample mean over the shared
rule's active window, rounded by that rule to 0.001 m/s). Secondary: peak velocity
(same window, 0.001 m/s) and ROM (cm, 0.01 cm). Do not replace the shared rule's
sample mean with a new duration-weighted estimator. OpenBar's vertical metric
axis is already positive up; no sign flip. Its derivative is backward difference;
WL's internal derivative is unknown.

## Pairing, exclusions and failures

Use existing `pairReps` with minimum temporal interval IoU **0.5**. It greedily
selects one-to-one pairs by descending IoU, then WL index and OpenBar index.
`offsetS=0` because both consume the same full video. If exports have a documented
time-origin offset, establish and hash it from a visible event/metadata **before**
numerical outcomes; never optimize it for correlation or number of matches.

Keep raw/lost samples and canonical bytes unchanged. The OpenBar parser identifies
null-velocity breaks or missing-sample intervals above 1.5 times median spacing;
reps spanning these breaks or touching series edges remain excluded with reasons.
Retain both sources' completeness flags, source-only reps and all excluded reps.
Never interpolate loss or remove extra detected reps to obtain the planned count.
Hand review checks paired identities against attempted reps; it cannot edit pairing.

Run #111 assessment for every produced analysis. Require processing `complete`
and all mechanical checks `valid` for an analyzable clip. Carry suitability
`unknown`/`rejected` and accuracy `not_established` unchanged. Research inclusion
under this protocol is a separate adjudication, not an assessment-status upgrade
or consumer training eligibility. Unknown mechanical/PTS validity is unresolved.
Machine-origin outputs never enter the formal human-confirmed cohort.

The current agreement CLI aborts the whole invocation on an unreadable input or
parser rejection; it does not emit a failed-video row. Therefore the final study
report must include an 18-slot accounting table alongside the existing per-lift
CLI reports. Log failed slots and exact stage/reason before the numerical run;
feed analyzable slots only to the existing CLI and explicitly identify its subset.
This is a reporting supplement, not another parser/segmenter. No missing slot is
dropped from the study denominator. Infrastructure failures may retry unchanged
inputs with the same pinned environment; changed evidence requires stopping and
documenting a separate study/amendment, not replacing this run silently.

## Frozen threshold and decision

Absolute tolerance **T = 0.05 m/s**. The owner delegated this choice before formal
results. It is a conservative personal training-use tolerance: avoid treating
differences of this size as interchangeable training signals. Published work
discusses 0.05–0.10 m/s performance changes in Smith-machine squat/bench contexts
([Martínez-Cava et al., 2020](https://doi.org/10.1371/journal.pone.0232465),
CC BY). That motivates the order of magnitude; it does not validate this tolerance
for Olympic lifts or turn WL agreement into physical accuracy. This choice is not
based on the development differences in #79 and is not the M0 physical-reference gate.

For each lift separately, use the existing report's differences `OpenBar - WL`,
sample SD (denominator n-1), bias, and descriptive limits `bias +/- 1.96 * SD`.
Use the report's serialized six-decimal LoA for the comparison, inclusive at
`lowerLoA >= -0.050000` and `upperLoA <= 0.050000`. No pooled cross-lift rescue.

**PASS** requires all of the following:

1. All 18 slots satisfy the frozen collection/confirmation/reference protocol and
   complete mechanical validation; six videos from three sessions for each lift.
2. Each video has exactly three video-verified attempted reps, three detected reps
   on each side, three one-to-one matches, both completeness flags true, and zero
   source-only or excluded reps. Thus each lift contributes exactly 18 pairs.
3. Every lift's two primary LoA satisfy the bounds above, with finite available
   statistics and no method mismatch.
4. Two report runs from identical retained inputs and pinned tools have identical
   JSON **and** Markdown bytes, and the predeclared hand-check succeeds.

**FAIL** is any completed study that does not meet these conditions, including
protocol/measurement failure, insufficient counts or unavailable statistics.
Report the numerical and completeness failures separately. During collection or
an unresolved infrastructure problem, status is **PENDING**, never PASS. Secondary
metrics and scale diagnostics cannot change the primary verdict.

Nested reps and the small one-owner cohort make these LoA descriptive; they are
not confidence intervals or a guarantee of agreement on future sets. Report
per-video statistics as delivered; no significance claim or post-hoc power claim.

## Input lock, execution and reproducibility

Before any formal pairing, commit this finalized preregistration with the tested
consumer commit pinned. Keep the OpenBar measurement baseline above fixed even
though documentation commits advance this branch. Record clean source states,
CLI binary SHA-256, Python/OpenCV/NumPy, FFmpeg/ffprobe versions, OS/CPU, tracker
version/config, parser/rule versions and WL version. Do not run on a dirty tool tree.

Before comparison, lock a private UTF-8 inventory in slot order containing video,
manifest entry, seeds, session/click CSVs, prediction, analysis, run/session record,
assessment, scale evidence, WL CSV and per-lift pairs-file SHA-256; also record
failed/not-recorded slots and reasons. Hash exact bytes, not reparsed JSON. Retain
all inputs under ignored `validation/private/vbt/` or owner storage. Commit only
the inventory's aggregate SHA-256, slot counts and freeze-commit references in an
aggregate collection-lock note, before the first formal CLI invocation. That binds
the prospective roster without publishing filenames, media, CSVs or analyses.

The existing consumer command, from its pinned `app/` directory, is:

```text
npm run evidence:velocity-agreement -- --pairs <private-lift-pairs.json>
  --output <private-report-basename> --segmentation concentric-segmentation-v2
  --min-overlap 0.5
```

Its input is `{"pairs":[{"label":"S1-SQ-1","loadKg":40,"wlCsv":"...",
"openBarAnalysis":"...","offsetS":0}]}`; paths are relative to the pairs file.
Use a separate pairs file for each lift containing its analyzable slots (all six
are required for PASS). If a lift has none, do not invoke the CLI's non-empty
pairs interface; record unavailable statistics and all six failures in the study
accounting table. No threshold option, importer
CLI, assessment ingestion or automatic PASS exists; adjudicate this preregistered
criterion from the delivered report and full accounting table.
Generate each lift's reports twice to new basenames without `--force`, comparing
the complete bytes/hashes. Rehash inputs afterwards. Retain both reports privately.
Synthetic regression reports used for consumer tests are not formal study outcomes.

Hand-check the first slot `S1-SQ-1`, all three reps, selected independently of
results. On shared-rule windows, recompute each OpenBar `vy_mps` from consecutive
filtered positions and authoritative timestamp deltas; confirm null/gap behavior.
Apply the rule's arithmetic mean/peak and precision, and endpoint ROM. Compare
rounded values exactly to parser/report output and verify pairing against video.
Retain the calculation and seed/reference review locally; publish only check status
and aggregate discrepancies. If that slot cannot be checked, record the failure;
do not substitute an easier video.

## Final report and reviewed decision

Commit a Markdown aggregate report covering **every** slot, failure stages/reasons,
lost/break counts, matched/unmatched/excluded counts, per-lift/per-video bias, LoA,
mean absolute difference, secondary summaries, scale ratios, proportional/constant
diagnostics (including unavailable reasons), report hashes, inventory hash,
tool/freeze commits, byte reproduction and hand-check status. Private full reports
retain per-rep values; do not commit CSVs, analyses or private input paths.

Record PASS/FAIL and a reviewed decision under #79 / plan step 4. Until a later
explicit reviewed consumer policy, retain WL Analysis as training source even if
this study passes. No database writes, live-trial eligibility promotion, default
source switch, production tracker selection, #58 physical-accuracy closure or
#80/M1 promotion is authorized by this study. Amendments after outcomes create a
new study identity; retain the original preregistration and failed result.
