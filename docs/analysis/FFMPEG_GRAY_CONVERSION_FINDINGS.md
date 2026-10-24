# FFmpeg `-pix_fmt gray` conversion findings (2026-10-19)

Status: evidence only. Nothing here changes ADR-0006, the decode arguments, schemas, goldens or
fixtures. Changing the frame-source contract is the owner's decision (AGENTS.md "Stop and ask").

Context: the in-process MediaCodec prototype (#258) decoded a Y plane bit-exact with FFmpeg's,
yet failed its luma gate against `-pix_fmt gray` (max abs diff 19). See
[ANDROID_LATENCY_EVIDENCE.md](../validation/ANDROID_LATENCY_EVIDENCE.md), section "In-process
MediaCodec prototype (#258, 2026-10-19)". ADR-0006 "Grayscale semantics" claims gray is the luma
plane expanded from limited to full range, which is a per-pixel function of Y.

## Summary

1. **On a BT.709-tagged clip, `-pix_fmt gray` is not a function of Y.** It is luma *recomputed
   from RGB*. FFmpeg converts YUV to RGB with the source matrix (BT.709), clips, then forms gray with
   **BT.601** weights. Chroma therefore leaks into canonical luma. Saturated colours shift
   by up to 20 codes, and warm or red regions come out brighter than their Y.
2. **The trigger is the frame's YUV matrix tag (`colorspace`) alone.** The trigger is not `-autorotate`,
   swscale flags, primaries or transfer. If the matrix tag is `unknown` or `bt470bg` (BT.601), the
   same command gives a pure per-pixel LUT. That LUT equals the ADR formula
   `clip(round((Y-16)*255/219), 0, 255)` exactly on every pixel of the clip.
3. **swscale flags only change rounding.** `accurate_rnd+full_chroma_int+bitexact` does not stop the chroma
   leak (219 Y codes still map to more than one output, max dev 18).
4. **The public synthetic fixture and the golden are unaffected.** `synthetic-clean-side-12.mp4` has no
   colour tags (`color_space=unknown`). Every gray variant tested yields byte-identical frames on it, so the
   `analyze` smoke and `analysis-v1.golden.json` cannot change under any of the fixes below.
5. **Real phone clips are affected.** Android cameras tag H.264 as BT.709. Canonical luma for real
   clips therefore depends on FFmpeg's colour-conversion path, which changes between releases. That
   explains why FFmpeg 8.1.3 (Termux, max dev 19) and 9.0.2 (desktop) disagree. The 8.1.3 path was
   not re-measured here; see "Not covered".

## Environment

- Desktop: Windows 11, `ffmpeg version 9.0.2-full_build-www.gyan.dev` (scoop), Python 3.11.9 +
  NumPy 2.4.6 (research scratch only, not repository tooling).
- Clip: `self-back-squat-side-002`, a private file in the main checkout,
  `validation/private/media/VID_20261002_075848~2.mp4`. It is `purpose: development`, tag
  `split-exploration`. The media is not committed. ffprobe reports H.264 `yuv420p`, `color_range=tv`,
  `color_space=bt709`, `color_transfer=bt709`, `color_primaries=bt709`, coded 1280×720, `rotation=90`,
  450 frames.
- Repository at `1b955ba`.

## Method

The reference is the decoded Y plane, taken losslessly through the same graph with
`-pix_fmt yuv420p`; autorotate's transpose is lossless on planar YUV. Each variant's gray output is
compared per pixel against the ADR formula and against Y. "Multi-valued Y codes" counts Y codes that
map to more than one gray value. A pure LUT gives 0.

Base command (the openbar-cli argument vector from `apps/openbar-cli/src/media/ffmpeg.rs`, plus
`-frames:v` and the variant's arguments):

```bash
ffmpeg -nostdin -hide_banner -v error -autorotate -i CLIP -map 0:v:0 -fps_mode passthrough -enc_time_base demux [VARIANT] -pix_fmt gray -f rawvideo - > gray.raw
```

```bash
ffmpeg -nostdin -hide_banner -v error -autorotate -i CLIP -map 0:v:0 -fps_mode passthrough -enc_time_base demux -pix_fmt yuv420p -f rawvideo - > yuv.raw
```

Comparison (frames are 720×1280 after autorotate):

```python
import numpy as np
w, h = 720, 1280
yuv = np.fromfile("yuv.raw", np.uint8).reshape(-1, w * h * 3 // 2)
y = yuv[:, : w * h].ravel()
g = np.fromfile("gray.raw", np.uint8).astype(np.int16)
adr = np.clip(np.round((np.arange(256) - 16) * 255 / 219), 0, 255).astype(np.int16)
d = g - adr[y]
pairs = np.unique(y.astype(np.int32) * 256 + g)
print("max", abs(d).max(), "mean", abs(d).mean(), "multi-valued Y codes",
      (np.bincount(pairs // 256, minlength=256) > 1).sum())
```

## Results

### Variants (first 30 frames, 27.6 M pixels)

| Variant (`[VARIANT]`) | Max \|gray − ADR\| | Mean | Pixels ≠ ADR | Multi-valued Y codes | corr(dev, U) / corr(dev, V) |
| --- | ---: | ---: | ---: | ---: | --- |
| none (openbar-cli contract) | 17 | 1.153 | 74.3 % | 220 | −0.65 / +0.82 |
| `-sws_flags bitexact` | 17 | 1.153 | 74.3 % | 220 | −0.65 / +0.82 |
| `-sws_flags accurate_rnd` | 18 | 0.784 | 59.1 % | 220 | −0.70 / +0.87 |
| `-sws_flags accurate_rnd+full_chroma_int+bitexact` | 18 | 0.725 | 53.8 % | 221 | −0.71 / +0.88 |
| `-vf format=gray` | 17 | 1.153 | 74.3 % | 220 | same as contract |
| `-vf scale=out_range=full,format=gray` | 17 | 1.153 | 74.3 % | 220 | same as contract |
| `-vf scale=in_range=tv:out_range=full:flags=accurate_rnd+full_chroma_int+bitexact,format=gray` | 18 | 0.725 | 53.8 % | 221 | same as all-flags row |
| `-vf setparams=color_primaries=unknown:color_trc=unknown` | 17 | 1.151 | — | 219 | unchanged |
| `-colorspace bt709` (output tag) | 17 | 1.151 | — | 219 | unchanged |
| **`-vf setparams=colorspace=unknown`** | **0** | **0** | **0 %** | **0** | — |
| **`-vf setparams=colorspace=bt470bg`** | **0** | **0** | **0 %** | **0** | — |
| **`-vf zscale=rangein=limited:range=full,format=gray`** (needs libzimg) | **0** | **0** | **0 %** | **0** | — |
| **`-vf extractplanes=y,lut=y='clip(round((val-16)*255/219),0,255)'`** | **0** | **0** | **0 %** | **0** | — |
| `-vf extractplanes=y` (raw Y, no expansion) | 20 | 7.23 | 97.3 % | 0 | — (gray = Y exactly) |

The setparams, `-colorspace` and `-noautorotate` rows used a 10-frame sample. The
`-noautorotate` run, with a matching un-rotated reference at 1280×720, gave the same statistics as
the contract: 219 multi-valued codes, max 17, mean 1.151. **Autorotate is not a factor.**

### Full clip, openbar-cli contract versus `setparams=colorspace=unknown` (450 frames, 414.7 M pixels)

| | Max \|dev\| | Mean \|dev\| | Pixels ≠ ADR | Y = 139 (ADR → 143): observed gray |
| --- | ---: | ---: | ---: | --- |
| contract `-pix_fmt gray` | 20 | 1.183 | 75.5 % | 130 … 151. Mode 143 (1 510 149 px), then 142 (855 237), 144 (210 527), 141 (50 109), 145 (23 820), 140 (9 886) |
| `setparams=colorspace=unknown` | 0 | 0 | 0 % | 143 only (2 669 369 px) |

The worst contract pixel has Y = 233, so the ADR formula gives 253, but gray is 233. That is a saturated pixel where
RGB clipping and the BT.601 re-weighting pull luma down by 20 codes.

### Identifying the conversion

A least-squares fit of the contract's gray output on 10 frames gives
`gray ≈ 1.164·(Y−16) + 0.120·(U−128) + 0.225·(V−128) − 1.16`. A pure range expansion would be
`1.164·(Y−16)` with no chroma terms. Candidate models (YUV→RGB with matrix *in*, clip to [0,1],
gray = luma with weights *out*, nearest-neighbour chroma):

| Model in → out | Contract: max / mean / exact | All-flags (`accurate_rnd+full_chroma_int+bitexact`): max / mean / exact |
| --- | --- | --- |
| 601 → 601 (≡ range expansion) | 14 / 1.149 / 25.8 % | 14 / 0.731 / 46.2 % |
| 709 → 709 (≡ range expansion) | 14 / 1.151 / 25.8 % | 14 / 0.732 / 46.2 % |
| 601 → 709 | 26 / 1.525 / 27.4 % | 26 / 1.437 / 28.7 % |
| **709 → 601** | **3 / 1.185 / 8.3 %** | **5 / 0.179 / 82.2 %** |

The 709 → 601 model has theoretical chroma coefficients 0.116 (U) and 0.223 (V), against the
fitted 0.120 and 0.225. With accurate rounding, 82 % of pixels match the model exactly, and the
remainder is within chroma-interpolation and fixed-point error. Without accurate rounding, the default
path adds a further ≈ −1 bias, which is the fit's −1.16 offset. The conversion is therefore:
**decode with BT.709 (the source tag), then take luma with BT.601 weights.** In swscale, gray
behaves as a BT.601-matrix (or unspecified) format. When the source matrix differs, swscale converts
between matrices through RGB, instead of copying and range-expanding Y. When the source is already
unspecified or BT.601, there is nothing to convert and the Y path is a LUT. This describes the
observed behaviour. The swscale source was not inspected.

### Public synthetic fixture and the canonical `analyze` path

`validation/fixtures/public/synthetic-clean-side-12.mp4`: H.264 `yuv420p` 320×240, 12 frames,
`color_range`, `color_space`, `color_transfer` and `color_primaries` all `unknown`, also at frame level.

| Variant on the fixture | SHA-256 of gray output (first 16 hex) |
| --- | --- |
| contract `-pix_fmt gray` | `5e174e593ceb94d5` |
| `-vf setparams=colorspace=unknown` | `5e174e593ceb94d5` |
| `-vf zscale=rangein=limited:range=full,format=gray` | `5e174e593ceb94d5` |
| `-sws_flags accurate_rnd+full_chroma_int+bitexact` | `5e174e593ceb94d5` |
| `-vf extractplanes=y,lut=…ADR formula…` | `5e174e593ceb94d5` |

The decoded frames are byte-identical under every variant, so the `analyze` smoke and the golden
cannot change. The `zscale` row also shows that untagged range is treated as limited. For the record,
the unmodified `analyze` smoke command from AGENTS.md exits 0 with `tracked=7 low_confidence=5 lost=0`.
Its output `target/analyze-smoke.json` has sha256
`61ffcf3f6562d41cb41ee50358f8c61348b6041ffd591f53f660c448a809572a`, with FFmpeg 9.0.2 recorded as the decoder.
No code, goldens or fixtures were changed. Because the synthetic fixture is untagged, **no existing
test exercises the BT.709 path.** This is a mechanical-check gap.

## Implications

- ADR-0006 "Grayscale semantics" is **wrong for BT.709-tagged sources**, which covers every Android phone
  clip seen so far. The recorded provenance (decoder version and argument vector) does not capture the
  hidden colour conversion. It is fully determined only together with the frame's `colorspace` tag
  and the FFmpeg build.
- Cross-build determinism of canonical luma is not guaranteed for real clips: 8.1.3 and 9.0.2 already
  differ. Same-build determinism still holds, because two decodes are byte-identical.
- Any non-FFmpeg frame source, such as MediaCodec (#258) or a future in-app decoder, can match the
  ADR formula exactly from Y. It cannot reasonably reproduce FFmpeg's version-specific 709→601
  path.
- Tracker inputs change in a colour-dependent way: coloured plates or bumper rims shift by several codes
  relative to neighbouring neutral pixels. The size of the effect on tracking metrics was not measured.

## Recommendation (for the owner's decision)

Make canonical gray an explicit function of Y in the decode graph, and stop relying on swscale's
colour handling. Options, in order of preference:

1. **`-vf extractplanes=y,lut=…` chosen per frame range.** This is pure integer per-pixel maths in core
   FFmpeg filters, with no colour matrix involved. It matches the ADR formula exactly and is the easiest
   to reproduce in a non-FFmpeg decoder. Caveat: the LUT must follow the *frame-level* range, as the ADR
   requires. A full-range (`yuvj`/`pc`) source needs the identity, so the CLI must select the
   LUT from probed range, or use an expression that reads the frame's range. Unknown range must be
   treated as limited, as FFmpeg does today.
2. **`-vf setparams=colorspace=unknown` before `-pix_fmt gray`.** This is the smallest change, keeps
   swscale's range handling, and is exact on this clip. However, it still relies on swscale's
   undocumented treatment of "unspecified ≡ gray's matrix ⇒ LUT", which could change again between
   releases.
3. **`zscale=rangein=…:range=full`.** This is exact, but it needs libzimg, which is not present in every
   build (Termux availability unchecked), and it adds a library to the decode contract.

Whichever option is chosen, it changes canonical frames for every BT.709-tagged real clip. It
therefore needs an ADR-0006 amendment, a decode-args and provenance version bump, and re-running any
real-clip evaluations. Add a **BT.709-tagged limited-range public fixture**, for example a re-tagged
synthetic clip with saturated colour patches. A test should then assert gray = ADR-LUT(Y), so this
check becomes mechanical. Swscale flags alone (`accurate_rnd`, `bitexact`, …) are **not** a fix.

## Not covered

- FFmpeg 8.1.3 (Termux) was not re-run here. Its max dev of 19 comes from the #258 evidence. Before
  adopting option 1 or 2, confirm on the phone that it removes the deviation there too.
- Full-range BT.709 (`yuvj420p`/`pc`) sources and HDR (HLG/PQ) clips were not tested.
- Y codes outside 15–247 do not occur in this clip. The exact-match claim for options 1–3 covers the
  codes present.
- The effect of the deviation on tracker or benchmark metrics was not measured.
