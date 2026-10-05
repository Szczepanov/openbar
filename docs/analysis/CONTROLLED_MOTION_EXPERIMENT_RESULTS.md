# Controlled whole-frame motion: existing development seed frames

Date: 2026-10-05. Implements the [frozen plan](../plans/CONTROLLED_MOTION_EXPERIMENT_PLAN.md)
after #102–#104 without new recordings, labels, seed placement or owner review.

## Decision

Retain a negative result for unchanged CSRT: complete reported coverage can coexist with
large injected-displacement error and high appearance confidence, even with static source
appearance and no added blur or occlusion. SAM 2.1 bplus-circle responds more accurately to
this specific whole-frame motion program. This does **not** select a production tracker,
validate real plate motion or establish physical velocity accuracy. Stop this bounded
experiment; no producer implementation defect was demonstrated and no tracker fix is made.

## Frozen inputs and method

Use confirmed frame 0 seeds from the audited clean, snatch and squat development clips.
The plan was committed at `061efa0` before generation/scoring; its SHA-256, consumed input
hashes and exact helper/script hashes were registered privately before any score.
Only these three source videos were decoded; all three passed their original media hash,
display-raster/rotation, frame-count and decoder-exit contracts. Confirmed geometry is
unchanged. Private synthetic seed documents bind it to generated fixture IDs and rotation 0
because the source raster is already display-oriented. They are not new human assessments.

Each of nine cases has 33 full-resolution 1080x1920 frames. Program integer motion from
(0, 0) to (64, -32) px, return to (0, 0), then hold for eight frames. Translate by copying
overlap into zero fill, without wrapping or clipping the seed disk plus its 8 px margin.
Separate cases add fixed Gaussian blur (15x15, sigma 3 px, including initialization) or a
four-frame moving opaque rectangle over the target (frames 12..15). No other factor changes.
Lossless FFV1/NUT replay preserves every programmed BGR pixel. Every generated decoded frame
matches its pre-encoding hash, and actual probed PTS match the declared i/30 s program within
one microsecond. Scoring uses stored six-decimal PTS after that verification, never nominal
FPS inferred from real recordings.

Reuse the unchanged top-level `track.track` and `track_gpu.track` producers. CSRT uses the
existing OpenCV 4.12.0 / NumPy 2.2.6 environment; SAM uses existing PyTorch 2.6.0+cu124,
OpenCV 5.0.0 / NumPy 2.4.6 on RTX 3060 Ti, the hash-verified bplus checkpoint, JPEG-q2 cache,
box plus positive centre point, bfloat16, CPU frame offload and original circle/centroid
fallback gates. Only model/cache paths are redirected. An observational mask wrapper retains
unchanged masks and JPEGs for CPU replay; it returns the original producer result.
The existing missing `sam2._C` post-processing warning is retained, with no environment repair.
Installed SAM Python source hashes and dependency/checkpoint provenance accompany the artifacts.

Error is the norm of predicted displacement from the emitted confirmed initialization centre
minus the programmed displacement. It includes any re-centering after initialization; it is
not independent absolute plate-centre error. Initialization is excluded from point statistics.
Adjacent-edge diagnostics include the seed-to-first edge, use only consecutive tracked
observations and actual gaps <=0.2 s, and never bridge loss.

## Displacement and support

Each method has 32 eligible post-seed observations per case. P90 uses nearest rank. All
per-frame residuals, confidence, P90, adjacent errors and drift vectors remain private.

| Source lift | Case | CSRT tracked | SAM tracked | CSRT mean error px | SAM mean error px | CSRT max error px | SAM max error px |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Clean | Translation | 32 | 32 | 1.640 | 1.095 | 4.521 | 1.499 |
| Clean | Blur | 32 | 32 | 2.027 | 1.397 | 3.600 | 1.871 |
| Clean | Occlusion | 32 | 32 | 6.824 | 1.290 | 42.973 | 3.377 |
| Snatch | Translation | 32 | 32 | 3.733 | 1.209 | 9.280 | 1.456 |
| Snatch | Blur | 32 | 32 | 3.085 | 0.806 | 5.059 | 0.930 |
| Snatch | Occlusion | 32 | 32 | 6.396 | 1.296 | 24.162 | 2.230 |
| Squat | Translation | 32 | 32 | 13.866 | 1.899 | 24.440 | 2.099 |
| Squat | Blur | 32 | 32 | 9.006 | 1.674 | 18.448 | 1.896 |
| Squat | Occlusion | 32 | 31 | 24.244 | 1.762 | 71.798 | 2.099 |

Common tracked support equals full support except squat occlusion. On its 31 shared frames,
CSRT mean error is **23.085 px**, SAM **1.762 px**; the omitted CSRT observation is retained
in its all-support metrics. SAM declares loss at frame 13 with no coordinate or confidence.
No observations are missing. Coverage comparisons never silently discard that loss.

Fixed blur worsens clean error but reduces snatch/squat averages in this experiment. Thus
blur alone does not explain the original real-video disagreement. CSRT's large squat error
already occurs in pure translation; this isolates an algorithm response limitation without
identifying a specific internal mechanism. The single retained seed texture per lift is not
a population sample, and no extra blur/motion configuration was tried after scoring.

