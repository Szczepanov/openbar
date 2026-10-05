# Paired target-layer / background motion experiment

Frozen 2026-10-05, after the declared CSRT box trace and before generation/scoring.
One paired configuration, six 33-frame sequences; no retuning after results.

## Source and synthesis

Reuse only the three already hash-verified full display seed PNGs, confirmed seeds and
development fixture identities from #105 (clean, snatch, squat). No new source decoding,
segmentation, annotations, seeds, checkpoints, environments or human input. Verify the old
artifact inventory and original seeds/media/checkpoint/helper hashes before and after use.

The target layer is the exact source BGR pixels inside the confirmed seed-centred disk of
radius (confirmed radius + 8 px). This is a deterministic synthetic cutout, not a new
plate segmentation or ground-truth label. Keep its shape/appearance fixed.
Construct one fixed background by copying the source raster and replacing the union of
all translated target-disk footprints with uniform BGR (128,128,128). Thus moving the
cutout cannot expose a duplicate original plate. No inpainting, feathering or wraparound.
Preserve all source pixels outside this explicitly erased corridor.

Both cases start from the identical raster: fixed background with the original disk pasted
at zero shift. At each programmed translation:

- Whole-frame control: translate that entire initial composite into zero fill, using the
  unchanged #105 transformation helper.
- Stationary background: keep the constructed background fixed and translate/paste only
  the source disk. No additional blur or occlusion in either case.

The source-disk pixels at every translated location must be identical in both cases.
Refuse target-margin clipping. Keep full 1080x1920 display coordinates (+X right, +Y down).
Program remains 33 frames: i=0..16 (4i,-2i); i=17..24
(64-8(i-16),-32+4(i-16)); i=25..32 (0,0). Encode FFV1/bgr0 in NUT, declared PTS i/30 s.
Check every decoded raster hash and actual probed PTS within 1 microsecond before scoring;
use the stored six-decimal PTS, not nominal real-video timing.

## Producers and scoring

Reuse unchanged `track.track` CSRT in the existing pinned CPU environment and unchanged
SAM 2.1 bplus-circle in the existing GPU environment/checkpoint. Preserve all defaults,
JPEG-q2 cache, initialization, bfloat16, offload and geometry fallback. Retain frozen masks,
geometry sidecars, raw prediction-v1 streams and algorithm-specific confidence. Adapt only
fixture binding and already-display-oriented rotation; confirmed target geometry is unchanged.

Reuse #105 seed-relative displacement error, drift, tracked/lost/missing support, >3 px wrong
support, confidence >=0.8 wrong support and adjacent diagnostics. No recovery metric applies:
these cases have no injected occlusion. Exclude initialization from point scores and paired
comparisons. Compare each tracker across background cases on exact common tracked timestamps;
also retain both all-support results and cross-tracker common support within each case.
Do not use tracker agreement as truth, lose failures, interpolate or alter raw observations.

## Checks, repetition and stop

Runnable tests must prove identical initialization, unchanged source inputs, foreground
pixel identity across cases, fixed background outside the moving layer, absence of an old
target copy, real non-wrapped translation, target bounds and finite-value rejection. Reuse
existing timestamp/loss/scoring checks. Hash plan/scripts/helpers/inputs before scoring.

Repeat all six generated containers and CSRT streams once. Repeat frozen-mask CPU circle
conversion and scoring byte-for-byte. Repeat the six GPU cases once on the same stack;
report mask/sample equality separately from CPU replay and runtime/allocator metadata.

Stop after these six cases and repetitions. Retain negative or mixed results without a
parameter search. Only a proven implementation defect permits one minimal shared-layer
fix, regression coverage and affected real-video reruns. Preserve #103 cleanup/retry.
No held-out access, model/environment installation, production tracker/filter selection,
fusion, M1 features or physical velocity claim.

The hard disk boundary and uniform erased corridor are artificial cues. The cutout can
include sleeve/background pixels and may exclude anatomy or plate details outside its
declared support. This separates background motion in a synthetic composite; it does not
recreate a natural lifting scene or establish independent absolute plate-centre accuracy.
Injected displacement remains a programmed reference only, not physical velocity truth.
