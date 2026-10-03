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

Trackers for the personal workflow: `opencv-csrt` runs on CPU and is Apache-2.0.
`sam2.1-bplus-circle` is an optional desktop-GPU research alternative. It reduced the specific
fast-lift spikes described below, but it is not a selected production tracker: #57 Phase 3 remains
PARTIAL and the SAM 2 circle candidates still fail some M0 tracker gates. The post-session command
requires an explicit `--tracker` choice, so there is no default. Both run from `research/` to produce
the prediction file; neither becomes an OpenBar production dependency here.

### Step 2, post-session command — `research/vbt-workflow/analyze_lift.py` (#86)

One script turns a lift video and a seed into `analysis-v1`. It is orchestration only. Tracking
stays in `research/opencv-tracking/track.py` (CSRT) or `research/gpu-tracking/track_gpu.py` (SAM 2),
and calibration, filtering and kinematics stay in `openbar-core` through `analyze --observations`. It
never edits a prediction or an analysis. It is stdlib-only, but run it with the research venv's Python
because `track.py` needs OpenCV.

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
     --tracker csrt --preset vbt-sg-0.15s-v1
   ```

   Check the `SEED:` line in the summary: the seed time must be before the first rep. The script
   cannot detect a late seed, so it does not fail on one.

**Trackers.** `--tracker` is required and has no default:

| `--tracker` | Runs | Needs | Cost |
|---|---|---|---|
| `csrt` | `track.py --tracker csrt --omit-runtime` with the interpreter running this script | the research venv (OpenCV) | seconds on CPU |
| `sam2.1-bplus-circle` | `track_gpu.py --candidate sam2.1-bplus-circle --omit-runtime --geometry-output ...` with `--gpu-python` | an NVIDIA GPU with CUDA; the GPU research environment (the requirements file is bootstrap constraints, plus SAM 2 installed as documented in `research/PLATE_TRACKING_PLAN.md`); and `sam2.1_hiera_base_plus.pt` in `validation/private/models/` with its committed SHA-256 (`download_models.py`) | about 80 s per 13 s clip on an RTX 3060 Ti; about 150 s was observed for an 832-frame clip |

For the personal/research workflow, SAM 2 is an opt-in diagnostic alternative when CSRT shows an
obvious drift or velocity spike; that does not promote it to the production path. On two private
owner 30 kg snatch clips, preliminary #79 agreement data found CSRT first-rep peaks of 3.1–3.4 m/s
against WL Analysis's 2.44 and 2.65 m/s, while SAM 2.1 base-plus with the circle fit removed those
spikes. With a separately measured stick scale, the SAM 2 per-rep peaks were within 0.01–0.09 m/s
of WL Analysis and the velocity-shape correlation was r = 0.999. WL Analysis is a comparison, not
ground truth, and these two private clips are not the pre-registered formal #79 study. Run the SAM 2
path like this:

```bash
py research/vbt-workflow/analyze_lift.py run \
  --video validation/private/vbt/media/<file>.mp4 \
  --seed validation/private/vbt/seeds/<id>.manual-target-seed-v1.json \
  --plate-diameter-m 0.45 --exercise snatch --output-dir validation/private/vbt/analyses \
  --tracker sam2.1-bplus-circle --gpu-python research/gpu-tracking/.venv/Scripts/python.exe \
  --preset vbt-sg-0.15s-v1
