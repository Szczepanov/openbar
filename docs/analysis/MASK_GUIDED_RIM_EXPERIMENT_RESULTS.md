# Mask-guided rim sampling: development result

Date: 2026-10-05. Implements the one-configuration
[mask-guided experiment plan](../plans/MASK_GUIDED_RIM_EXPERIMENT_PLAN.md), following the
[radial failure analysis](M0_RADIAL_GEOMETRY_FAILURE_ANALYSIS.md).

## Decision

**REJECT this configuration for complete-pipeline selection.** Constraining luminance edge
selection to the existing SAM mask boundary does not recover squat coverage, makes only a small
clean coverage change, and supplies essentially no displacement evidence. It is not a candidate
for freeze, fusion or production integration. Stop this bounded experiment without retuning the
band or relaxing the gates after seeing the results.

The result does not reject all possible mask-guided or shape-aware methods. It establishes that
this specific attempt is insufficient on the available development footage. Further unbounded
tuning would not resolve the missing independent rim/annotation evidence.

## Method and retained evidence

The configuration was recorded in a hashed local `registration.json` before mask production and
evaluation. Use only the existing labelled squat, clean and snatch development clips. Reuse the
owner-confirmed seeds, historical SAM small-circle coarse centres, verified SAM 2.1 small
checkpoint, and existing `track_gpu.sam2_masks` helper. No held-out footage was processed.

Retain the largest connected component of each valid SAM mask. Compute exact L2 distance to the
nearest opposite mask-class pixel centre, then bilinearly sample that distance along the existing
radial grid. Eligible gradient samples must lie within **4.0 px**. Select the strongest eligible
gradient; keep all existing intensity, circle, residual, support and angular gates unchanged.
Eligibility is checked before the original subpixel peak refinement. There is no fallback to a
mask centre, no measurement from the mask alone, and no interpolation through loss.

The local prototype reuses the original `vision.radial_center` source body, replacing only the
gradient candidate-selection block. Both the generated source and its hash are retained. The
public estimator, runner, predictions, defaults and canonical schemas remain unchanged.

Image measurement uses the pinned OpenCV 4.12.0 / NumPy 2.2.6 environment and fully validates the
three source clips through the existing FFmpeg decoder. The GPU mask environment is separately
recorded: PyTorch 2.6.0+cu124, OpenCV 5.0.0 and NumPy 2.4.6 on the existing RTX 3060 Ti. The model
uses the existing JPEG-q2 frame cache, CPU-offloaded frames and bfloat16 autocast. Its optional
`sam2._C` extension is absent; the producer warns that hole-filling post-processing is skipped.
No package or environment was changed to address that warning.

The 267 current SAM small-circle prediction samples regenerated from these masks match the
historical samples **exactly**, including states, coordinates, timestamps and confidence. This
checks the comparator in this run; it is not proof that every future GPU mask run is deterministic.

All private evidence remains under `validation/private/mask-guided-rim/`: the registration, local
export/measurement scripts, generated estimator source, 267 mask PNGs, JPEG cache, mask metadata,
strict prediction streams, raw diagnostics, comparisons, crop grids and separate GPU timing.
Metadata records input/checkpoint/source hashes and producer/measurement environments. A separate
`producer-environment-notes.json` records the unavailable extension and hashes of the installed
SAM Python source files. No private image, mask or coordinate is committed.

## Coverage and error

Every table excludes the manual seed. Frame counts cover all producer observations; labelled
counts cover sparse manual annotations. These are development diagnostics, not new canonical
benchmark gates or dense frame-accuracy measurements.

| Lift | Default radial accepted frames | Mask-guided accepted frames | Default radial labelled coverage | Mask-guided labelled coverage | Mask-guided centre MAE px |
| --- | ---: | ---: | ---: | ---: | ---: |
| Squat | 0/72 | 0/72 | 0/24 | 0/24 | unsupported |
| Clean | 6/60 | 8/60 | 1/20 | 2/20 | 3.283 |
| Snatch | 4/132 | 4/132 | 1/22 | 1/22 | 2.803 |

The historical and regenerated SAM small-circle comparators remain tracked on every frame and
label. Their all-label centre MAEs are 1.922 px (squat), 4.338 px (clean) and 3.961 px (snatch).
Those full-support averages cannot be compared fairly with the mask-guided surviving subset.

### Comparisons on the same labelled support

| Lift | Shared labels with SAM small-circle | Mask-guided centre MAE px | SAM small-circle centre MAE px on those labels |
| --- | ---: | ---: | ---: |
| Squat | 0 | unsupported | unsupported |
| Clean | 2 | 3.283 | 2.545 |
| Snatch | 1 | 2.803 | 1.228 |

On the single shared label with default radial in each fast lift, mask guidance changes centre
error from 2.990 to 2.916 px (clean), and 2.829 to 2.803 px (snatch). These tiny subsets cannot
establish improvement; the new clean label also makes the overall surviving subsets different.

