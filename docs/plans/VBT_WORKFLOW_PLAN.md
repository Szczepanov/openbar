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

### Step 2, post-session command — `research/vbt-workflow/analyze_lift.py` (#86)

One script turns a lift video and a seed into `analysis-v1`. It is orchestration only: CSRT stays
in `research/opencv-tracking/track.py`, and calibration, filtering and kinematics stay in
`openbar-core` through `analyze --observations`. It never edits a prediction or an analysis. It is
stdlib-only, but run it with the research venv's Python because `track.py` needs OpenCV.

**Personal manifest.** Personal videos go into a separate manifest,
`validation/private/vbt/manifest.json` by default. Manifests are accepted only under
`validation/private/vbt/` or `target/`. Any path ending in `validation/private/manifest.json` (the
#57 development/held-out manifest) is refused, including the main checkout's copy seen from a
worktree. The match ignores case, `\` versus `/`, trailing dots and spaces, and an NTFS `:stream`
suffix, and it also applies after following symlinks. Personal lifts therefore cannot contaminate
tracker-selection evidence.
The fixture id is `vbt-` plus the first 16 hex digits of the video's SHA-256. Entries are drafted
by `fixture_probe.py` as `purpose: development`, `private_only`, side view, fixed camera, tagged
`personal-vbt`.

**Owner flow** (Windows paths shown; `py` = `research/opencv-tracking/.venv/Scripts/python`):

1. Copy the video to `validation/private/vbt/media/` (it must be inside the repository).
2. Register it. Registration is idempotent: the same video gets the same id, no duplicate entry,
   and an identical entry is never rewritten. It prints the next commands with the id filled in.

   ```bash
   py research/vbt-workflow/analyze_lift.py register \
     --video validation/private/vbt/media/<file>.mp4 --plate-diameter-m 0.45 --exercise clean
   ```

3. Make the seed on a frame **before the first rep**. Tracking runs forward from the seed, so
   anything earlier is never tracked. Pick a time from the video player, then:

   ```bash
   python validation/tools/label_package.py --manifest validation/private/vbt/manifest.json \
     --fixture <id> --at-s <seconds-before-first-rep> --annotator-id seed
   # Open validation/private/annotations/work/<id>.seed/index.html in a browser:
   # click the plate centre, Shift+click the rim, press 1/2/3 for quality, download the CSV.
   python validation/tools/annotations.py seed --manifest validation/private/vbt/manifest.json \
     --metadata validation/private/annotations/work/<id>.seed/metadata.json --csv <downloaded.csv> \
     --output validation/private/vbt/seeds/<id>.manual-target-seed-v1.json
   ```

4. Run the workflow:

   ```bash
   py research/vbt-workflow/analyze_lift.py run \
     --video validation/private/vbt/media/<file>.mp4 \
     --seed validation/private/vbt/seeds/<id>.manual-target-seed-v1.json \
     --plate-diameter-m 0.45 --exercise clean --output-dir validation/private/vbt/analyses \
     --preset vbt-sg-0.15s-v1
   ```

   Check the `SEED:` line in the summary: the seed time must be before the first rep. The script
   cannot detect a late seed, so it does not fail on one.

**Outputs**, side by side in `--output-dir`. The directory must be inside the repository, under
`validation/private/vbt/` or `target/`, or somewhere every output is git-ignored; anything else, such
as `validation/fixtures/public/`, is refused. The video, seed and manifest must also be inside the
repository, so the run record only holds repository-relative paths. Outside the two dedicated
roots, both the final output names and their `.tmp` staging names must be git-ignored.

- `<id>.opencv-csrt.prediction-v1.json`: `track.py --omit-runtime` output, unedited, whole clip from
  the seed;
- `<id>.analysis-v1.json`: canonical analysis for the recommender import (step 3a);
- `<id>.run-record.json` (`openbar-research-vbt-run-record`, format version 1): both commands as
  run from the repository root, the video, seed and manifest-entry SHA-256, the seed timestamp, the
  explicit analyze options and preset name, and the output SHA-256. It also records the OpenBar
  git state: the commit, whether tracked files changed, the SHA-256 of `git diff HEAD --binary --no-ext-diff --no-textconv --no-color`, and
  the number of untracked files under `crates/`, `apps/` and `research/`. Tool versions: Python,
  FFmpeg, ffprobe, cargo and rustc (or the `--openbar-cli` path and SHA-256), OpenCV and NumPy. It
  has no wall-clock time.

**No silent defaults.** `--plate-diameter-m` and `--exercise` are required. The analyze
configuration is either a named preset or the explicit `--filter ...` flags plus
`--kinematics-max-gap-s` and `--kinematics-min-confidence`, never both. The only preset,
`vbt-sg-0.15s-v1`, expands to `--filter savitzky-golay --filter-window-s 0.15
--filter-polynomial-order 2 --filter-max-gap-s 0.2 --kinematics-max-gap-s 0.2
--kinematics-min-confidence 0`, and the run record stores the expanded flags. 0.15 s resolves to 9
samples at 60 fps and 5 at 30 fps; at 12 fps `analyze` rejects it, so use explicit flags there. The
`analyze` step uses `cargo run --locked --release -p openbar-cli`, or `--openbar-cli <binary>`. The
binary path is resolved from the current directory, `.exe` may be left out, and it must exist inside
the repository.

**Fails closed**, before tracking and without touching the manifest, on:

- a seed for a different video (its `fixture_id` is not the id derived from the video hash), or a
  seed with no `fixture_id`;
- missing `ffmpeg` or `ffprobe`;
- an existing output without `--force`;
- a manifest, video, seed, output directory or `--openbar-cli` binary outside the allowed
  locations above, or a missing `--openbar-cli` binary;
- the #57 manifest, or a personal manifest that is not valid `fixture-manifest-v1`;
- a registered entry with a different exercise, media, video metadata or plate diameter. The
  error shows the registered and new values. Conditions and notes may be hand-edited;
- the same video already registered under another id;
- no readable git state;
- a generated tracker prediction or analysis that does not match its committed JSON schema;
- the video, seed, or registered manifest entry changing while tracking/analysis is running.

Every step writes to `.<name>.tmp` files in the output directory; leftovers of a crashed run are
removed first. If tracking, analysis or writing the run record fails, nothing is renamed, so the
previous outputs and any unrelated files stay untouched (this includes a failed `--force` run).
Before promotion, the workflow re-checks the video SHA-256, seed SHA-256 and registered manifest
entry hash, and validates the generated prediction and analysis against
`tracker-prediction-v1.schema.json` and `analysis-v1.schema.json`. A mismatch fails without
promoting the staged set. Once everything has succeeded, the old run record is removed first. The files are then renamed in
order: prediction, analysis, and the run record last. Each rename retries 5 times, 0.2 s apart, on
a Windows `PermissionError`. Three renames cannot be atomic as a set, so the guarantee is: **an
output set without a run record is incomplete**; re-run with `--force`. A set with a run record is
complete, and the record's SHA-256 values identify its files. The recorded commands use the final
file names; neither output embeds its own path.

**Determinism.** Re-running on the same inputs gives byte-identical predictions and `analysis-v1`.
The workflow always passes `track.py --omit-runtime`. With that flag, `track.py` leaves the
wall-clock `runtime` out of the prediction, prints it to the console only, and writes LF line
endings on every platform. Without the flag, `analyze` would hash a prediction that changes on
every run into `prediction_sha256`. `track.py`'s default output is unchanged, because `compare.py`,
the benchmark and `track_gpu.py` read `runtime`. The run record has no runtime either; re-running
into the same output directory gives a byte-identical record. The opt-in test
`OPENBAR_VBT_E2E=1 ... -k EndToEnd` in `research/vbt-workflow/tests` checks this with the real tools.

**Do not commit** anything under `validation/private/`. Report aggregates only.

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
  is then done). The #86 post-session command is documented above and in
  `docs/validation/CLI_PIPELINE.md`.
- Step 4: this plan's status, with the decision recorded in #79.
- Step 5: an ADR-0008 M1 entry record and a phone plan in `docs/plans/`.
