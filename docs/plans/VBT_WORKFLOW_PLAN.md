# Personal VBT workflow — implementation plan

Status: proposed (owner-requested, 2026-10-02)
Related: #78, #79, #80, #57, #58, #59, #77; Szczepanov/adaptive-training-recommender#981, #982
Governing: ADR-0001, ADR-0003, ADR-0005, ADR-0006, ADR-0007, ADR-0008, ADR-0009

## 1. Context

The owner wants to use OpenBar for velocity-based training (VBT): per-rep velocities for their own
training, exported to their adaptive training recommender (Szczepanov/adaptive-training-recommender),
and eventually feedback between sets on the phone, replacing WL Analysis.

Current state:

- **Velocity source today.** The owner films sets with WL Analysis. The recommender imports WL
  Analysis' per-frame CSV export and does its own VBT maths. Its versioned parser
  (`app/src/observations/wlAnalysisCsv.ts`, `wl-analysis-csv-v1`) segments concentric reps and
  computes mean concentric velocity, peak velocity and ROM per rep.
- **OpenBar output.** `analyze` writes `analysis-v1`, whose `derived.kinematics.samples` carry
  per-frame `timestamp_s`, `x_m`, `y_m`, `vx_mps`, `vy_mps` and `confidence`. That is the same kind of
  data the recommender consumes.