## Drift, confidence and occlusion recovery

Final drift is the displacement-error norm at frame 32 after return to zero. The private
reports also retain its signed vector and the eight-frame return-hold mean.

| Source lift | Case | CSRT final drift px | SAM final drift px | CSRT errors >3 px with confidence >=0.8 | SAM errors >3 px with confidence >=0.8 |
| --- | --- | ---: | ---: | ---: | ---: |
| Clean | Translation | 1.741 | 1.390 | 7 | 0 |
| Clean | Blur | 1.973 | 1.826 | 3 | 0 |
| Clean | Occlusion | 1.312 | 1.302 | 7 | 0 |
| Snatch | Translation | 0.347 | 1.291 | 22 | 0 |
| Snatch | Blur | 2.762 | 0.835 | 20 | 0 |
| Snatch | Occlusion | 1.265 | 1.298 | 21 | 0 |
| Squat | Translation | 8.181 | 1.836 | 16 | 0 |
| Squat | Blur | 18.448 | 1.796 | 22 | 0 |
| Squat | Occlusion | 16.430 | 1.809 | 3 | 0 |

Across the nine cases CSRT tracks **288/288**, with **181** displacement errors >3 px,
**121** at confidence >=0.8. SAM tracks **287/288**, with one error >3 px at confidence
0.6289 and none at >=0.8. These are repeated synthetic-frame diagnostics, not independent
real-video label accuracy counts. CSRT's NCC-to-seed score and SAM's object-score/geometry
fallback score remain algorithm-specific and are not comparable calibrated probabilities.

CSRT remains tracked through all twelve occluded observations, with errors spanning
22.144–71.798 px. SAM tracks eleven of twelve, with errors spanning 0.580–3.377 px.
The rectangle itself moves with the programmed target, and background motion supplies
context. SAM's low residual during opaque occlusion therefore does not establish visible
plate identity or appropriate loss behavior on real occlusion.

Recovery requires three consecutive visible frames with error <=3 px, measured from the
first visible PTS (frame 16). CSRT recovers at frame 25 / 0.300 s for clean and frame 28 /
0.400 s for snatch; squat never recovers within the declared tail. SAM meets the criterion
at frame 16 / 0 s for all three. Loss and wrong-but-tracked states are retained separately.

## Reproducibility, preservation and handoff

All nine generated lossless containers and frame-hash lists reproduce byte-for-byte.
All nine CSRT prediction files reproduce byte-for-byte in the same CPU stack. Frozen-mask
CPU circle conversion reproduces all nine original SAM sample and geometry arrays exactly.
Repeated scoring produces ten byte-identical artifacts (nine detailed reports and summary).

A second GPU pass over the same nine inputs reproduces all prediction files, sample arrays,
geometry samples and mask metadata/hash lists exactly on this one stack. This is observed
GPU repeatability, separately recorded from frozen-input CPU reproducibility; it is not a
guarantee across GPU/driver/package changes. Runtime is retained separately from predictions.

The first generation-repeat assertion exposed a private harness comparison mistake:
in-memory tuples were compared directly to JSON lists. The correction normalizes that
comparison through JSON. The original frozen script/registration and a hash-linked amendment
are retained; no plan, transformation, media, PTS, metric or producer setting changed.
The subsequent full generation repeat passes. This is a harness correction, not a CSRT/SAM
implementation defect or reason to change real-video tracking.

Original consumed media, manifest, seeds, retained SAM predictions and checkpoint hashes
match after completion. Existing raw predictions/canonical analyses, held-out clips and
annotation pages remain untouched. No fix to either producer was justified, so no additional
real-video tracker pass or new canonical measurement run was introduced. The three source
decoder checks did fully drain the existing real recordings. #103's adjusted cleanup/retry
behavior is unchanged, and its six existing regression tests pass in the full research suite.

The owner handoff is `validation/private/controlled-motion-2026-10-05/index.html` in the
main project checkout. It contains nine separate error plots, raw prediction/geometry
streams, synthetic manifests/seeds, lossless sequences, frozen masks/JPEGs, per-frame
scores, hashes, source snapshots, logs, repeatability evidence and a portable stdlib score
replay. All private media, coordinates and checkpoints stay out of Git.

Verification: **88 research tests** with warnings as errors, **211 validation tests**, all
**12 committed schema documents**, strict validation of eighteen original and eighteen
repeat prediction streams, and clean diff whitespace. Three new focused tests cover
transformation/copy/sign/margins, PTS binding, finite-value rejection, loss, high-confidence
wrong displacement, recovery/common support and input preservation. No Rust source,
dependency, canonical schema or production configuration changed.

Whole-frame translation moves the background too. This controlled static appearance does
not reproduce bar motion against a stationary background, changing perspective, rim rotation,
exposure or real motion blur. The injected reference cannot establish independent absolute
plate-centre accuracy or physical velocity truth, resolve the eight real clips' disagreement,
close #57 held-out/freeze gates or justify M1. Stop here with the retained failure evidence.
