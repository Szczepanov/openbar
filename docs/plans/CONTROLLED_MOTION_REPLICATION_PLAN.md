# Controlled-motion replication on current main

Frozen before generation or scoring on 2026-10-06. Main already contains #105 and #106;
this request runs one exact replication of #105, not another configuration search.

Reuse the [original frozen experiment](CONTROLLED_MOTION_EXPERIMENT_PLAN.md) without
changing source selection, motion, degradation, producers, scoring or thresholds:

- Confirmed frame 0 from development clean `vbt-e169ecc156007a9e`, snatch
  `vbt-bb101cea545505fc`, squat `vbt-4eb16d0c845e4bea`; unchanged confirmed geometry.
- 33 full display-raster frames. Frames 0..16 shift by (4i, -2i) px; 17..24 return
  by (64 - 8(i-16), -32 + 4(i-16)); 25..32 hold (0, 0). Copy overlap into zero fill;
  refuse a target disk plus 8 px margin outside the raster. No wrapping or resizing.
- Three cases per source: pure translation; fixed Gaussian 15x15, sigma 3 px,
  BORDER_REFLECT_101 before translation including initialization; or translation with
  opaque zero rectangle over the disk plus 8 px at frames 12..15. One factor per case.
- FFV1/bgr0 in NUT, programmed i/30 s PTS. Verify every decoded frame hash and actual
  FFprobe PTS within 1 microsecond; score the verified six-decimal PTS.
- Unchanged CSRT and SAM 2.1 bplus-circle through existing top-level helpers/environments
  and checkpoint. Preserve JPEG-q2, bfloat16, CPU frame offload, gates and fallback.
  Copy the retained corrected private harness into a new private destination, redirecting
  only its plan path. Record current source, input, checkpoint and environment hashes.
- Exclude initialization from point metrics. Retain seed-relative displacement error
  mean/p90/max, adjacent supported error, final/return-hold drift, tracked/lost/missing
  support, exact common support, confidence ranges for errors >3 px and counts at >=0.8.
  Retain all occluded states; recovery needs three visible consecutive frames <=3 px
  starting at frame 16, with latency from its actual PTS. Never bridge loss.

Run generation and CSRT twice; SAM twice on the existing GPU; frozen-mask CPU conversion
once; score twice with byte comparison. Compare samples, geometry, frame hashes and scores
with retained #105 separately from runtime/allocator/provenance fields. Reuse the existing
runnable transformation, timestamp, loss, finite-value and preservation tests, including
#103 cleanup/retry regressions. Rehash original inputs and earlier artifact files afterward.
Retain source snapshots and a portable read-only score replay in the owner project folder.

Stop after nine cases and these repetitions. Preserve failed outputs; no tuning or replacement
configuration after failure. Only a proved implementation defect permits a minimal shared-layer
fix, regression test and affected existing real-video rerun. Otherwise retain limitations.
No installations, new labels/seeds, held-out access, fusion or production selection.

Injected displacement is not independent absolute plate-centre or physical velocity truth.
Whole-frame translation moves the background and does not reproduce a moving bar against a
stationary background. Static appearance, fixed blur and synthetic occlusion limit inference.
CPU frozen-input reproducibility and observed same-stack GPU repeatability are separate claims.
