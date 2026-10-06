# Controlled-motion replication on current main

2026-10-06. The requested starting context ended at #104; safely fetched main already
included #105's controlled-motion experiment and #106's box/background investigation.
Run one exact nine-case replication under the separately
[frozen replication plan](../plans/CONTROLLED_MOTION_REPLICATION_PLAN.md), without expanding
the motion program or searching parameters. No human task was required.

## Result

All nine score reports and the aggregate summary match retained #105 **byte-for-byte**.
The negative CSRT result persists with unchanged helpers: complete reported coverage
coexists with large injected-displacement error and high appearance confidence. SAM
responds more accurately to this particular whole-frame program, while retaining its
one coordinate-free loss. No implementation defect or producer fix is established.
Stop after the declared replication; no production tracker/filter is selected.

Each method has 32 eligible post-seed observations per case. The following are
seed-relative injected-displacement errors, not independently labelled centre errors.

| Source | Case | CSRT / SAM tracked | CSRT mean / max error px | SAM mean / max error px |
| --- | --- | ---: | ---: | ---: |
| Clean | Translation | 32 / 32 | 1.640 / 4.521 | 1.095 / 1.499 |
| Clean | Fixed blur | 32 / 32 | 2.027 / 3.600 | 1.397 / 1.871 |
| Clean | Brief occlusion | 32 / 32 | 6.824 / 42.973 | 1.290 / 3.377 |
| Snatch | Translation | 32 / 32 | 3.733 / 9.280 | 1.209 / 1.456 |
| Snatch | Fixed blur | 32 / 32 | 3.085 / 5.059 | 0.806 / 0.930 |
| Snatch | Brief occlusion | 32 / 32 | 6.396 / 24.162 | 1.296 / 2.230 |
| Squat | Translation | 32 / 32 | 13.866 / 24.440 | 1.899 / 2.099 |
| Squat | Fixed blur | 32 / 32 | 9.006 / 18.448 | 1.674 / 1.896 |
| Squat | Brief occlusion | 32 / 31 | 24.244 / 71.798 | 1.762 / 2.099 |

Exact common tracked support is full except squat occlusion: on 31 shared timestamps,
CSRT mean error is 23.085 px and SAM 1.762 px. SAM loses frame 13; no observations
are missing and no loss is interpolated. The unpaired CSRT failure remains in its own
all-support statistics.

CSRT tracks 288/288 post-seed observations, with 181 errors >3 px and **121** at
algorithm-specific confidence >=0.8. SAM tracks 287/288, with one error >3 px at
confidence 0.6289 and zero at >=0.8. CSRT's NCC appearance score and SAM's mask/geometry
score are not comparable calibrated probabilities. Per-frame errors and wrong-displacement
confidence ranges, P90, adjacent supported errors and signed drift remain private.

After returning to zero translation, CSRT final drift spans 0.347–18.448 px; SAM spans
0.835–1.836 px. For the occlusion cases, final drift is 1.312 / 1.302 px (clean),
1.265 / 1.298 px (snatch), and 16.430 / 1.809 px (squat), CSRT / SAM respectively.
Three-consecutive-visible-frame recovery to <=3 px occurs at 0.300 s for CSRT clean,
0.400 s for snatch, and never in the declared squat tail. SAM meets that criterion at
the first visible PTS for all three. CSRT stays tracked through all twelve occluded
observations despite 22.144–71.798 px error. These states are retained as failures.

## Execution and checks

Plan commit `86164dc` precedes generation/scoring; private registration records its hash,
current helper/harness hashes, original manifest/media/seed/prediction/checkpoint hashes,
decoder versions and thread settings. The existing corrected #105 private harness is
reused with only its plan path redirected. Confirmed frame 0 geometry from the same
development clean, snatch and squat clips is unchanged. Three original videos pass full
decoder drain/count/exit and display rotation checks on both generation passes.

Nine 33-frame full 1080x1920 display-raster sequences follow the original outward/return/hold
program, fixed 15x15 Gaussian sigma-3 blur case and four-frame opaque occlusion case.
Non-wrapped copying preserves coordinates and target margins. Every decoded generated
raster matches its pre-encoding hash; actual PTS match programmed i/30 s within one
microsecond before scoring verified six-decimal timestamps. Synthetic seeds adapt only
fixture binding/display rotation; references remain injected programs, never annotations.

Unchanged top-level CSRT and SAM 2.1 bplus-circle producers use the existing OpenCV
4.12.0 / NumPy 2.2.6 CPU environment and PyTorch 2.6.0+cu124 / OpenCV 5.0.0 /
NumPy 2.4.6 RTX 3060 Ti stack. The existing hash-verified bplus checkpoint, JPEG-q2
cache, bfloat16, CPU offload, circle gates and centroid fallback remain intact. The
missing `sam2._C` warning remains recorded. Nothing is installed or repaired.

Generation and CSRT repeat byte-for-byte. Frozen-mask CPU circle conversion reproduces
all nine sample/geometry arrays. Two score passes reproduce ten artifacts byte-for-byte.
The second GPU pass reproduces prediction bytes, masks/metadata and geometry arrays on
this stack. Generated references, samples, masks, geometry and scores also match retained
#105. Runtime and allocator/provenance fields are assessed separately from measurement
equality; observed GPU repeatability is not a guarantee across GPU/driver/package changes.

Existing runnable checks are reused: **91 research tests** with warnings as errors,
**211 validation tests**, **12 schema documents**, **3 CSRT output tests** and **7 SAM
output tests** pass. The controlled-motion tests cover sign/copy preservation, target
margins, authoritative PTS, finite values, coordinate-free loss, high-confidence wrong
displacement, recovery, common support and unchanged scorer inputs. All 36 original/repeat
prediction streams pass strict existing contracts. #103's six batch regressions, including
adjusted partial-destination cleanup/retry, pass without edits. No Rust source, dependency,
canonical schema or production setting changed; Rust gates are outside this documentation
replication's affected scope.

A pre-scoring private preservation-check correction compares file membership rather than
Windows Path order against JSON key order. Its frozen original and hash-linked amendment
are retained. It changes no input, producer, transformation or metric. All **2,487 historical
files** and original consumed hashes match after completion. Existing raw streams,
canonical analyses and prior reports are preserved; held-out clips are not accessed.
No producer fix was warranted, so no additional real-video tracking replay was introduced.

## Handoff and limits

The verified owner copy is `validation/private/controlled-motion-replication-2026-10-06/index.html`
in the main project folder. It retains lossless sequences, source seed rasters, manifests/seeds,
raw original/repeat predictions, geometry, masks/JPEGs, per-frame scores/plots, registrations,
source snapshots, logs, preservation/replication evidence and portable stdlib score replay.
Private coordinates, media and checkpoint contents stay out of Git.

Whole-frame translation moves the background too; it does not reproduce bar motion against
a stationary background. The moving opaque rectangle/context may support a tracker during
occlusion without visible plate evidence. Static seed appearance, fixed blur and synthetic
timing exclude real perspective, rim rotation, exposure and motion-blur dynamics. These
injected displacements establish neither independent absolute plate-centre nor physical
velocity truth, resolve the eight real clips' disagreement, or close held-out/freeze gates.
The [earlier box/background investigation](CSRT_BACKGROUND_MOTION_RESULTS.md) remains
separate evidence; it is not rerun or extended here. Stop with the retained limitations.