```

`--gpu-python` is required with `sam2.1-bplus-circle` and refused with `csrt`. It is resolved like
`--openbar-cli` (from the current directory, `.exe` optional) and must be inside the repository.
Directory links are followed, so a venv folder that links outside the repository is refused. The
interpreter file itself is not followed, because a POSIX venv `python` is a symlink to the base
interpreter. The run record stores the resolved folder plus the file name as it is spelled on disk.
The same interpreter is therefore recorded identically whether you type `python`, `python.exe` or a
different case, and a `--force` re-run gives a byte-identical record. Before
registering or tracking, the workflow runs a short check in that interpreter. It stops with a clear
error if torch or SAM 2 cannot import, or if no CUDA device is visible. `track_gpu.py` then verifies the
checkpoint's SHA-256 before decoding. A missing or wrong checkpoint, or any other `track_gpu.py`
failure, fails the run, and the error quotes `track_gpu.py`'s own last error line.
`track_gpu.py` decodes the tracked window to temporary JPEGs under
`validation/private/work/gpu-tracking/` and deletes them when it finishes. Calibration is still
plate-based: the analyze step is the same for both trackers.

**Outputs**, side by side in `--output-dir`. The directory must be inside the repository, under
`validation/private/vbt/` or `target/`, or somewhere every output is git-ignored; anything else, such
as `validation/fixtures/public/`, is refused. The video, seed and manifest must also be inside the
repository, so the run record only holds repository-relative paths. Outside the two dedicated
roots, both the final output names and their `.tmp` staging names must be git-ignored.

Every name carries the tracker implementation (`<impl>` is `opencv-csrt` or `sam2.1-bplus-circle`).
CSRT and SAM 2 outputs for the same video can therefore sit in one folder, and `--force` for one
tracker never touches the other's files. Outputs written by workflow version 2 (`<id>.analysis-v1.json`,
`<id>.run-record.json`) are not renamed or replaced. A run prints a `warning: legacy workflow-v2
outputs ...` line when it finds them in `--output-dir`; delete them by hand if they are no longer
wanted. Coexisting outputs mean one lift can have more than one `analysis-v1`; see step 3 for which
one to import.

- `<id>.<impl>.prediction-v1.json`: the tracker's `--omit-runtime` output, unedited, whole clip from
  the seed;
- `<id>.sam2.1-bplus-circle.geometry.json` (SAM 2 only): `track_gpu.py`'s circle-fit geometry
  sidecar (`openbar-research-geometry-sidecar`, format 0) for the same run. It has one entry per sample:
  whether the fit was accepted, why not, the radius, inliers and edge coverage. A rejected fit that
  still has a centre stays a tracked sample at 0.7× confidence, and this file shows which samples
  those are. It is staged and
  promoted with the other outputs and hashed in the run record. The centroid sibling prediction
  (`--sibling-output`) is not produced: it is never analysed, so it would be an unrecorded side file;
- `<id>.<impl>.analysis-v1.json`: canonical analysis for the recommender import (step 3a);
- `<id>.<impl>.run-record.json` (`openbar-research-vbt-run-record`, format version 2, workflow
  `vbt-workflow-3`): both commands as run from the repository root, the video, seed and manifest-entry
  SHA-256, the seed timestamp, the tracker (`tracker`, `tracker_implementation`, `tracker_script`,
  `tracker_determinism`), the explicit analyze options and preset name, and the SHA-256 of every
  other output (`prediction`, `analysis`, and `geometry` for SAM 2). It also records the OpenBar
  git state: the commit, whether tracked files changed, the SHA-256 of `git diff HEAD --binary --no-ext-diff --no-textconv --no-color`, and
  the number of untracked files under `crates/`, `apps/` and `research/`. Tool versions: Python,
  FFmpeg, ffprobe, cargo and rustc (or the `--openbar-cli` path and SHA-256). For CSRT it also
  records OpenCV and NumPy. For SAM 2 it records the `--gpu-python` repository-relative path (no
  SHA-256), the GPU model, the driver version, and the torch/torchvision/NumPy/OpenCV/`sam2`
  package provenance that `track_gpu.py` wrote into the prediction. In `commands`, the CSRT track
  step's interpreter is written as `python`; the SAM 2 track step's is the `--gpu-python` path. It
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
- no `--tracker`, `sam2.1-bplus-circle` without `--gpu-python`, or `--gpu-python` with `csrt`;
- a missing `--gpu-python` interpreter, one without torch, or no visible CUDA device;
- an existing output of the same tracker without `--force`;
- a manifest, video, seed, output directory, `--openbar-cli` binary or `--gpu-python` interpreter
  outside the allowed locations above, or a missing `--openbar-cli` binary;
- the #57 manifest, or a personal manifest that is not valid `fixture-manifest-v1`;
- a registered entry with a different exercise, media, video metadata or plate diameter. The
  error shows the registered and new values. Conditions and notes may be hand-edited;
- the same video already registered under another id;
- no readable git state;
- a generated tracker prediction or analysis that does not match its committed JSON schema, a
  prediction whose `implementation.name` or `fixture_id` is not the requested tracker and video, or
  (SAM 2) a geometry sidecar that is not format 0 for the same tracker and video, does not carry
  the same implementation/provenance as the prediction, or is not sample-for-sample timestamp
  aligned with the prediction. This includes a missing sidecar;
- the video, seed, or registered manifest entry changing while tracking/analysis is running.

Every step writes to `.<name>.tmp` files in the output directory; leftovers of a crashed run are
removed first. If tracking, analysis or writing the run record fails, nothing is renamed, so the
previous outputs and any unrelated files stay untouched (this includes a failed `--force` run).
Before promotion, the workflow re-checks the video SHA-256, seed SHA-256 and registered manifest
entry hash, and validates the generated prediction and analysis against
`tracker-prediction-v1.schema.json` and `analysis-v1.schema.json`. A mismatch fails without
promoting the staged set. Once everything has succeeded, the old run record is removed first. The files are then renamed in
order: prediction, geometry sidecar (SAM 2), analysis, and the run record last. Each rename retries 5 times, 0.2 s apart, on
a Windows `PermissionError`. Several renames cannot be atomic as a set, so the guarantee is: **an
output set without a run record is incomplete**; re-run with `--force`. A set with a run record is
complete, and the record's SHA-256 values identify its files. The recorded commands use the final
file names; neither output embeds its own path.

**Determinism.** With CSRT, re-running on the same inputs gives byte-identical predictions and
`analysis-v1`. The workflow always passes `--omit-runtime` to the tracker. With that flag,
`track.py` and `track_gpu.py` leave the wall-clock `runtime` out of the prediction, print it to the
console only, and write LF line endings on every platform. `track_gpu.py` also writes the geometry
sidecar that way. Without the flag, `analyze` would hash a prediction that changes on every run into
`prediction_sha256`. Both scripts' default output is unchanged, because `compare.py` and the
benchmark read `runtime`. The run record has no runtime either; re-running into the same output
directory gives a byte-identical record.

SAM 2 runs on a GPU with bfloat16 autocast, so byte identity is an observation, not a guarantee. On
one RTX 3060 Ti, two runs gave identical samples: max centre difference 0 px, and no state or
confidence differences. That held for the 12-frame public synthetic fixture (byte-identical
prediction and sidecar with `--omit-runtime`) and for an 832-frame private clip (identical apart from
`runtime`, compared in default mode). Another GPU model, driver, or torch or CUDA build may give
different numbers. The prediction records those under `implementation.config`, and the run record
states this limit in `configuration.tracker_determinism`. The same applies to CSRT: its byte identity
was observed with the OpenCV and NumPy versions in the run record, on CPU.

`implementation.config` is part of the hashed prediction (`prediction_sha256`). For SAM 2 it also
holds two fields that are not tracking results and can change between otherwise identical runs:

- `peak_gpu_memory_mb`, the CUDA allocator's peak, which can move with allocator or kernel choices;
- `driver_version`, which falls back to `"unknown"` if `nvidia-smi` fails during that run.

Both were identical in the runs above. If two SAM 2 predictions differ only in those fields, the
tracking is the same; compare `samples` before calling it a determinism failure. They stay in the
prediction even under `--omit-runtime`. They are hardware provenance that should travel with the
derived data, and removing them would add a second, run-dependent output shape to `track_gpu.py`
for a variation that has not been observed.

The opt-in tests `OPENBAR_VBT_E2E=1 ... -k EndToEnd` in `research/vbt-workflow/tests` check both
trackers with the real tools. The SAM 2 test also checks that CSRT and SAM 2 outputs coexist in one
folder. It finds the GPU venv through `OPENBAR_VBT_GPU_PYTHON`, or `research/gpu-tracking/.venv` by
default, and skips when the venv, torch/SAM 2 environment, a CUDA device or the SHA-verified
checkpoint is missing, as it is in CI.

**Do not commit** anything under `validation/private/`. Report aggregates only.

### Step 2, one-page session — `research/vbt-workflow/vbt_session.py` (#95)

The per-clip flow above takes about ten manual steps per clip. `vbt_session.py` turns a whole filmed
session into two commands and one page. It orchestrates the same tools (`label_package.py`,
`annotations.py seed`, `scale_reference.py`, `analyze_lift.py run`) and adds no measurement logic.
Run it with the research venv (`py` below), because the suggestions use OpenCV.

```bash
# 1. Copy new videos from an inbox folder, extract each seed frame, suggest, build the page.
py research/vbt-workflow/vbt_session.py ingest --inbox <folder> --session 2026-10-03
# 2. Open validation/private/vbt/sessions/2026-10-03/session.html. For each clip, confirm or drag the
#    plate centre and rim and the two stick markers, pick the lift, tick Confirm (or Skip), then
#    click "Download session CSV" (vbt-session-2026-10-03.csv).
# 3. Run everything; --watch <Downloads> instead of --csv starts when the CSV appears.
py research/vbt-workflow/vbt_session.py run --session 2026-10-03 --csv <downloaded.csv> \
  --plate-diameter-m 0.45 --stick-length-m 1.30 --preset vbt-sg-0.15s-v1 --tracker-policy <policy> \
  [--gpu-python research/gpu-tracking/.venv/Scripts/python.exe]
