# CSRT box trace and paired background-motion result

2026-10-05. Continues merged #105 without recordings, labels, seed placement or owner review.
Implements the separately frozen [box trace](../plans/CSRT_BOX_TRACE_PLAN.md) and
[paired experiment](../plans/STATIONARY_BACKGROUND_EXPERIMENT_PLAN.md).

## Decision

CSRT's wrong returned box explains the retained squat residuals; the OpenBar wrapper's
pixel-centre conversion and scoring agree exactly. No shared implementation defect is
proved, so no tracker fix or parameter change is made. The paired synthetic experiment
also retains failures: keeping the background stationary increases CSRT mean error on
all three seed appearances. SAM's response is mixed and includes high-confidence errors.
Stop these declared investigations without selecting a production tracker/filter.

## Trace: returned geometry, not a conversion failure

Replay only the original 33-frame squat pure-translation sequence from #105. An observational
proxy forwards the original CSRT `init`/`update` calls and records valid boxes, leaving the
existing producer untouched. Its runtime-free prediction matches the retained prediction
byte-for-byte, including every timestamp, state, centre and confidence. The repeat also
produces byte-identical box/decomposition reports. All consumed hashes remain unchanged.

The initial integer box is 364x364 px. Its centre differs from the confirmed seed by just
(-0.09, -0.26) px. Returned box width/height range from **323 to 364 px**, although the
program applies translation only and target appearance/size do not change. Per-axis
seed-relative residual is decomposed as:

`top-left displacement residual + half box-size change + fixed seed-box rounding offset`.

| Observation | Top-left residual (x,y) px | Half-size term (x,y) px | Combined residual (x,y) px | Error norm px | Confidence |
| --- | --- | --- | --- | ---: | ---: |
| First error >3 px, frame 2 / 0.066667 s | (1, 1) | (-3.5, -3.5) | (-2.59, -2.76) | 3.785 | 0.9462 |
| Maximum, frame 6 / 0.200000 s | (4, -8) | (-14, -14) | (-10.09, -22.26) | 24.440 | 0.7401 |
| Final, frame 32 / 1.066667 s | (8, 3) | (-10.5, -10.5) | (-2.59, -7.76) | 8.181 | 0.8329 |

Every emitted post-seed centre equals the independently decomposed returned box centre.
All 32 post-seed frames remain tracked; 16 errors >3 px have confidence >=0.8, reproducing
#105. Both location and extent terms matter. This is an algebraic attribution, not proof
that disabling scale adaptation would repair the location estimate: those states may be
coupled inside CSRT. No internal parameter ablation or source modification was attempted.

