# Controlled development seed-frame motion experiment

Frozen before generation/scoring on 2026-10-05. One configuration, nine cases, no tuning.

## Inputs and transformation

Use only confirmed frame 0 seeds from development fixtures `vbt-e169ecc156007a9e`
(clean), `vbt-bb101cea545505fc` (snatch), `vbt-4eb16d0c845e4bea` (squat).
Hash-check media/manifest/seeds and check seed frame/PTS and display rotation using existing
helpers. Fully drain the existing decoder. Retain the decoded seed raster unchanged.

Each sequence has 33 frames, full display raster, no resizing or crop-origin change.
Integer (dx, dy) pixels: frames 0..16 = (4i, -2i); frames 17..24 =
(64 - 8(i-16), -32 + 4(i-16)); frames 25..32 = (0, 0).
Copy real overlap into a zero-filled raster, never wrap. Refuse any translated seed disk
(plus 8 px margin) that leaves the raster. Encode losslessly as FFV1/bgr0 in NUT.
Program PTS at i/30 seconds; score actual FFprobe timestamps rounded to six decimals only
after confirming all 33 PTS agree with the program within 0.000001 s. This is synthetic
timing, not the source recording's motion or timing. Retain frame hashes and verify every
decoded generated raster equals its pre-encoding raster.

Three cases per lift; change only the named factor:

- Translation: unchanged seed raster translated.
- Blur: Gaussian 15x15, sigmaX=sigmaY=3 px, BORDER_REFLECT_101, applied once to the source
  raster before translation (including initialization). Same motion, fill and timing.
- Occlusion: translation case with a zero-filled axis-aligned rectangle covering the
  translated seed disk plus 8 px on frames 12..15 inclusive. Frame 0 is untouched.

Geometry comes from the existing confirmed seed; synthetic seed documents only bind it
to the new fixture ID and rotation 0 (already display-oriented). No new human seed or label.
The private reference is an injected transform program, never an annotation-v1 document.

## Producers and metrics

Run unchanged `track.track` CSRT in the existing OpenCV environment and
`track_gpu.track` SAM 2.1 bplus-circle in the existing GPU environment/checkpoint, preserving
JPEG-q2 cache, box/point initialization, bfloat16, CPU frame offload, circle gates and fallback.
Only redirect media/model/cache locations. Retain prediction-v1 and SAM geometry sidecars,
source/checkpoint/helper/environment hashes, synthetic manifest/seeds and timing separately.

Exclude initialization. Error is norm of (predicted centre minus emitted seed centre) minus
programmed translation, not independent absolute centre error. Report tracked/lost/missing
support; mean/p90/max displacement error; final-frame error vector/norm and return-hold
mean error (drift); errors >3 px with algorithm-specific confidence >=0.8, confidence ranges
on wrong displacement; per-frame error/state/confidence; exact common tracked support for
both methods. Never replace loss or interpolate it. Also retain adjacent displacement errors
only across consecutive tracked observations and actual PTS gaps <=0.2 s.

For occlusion report all four occluded states, post-occlusion tracked/error support and first
three-consecutive-frame recovery to <=3 px from frame 16 onward (latency from first visible
PTS), or unsupported/not recovered. Complete coverage does not imply accurate tracking.

## Checks, repetition and stop

Runnable checks: non-wrapped translations/sign/copy preservation, out-of-bounds target refusal,
exact PTS binding, finite values, coordinate-free loss, seed exclusion/common support,
known wrong high-confidence displacement, recovery, and preserved scorer inputs.
Repeat generation and CSRT from frozen inputs; repeat scoring byte-for-byte. Retain SAM
frozen masks and repeat CPU circle conversion. Repeat the nine GPU cases once on this same
stack; report sample/mask equality separately from CPU replay and allocator/runtime fields.
Hash original consumed inputs again after completion. No held-out media is opened.

Stop after these nine cases and the declared repetitions. No parameter search, model,
dependency, fusion, production tracker/filter selection or physical velocity claim. Only a
proven implementation defect permits one minimal shared-layer fix with regression coverage
and affected real-video replay; otherwise retain algorithm limitations unchanged. Preserve
#103's adjusted destination cleanup/retry behavior. A failed producer is retained as failed,
not replaced by another configuration; infrastructure corrections must be documented.

Whole-frame translation moves the background too; it does not reproduce plate movement
against a stationary background. Static seed appearance, artificial occlusion and fixed blur
also exclude real rotation, deformation, exposure and motion-blur dynamics. A programmed
displacement reference cannot remove seed-placement bias or establish physical velocity truth.
