# Android in-process decode prototype (#258)

Research prototype for [#258](https://github.com/Szczepanov/openbar/issues/258) in support of the
[#179](https://github.com/Szczepanov/openbar/issues/179) latency budget. It decodes video in-process
with the Android NDK (`AMediaExtractor` + `AMediaCodec`, `libmediandk`) and checks whether the result
meets the ADR-0006 frame contract fast enough to replace the external FFmpeg processes on the phone.

On the Xiaomi 14T Pro, `ffprobe` + `ffmpeg` cost about 5.3 s on the 6 s lift before tracking starts
([evidence](../../docs/validation/ANDROID_LATENCY_EVIDENCE.md)). The target here is **< 0.50 s** median
decode for that window, with full parity.

This crate is **outside the Rust workspace and the M0 gates**. It changes nothing in `openbar-cli`,
the tracker frame boundary (ADR-0005) or ADR-0006. Promotion would need an owner decision, a new or
amended ADR and a workspace `VideoDecoder` boundary in a separate change.
The plan is [`docs/plans/ANDROID_INPROCESS_DECODE_PLAN.md`](../../docs/plans/ANDROID_INPROCESS_DECODE_PLAN.md).

## What it measures against (ADR-0006)

| Contract | How the prototype handles it |
| --- | --- |
| PTS-authoritative timestamps | `presentationTimeUs` from the decoder, converted as `(pts_us - origin_us) / 1e6` with one final division. `origin_us` is the earliest sample time in the first GOP; it plays the role of FFmpeg's `start_pts` and is recorded in the report. Every output buffer must be strictly later than the one before, including pre-roll frames and the frame that ends the window, or the run fails. Negative sample times are refused, never clamped. Timestamps are never derived from FPS. |
| Display orientation | Android `rotation-degrees` (clockwise) is applied while repacking. The report records both it and the FFmpeg counter-clockwise equivalent (`(360 - cw) % 360`). |
| Full-range 8-bit luma | The Y plane is used directly. If the decoder reports `color-range` = limited, codes are expanded with `round((Y-16)*255/219)` and clipped. If the decoder reports no range, the run fails unless `--assume-range` is given, and the report then says `"luma_range_source": "assumed"`. |
| Packed raster, no scaling | Crop, then stride removal, then rotation give a `w*h` buffer in FFmpeg `-f rawvideo -pix_fmt gray` layout. |
| No silent drops | Every in-window frame is emitted. Pre-roll frames decoded after the seek, before `start_s`, are counted in `preroll_discarded`. Frame count and PTS are checked against `ffprobe` by `compare.py`. |
| Fail closed | Only I420 (19) and NV12 (21) ByteBuffer output is accepted. Any vendor or tiled colour format is refused and named. All of these are errors:<br>• inverted or out-of-plane crop rectangles;<br>• truncated or out-of-bounds output buffers;<br>• an extractor read failure before end of track;<br>• a display size change inside the window. |
| Recorded fallbacks | If the decoder omits `stride`/`slice-height` or the crop, width and height are used instead. The report records this as `stride_source` / `crop_source` = `"fallback"`. |

Windows are inclusive, the same as `openbar-cli` `TimeRange`. Unlike the FFmpeg path, which decodes
the whole file and then filters, the prototype seeks to the previous sync frame before `start_s`.

## Layout

- `src/raster.rs`, `src/timing.rs`, `src/decode.rs`: pure logic and the report, unit-tested on any host.
- `src/android.rs`: NDK FFI, compiled only for `target_os = "android"`.
- `src/main.rs`: CLI.
- `compare.py`, `tests/test_compare.py`: stdlib-only parity and repeat-summary harness.

## Dependency

`ndk-sys = "=0.6.0"` (feature `media`, links `libmediandk`) was approved by the owner for this research
crate only on 2026-10-19. It is used only for Android targets, so host builds have no dependencies.

- Licence: MIT OR Apache-2.0.
- Source: crates.io, [rust-mobile/ndk](https://github.com/rust-mobile/ndk).
- Transitive crates (`jni-sys`, `jni-sys-macros`, `syn`, `quote`, `proc-macro2`, `unicode-ident`) are MIT and/or Apache-2.0, with Unicode-3.0 for `unicode-ident`.
- `libmediandk.so` is an Android platform library and is not redistributed.

## Host checks

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
cargo clippy --target aarch64-linux-android --all-targets -- -D warnings   # type-check only, no linker needed
python -m unittest discover -v -s tests
```

On a host the binary exits with a "needs an Android build" error.

## Build and run on the phone

**Option A: Termux.** This is the same environment as the FFmpeg baseline.

```bash
pkg install git rust python ffmpeg
cd research/android-decode
cargo build --locked --release
```

**Option B: cross-compile.** Needs an NDK and [`cargo-ndk`](https://github.com/bbqsrc/cargo-ndk).

```bash
cargo ndk -t arm64-v8a --platform 28 build --locked --release
adb push target/aarch64-linux-android/release/openbar-android-decode /data/local/tmp/
```

The NDK APIs used (`getName`, `KEY_DISPLAY_CROP`, `KEY_COLOR_RANGE`) need API 28 or newer.

Decode the lift window of `self-back-squat-side-002`, the same window `phone-runtime-suite-v1.json`
uses (`start_s` 4.8, `end_s` 10.8). Private media lives in the main checkout under
`validation/private/media/` and must never be committed.

```bash
BIN=target/release/openbar-android-decode
for i in 1 2 3 4 5; do
  $BIN --input <clip.mp4> --start-s 4.8 --end-s 10.8 --report out/lift-$i.json
done
python compare.py summarize out/lift-*.json

# parity (needs ffmpeg/ffprobe; slow in pure Python, run once per clip)
$BIN --input <clip.mp4> --start-s 4.8 --end-s 10.8 --report out/lift-parity.json --dump-gray out/lift.gray
python compare.py parity --input <clip.mp4> --report out/lift-parity.json --dump out/lift.gray --output out/lift-parity-result.json
```

Repeat the same steps for the full clip (no window) and for `validation/fixtures/public/synthetic-clean-side-12.mp4`.

`--dump-gray` adds file-write time to `sink_s` and `total_s`, so benchmark runs leave it off.
Timings live in the report's separate `timings` object. They are diagnostics, not measurement output.

## Known limits

- **Range.** The prototype follows the `color-range` the decoder reports. ADR-0006 records a clip whose container said `tv` but whose frame-level range was full. If a decoder reports the container value there, the frames get expanded twice and `compare.py` shows a large luma difference. That is a parity finding, not a reason to loosen the gate.
- **Mirrors.** Android `rotation-degrees` cannot express a display-matrix mirror, which FFmpeg `-autorotate` would apply. That is fine for rear-camera clips. A mirrored clip shows up as a parity failure.
- **Early stop.** Decoding stops at the first output past `end_s`. That relies on presentation-order output, which is checked for every output up to that point.

## Gates

- Promotion (owner decision): median `total_s` below 0.50 s on the lift window, parity passing with equal frame count, PTS within 1 µs, equal display dimensions and luma max-abs-diff ≤ 2, and no fail-closed format errors on the 14T Pro.
- Stop and report: the device offers only a vendor colour format through ByteBuffer output, which makes an `AImageReader` path a new decision; or there is a PTS or frame-count mismatch against `ffprobe`, which is an ADR-0006 revisit finding.

## Status

Host logic is tested and the Android target type-checks. No device measurement has been recorded yet.
