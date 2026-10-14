# Phone runtime benchmark

Issue: #59

## Purpose

This harness measures the frozen OpenBar offline analysis pipeline on the M0 reference phone without
turning the experiment into a Flutter or production Android architecture project.

Reference target and runtime are defined by
[ADR-0009](../adr/0009-m0-phone-runtime-benchmark-boundary.md):

- physical **Xiaomi 14T Pro** (model 2407FPN8EG, device codename rothko, Android 16; identity read
  from the unit, see ADR-0009 Amendment 1);
- Android arm64 runtime;
- native release-built openbar-cli under Termux;
- external ffmpeg/ffprobe under the existing ADR-0006 M0 decode boundary.

Desktop and non-reference-device results are diagnostics only. They cannot satisfy the M0
offline-processing gate.

## What is measured

For each configured case the harness records:

- exact selected media span from decoder provenance;
- repeated end-to-end analyze wall time;
- median and maximum processing-time / video-duration ratio;
- peak observed RSS for the OpenBar process tree;
- deterministic grayscale frame-buffer size implied by selected decoded frames;
- median decoder and tracker wall time from tracker-run;
- decoder/video and tracker/video ratios;
- battery capacity and temperature before/after where readable;
- maximum readable thermal-zone temperature before/after where readable;
- phone, Android, memory and toolchain provenance.

Runtime evidence deliberately does not enter canonical analysis JSON. Identical video/configuration
must continue to produce the same deterministic measurement artifact regardless of how long a run
takes.

## Gate semantics

The provisional gate remains:

> offline processing faster than video duration on reference hardware.

A case explicitly marked representative for the gate is eligible only when it is:

- executed on the designated Xiaomi 14T Pro reference runtime;
- non-synthetic;
- inside the evidence-backed supported recording envelope;
- run with the frozen pipeline configuration;
- complete for every repeated analyze run;
- measurable for process-tree RSS.

At least two representative cases are required because #59 asks for representative clips, plural.

For every eligible case:

    processing_ratio = end_to_end_analyze_wall_s / selected_media_span_s

Default repeat count is three. The median ratio is used for the gate.

- PASS: every representative case has median processing_ratio < 1.0.
- FAIL: a representative run fails, or any representative median is >= 1.0.
- NOT MEASURABLE YET: the phone/runtime, supported real clips, or required runtime evidence is
  missing.

A target revision is not automated by the harness. REVISED requires separate evidence and rationale
under ADR-0008.

## Current dependency on #57 and #53

The initial executable harness uses the current #14 integration probe:

- template-sad-v1@1;
- raw-identity@1;
- backward-difference@1;
- kinematics max gap 0.2 s;
- minimum kinematics confidence 0.0.

That is sufficient to exercise and profile the phone boundary, but it is not a production tracker
selection.

Final gate evidence must use the candidate frozen by #57 and representative real clips classified
supported by #53. If #57 chooses a different tracker/filter, update the harness constants and this
document before collecting final #59 evidence.

## Phone setup

One reproducible setup is Termux on the Xiaomi 14T Pro with Rust, Python and FFmpeg available on PATH.

Example package setup:

    pkg update
    pkg install git rust python ffmpeg

From the repository checkout:

    cargo build --locked --release -p openbar-cli
    ffmpeg -version
    ffprobe -version
    rustc --version
    python --version

Do not use debug cargo run timings for the gate. The harness executes target/release/openbar-cli
directly.

The reference unit reported manufacturer Xiaomi, model 2407FPN8EG, device rothko, ABI arm64-v8a,
Android 16 (SDK 36). To re-check the identity the harness compares against (`REFERENCE_MODEL`,
`REFERENCE_DEVICE`, `REFERENCE_ABI` in the tool):

    for p in ro.product.manufacturer ro.product.model ro.product.device ro.product.cpu.abi \
             ro.build.version.release ro.build.version.sdk; do echo "$p=$(getprop $p)"; done

If `ro.product.device` is not `rothko`, correct the constant and ADR-0009 Amendment 1 to the
observed value in the same change. Install Termux from F-Droid or
GitHub releases; the Play Store build is outdated.

## Benchmark suite

The suite is intentionally small and local. Private media remains under validation/private/ and is
not committed merely to run the benchmark.

Example validation/private/phone-runtime-suite-v1.json:

    {
      "schema_version": 1,
      "production_candidate_frozen": false,
      "cases": [
        {
          "id": "held-out-clean-reference",
          "manifest": "manifest.json",
          "fixture_id": "held-out-clean-reference",
          "seed": "seeds/held-out-clean-reference.manual-target-seed-v1.json",
          "plate_diameter_m": 0.45,
          "representative_for_gate": true
        },
        {
          "id": "held-out-squat-reference",
          "manifest": "manifest.json",
          "fixture_id": "held-out-squat-reference",
          "seed": "seeds/held-out-squat-reference.manual-target-seed-v1.json",
          "plate_diameter_m": 0.45,
          "representative_for_gate": true,
          "start_s": 2.0,
          "end_s": 14.0
        }
      ]
    }

Suite manifest and seed paths are resolved relative to the suite file.

Keep production_candidate_frozen false while the suite uses only the current development
integration probe. Change it to true only after #57 has frozen the candidate and the harness
constants have been checked against that decision. A false value forces the gate to remain
NOT MEASURABLE YET even on the reference phone.

The fixture manifest remains the source of media identity, source kind, recording conditions and
purpose. The harness does not duplicate that metadata.

