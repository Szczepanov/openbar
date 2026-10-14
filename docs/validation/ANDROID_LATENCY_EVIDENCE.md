# Android import-to-results latency evidence

Issue: #179 (child of #80). **Status: Proposed contract; no numeric target is approved and no
qualifying measurement exists.**

This document defines what "results within seconds" has to mean before anyone measures it: which
phone, which clips, which clock start and stop, which statistics, and which evidence is allowed to
produce a pass or fail. It does not choose the numbers. Every value that needs a product decision is
listed in [Owner decisions](#owner-decisions-required) and stays **PENDING** until the owner records
it here.

It extends, and does not replace, the [phone runtime benchmark](PHONE_RUNTIME_BENCHMARK.md)
and [ADR-0009](../adr/0009-m0-phone-runtime-benchmark-boundary.md).

## Relationship to the existing M0 gate

Two questions are measured from the same runs and reported separately:

| | M0 gate (ADR-0009, unchanged) | Product latency target (this document) |
| --- | --- | --- |
| Question | Is offline analysis faster than the video is long? | Does the user see results within the approved seconds budget? |
| Quantity | `median(analyze wall / selected media span) < 1.0` | Time-to-first-results and time-to-complete in seconds (thresholds PENDING) |
| Status today | NOT MEASURABLE YET | Not defined numerically; not measurable |
| Owner | ADR-0008 / ADR-0009 | Owner decision recorded below |

Passing one says nothing about the other. A 40 s clip analysed in 30 s passes the M0 ratio and may
still miss a "within N seconds" target; a 5 s clip analysed in 6 s fails the M0 ratio and may still
meet one. The harness never derives one status from the other and never revises the M0 target.

## Owner decisions required

These values are product decisions, not engineering defaults. Nothing below may be filled in from
desktop timings, from the synthetic benchmark, or from "what the harness happens to achieve".

| Parameter | Value | What the decision must settle |
| --- | --- | --- |
| Target phone(s) | Reference: Xiaomi 14T Pro (decided, ADR-0009 Amendment 1). Product reference and any lower-tier phone: PENDING | Whether the 14T Pro is also the product reference, and whether a lower-tier phone is added. It is a high-end phone, so a pass on it supports no minimum-spec claim. |
| Clip duration(s) | PENDING | The selected lift duration the budget applies to (for example a single set versus a trimmed rep window). |
| Resolution / frame rate / codec | PENDING | Per-case values (for example 1080p 30 fps H.264 and HEVC; 4K only if supported). Variable-frame-rate sources are in scope per ADR-0006. |
| Display rotation | PENDING | Which rotation metadata values are in the workload; rotated phone footage is the common case. |
| Cold / warm definition | PENDING | See [Cold and warm](#cold-and-warm). The app-level definition needs a product decision. |
| Repeat count | PENDING | Number of repeats per case per condition. The statistic that can be reported depends on it (see [Statistics](#statistics)). |
| Time-to-first-results threshold | PENDING | Numeric seconds, and whether it applies to the median, the p95, or both. |
| Time-to-complete threshold | PENDING | Numeric seconds, same question. |
| Sustained-use condition | PENDING | Whether back-to-back runs (thermal soak) are part of the target and how many. |
| Memory limit | PENDING | A peak process-tree RSS ceiling, or "record only". |

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
| Peak RSS | Yes | Unchanged |
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
| Reference phone run | The unit's identity is confirmed (model 2407FPN8EG, `rothko`, arm64-v8a, Android 16), but no harness run on it exists. Without one, delivery is the contract and harness only. |

Next steps once unblocked: record the owner decisions here; freeze the candidate and clip set; run
the harness with `--stage-timings` on the reference phone; retain the artifact under the checklist
above; report the evidence class honestly.

## Non-goals

- Choosing numeric latency thresholds, supported phones or the clip set.
- Building the Android app, selecting a decoder, packaging, or a streaming frame boundary.
- Changing the ADR-0009 gate, its artifact semantics, or the frozen probe configuration.
- Using desktop or synthetic timings as phone evidence.
- Any change to the canonical `Analysis`, schemas, golden file or fixtures.
