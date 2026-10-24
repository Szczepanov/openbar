# OpenBar next steps — 2026-10-24

Recon for [#234](https://github.com/Szczepanov/openbar/issues/234), checked against open issues,
merged PRs through #265 (`a5130d3`), and the committed evidence. This is a dated work order,
not an owner decision or a replacement for the live issue tracker. No open PRs were returned
at recon time. Locally, `adb devices -l` returned no connected devices.

## Current state

| Track | Delivered evidence | Remaining work |
| --- | --- | --- |
| #258 hardware decode | #263: lift median 2.02 s versus 5.28 s FFmpeg probe/decode. #266: amended canonical luma is bit-exact on lift/full re-checks. #265: seven research variants implemented. | Measure the variants on the 14T Pro; the < 0.50 s decode target is still missed by the baseline. |
| #251 / #257 mobile inference | #255: student v2 static `.pt2` export with desktop parity. #259/#262: EdgeTAM vision encoder, memory encoder and mask decoder exports with desktop parity. | Actual mobile-runtime conversion, parity and on-device detector/tracker timing. A `.pt2` export alone is not a measured mobile runtime. |
| #57 tracker/filter selection | Development comparisons and research tooling exist. | Independent recordings, repeatability labels, frozen candidate/configuration and held-out evaluation; no production selection from development scores. |
| #79 agreement | Preregistered tooling exists. | Owner prospective cohort and reviewed agreement decision. |
| #80 M1 entry | Accepted metrics/overlay designs; physical-phone CLI diagnostics exist. | #57, #79 and qualifying runtime evidence, then the explicit ADR-0008 entry decision. |

The body of #234 still describes the phone as never connected and lists student v1 results.
Its 2026-10-19 progress comment and subsequent merged PRs supersede those delivery details.
#179 is closed for its delivered diagnostic work; that does not establish product latency success.
The [latency evidence](../validation/ANDROID_LATENCY_EVIDENCE.md) remains non-qualifying:
the candidate is not frozen and app import, rep metrics and overlay are not measured.

## 1. Measure the existing decoder variants (#258)

This is the next executable experiment once the reference phone is reachable. Reuse
[`run_device_suite.sh`](../../research/android-decode/run_device_suite.sh); do not add another harness.
On the Xiaomi 14T Pro, update the checkout and build the research binary, then run from
`research/android-decode` with the repository root as the first argument:

```bash
cargo build --locked --release
REPEATS=5 VARIANTS="packed view view-scan hints-none realtime rate-240 image-reader" \
  ./run_device_suite.sh ~/openbar ~/openbar/validation/private/android-decode-2026-10-24
```

Use a fresh output directory. Confirm the two input files named by the driver are present:
the public synthetic smoke fixture and the development-only `self-back-squat-side-002` media.
The suite runs synthetic, lift-window and full-clip cases per variant, with separate untimed
parity dumps. It needs the phone's existing Rust, Python and FFmpeg tools.

Retain environment/binary provenance, all repeats, summaries and parity results under
`validation/private/`. Capture logcat around the hint runs to investigate whether the codec
honours the hints; echoed keys alone do not prove that it does. Record format refusals,
crashes and mismatches, including a failure of the unmeasured AImageReader path.
The driver stops on tool/input errors, so run remaining variants separately after retaining
the failed variant's outputs. A suite reaching its final message does not itself prove parity:
measured parity failures are retained and the suite continues.

Compare total latency and per-frame decode wait, copy, repack and scan costs. Evaluate `view`
together with `view-scan`: deferring rotation/range expansion moves work into the consumer.
Retain the existing promotion gates: lift median < 0.50 s, equal counts/dimensions, PTS within
1 microsecond and luma max-abs-diff <= 2; the variant follow-up also requires bit-exact raw-Y
diagnostics. Do not tune thresholds after measurement. Add a dated subsection to
[ANDROID_LATENCY_EVIDENCE.md](../validation/ANDROID_LATENCY_EVIDENCE.md), failures included.
Production decoder selection remains an owner decision.

## 2. Establish mobile inference feasibility (#257 / #251)

Inventory the existing private student v2 and EdgeTAM export manifests before converting anything.
Select a concrete mobile runtime/conversion path using its current documentation. Any new
dependency, model or distribution/licensing change needs owner approval under AGENTS.md;
the existing export work does not grant blanket runtime dependency approval.

Convert and verify the student detector first: retain hashes, exact runtime/version and precision,
check output parity before timing, then measure cold/warm latency, memory and thermal readings
on the reference phone. Keep weights and raw reports private. The Phase B detector target
is < 250 ms; separately assess the < 5.0 s median / < 8.0 s maximum total budget.
Then establish the complete EdgeTAM tracking path: component export parity alone proves neither
temporal tracking parity nor end-to-end phone performance. Preserve failures rather than
reporting desktop timings as phone evidence. See [Phase B WP4](PLATE_DETECTION_PHASE_B_PLAN.md).

## 3. Collect independent evidence before M1 (#57 / #79 / #80)

Owner collection can proceed alongside device work: new independent side-view sessions,
pilot repeatability labels and refusal-policy decisions under
[the next-round plan](PLATE_SELECTION_NEXT_ROUND_PLAN.md), plus #79's prospective cohort.
Freeze roles/configuration before final evaluation. Never inspect `split-reserved-57` clips
without specific owner authorization; use the held-out guard. No split, fixture, schema,
calibration or canonical frame-boundary change is part of this work order.

After #57 selection/rejection and #79 review, assess recording-envelope limits and qualifying
phone evidence for the explicit ADR-0008 M1 decision. A rejected tracker or a closed tooling
issue cannot satisfy a successful selection gate. Keep #68/#69 parked.
