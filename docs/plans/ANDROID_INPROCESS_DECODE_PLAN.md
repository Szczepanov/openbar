# Android in-process hardware decode prototype (#258)

Status: **experiment implemented on the host. Device evidence has not been collected.** This plan
does not change ADR-0006, the ADR-0005 tracker frame boundary or any canonical output.

## 1. Context

The 5-repeat Xiaomi 14T Pro benchmark ([evidence](../validation/ANDROID_LATENCY_EVIDENCE.md)) spends
**1.59 s in `ffprobe` + 3.69 s in the `ffmpeg` pipe** on the 6.0 s `self-back-squat-side-002` lift
window. That is about 5.3 s before tracking starts, which is more than the
[#179](https://github.com/Szczepanov/openbar/issues/179) median budget of < 5.0 s. ADR-0006 already
names platform decoders (option E) as the app's future path behind the same frame boundary.
ADR-0009 lists external-process decode as an architecture follow-up trigger.

## 2. Goal and non-goals

**Goal:** measure whether `AMediaExtractor` + `AMediaCodec` can produce frames that satisfy the ADR-0006
contract in **< 0.50 s** median on the lift window.

**Non-goals:**
- a workspace `VideoDecoder` trait or any `openbar-cli` change;
- JNI or app integration;
- a streaming tracker interface;
- any schema, golden or fixture change;
- tracker latency, which is tracked under #251.

## 3. Governing constraints

The ADR-0006 contract applies in full:
- PTS-authoritative timestamps, strictly increasing;
- display orientation;
- full-range 8-bit luma, with limited-range input expanded;
- no scaling;
- no silent drops;
- fail closed.

Coordinates follow ADR-0007. Clean-room rules and the MIT licence apply.

`ndk-sys` was approved by the owner on 2026-10-19 for the research crate only. It is not a workspace
dependency.

## 4. Dependencies and prerequisites

- The Xiaomi 14T Pro reference unit, with Termux (Rust, Python, FFmpeg) or an NDK with `cargo-ndk` plus adb.
- The private clip `self-back-squat-side-002` (`purpose: development`, no split tag). The lift window is 4.8–10.8 s, as in `phone-runtime-suite-v1.json`.

## 5. Work packages

**Implement experiment**

1. Crate skeleton `research/android-decode/`. It is standalone (its own `[workspace]`) and pins `ndk-sys` for Android only. Done.
2. Host-testable logic. Done, with deterministic unit tests:
   - crop, stride removal and rotation repack;
   - limited-to-full range expansion;
   - Android clockwise to FFmpeg counter-clockwise rotation mapping;
   - PTS conversion;
   - inclusive window;
   - monotonicity check;
   - report JSON.
3. NDK decoder. Done, type-checked for `aarch64-linux-android`, not yet run:
   - ByteBuffer output;
   - only I420/NV12 accepted;
   - seek to the previous sync frame;
   - pre-roll counted;
   - RAII handles;
   - stall guard;
   - PTS made relative to the stream origin (earliest sample time in the first GOP, standing in for FFmpeg `start_pts`);
   - fail closed on read errors, negative sample times, out-of-order output, out-of-bounds buffers and mid-window size changes;
   - geometry fallbacks recorded in the report.
4. CLI with `--report` and `--dump-gray`. Done.
5. `compare.py` with `parity` (against the same FFmpeg arguments as `openbar-cli`) and `summarize` (median/min/max over repeats). Done. Self-checked on desktop against the public synthetic fixture.

**Evaluate evidence**

6. Build on the phone, then run 5 repeats on each of: the synthetic smoke clip, the lift window and the full clip. Run parity once per clip.
7. Record the results in an "In-process MediaCodec prototype" section of `ANDROID_LATENCY_EVIDENCE.md`, whether they pass or fail. Include device, commit, toolchain, decoder name, colour format and range source, and the median stage timings next to the FFmpeg baseline.

**Promote (separate change, owner decision)**

8. New or amended ADR for the mobile decode boundary.
9. A `VideoDecoder` abstraction with the FFmpeg and MediaCodec back-ends.
10. A versioned frame-source id (`android-mediacodec-gray`).

## 6. Files

`research/android-decode/{Cargo.toml,Cargo.lock,README.md,src/*.rs,compare.py,tests/test_compare.py}`.
After step 7, `docs/validation/ANDROID_LATENCY_EVIDENCE.md`.

## 7. Tests and commands

See the [prototype README](../../research/android-decode/README.md):
- host: `cargo fmt`, `cargo clippy -D warnings` (host and Android target), `cargo test`;
- `python -m unittest discover -s tests`;
- on the device: `compare.py summarize` and `compare.py parity`.

## 8. Evidence to retain

Keep the report JSON per repeat, the parity JSON and the summary JSON under `validation/private/`. These
must not be committed. Copy the numbers into the evidence doc.

## 9. Decision gates

Promotion requires all of:
- median `total_s` < 0.50 s on the lift window;
- parity passing: equal frame count, PTS within 1 µs, equal display dimensions, luma max-abs-diff ≤ 2;
- no fail-closed format refusal on the 14T Pro.

The PROCEED/STOP decision is the owner's.

## 10. Stop conditions

- The decoder offers only a vendor or tiled colour format. An `AImageReader` surface path would then be a new decision.
- PTS or frame-count mismatch against `ffprobe` on real footage. Report it as an ADR-0006 revisit finding.
- Never use a `split-reserved-57` clip.

## 11. Rollout

Research only until step 8. The canonical `analyze` path keeps FFmpeg (ADR-0006) for desktop and CI.

## 12. Docs and ADRs if promoted

- ADR-0006, which would gain an amendment or a successor.
- ADR-0009, whose revisit list includes Android media boundary selection.
- `docs/validation/PHONE_RUNTIME_BENCHMARK.md`.
- `ARCHITECTURE.md` dependency policy, if `ndk-sys` enters the workspace.