```

**Inbox and Google Drive.** `--inbox` is any local folder; it is only read. With Google Drive for
desktop, make a Drive folder such as `OpenBar inbox`, upload the session's clips to it from the
phone, and set that folder to *Available offline* (or use *Mirror files*), so the files are real
local bytes rather than placeholders. Pass its local path (for example `G:\My Drive\OpenBar inbox`).
Ingest copies each `.mp4`/`.mov`/`.m4v` byte for byte into `validation/private/vbt/media/` through a
staging name and checks the copy's SHA-256 against the source; a copy that differs (for example a
file still syncing) is refused. A different file with the same name already in the media folder is
never overwritten. There is no Drive API access and no upload step.

**Idempotent ingest.** A video whose SHA-256 is already registered in the personal manifest is
skipped (`--include-registered` adds it, reusing the registered copy). Re-running ingest keeps the
session's clips in their order and appends new inbox videos in file-name order; identical inputs give
a byte-identical `session.json` and `session.html`. The seed frame is frame 0, or the decoded frame
nearest `--at-s <file name or fixture id>=<seconds>`, which is remembered for later ingests. Frames
are decoded only through `label_package.py` (ADR-0006, PTS-checked).

**Registration waits for the lift.** Ingest never writes the personal manifest. `label_package.py`
needs a manifest to find the media, so ingest writes a session-local `frame-manifest.json` with only
the fields it reads (media path and SHA-256, raster size, rotation, `private_only`). It is not a
fixture manifest and is never passed to tracking or `analyze`. `run` registers each confirmed clip
with the lift chosen on the page, through the same `analyze_lift.register_video` as `register`.
Registering as `other` first and changing it later would rewrite a binding field of a manifest entry;
deferring registration means an entry is only ever written once, with the confirmed lift. If a video
is already registered with a different lift or plate diameter, `run` stops before writing anything and
names both values; registered exercises are never changed silently.

**Suggestions, confirmed by a human.** The owner approved this on 2026-10-03 (#95): the tool may
*propose* the plate circle and the two stick markers, but nothing is used until the clip is confirmed
on the page. Fully automatic seeding stays out of scope. `vbt_suggest.py` records each method id and
its parameters in `session.json`; each suggestion has an id and a heuristic confidence in [0, 1]
(not a probability):

- `plate-hough-edge-v1`: OpenCV Hough circles on a blurred, downscaled grey frame, limited to radii
  of 4–30 % of the shorter frame side and to circles that fit inside the frame. Each candidate is
  scored by edge support: the fraction of 360 rim samples within 2 px of a Canny edge whose smoothed
  gradient is radial. The best is refined by a coarse-to-fine local search (1.5 px tolerance) and
  proposed only if at least 40 % of the rim is supported.
- `stick-yellow-markers-v1`: the most elongated yellow (HSV) component, extended along its principal
  axis to the collinear yellow pieces between the markers. Markers are runs along the axis where both
  side bands next to the stick are dark, with yellow beyond both ends of the run (which rejects the
  dark stand at the foot). The lowest and highest markers are proposed: 0.20 m and 1.50 m in the
  owner's protocol, so `--stick-length-m 1.30`. The length is a `run` flag, not built in.

A failed suggestion leaves that item empty on the page; a tap or click places the next missing item
(plate centre, rim, lowest marker, highest marker). On the session's 8 real clips (2026-10-03), the
suggestions were checked against the owner's own confirmed clicks; see #95 for the aggregates.

**Page.** One self-contained, private HTML file (the frames are embedded PNG data URIs) under
`validation/private/vbt/sessions/<session>/`. Coordinates use the pixel-centre convention and the
ADR-0007 v1 point window, like `label_page.html`. Per clip: the frame with draggable plate centre and
rim and stick markers (zoom 1×/3×/6×, arrow keys nudge 1 px, Shift 0.25 px), the lift dropdown, and
Confirm and Skip toggles. Confirm is disabled until every item is present, the circle fits inside the
frame and a lift is chosen; any later edit withdraws the confirmation. The download is blocked until
every clip is confirmed or skipped and at least one is confirmed. Progress autosaves in the browser.

**Session CSV contract** (`openbar-vbt-session-v1`, one row per clip in page order, header exactly):

| Column | Meaning |
|---|---|
| `format` | `openbar-vbt-session-v1` |
| `session_id`, `page_id` | the session, and the hash of the page's clips, frames, suggestions and template |
| `clip_index`, `fixture_id`, `source_video_sha256`, `package_id`, `frame_index`, `timestamp_s`, `width_px`, `height_px` | the clip and its seed frame, exactly as in `session.json` |
| `decision` | `confirmed` or `skipped` |
| `exercise` | `snatch`, `clean`, `back_squat` or `other` (blank when skipped) |
| `plate_suggestion_id`, `stick_suggestion_id` | the suggestion's id, blank when there was none |
| `plate_center_x_px`, `plate_center_y_px`, `plate_radius_px` | confirmed plate circle, display pixels, 2 decimals |
| `stick_low_x_px`, `stick_low_y_px`, `stick_high_x_px`, `stick_high_y_px` | confirmed lowest and highest marker centres |
| `plate_center_status`, `plate_radius_status`, `stick_low_status`, `stick_high_status` | `accepted` (suggestion unchanged), `adjusted` (suggestion moved), or `manual` (no suggestion) |

A skipped row has blank exercise, geometry and status cells. `run` fails closed, before writing
anything, on: another session or a rebuilt page (`page_id`); a row whose video, package, frame or
size differs from the session; a missing decision; a missing lift or item; a status that is not true
of its values (`accepted` with changed values, `adjusted` with unchanged ones, `manual` when there
was a suggestion, or a wrong suggestion id); a point outside `[0,width) × [0,height)`; a circle
outside the frame; markers 2 px apart or less; non-finite numbers; a wrong header, cell count or
encoding; a wrong row count or order; and no confirmed clip. No JSON fixture type is introduced, so
no schema or `schema_check.py` entry is added.

**Seeds and scale.** For each confirmed clip, `run` writes the confirmed circle as a one-row label CSV
(`seeds/<id>.session-label.csv`) and builds the seed with `annotations.build_seed`, the code path of
`annotations.py seed`, against the personal manifest. The seed schema is unchanged; the notes
append the page provenance, for example `... plate centre accepted, plate radius adjusted;
suggestion plate-hough-edge-v1:<12 hex> (method plate-hough-edge-v1, confidence 0.78).` The label
row's `quality` is `high`: the per-clip Confirm stands in for the label page's quality step, and
quality never reaches the seed. The stick markers become a `scale_reference.py` click CSV
(`scale/<id>.scale-reference.csv`, point A = lowest marker, point B = highest), bound to the same
seed-frame package, whose `reference-config.json` records `--stick-length-m`.

**Tracker policies.** `--tracker-policy` is required, with no default, and is recorded in the
session record:

| Policy | snatch | clean | back_squat | other |
|---|---|---|---|---|
| `csrt-all-v1` (machines without a CUDA GPU) | csrt | csrt | csrt | csrt |
| `sam2-all-v1` | sam2.1-bplus-circle | sam2.1-bplus-circle | sam2.1-bplus-circle | sam2.1-bplus-circle |
| `sam2-olympic-csrt-squat-v1` | sam2.1-bplus-circle | sam2.1-bplus-circle | csrt | csrt |

When the confirmed lifts need SAM 2, `--gpu-python` is required and the CUDA check from
`analyze_lift.py` runs before anything is written; without CUDA, `run` stops and suggests
`csrt-all-v1`. `--gpu-python` with a policy that never uses SAM 2 is refused.

**Outputs**, all under `validation/private/vbt/sessions/<session>/` (sessions hold frames of private
videos, so `--sessions-root` must be under `validation/private/vbt/`):

- `session.json`, `session.html`, `frame-manifest.json`, `packages/<id>/` (from ingest);
- `session-input.csv`: the exact CSV bytes that were run;
- `seeds/<id>.manual-target-seed-v1.json` and the label CSV it was built from;
- `scale/<id>.scale-reference.csv`, and `scale-report/` (`scale-reference-v1.json`,
  `SCALE_REFERENCE_REPORT.md`) from `scale_reference.py report`;
- `analyses/`: per clip, `analyze_lift.py run`'s prediction, analysis-v1, run record (and SAM 2
  geometry sidecar), unchanged;
- `report.html`: one self-contained page (inline SVG, PNG data URIs). Per clip: lift, seed time,
  tracker, tracked and lost counts, plate and stick scales and their ratio, the vertical-velocity plot,
  and tracking-check crops of the frames 2 before, at and 2 after the peak upward velocity (decoded
  through `label_package.py`), with the tracked centre and seed radius drawn on top. A per-rep table
  shows mean and peak concentric velocity at plate scale and stick-corrected (× stick/plate ratio).
  It is a labelled, non-authoritative **preview**: a rep is a run of positive `vy_mps` rising at least
  0.10 m; authoritative segmentation stays with the recommender. Then the scale-ratio table;
- `session-record.json` (`openbar-research-vbt-session-record`, version 1): the CSV, session-state and
  manifest paths and hashes, the configuration and tracker policy, per clip the decision, lift,
  tracker, item statuses, suggestion ids, video, seed, label CSV, click CSV and analyze_lift run-record
  hashes and the `analyze_lift.py` command, the `scale_reference.py report` command and output hashes,
  the report hash, and the OpenBar git state. It has no wall-clock time.

Existing outputs are refused without `--force`. The session record is removed first and written last,
so **a session without `session-record.json` is incomplete**; re-run with `--force`. Re-running with
`--force` on the same inputs gives byte-identical seeds, analyses (CSRT), reports and session record
(the recorded commands leave out `--force`). `--watch <folder>` polls every 2 s for
`vbt-session-<session>.csv` (or a browser's `vbt-session-<session> (1).csv`), reads it once its size is
stable, and stops after `--watch-timeout-s` (default 900 s, at most 6 h) with a message to download
the CSV or pass `--csv`. Two candidates are refused as ambiguous.

Tests: `research/vbt-workflow/tests/test_vbt_session.py`, `test_session_contract.py`,
`test_session_report.py`, `test_session_page.py` (node-gated) and `test_vbt_suggest.py` (OpenCV-gated)
run with fakes; `OPENBAR_VBT_E2E=1` adds `test_vbt_session_e2e.py`, which runs ingest, a CSV written by
the test, `run` and a byte-identical `--force` re-run on the public synthetic fixture.

### Step 3 — recommender import and report (Szczepanov/adaptive-training-recommender#981, #982)

- **One analysis per lift.** Import exactly one `analysis-v1` per lift into the recommender (3a). An
  output folder can hold `<id>.opencv-csrt.analysis-v1.json`, `<id>.sam2.1-bplus-circle.analysis-v1.json`
  and a legacy workflow-v2 `<id>.analysis-v1.json` for the same video. Import the file from the
  tracker that the import policy names (`analysis.provenance.tracker` and the run record's
  `configuration.tracker` say which tracker that is). Never import two trackers' analyses of one lift
  as separate trials, and don't import a legacy unnamed file when a tracker-named one exists.
  Comparing trackers belongs in the 3b report or step 4, not in the training history.
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
