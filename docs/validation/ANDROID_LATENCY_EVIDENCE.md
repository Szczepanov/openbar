# Android import-to-results latency evidence

Issue: #179 (child of #80). **Status: Accepted target parameters (owner decision 2026-10-18); reference
phone benchmark evidence recorded (2026-10-19); no qualifying measurement exists yet.**

This document defines what "results within seconds" has to mean before anyone measures it: which
phone, which clips, which clock start and stop, which statistics, and which evidence is allowed to
produce a pass or fail. It does not choose the numbers. Every value that needs a product decision is
listed in [Owner decisions](#owner-decisions-required) and recorded by the owner here.

It extends, and does not replace, the [phone runtime benchmark](PHONE_RUNTIME_BENCHMARK.md)
and [ADR-0009](../adr/0009-m0-phone-runtime-benchmark-boundary.md).

## Relationship to the existing M0 gate

Two questions are measured from the same runs and reported separately:

| | M0 gate (ADR-0009, unchanged) | Product latency target (this document) |
| --- | --- | --- |
| Question | Is offline analysis faster than the video is long? | Does the user see results within the approved seconds budget? |
| Quantity | `median(analyze wall / selected media span) < 1.0` | Time-to-first-results and time-to-complete in seconds (median < 5.0 s, max < 8.0 s) |
| Status today | NOT MEASURABLE YET | Target declared (2026-10-18); NOT MEASURABLE YET |
| Owner | ADR-0008 / ADR-0009 | Owner decision recorded below |

Passing one says nothing about the other. A 40 s clip analysed in 30 s passes the M0 ratio and may
still miss a "within N seconds" target; a 5 s clip analysed in 6 s fails the M0 ratio and may still
meet one. The harness never derives one status from the other and never revises the M0 target.

## Owner decisions required

These values are product decisions, not engineering defaults. Nothing below may be filled in from
desktop timings, from the synthetic benchmark, or from "what the harness happens to achieve".
Recorded by the owner on 2026-10-18:

| Parameter | Value | What the decision settles |
| --- | --- | --- |
| Target phone(s) | Xiaomi 14T Pro (`rothko`, model `2407FPN8EG`, Android 16 arm64-v8a) for both M0 reference and initial product reference (decided 2026-10-18). Lower-tier phone: deferred | The 14T Pro is a high-end phone, so a pass on it supports a reference-phone claim only and no minimum-spec claim. |
| Clip duration(s) | 5–20 s single-set clips (decided 2026-10-18) | The selected lift duration the budget applies to. |
| Resolution / frame rate / codec | 1080p, 30 fps and 60 fps, H.264 and HEVC, CFR and VFR per ADR-0006 (decided 2026-10-18) | Per-case workload values; 4K is outside the initial latency budget. |
| Display rotation | 0°, 90°, 180°, 270° display-matrix rotation (decided 2026-10-18) | Rotated portrait and landscape phone footage are both in the workload. |
| Cold / warm definition | Termux harness: first `analyze` repeat vs repeats 2..N (with cache limitation stated); app-level cold start deferred to M1 app path | See [Cold and warm](#cold-and-warm). |
| Repeat count | 5 repeats per case (decided 2026-10-18) | With `n = 5`, `small_n` is flagged (`n < 20`) and the tail threshold is expressed on the maximum rather than p95. |
| Time-to-first-results threshold | Median < 5.0 s and maximum < 8.0 s across 5 repeats (decided 2026-10-18) | Applies to both median and maximum over the 5 repeats. |
| Time-to-complete threshold | Median < 5.0 s and maximum < 8.0 s across 5 repeats (decided 2026-10-18) | Identical to time-to-first-results while whole-clip analysis is used. |
| Sustained-use condition | Record `warm_slowdown_ratio` across the 5 repeats; separate thermal-soak gate deferred (decided 2026-10-18) | Reported for review without an automatic thermal-soak pass/fail threshold. |
| Memory limit | Record peak process-tree RSS (must be enumerable); no fixed MiB ceiling yet (decided 2026-10-18) | Any incomplete RSS enumeration keeps the run NOT MEASURABLE YET. |

A threshold must be declared **before** the measurement it judges, together with the clip set and the
frozen candidate. A threshold chosen after seeing results is a revision, not a target, and is
reported as such (ADR-0008).

## Metric definitions

All durations use a monotonic clock. Wall-clock timestamps, frame index and nominal FPS are never
used to compute them. Media time stays authoritative from decoder timestamps (ADR-0005/0006).

**T0 — import request.** The moment the app, or for CLI-level evidence the harness, is given a
selected clip to analyse. For the Termux harness T0 is the start of the `analyze` process. For the
future app it is the user's confirmed import action, after the system file picker and permission
dialogs have returned. Picker and permission time is excluded.

**Time-to-first-results.** T0 until the first complete, usable result is available to the user: the
per-rep metrics ([REP_PHASE_METRICS.md](REP_PHASE_METRICS.md)) and the bar-path overlay
([VIDEO_OVERLAY_CONTRACT.md](VIDEO_OVERLAY_CONTRACT.md)) derived from one `Analysis`, with playback
ready.

**Time-to-complete.** T0 until all work for that import is finished and the result is stored.

For the current M0 boundary these are the same instant: the tracker consumes the whole selected clip
and one `Analysis` is produced at the end, so there is no partial result to show earlier. They
become different quantities only if a later design streams partial results, and that needs its own
decision (ADR-0009 already requires a dedicated issue and ADR before a streaming frame boundary).
Until then the harness reports a single analyze wall time per run, which stands for both.

**Excluded from both metrics**, per the overlay contract and the automatic-selection plan:

- time the user spends confirming or correcting the plate selection or diameter;
- annotated video export (a separate background job);
- file picker and permission prompts;
- any network time (processing is local and offline).

### Stages

A latency is only useful with its breakdown. Stage durations are summed against the total, and any
difference is reported as **unattributed**, never silently folded into a stage.

| Stage | Observable today | Source |
| --- | --- | --- |
| App import / picker hand-off | no | needs the app path (not built) |
| `setup`: manifest, seed, plate-diameter check, tracker construction | yes | `analyze --timings-output` |
| `probe`: `ffprobe`, tool-version checks, source hash | yes, merged | `analyze --timings-output` |
| `decode`: `ffmpeg` | yes | `analyze --timings-output` |
| `recording_support`: conditions and envelope assessment | yes | `analyze --timings-output` |
| `tracking`: tracker initialisation and per-frame tracking | yes, merged | `analyze --timings-output` |
| `calibration` | yes | `analyze --timings-output` |
| `filtering` | yes | `analyze --timings-output` |
| `kinematics` | yes | `analyze --timings-output` |
| `output_write`: serialisation and write | yes | `analyze --timings-output` |
| Per-rep metrics ([REP_PHASE_METRICS.md](REP_PHASE_METRICS.md)) | no | `analyze` computes none; `kinematics` is not this stage |
| Overlay path preparation and first frame | no | needs the app path (not built) |

`probe` is one stage because ffprobe, the tool-version checks and the source hash happen in one
media call; separating them would change `apps/openbar-cli/src/media/`. `tracking` is one stage
because the `Tracker` API has a single `track` call, so initialisation cannot be split from
per-frame cost without changing `openbar-tracking`. Seed validation, filter resolution and
`Analysis::try_new` are not stages; their time is part of the unattributed remainder. The sidecar
format and exact boundaries are in [CLI_PIPELINE.md](CLI_PIPELINE.md).

Stages that are not observable are listed in the artifact as missing, so a CLI-only run can never be
read as end-to-end evidence.

Timings are written to a sidecar file and to the harness artifact. They are never added to the
canonical `Analysis` JSON, the recording-support JSON, or any fixture (AGENTS.md determinism rule).

## Statistics

For each case and condition the report gives `n`, median, p95, maximum, and the number of failed
runs. The statistics are computed over completed runs only. A failed run exits early, so averaging
its short elapsed time in would make the numbers look better; instead, when any run failed the
statistics are withheld (`null`, with a reason) and the failed runs' durations and exit codes are
listed separately. A failed run is therefore never averaged away and never silently dropped. The
harness has no run timeout today, so a hung run blocks it; a timeout policy is a decision for the
owner.

Percentiles use the deterministic nearest-rank definition already used by
[BENCHMARK.md](BENCHMARK.md): sort ascending, rank = ceil(p · n), one-indexed.

With few repeats the tail statistic degenerates: for `n < 20`, p95 equals the maximum. The report
states `n` next to every percentile, flags `small_n` below 20, and does not describe a small-`n` p95
as a tail estimate. If the
owner wants a p95 threshold, the repeat count above must be large enough for it to mean something, or
the threshold should be expressed on the maximum.

Repeats of one clip measure run-to-run variation of one input. They are not independent clips and do
not widen the evidence about other recordings; the report keeps them separate from the number of
distinct clips.

### Cold and warm

The harness can define only what it controls. In the Termux harness:

- **cold**: the first `analyze` repeat of a case in one harness invocation;
- **warm**: `analyze` repeats two and later.

This is **not** a verified cold start. The harness first runs `tracker-run` stage-diagnostic passes
over the same media, so the "cold" repeat always follows full reads of that file; the operating-system
file cache, CPU frequency state and thermal state are not controlled either. The artifact states this
limitation next to the numbers. A true cold measurement (fresh process after
the cache is dropped or after a reboot, and the app's own cold start) needs a procedure the owner
approves and, for the app, the app itself.

## Memory and thermal

- **Memory:** peak process-tree RSS sampled while the run executes, as in the existing harness. If
  it cannot be read, the run is unmeasurable for memory; the deterministic frame-buffer estimate is
  reported alongside, never substituted.
- **Thermal:** battery and maximum readable thermal-zone temperature before and after, where Android
  exposes them. The harness does not infer throttling from temperatures. Slowdown across back-to-back
  repeats is reported as `warm_slowdown_ratio`, the wall time of the last warm repeat divided by the
  first warm repeat. It needs at least three repeats and is `null` otherwise or when either run
  failed. A reviewer decides whether it reflects throttling.
- A sustained-use condition (back-to-back runs) is only part of the target if the owner adds it.

## Determinism

Timing is environment evidence and must not change measurement. For every case the harness compares
the bytes of the canonical analysis JSON and of the recording-support JSON across all repeats and
records whether they are identical (with a SHA-256 of the first). A difference is recorded in the
artifact, listed in `diagnostics_warnings` and printed to stderr rather than hidden, and a run that
produced no output is `null`, not "identical".
Enabling the stage-timing sidecar must not change those bytes; a CLI test checks it.

## Evidence classes and result states

| Class | Conditions | Allowed statement |
| --- | --- | --- |
| Development diagnostic | Any device or runtime; synthetic or unreviewed clips; candidate not frozen; no approved target | **NON-QUALIFYING DIAGNOSTIC**. Numbers are reported with their conditions. Never PASS or FAIL. |
| Pipeline-level qualifying | Designated phone; real clips inside the supported envelope (#53); candidate frozen by #57; thresholds declared and approved before the run; all repeats complete; RSS measurable | **PASS**, **FAIL** or **NOT MEASURABLE** for the CLI pipeline portion only. |
| App-level qualifying | Everything above, measured through the app import and overlay path on the reference phone | **PASS**, **FAIL** or **NOT MEASURABLE** for the product target. |

A CLI timing is never reported as app-level completion. A run is **NOT MEASURABLE** when any required
condition is missing (device, candidate, envelope, target, memory reading, a stage). It is **FAIL**
when a qualifying run does not complete or misses a declared threshold.

The harness artifact always states `NON-QUALIFYING DIAGNOSTIC` for the product target at this stage,
lists the missing stages, and leaves the existing ADR-0009 `gate` object untouched.

## Harness audit

Audit of `validation/tools/phone_runtime_benchmark.py` before this change:

| Requirement | Before | Now |
| --- | --- | --- |
| Reference device identification | Yes (`environment`) | Unchanged |
| End-to-end analyze wall time, repeated | Yes | Unchanged |
| Median and maximum ratio | Yes | Unchanged; p95, run counts and failed-run durations added under `latency_diagnostics` |
| Cold versus warm | No | First `analyze` repeat versus later repeats, with the limitation stated |
| Stage timing | Only `tracker-run` decode and track; ffprobe folded into decode | Optional `--stage-timings` using the new `analyze --timings-output` sidecar |
| Tail statistics with failures | Any failed run nulled every median and ratio; failed durations were not kept | Statistics over completed runs, withheld when any run failed; failed durations and exit codes listed |
| Peak RSS | Parent process only on kernels without `task/<pid>/children` (first phone run, 2026-10-14: 7.2 MiB, parent only, discarded as invalid) | Process tree found from `/proc` stat parent PIDs; an unenumerable tree gives no RSS (`process_tree_rss_complete: false`) and the gate stays NOT MEASURABLE YET |
| Thermal and battery | Before/after only | Unchanged; slowdown across repeats reported |
| Output byte equality across repeats | No (outputs written, never compared) | Compared and recorded |
| Product target and qualifying status | No | Explicit `NON-QUALIFYING DIAGNOSTIC` block; no PASS/FAIL |
| App import, per-rep metrics and overlay stages | No | Listed as missing |
| Candidate under test | `template-sad-v1` probe only | Unchanged; #176 tracker has no CLI wiring |

Not changed: the ADR-0009 gate, `summarize_repeats`, the frozen probe configuration, canonical
analysis output, schemas and fixtures.

The artifact keeps `phone-runtime-benchmark-v1`. The new top-level and per-case keys are additive,
nothing validates or consumes this artifact (no schema, no reader outside the tool and its tests),
and bumping the version would imply changed gate semantics. This is a deliberate decision and is
flagged for the owner in the pull request. When `--stage-timings` is used the sidecar write is inside
each gated `wall_s`; the artifact records whether it was requested.

## Retained evidence for a run

Keep with each reported run, outside the canonical measurement files:

- the exact commands and flags, the evaluated commit, and the harness artifact;
- the device model, codename, ABI, Android release/SDK, memory, and tool versions;
- the clip identities (manifest ids), duration, resolution, frame rate, codec, rotation;
- every run's wall time including failures, plus the cold/warm labelling;
- stage timings and their unattributed remainder, or the reason they were not collected;
- peak RSS, frame-buffer estimate, battery and thermal readings or their absence;
- the byte-equality result and the analysis hash;
- the list of missing stages and the evidence class under which the run is reported.

Review private-clip artifacts before committing; the harness emits no frames, coordinates or local
paths.

## Blockers and sequencing

| Item | Effect |
| --- | --- |
| #57 (held-out tracker/filter selection) | Open. Without a frozen candidate, no run is qualifying and `production_candidate_frozen` stays `false`. |
| #53 supported recording envelope | Representative clips must be inside it for a qualifying case. |
| #176 tracker | Implemented as a library only; no `analyze` or `tracker-run` wiring, so it cannot yet be timed on a phone. Wiring and real-video evaluation are separate work. |
| Owner decisions above | No numeric threshold may be applied until recorded. |
| Android app import and overlay path | Not built. App-level evidence waits for an approved mobile implementation. |
| Reference phone run | Unit identity confirmed and 5-repeat benchmark run executed on 2026-10-19 on the physical Xiaomi 14T Pro (see below). |

Next steps once unblocked: freeze the production candidate and clip set; measure through the
production app import and overlay path once built; report the evidence class honestly.

## Reference phone benchmark evidence (2026-10-19)

**Status:** NON-QUALIFYING DIAGNOSTIC (pipeline-level diagnostic on physical reference phone; production candidate not frozen by #57; app import, per-rep metrics and overlay stages not measured).

- **Evaluated commit:** `46e865954258d64c6446affda290c9e546a1c51c`
- **Device & Environment (`reference_match: true`):**
  - Model: Xiaomi 14T Pro (`2407FPN8EG`, device codename `rothko`)
  - SoC: MediaTek Dimensity 9300+ (4x Cortex-X4 @ 3.4 GHz + 4x Cortex-A720 @ 2.0 GHz)
  - OS / ABI: Android 16 (SDK 36, `arm64-v8a`)
  - Total RAM: 11,190.9 MiB (~12 GB)
  - Tool versions: Termux 0.118.3, `openbar-cli` (release profile, rustc 1.99.0), FFmpeg 8.1.3, Python 3.14.6
- **Frozen pipeline configuration under test:**
  - Tracker: `template-sad-v1@1`
  - Filter: `raw-identity@1`
  - Kinematics: `backward-difference@1` (max gap 0.2 s, min confidence 0.0)
  - Decode boundary: ADR-0006 external ffmpeg/ffprobe process
- **Harness command:**
  ```bash
  python validation/tools/phone_runtime_benchmark.py \
    --suite validation/private/phone-runtime-suite-v1.json \
    --output target/phone-runtime-benchmark-20261019.json \
    --repeats 5 \
    --stage-timings
  ```

### Case results (5 repeats per case)

| Case ID | Fixture | Media span | Cold wall | Warm median | Max wall | Median ratio | Peak RSS | Warm slowdown | Determinism |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `synthetic-clean-side-12-smoke` | `synthetic-clean-side-12` | 0.917 s | 0.274 s | 0.275 s | 0.285 s | 0.299x | 49.6 MiB | 1.029 | Identical (`49cf10f1...`) |
| `self-back-squat-side-002-lift` | `self-back-squat-side-002` (4.8–10.8 s) | 5.966 s | 15.284 s | 15.330 s | 15.358 s | 2.566x | 249.7 MiB | 1.0028 | Identical (`348ba016...`) |
| `self-back-squat-side-002-full` | `self-back-squat-side-002` (0.0–15.0 s) | 15.001 s | 22.093 s | 23.097 s | 23.190 s | 1.539x | 482.6 MiB | 0.9964 | Identical (`323fffbd...`) |

### Stage timings breakdown (median seconds per stage)

| Stage | `synthetic-smoke` (0.92 s) | `back-squat-lift` (5.97 s) | `back-squat-full` (15.00 s) |
| --- | --- | --- | --- |
| `setup` | 0.084 ms | 0.285 ms | 0.326 ms |
| `probe` (ffprobe + media hash) | 0.122 s | 1.592 s | 1.657 s |
| `decode` (external ffmpeg process) | 0.072 s | 3.688 s | 3.598 s |
| `tracking` (`template-sad-v1` CPU) | 0.063 s | 9.992 s | 17.705 s |
| `calibration` | 0.007 ms | 0.011 ms | 0.020 ms |
| `filtering` (`raw-identity`) | 0.007 ms | 0.004 ms | 0.011 ms |
| `kinematics` (`backward-difference`) | 0.013 ms | 0.020 ms | 0.041 ms |
| `recording_support` | 0.016 ms | 0.012 ms | 0.014 ms |
| `output_write` | 1.198 ms | 1.908 ms | 2.090 ms |
| `unattributed` remainder | 0.133 ms | 0.194 ms | 0.157 ms |
| **Total Analyze Wall Time** | **0.274 s** | **15.308 s** | **23.087 s** |

### Key Findings & Architectural Bottlenecks

1. **Decoder & Probe Overhead:**
   Spawning external subprocesses per ADR-0006 consumes **5.28 s** on the 6.0 s lift (`1.59 s` probe + `3.69 s` ffmpeg pipe decode). Spawning overhead and raw-pipe IPC alone exceed the `< 5.0 s` product median budget before any tracking starts.
2. **Tracker CPU Cost:**
   The development template probe (`template-sad-v1`, pure CPU template SAD) takes **9.99 s** for 179 frames of 1080p (~55 ms/frame). Combined with probe/decode, total wall time reaches **15.3 s**.
3. **Implications for the Product Latency Budget (< 5.0 s median, < 8.0 s max):**
   Meeting the owner-approved latency target requires two architectural steps:
   - **Hardware-accelerated in-process video decoding** (e.g. Android `AMediaCodec` / NDK `MediaCodec`), eliminating the ~5.3 s external ffmpeg/ffprobe pipeline latency.
   - **Neural detector & tracker execution** (static-shape `D-FINE-S` student model at ~28 ms GPU / ~140 ms CPU on frame 0, combined with EdgeTAM/CSRT).
4. **Thermal Stability & Memory:**
   - Over 5 back-to-back repeats, `warm_slowdown_ratio` was **1.0028** on the lift segment and **0.9964** on the full clip. Zero thermal throttling degradation was observed on the Dimensity 9300+.
   - Peak RSS was strictly bounded: **249.7 MiB** for the 6.0 s lift, **482.6 MiB** for the full 15.0 s 1080p clip, with complete process-tree enumeration (`process_tree_rss_complete: true`).
5. **Determinism:**
   Analysis bytes across all 5 repeated runs were **100% byte-identical** (`analysis_bytes_identical: true`).

## In-process MediaCodec prototype (#258, 2026-10-19)

**Research evidence only.** This is not gate evidence, and it does not change ADR-0006, the canonical
`analyze` path or this document's harness. The prototype is in
[`research/android-decode/`](../../research/android-decode/README.md) and the plan is in
[`ANDROID_INPROCESS_DECODE_PLAN.md`](../plans/ANDROID_INPROCESS_DECODE_PLAN.md). It decodes in-process with
`AMediaExtractor` + `AMediaCodec` (ByteBuffer output). It then copies the luma plane, rotates it to display
orientation and expands limited-range values to full range, giving the same packed raster as the FFmpeg path.

### Environment

| | |
| --- | --- |
| Device | Xiaomi 14T Pro (`2407FPN8EG`, `rothko`), Android 16 (SDK 36) |
| Decoder | `c2.mtk.avc.decoder` (MediaTek hardware H.264) |
| Output format | `COLOR_FormatYUV420Flexible`, with the luma layout read from `image-data` (`MediaImage2`) |
| Toolchain | Termux, rustc 1.99.0, FFmpeg 8.1.3 (reference only), Python 3.14.6 |
| Run | `research/android-decode/run_device_suite.sh` over adb-forwarded SSH; 5 timing repeats per case without `--dump-gray`, then one parity run |
| Codec hints | `priority=1`, `operating-rate=32767` |
| Range | `--assume-range limited`. The decoder reported `color-range=2` (limited) for the squat clip, so the flag was not needed there. The synthetic clip reports no range; FFmpeg treats its `yuv420p` as limited, so the run uses an assumed range, recorded in the report. |

The cases match `phone-runtime-suite-v1.json`:
- the synthetic smoke clip;
- `self-back-squat-side-002`, 1280×720 coded with 90° rotation, as the lift window (4.8–10.8 s);
- the same clip in full.

### Latency (median of 5, seconds)

| Stage | `synthetic-smoke` | `back-squat-lift` | `back-squat-full` |
| --- | --- | --- | --- |
| extractor open | 0.057 | 0.061 | 0.058 |
| codec configure | 0.105 | 0.108 | 0.118 |
| first in-window frame (from start) | 0.248 | 0.364 | 0.369 |
| decode loop (includes repack) | 0.114 | 1.788 | 4.892 |
| of which luma copy | 0.001 | 0.032 | 0.089 |
| of which copy + rotate + range expansion | 0.005 | 0.733 | 2.026 |
| **total** (min–max) | **0.341** (0.329–0.354) | **2.021** (1.803–2.117) | **5.132** (4.849–5.556) |
| FFmpeg baseline `probe` + `decode` (above) | 0.194 | 5.280 | 5.255 |

On the lift window, 23 pre-roll frames were decoded after the seek to the previous sync frame and then
discarded; 180 frames were emitted.

Two exploratory lift runs were pinned to the big cores with `taskset -c 4-7`. They took 1.23–1.50 s, but
they are not part of the measured set.

### Parity against the ADR-0006 FFmpeg path

| Check | synthetic | lift | full |
| --- | --- | --- | --- |
| Frame count | 12 = 12 | 180 = 180 | 450 = 450 |
| Max PTS difference | 0.67 µs | 0.89 µs | 0.89 µs |
| Display dimensions | equal | equal (720×1280) | equal |
| Luma against `-pix_fmt gray`: max / mean abs diff | 0 / 0 | **19 / 0.68** | **19 / 0.68** |
| Diagnostic: luma against FFmpeg's raw Y plane, expanded by the ADR-0006 rule | bit-exact 12/12 | **bit-exact 180/180** | **bit-exact 450/450** |
| Preregistered gate (max-abs-diff ≤ 2 against `gray`) | pass | **fail** | **fail** |

### Findings

1. **The hardware decoder is bit-exact with FFmpeg's decoder.** Every luma sample matches FFmpeg's raw Y plane
   after the documented `(Y-16)·255/219` expansion. Timestamps match to within µs truncation, and frame
   counts match with no drops.
2. **The parity gate fails because of FFmpeg's `-pix_fmt gray` conversion, not because of the decoder.**
   - On this limited-range clip, FFmpeg 8.1.3 (Termux) maps the same raw Y code to a spread of gray values: code 139 becomes 133–149.
   - Desktop FFmpeg 9.0.2 (gyan.dev) also deviates from per-pixel expansion (max 13, with or without autorotate), and it does not agree with 8.1.3.
   - So ADR-0006's statement that limited-range "codes 16–235 map to 0–255" does not describe current FFmpeg output exactly, and canonical luma differs between FFmpeg versions.
   - **Owner decision needed:** this touches the ADR-0006 contract and determinism across FFmpeg builds. The prototype was not changed to imitate FFmpeg.
3. **Latency target missed.** The < 0.50 s target for the lift window is not met: the median is 2.02 s against 5.28 s for FFmpeg probe + decode, about 2.6× faster. On the full clip the prototype (5.13 s) is no faster than FFmpeg (5.26 s).
4. **Where the time goes:**
   - About 0.73 s (lift) and 2.0 s (full) is the materialised rotation and expansion, roughly 4 ms per 0.9 MP frame on unpinned cores. ADR-0005 allows a rotated `GrayscaleImage` view, which would make the rotation zero-copy.
   - The remaining decode loop runs at about 5–6 ms per frame, which points to decoder throughput or pacing in ByteBuffer mode. Whether the `operating-rate` hint was honoured was not verified.
   - Timings vary with CPU placement: pinning to big cores cut the lift total to 1.2–1.5 s.
5. **Format refusal and the layout path used.**
   - The decoder offers only flexible YUV in ByteBuffer mode, which the first version refused.
   - Accepting it through `image-data` reads an exact layout rather than guessing. It needed no `AImageReader` path.
   - The NDK `AMEDIAFORMAT_KEY_*` symbols are missing from Termux's `libmediandk` stub, so the prototype uses the public key strings instead.

## Non-goals

- Choosing numeric latency thresholds, supported phones or the clip set.
- Building the Android app, selecting a decoder, packaging, or a streaming frame boundary.
- Changing the ADR-0009 gate, its artifact semantics, or the frozen probe configuration.
- Using desktop or synthetic timings as phone evidence.
- Any change to the canonical `Analysis`, schemas, golden file or fixtures.