The [OpenCV update API](https://docs.opencv.org/4.13.0/d0/d0a/classcv_1_1Tracker.html)
returns a success flag and the likely target box; success is not an independent accuracy
test. Execution still uses the retained 4.12.0 environment, not the documentation's newer
build. The existing NCC-to-seed appearance score can stay high on a wrong returned box;
it remains an algorithm-specific appearance diagnostic, not a calibrated probability.

## Pair: same target pixels and initialization, different background motion

Use the same three confirmed clean/snatch/squat seed PNGs and geometry as #105, without
another source decoder run or segmentation. The foreground is the exact source pixels
inside the confirmed seed-centred disk of radius (seed radius + 8 px). This deterministic
cutout is not a new plate annotation or verified segmentation.

Construct a fixed background by replacing the union of all translated disk footprints
with BGR (128,128,128), preserving the source raster outside that corridor. Paste the disk
at zero shift for the shared initial image. The whole-frame control translates that initial
composite into zero fill; the stationary case moves just the disk over the fixed background.
Both have identical initialization and identical disk pixels at every programmed location.
The erased corridor prevents exposing a duplicate original plate. No inpainting or feathering.

Reuse #105's 33-frame outward/return/hold translation, full 1080x1920 display raster,
lossless FFV1/NUT, checked actual PTS and unchanged CSRT/SAM 2.1 bplus-circle producers.
Only background motion changes within each pair; there is no added blur or occlusion.
The private registration hashes plans, scripts/helpers and inputs before generation/scoring.
Original confirmed geometry is unchanged; only synthetic fixture binding and display rotation
0 are adapted. No annotation-v1 document is produced.

## Displacement, drift and confidence

Each stream has **32/32 tracked post-seed observations**, with no loss or missing observation.
Thus all comparisons use the same exact timestamp support, both across background cases
for a tracker and between trackers within a case. Report seed-relative injected displacement
error, not independent absolute plate-centre error. Initialization is excluded from point
statistics; adjacent diagnostics retain the initialization-to-first edge.

| Lift | Background | CSRT mean / max error px | SAM mean / max error px | CSRT final drift px | SAM final drift px | CSRT / SAM high-confidence errors >3 px |
| --- | --- | --- | --- | ---: | ---: | --- |
| Clean | Moving whole frame | 2.026 / 6.004 | 3.877 / 4.258 | 2.735 | 4.030 | 7 / 31 |
| Clean | Stationary | 2.737 / 4.859 | 3.240 / 4.518 | 2.894 | 3.989 | 12 / 20 |
| Snatch | Moving whole frame | 4.200 / 6.863 | 2.487 / 3.219 | 2.263 | 2.684 | 22 / 2 |
| Snatch | Stationary | 6.477 / 11.318 | 2.463 / 3.794 | 3.696 | 2.680 | 27 / 2 |
| Squat | Moving whole frame | 5.849 / 12.624 | 1.342 / 1.586 | 3.437 | 1.143 | 28 / 0 |
| Squat | Stationary | 10.492 / 15.715 | 1.553 / 3.469 | 5.975 | 1.163 | 18 / 1 |

Confidence >=0.8 is the frozen diagnostic threshold; its meaning is algorithm-specific.
CSRT has **132** errors >3 px across 192 eligible observations, **114** at high confidence.
SAM has **56** errors >3 px, all at high confidence. Those are synthetic repeated-frame
counts, not real-video label accuracy estimates. Full coverage is insufficient for both.

SAM's clean error includes re-centering after initialization: stationary frame 1 already
has 3.871 px residual at confidence 1.0; whole-frame frame 2 has 4.115 px at confidence 1.0.
The seed-relative statistic deliberately preserves these offsets. It does not isolate them
from later drift or establish independent seed/absolute centre truth. Private reports retain
all signed residuals, P90, adjacent errors, confidence/error support and return-hold means.

Within this synthetic construction, CSRT mean error increases from 2.026 to 2.737 px
(clean), 4.200 to 6.477 px (snatch), and 5.849 to 10.492 px (squat) when the background
stops following the target. SAM improves on clean, changes little on snatch and worsens
on squat. Do not generalize three seed appearances to natural lifting footage.

The new whole-frame controls also differ from #105's original full-raster controls because
of the erased corridor and cutout. Differences between the two studies cannot be assigned
solely to background motion. In particular, this does not reverse #105 or validate either
method on the eight real clips. No production fix follows from these responses.

## Repetition, preservation and owner handoff

All six generated containers/frame-hash lists and six CSRT predictions repeat byte-for-byte.
Every decoded generated raster matches its pre-encoding hash, and actual PTS match i/30 s
within one microsecond before scoring stored six-decimal timestamps. Frozen-mask CPU
conversion matches all six original SAM sample/geometry arrays. Repeated scoring yields
eight byte-identical artifacts: six detailed scores, summary and paired background scores.

The second six-case GPU pass reproduces prediction files, mask metadata/hash lists,
sample arrays and geometry samples exactly on the retained RTX 3060 Ti stack. Record that
observed GPU repeatability separately from frozen-input CPU replay; it is not a guarantee
on other GPUs/drivers/package versions. Runtime remains separate. Existing checkpoint,
JPEG-q2, bfloat16, CPU offload, circle gates/centroid fallback and missing `sam2._C` warning
are preserved. No environment or package was installed or changed.

All original consumed manifest/media/seed/retained-prediction/checkpoint and seed-raster
hashes match after execution. Earlier artifacts, raw observations, canonical analyses,
held-out footage and annotation packages are untouched. No producer fix was warranted,
so no affected real-video tracking rerun was needed. #103 cleanup/retry behavior remains
unchanged; its six regression tests pass with the full research suite.

Owner artifacts are in the main project checkout:

- `validation/private/csrt-box-trace-2026-10-05/`: raw valid boxes, decomposition, matched
  predictions, hashes, repeat and a portable read-only trace replay.
- `validation/private/stationary-background-2026-10-05/index.html`: six separate error plots,
  generated initial/background/mask images, lossless sequences, manifests/seeds, frozen
  masks/JPEGs, raw predictions, geometry, all-support and paired scores, source snapshots,
  environment/checkpoint/hash provenance and portable read-only score replay.

Verification: **91 research tests** with warnings as errors, **211 validation tests**,
**12 committed schema documents**, strict contracts for twelve original and twelve repeat
prediction streams, independent helper review, and clean diff whitespace. Three new focused
tests add decomposition/loss/finite/input-preservation checks and foreground identity,
unchanged initialization, fixed background, ghost removal and margin checks. Existing PTS,
non-wrapped transformation and scoring tests remain unchanged in behavior.

The hard disk boundary, static copied sleeve/background pixels and uniform corridor are
artificial cues. This establishes response to a programmed cutout/background transformation,
not natural bar movement, independent absolute plate-centre or physical velocity accuracy.
Neither study closes #57's freeze/held-out gates or justifies fusion, a detector or M1.
Stop here with the declared failure evidence and unchanged producers.