- **The missing link.** `analyze` can only run OpenBar's own `template` / `contrast` trackers, which
  are far off on real video (seed-excluded MAE 34–783 px). The research trackers are good enough for
  VBT but live outside the canonical pipeline (#77):
  - `opencv-csrt`: 3.2–7.5 px;
  - `sam2.1-*-circle`: 1.9–4.4 px.

## 2. Decision this plan records

**For personal VBT, tracker accuracy is not the bottleneck, so the #57 pixel-gate work no longer
blocks it.** Evidence:

- Tracker error is about 1 % of plate diameter (#77).
- Plate-scale uncertainty is 3–12 % (`docs/analysis/VBT_REFERENCE_EXPERIMENTS_FINDINGS.md` §5), and
  it scales every velocity directly.
- Mean concentric velocity averages over the whole movement.

The pass B label-noise measurement, the confidence redesign and the freeze / held-out phases of #57
stay valid, but become optional rigour for the personal workflow. They still gate a production tracker
(#57 → #59 / #65 phone runtime benchmark → #80).

What does not change: every measurement rule in AGENTS.md, ADR-0003 and ADR-0005. That means raw
observations preserved, lost means no coordinate, authoritative timestamps, determinism, and
provenance. These rules are what make the comparison in step 4 trustworthy.

## 3. Goals and non-goals

Goals:

1. Post-session VBT from OpenBar on the owner's PC, imported into the recommender.
2. An evidence-based answer to "can OpenBar replace WL Analysis as the velocity source?"
3. A sequenced path to between-set feedback on the phone, after M0.

Non-goals:

- Rep segmentation or VBT metrics inside OpenBar for this workflow. The recommender already owns them.
  OpenBar's own phase-scoped metrics stay governed by `docs/validation/KINEMATIC_METRICS.md`.
- Treating WL Analysis as ground truth. It is a comparison (#79); the physical reference stays #58.
- Any phone, Flutter, camera or on-device inference code before #80's entry decision.
- Tuning OpenBar to agree with WL Analysis.

## 4. Steps

| Step | Repo | Issue | What | Depends on |
|---|---|---|---|---|
| 1 | — | — | Keep using WL Analysis → recommender (already works) | — |
| 2 | openbar | #78 | `analyze --observations <tracker-prediction-v1>`: CSRT / SAM 2 predictions through canonical calibration and kinematics | — |
| 3a | recommender | Szczepanov/adaptive-training-recommender#981 | Import `analysis-v1` as strength-trial evidence (provider `OpenBar`), reusing the WL segmentation | 2 (a synthetic analysis is enough to start) |
| 3b | recommender | Szczepanov/adaptive-training-recommender#982 | Per-rep agreement report: WL Analysis vs OpenBar through identical segmentation | 3a |
| 4 | openbar | #79 | Pre-registered agreement study on the owner's lifts → PASS / FAIL → switch the default velocity source or not | 2, 3a, 3b |
| 5 | openbar | #80 | Later: between-set feedback on the phone (M1 entry decision, ADR-0008) | 4, #57, #65 |

### Step 2 — `analyze --observations` (#78)

- **Flags.** `--observations` and `--tracker` are mutually exclusive, and exactly one is required.
  There is still no default tracker (`docs/validation/CLI_PIPELINE.md`).
- **Validation before writing anything.** The prediction stream must have:
  - the right schema version and coordinate space;
  - a source video hash matching the decoded media;
  - every timestamp on the decoded timeline, strictly increasing, with a sample at the seed;
  - finite coordinates inside the display window, and confidence in [0, 1];
  - no centre on lost samples.
- **Mapping.** Imported samples become raw observations unchanged. Lost stays lost.
- **Provenance.** `provenance.tracker` records the external implementation, version, config and the
  prediction file's SHA-256. It is expected to fit `analysis-v1` without a shape change. **If it does
  not, stop and ask before touching the schema or `ANALYSIS_SCHEMA_VERSION`.**
- **Compatibility.** Existing `template` / `contrast` output stays byte-identical (golden test
  unchanged).

Default tracker for the personal workflow: `opencv-csrt`, which runs on CPU and is Apache-2.0.
`sam2.1-bplus-circle` is the optional higher-accuracy mode on a desktop GPU. Both run from
`research/` to produce the prediction file; neither becomes an OpenBar production dependency here.

### Step 3 — recommender import and report (Szczepanov/adaptive-training-recommender#981, #982)

- **Mapping to vertical-up.** `analysis-v1` calibrated and kinematic coordinates use
  `calibration.coordinate_convention` = `reference_centre_x_right_y_up`, so `y_m` and `vy_mps` are
  already upward-positive. Use them as-is: displacement = y_m − y_m at the first sample, velocity =
  vy_mps. Reject any other `coordinate_convention`. Only raw pixel observations are +Y down
  (ADR-0007).
- **Gaps.** OpenBar omits samples that are lost or below the confidence floor. Segmentation must not
  bridge a gap as if it were continuous.
- **Load.** It is not in `analysis-v1`; the athlete enters it on the capture screen.
- **Not comparable with WL Analysis trials.** OpenBar trials are a different device and setup. They
  must not be mixed into WL Analysis trends without a reviewed decision (recommender #897 requires the
  same device and setup for longitudinal velocity).

### Step 4 — agreement study (#79)

1. **Pre-register before pairing any results:**
   - the videos;
   - the tracker, filter and kinematics parameters;
   - the agreement threshold, chosen by the owner.
2. Run each video through WL Analysis and OpenBar, then through the same recommender segmentation.
3. Report per rep:
   - bias, limits of agreement and mean absolute difference;
   - the proportional component (scale) separately from the constant component (tracking).
4. Record PASS / FAIL and the decision.

Only aggregates are committed.

**Trial run (2026-10-02, excluded from the formal study).** One clean & jerk set:
`self-clean-jerk-side-002`, 40 kg, four reps, 60 fps. It was run through WL Analysis and through
`opencv-csrt` → `analyze --observations`, then the recommender's segmentation rules (re-implemented for
the check).

- **Same reps.** Both sources found the same 4 reps.
- **Positions agree closely.** Over 533 matched frames, OpenBar = 1.022 × WL Analysis, with a residual
  SD of 0.39 cm. That is about 2 % scale and negligible tracking disagreement.
- **Unfiltered velocity is too noisy to compare.** With `--filter raw`, frame-difference noise at the slow
  start of each pull delayed rep onset by 0.08–0.15 s on three of the four reps. That raised OpenBar's mean concentric velocity by
  0.009–0.174 m/s (mean +0.088).
- **A generic smoother fixes it.** With a Savitzky–Golay filter (window 9, order 2, max gap 0.2 s) — a
  textbook setting chosen before the run, not tuned — the mean concentric velocity difference was
  +0.006 to +0.050 m/s (mean +0.032), consistent with the 2 % scale difference. Peak velocity stayed
  0.10–0.14 m/s higher, because WL Analysis smooths more heavily.

Implications for #79:
- Pre-register the OpenBar filter and its parameters.
- Make mean concentric velocity the primary metric, with peak velocity secondary (it is
  filter-dependent).
- Report the 2 % scale difference separately: deciding which source is right is #58's job.

## 5. Known risks

| Risk | Effect | Mitigation |
|---|---|---|
| Plate-scale uncertainty, 3–12 % (#58 findings) | Proportional velocity bias against WL Analysis | Step 4 measures the proportional bias separately. A dedicated scale follow-up if it dominates. |
| Raw backward-difference velocity is noisy | The recommender's velocity > 0 run detection may split one rep into several | The filter choice is part of the step 4 pre-registration. #981 tests rep counts on real analyses. |
| Lost frames inside a rep | Missing velocity mid-rep | Explicit gap rule in #981. The report lists reps found by only one source (#982). |
| WL Analysis is itself approximate | Agreement is not accuracy | Stated as agreement in #79. The physical reference stays #58. |
| Desktop-only workflow | No between-set feedback until #80 | Accepted. WL Analysis remains the in-session tool until then. |

## 6. Stop conditions

- Step 2 needs an `analysis-v1` shape change: stop and ask (AGENTS.md schema rules).
- Step 4 FAIL with a proportional bias: do not switch the velocity source. Open a scale follow-up
  under #58 instead of tuning trackers.
- Step 4 FAIL with tracking-type disagreement: re-check with `sam2.1-bplus-circle` before considering
  tracker work. Resume #57 only with a pre-registered plan.

## 7. Documents to update when steps land

- Step 2: `docs/validation/CLI_PIPELINE.md`, and `research/PLATE_TRACKING_PLAN.md` (Phase 5 option (a)
  is then done).
- Step 4: this plan's status, with the decision recorded in #79.
- Step 5: an ADR-0008 M1 entry record and a phone plan in `docs/plans/`.