### Displacement evidence

| Lift | Usable mask-guided labelled intervals | Mask-guided delta MAE px | SAM small-circle delta MAE px on the same intervals |
| --- | ---: | ---: | ---: |
| Squat | 0/23 | unsupported | unsupported |
| Clean | 1/19 | 3.486 | 3.507 |
| Snatch | 0/21 | unsupported | unsupported |

Consecutive label intervals are scored only when every intervening predicted observation is
tracked and gaps stay within 0.2 s. The only usable clean interval gives a 0.020 px difference,
which provides no credible cross-lift motion improvement. No physical velocity claim follows.

## Rejection and confidence diagnostics

| Lift | Insufficient edge support | Geometry gate | Accepted |
| --- | ---: | ---: | ---: |
| Squat | 70 | 2 | 0 |
| Clean | 44 | 8 | 8 |
| Snatch | 5 | 123 | 4 |

Boundary conditioning reduces some distractor selections, but most squat/clean frames cannot
collect enough contrast/gradient-qualified rim edges under the unchanged gates. The crop grids
show this remaining shortage after conditioning. The experiment does not independently isolate
blur, exposure, shape mismatch or segmentation error as its physical cause.

Mask-guided tracker confidence remains the existing algorithm-specific support/RMS score, not a
calibrated probability or the SAM mask score. None of its three scored label observations reaches
the 0.8 high-confidence threshold. Consequently, zero high-confidence false tracks is **not**
evidence of safety. One of the two clean observations exceeds 3 px error. The original SAM
small-circle stream has 4/11/11 high-confidence false tracks on squat/clean/snatch respectively;
its availability does not establish complete-candidate eligibility either.

## Verification and next action

The local runnable check covers a true circular edge with a stronger competing outer ring,
equivalence with the original estimator when all samples are eligible, absent/weak image evidence,
empty/full/invalid masks, exact opposite-class distance semantics and input preservation.
All new prediction streams pass the existing strict schema and finite/timestamp/loss checks.
The unchanged public research suite passes 79 tests with warnings treated as errors.

CPU measurement repeated from the same frozen masks produces byte-identical predictions,
diagnostics, comparison JSON and crop grids. This tests frozen-input CPU reproducibility; the
GPU producer was run once, and no GPU repeatability or full-phone runtime claim is made.

Run from this worktree with the existing owner environments and retained private inputs:

```powershell
& C:/Users/mdszc/Downloads/projekty/openbar/research/gpu-tracking/.venv/Scripts/python.exe validation/private/mask-guided-rim/export_masks.py
& C:/Users/mdszc/Downloads/projekty/openbar/research/opencv-tracking/.venv/Scripts/python.exe validation/private/mask-guided-rim/measure.py
```

The exporter retains existing frozen masks rather than overwriting them. The measurement script
checks the registration and input hashes before replay. The prototype is a retained local research
artifact, not a new shipped tool.

Prioritize dense labels and a blind repeat annotation pass on selected windows of **existing**
development footage when the owner is available; no new video is required for that step. Marker
and independent physical-reference capture can wait until recording is possible. Preserve the
six held-out clips, current gates and production boundaries. This result does not justify a new
detector, an ellipse implementation, a production tracker/filter choice, or a move to M1.

### Prepared owner annotation handoff

The existing annotation-package tool has prepared six independent pages: two blind passes over
24 frames per development clip, **72 unique frames and 144 centre assessments**. Each clip has
two disjoint windows of 12 consecutive decoded frames, separated by more than 0.2 s. Select the
first by minimum historical SAM coarse-centre bounding span, excluding the seed; select the
second by maximum endpoint displacement among separated windows, with earliest ties. No manual
label residual enters selection. Both passes show the same raw full-raster images, with separate
package/storage IDs and no prefilled coordinates or radius.

The owner copy is in the main project folder at
`validation/private/annotations/work/dense-rework-2026-10-05/index.html`. Its adjacent README
contains the six import commands and three repeatability commands. Complete pass A, take a break
(ideally another day), then complete pass B without consulting A exports, earlier annotations or
tracker overlays. Enter the actual annotation date/time before import. Imported annotations and
repeatability reports use new private paths; original annotations and seeds are preserved.

The private inventory records selected frame indices, timestamps and image hashes. All six pages
were checked in the browser: 24 correctly sized images and CSV rows each, six distinct package IDs,
zero placed centres/assessments and no aiming-ring radius. The delivery copy matches the source
frame/page bytes. Existing annotation-tool tests cover export/import and independent pass storage.
The full stdlib validation suite passes 211 tests; the committed-fixture schema check passes all
12 mapped documents, and `git diff --check` is clean.

These are **prepared packages, not completed human annotations**. Lower-motion selection does
not establish stationarity (the clean window still moves substantially); assess intervals within
each window separately. Repeat annotation can estimate centre-placement disagreement but cannot
supply independent rim truth, eliminate shared annotator bias or validate physical velocity.