## Run

    python validation/tools/phone_runtime_benchmark.py \
      --suite validation/private/phone-runtime-suite-v1.json \
      --output target/phone-runtime-v1.json

Optional:

    --binary target/release/openbar-cli
    --repeats 5

The benchmark exits non-zero for malformed configuration or when it cannot obtain the timing needed
to evaluate the configured pipeline. A completed benchmark may still report NOT MEASURABLE YET when
the run is not gate-eligible.

## Output contract

The JSON artifact records:

- benchmark version and evaluated Git commit;
- frozen pipeline configuration;
- expected and observed reference runtime;
- per-case timing/memory/stage diagnostics;
- battery/thermal observations where available;
- explicit gate status, evidence and rationale.

Review the artifact before committing it if private fixtures are involved. The harness does not emit
media frames, coordinates, seed notes, local stderr or local file paths in the aggregate JSON.

## Latency diagnostics (#179)

The artifact also carries, per case, a `latency_diagnostics` block and, at the top level, a
`latency_evidence` block. They are additive: the ADR-0009 `gate`, `end_to_end` and
`stage_diagnostics` content and the artifact version are unchanged.

`latency_diagnostics` records the cold (first) and warm `analyze` repeats, median, p95
(nearest-rank) and maximum wall time over completed repeats, and whether the canonical analysis and
recording-support bytes were identical across repeats. If any repeat failed, the statistics are
withheld and the failed durations and exit codes are listed instead. `qualifying` is always `false`:
no product latency target is approved. The cold repeat is only the first `analyze` repeat in the
invocation, run after the `tracker-run` diagnostic passes have read the same media; it is not a
verified cold start.

Optional:

    --stage-timings

passes `--timings-output` to `openbar-cli analyze` and aggregates the per-stage sidecar. It is off by
default so the gated runs are unchanged. See
[ANDROID_LATENCY_EVIDENCE.md](ANDROID_LATENCY_EVIDENCE.md) for the metric definitions, the owner
decisions still required, evidence classes and what a CLI timing can and cannot claim. A CLI timing
is not app-level completion.

## Decode/tracker split

tracker-run already reports decode and tracker wall times. The phone harness reuses that existing
instrumentation rather than adding a second timing implementation inside tracking.

These values are diagnostic because tracker-run runs both retained tracker baselines and can return a
non-zero status when the non-probe tracker fails. If template-sad-v1 produced its prediction and
timing, its stage timing can still be used diagnostically.

The authoritative gate uses analyze wall time.

## Memory interpretation

Two different memory observations are retained:

1. peak process-tree RSS: sampled from /proc while analyze runs;
2. frame_buffer_mib: selected_frame_count × display_width × display_height for the current 8-bit
   grayscale whole-clip buffer.

The second is deterministic and explains the current ADR-0006/ADR-0005 buffering cost. It is not a
replacement for RSS.

The process tree is always derived from the parent PIDs in `/proc/<pid>/stat`; the
`task/<pid>/children` file is not used, because some kernels lack it (observed on the Xiaomi 14T
Pro, Android 16) and where present it lists only the main thread's children. A harness that read
only that file would have reported the parent process alone and excluded the ffmpeg/ffprobe
children. The peak is reported as unavailable (`process_tree_rss_complete: false`, gate NOT
MEASURABLE YET) instead of undercounting when `/proc` cannot be scanned, a stat or status file of a
live process cannot be read or parsed, the harness's own process is missing from the scan while it
runs, the sampler thread fails or does not finish, or no RSS value was read. One incomplete repeat
makes the whole case's peak unavailable, because the case peak is a maximum over repeats.
"Complete" means the tree was fully enumerated at each 20 ms sample, not that every short-lived
process between two samples was caught.

A full `/proc` scan runs at every sample and has a CPU cost that is inside the measured wall time.
Each case records `rss_samples` and `rss_mean_scan_ms` under `latency_diagnostics` so that cost is
visible.

If process-tree RSS is inaccessible on the phone, the run stays NOT MEASURABLE YET rather than
silently substituting the deterministic buffer estimate.

## Thermal and battery interpretation

Android may restrict access to some sysfs data. Missing thermal/battery readings are represented as
null.

The first #59 pass should document:

- battery capacity change over the benchmark where available;
- battery temperature change where available;
- maximum readable thermal-zone temperature change where available;
- whether repeated runs show material slowdown consistent with thermal throttling.

The harness does not currently infer throttling from temperatures. Compare the repeated wall times
and record the observation explicitly in the evidence review.

## Architecture follow-up rule

Do not replace ADR-0006 or change the tracker frame boundary merely because a native decoder sounds
more appropriate for mobile.

If reference-phone evidence shows one of these problems:

- external process decode dominates runtime;
- ffmpeg/ffprobe cannot run reliably in the reference environment;
- whole-clip grayscale buffering causes unacceptable memory pressure;
- the process boundary prevents required phone measurements;

open a dedicated issue and ADR before implementing a native decoder or streaming frame boundary.

## Verification

Pure benchmark logic is covered by validation/tests/test_phone_runtime_benchmark.py, including:

- parsing decoder/tracker stage timing;
- processing/video ratio semantics;
- fail-closed handling of failed runs;
- reference-device eligibility;
- rejection of synthetic/warning-only gate evidence;
- PASS and FAIL thresholds.

The normal validation tooling suite executes these tests in CI. Physical-phone execution remains a
manual evidence step because hosted CI is not the reference hardware.
