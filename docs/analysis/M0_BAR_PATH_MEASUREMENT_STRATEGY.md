# M0 bar-path measurement strategy

Status: analysis / research direction  
Related: #57, #53, #59, ADR-0004, ADR-0008, ADR-0009, research/PLATE_TRACKING_PLAN.md  
Execution plan: [M0_BAR_PATH_MEASUREMENT_EXPERIMENT_PLAN.md](../plans/M0_BAR_PATH_MEASUREMENT_EXPERIMENT_PLAN.md)

## 1. Purpose

This document synthesizes the current OpenBar tracker evidence, recent VBT workflow evidence, public
barbell-tracking implementations, direct-displacement VBT systems, and relevant computer-vision
literature into a focused answer to one question:

> For M0, what is the most accurate and computationally efficient way to estimate barbell motion
> from ordinary side-view phone video?

The conclusion is not "find a better generic object tracker". The stronger formulation is:

> Treat the visible plate as a known rigid geometric target attached to the bar, separate coarse
> localization from precision measurement, and evaluate relative displacement as a first-class
> observable alongside absolute plate-centre position.

This is a research direction, not a production architecture decision. The current canonical
measurement contracts, provisional M0 gates, manual seed, PlateDiameterCalibration@1, timestamp
semantics, loss semantics, and production scope remain unchanged until evidence justifies a
separate promotion decision.

## 2. Repository cross-check

### 2.1 What OpenBar already proves

The repository already has substantially more evidence than a tracker-selection discussion alone
suggests:

- research/PLATE_TRACKING_PLAN.md has compared classical box trackers, optical flow, neural point
  trackers, SAM 2.1, Cutie, BootsTAPIR and CoTracker3;
- docs/plans/PLATE_GEOMETRY_REFINEMENT_PLAN.md already tested a CSRT-ROI circle-refinement family;
- docs/plans/TRACKER_BAKEOFF_PLAN.md defines the broader tracker comparison protocol;
- docs/analysis/VBT_REFERENCE_EXPERIMENTS_FINDINGS.md shows that aggressive smoothing can improve
  synthetic velocity RMSE while materially attenuating true peaks, and that visible plate diameter
  changes by several percent in real footage;
- docs/validation/M0_REFERENCE_STUDY.md already defines the independent physical/reference contract
  for ROM and velocity;
- research/vbt-workflow and #95 provide a practical personal-session workflow and development
  evidence, including a filmed stick scale and WL Analysis comparisons.

Therefore the next useful experiment should not duplicate the existing tracker zoo or replace the
existing validation system.

### 2.2 Current tracker evidence

On the current development clips, generic/traditional tracking has reached a plateau:

- CSRT remains robust but its seed-excluded centre MAE is approximately 3.2 px on squat, 6.4 px on
  clean & jerk, and 7.5 px on snatch;
- the earlier Canny/RANSAC circle refinement materially improved the snatch but was inconsistent
  across lifts and tracker re-initialization created feedback lock-in;
- SAM 2.1 circle candidates improve the development results substantially and preserve availability,
  but still miss the provisional 3 px gate on fast lifts and their confidence does not discriminate
  positional error well;
- the SAM error diagnosis already indicates that a meaningful fraction of error is a shared,
  motion-linked offset rather than unstructured scatter.

This last point matters. A stable spatial bias and frame-to-frame jitter have very different effects
on velocity.

### 2.3 Current kinematic/reference status

Issue state alone is not sufficient evidence. At the time of this analysis GitHub shows #58 closed,
while docs/validation/M0_REFERENCE_STUDY.md still states that the repository contains no qualifying
independent physical/reference dataset. The authoritative conclusion therefore remains: the ROM,
mean-velocity and peak-velocity gates are not established by an independent physical reference.

The #95 personal-session results and WL Analysis agreement are useful development evidence, but WL
Analysis is not independent physical ground truth.

## 3. The measurement problem is not generic object tracking

A generic tracker asks:

> Where is an object or bounding box in the next frame?

M0 ultimately needs:

> What was the displacement of the physical bar axis between authoritative timestamps?

A detector, segmentation model or bounding-box tracker can be excellent at the first objective
while still producing enough frame-to-frame localization jitter to damage the second.

The plate gives OpenBar unusually strong priors:

- it is rigid;
- a standard bumper plate has a known physical diameter for calibration;
- its physical centre is mechanically tied to the bar axis;
- its boundary is circular in its own plane;
- under perspective a circle projects to a conic/ellipse;
- apparent size and ellipticity are measurable diagnostics;
- motion is temporally continuous;
- the current M0 camera is fixed;
- M0 analysis is offline, so future frames are available to the estimator.

The preferred research architecture should exploit these constraints instead of asking a generic
model to solve a harder problem than necessary.

## 4. Absolute position and relative displacement are different observables

Let the true image-space bar centre be p(t), and suppose an estimator has a nearly constant offset b:

    p_hat(t) = p(t) + b

Absolute centre error includes b, but consecutive displacement does not:

    p_hat(t2) - p_hat(t1) = p(t2) - p(t1)

This does not make absolute accuracy irrelevant. Absolute anchors are required to prevent drift,
support overlays, detect failure and preserve the current M0 plate-centre gate. It does mean that
centre MAE alone does not completely characterize usefulness for kinematics.

The opposite failure mode is temporal jitter. For a 450 mm plate spanning 180 px:

    scale = 0.45 / 180 = 0.0025 m/px

At 60 fps, a one-pixel error in a single frame-to-frame displacement corresponds to:

    0.0025 m * 60 s^-1 = 0.15 m/s

If adjacent frames each contain independent 1 px position noise, the difference noise has standard
deviation sqrt(2) px, or approximately 0.21 m/s at this scale and frame rate. Even at 30 fps the
same approximation is about 0.11 m/s.

Those values are on the order of, or larger than, the provisional M0 peak-velocity error target.
This is why a visually stable path and low average centre MAE can still produce poor velocity.

### Consequence

Research benchmarking should retain the current absolute-centre metrics and add diagnostic metrics
for:

- per-axis consecutive-displacement error;
- trajectory error after removing a constant per-clip XY offset;
- cumulative relative-displacement drift;
- static or near-static temporal jitter;
- velocity RMSE/bias on definition-matched intervals;
- downstream mean and peak velocity error.

Adding research diagnostics does not revise the normative M0 gates. A gate change requires an
explicit validation/ADR decision.

## 5. Proposed estimator decomposition

The central proposal is to separate three jobs.

### 5.1 Coarse localization

Purpose: keep the plate inside a small region of interest and reacquire it after a failure.

Accuracy requirement: modest. The coarse estimate must contain the target; it is not the final
measurement.

Candidate sources include:

- the existing manual seed plus a motion prediction;
- CSRT;
- SAM 2.1 as a research reference;
- a future small detector/segmenter;
- YOLO26 research variants if their training/licensing cost is justified.

### 5.2 Precision absolute measurement

Purpose: estimate the plate/bar-axis location from raw pixels in the localized ROI.

A promising plate-specific family is:

1. predict a centre/radius search region;
2. sample image gradients along many radial rays near the expected rim;
3. find plausible rim transitions with sub-pixel edge localization;
4. reject weak or inconsistent rays;
5. fit circle/ellipse geometry robustly;
6. refine the centre from inliers;
7. emit fit residual, arc coverage, radius/axes and edge strength as diagnostics.

This differs from the rejected Phase-2 geometry experiment in an important way. The prior experiment
was a refinement attached to a CSRT box and, in some variants, fed the fitted geometry back into
CSRT. The proposed experiment treats the coarse tracker only as an ROI provider. Precision
measurement is independently estimated from raw ROI evidence, and no geometric refinement is fed
back into the coarse tracker unless a later experiment separately proves that safe.

Radial-symmetry methods are also worth a development spike. Parthasarathy (2012) describes an
analytic, non-iterative sub-pixel centre estimator for radially symmetric image distributions with
near-theoretical localization accuracy and much lower compute than iterative Gaussian fitting. The
method is not plate-specific evidence by itself; it motivates a cheap candidate.

Source:
- https://doi.org/10.1038/nmeth.2071

### 5.3 Precision relative displacement

Purpose: estimate how much the plate ROI moved from one frame to the next, independently of the
absolute centre estimate.

Candidate methods:

- phase correlation;
- localized DFT registration;
- ECC/image alignment where its assumptions fit;
- restricted translation/affine registration on a plate-centred annulus or texture region.

Guizar-Sicairos, Thurman and Fienup (2008) demonstrate efficient sub-pixel 2D image registration
using local matrix-multiply DFT refinement instead of globally upsampling the Fourier transform.

Source:
- https://doi.org/10.1364/OL.33.000156

The purpose is not to accumulate relative motion forever. Relative observations provide high
precision locally; absolute geometric anchors prevent drift.

## 6. Fuse the evidence over the whole offline clip

M0 does not need to behave like a causal real-time tracker. The entire clip is available.

Represent each frame centre C_t as an unknown. Two evidence families constrain it:

    absolute: C_t ~= G_t
    relative: C_t - C_(t-1) ~= D_t

where G_t is the geometry-derived absolute observation and D_t is the image-registration
displacement.

A robust weighted least-squares or equivalent deterministic trajectory optimizer can solve all
frames jointly. Observation weights should come from documented measurable diagnostics rather than
a fabricated universal probability, for example:

- edge/radial support;
- circle/ellipse residual;
- visible arc coverage;
- registration peak quality;
- forward/backward registration consistency;
- agreement between absolute and relative evidence.

The solver must preserve explicit loss. A frame with insufficient evidence remains lost rather than
being filled simply because a smooth trajectory looks plausible.

Initial research output should still be tracker-prediction-v1 so the existing
analyze --observations path remains authoritative for calibration, filtering and kinematics.

## 7. Geometry caveats

### 7.1 Ellipse centre is not automatically the physical circle-centre projection

Under perspective projection, the geometric centre of the observed ellipse can differ from the
image projection of the physical circle centre. Recent optical-metrology literature describes this
as circular-target eccentricity and shows that it can create systematic error.

Source:
- https://doi.org/10.3390/metrology6020028

Therefore a future ellipse fit should retain axes/orientation/residual and should not silently claim
that ellipse centre equals bar-axis projection under arbitrary camera pose. M0's near-side-view,
fixed-camera envelope reduces the problem but does not prove it negligible.

Perspective/eccentricity correction is a later controlled experiment, not a prerequisite for the
first plate-metrology spike.

### 7.2 Motion blur

Fast bar motion changes the actual edge image during exposure. A free circle/ellipse fit can mistake
motion blur for geometry. Candidate diagnostics should therefore record local edge sharpness and
motion direction. A later development variant may down-weight rim evidence most affected by blur,
but only after the base estimator is measured.

### 7.3 Lens distortion

Kostecky's reference project undistorts the camera before measurement. OpenBar already records lens
distortion as an unvalidated M0 geometry limitation. The existing lens-distortion sensitivity study
remains the right place to decide whether correction is needed; this strategy does not introduce
mandatory user calibration.

## 8. What the reviewed open-source projects teach

### 8.1 kostecky/VBT-Barbell-Tracker

Reviewed repository:
- https://github.com/kostecky/VBT-Barbell-Tracker
- reviewed source head: 7c355235b1a5dd5b7c6b8e9e243f7bad3c6fdca8
- repository licence: CC0-1.0

The project's most useful result is not its velocity formula. The author reports trying generalized
CNN/object trackers and then deliberately simplifying the sensing problem with a high-contrast
circular marker on the bar end. The implementation uses camera undistortion, HSV thresholding,
morphology/contours and a minimum enclosing circle.

Useful lesson: controlled instrumentation can provide a cheap reference trajectory.

Do not copy its measurement shortcuts: fixed/overridden FPS, one-frame scale, simple finite
differences and heuristic rep logic conflict with OpenBar's timestamp-authoritative and
validation-first design.

### 8.2 Marticles/barbell-path-tracker

Reviewed repository:
- https://github.com/Marticles/barbell-path-tracker
- reviewed source head: 501daea5c0666b3564fadbbebb51e6f6ac0b2428
- repository licence: MIT

The project compares generic bounding-box/appearance trackers and draws the tracked box centre.
There is no physical calibration, authoritative timestamp/velocity system or measurement accuracy
study.

Useful lesson: path visualization and generic tracking are not evidence of metrology quality.

### 8.3 tlancon/barbellcv

Reviewed repository:
- https://github.com/tlancon/barbellcv
- reviewed source head: 7ffcd8853a3dbfb8da2b1993f63d844d3279cd2a
- no repository licence was found during this review; reference-only, no code reuse

This project independently converges on a distinctive fluorescent circular marker. It interactively
learns the marker HSV range, uses morphology and the largest contour, estimates a minimum enclosing
circle, uses multiple initial radii for scale, and records elapsed capture time.

Useful lessons:
- the marker/reference pattern is independently repeated;
- multi-sample scale is worth measuring as a research question;
- rep segmentation from a vertical derivative plus one-dimensional morphological cleanup is an
  interesting post-M0 deterministic idea.

Do not copy its implementation. In addition to the missing licence, its Euclidean frame-to-frame
speed turns both X and Y localization noise into positive speed, and its reported power formula
does not represent total athlete/bar mechanical power.

### 8.4 squatsandsciencelabs/OpenBarbellApp / OpenBarbell hardware

Reviewed repository:
- https://github.com/squatsandsciencelabs/OpenBarbellApp
- reviewed app head: 63a7fcb1826fc87d0bea697d257fc4cd5e2847df
- repository licence: MIT

The app is not a CV tracker. It receives rep metrics from direct-displacement hardware. That makes it
more useful as a conceptual measurement reference than as image-processing code.

OpenBarbell's encoder system measures displacement increments and elapsed time directly. Published
agreement work against Tendo reports much smaller mean-velocity differences than peak-velocity
differences, reinforcing that peak velocity is particularly sensitive to measurement/filtering
noise.

Sources:
- https://pmc.ncbi.nlm.nih.gov/articles/PMC6572172/
- https://pmc.ncbi.nlm.nih.gov/articles/PMC7900050/

## 9. Controlled marker/fiducial as a same-video reference

The repeated marker pattern deserves promotion from "interesting external idea" to a concrete
development experiment, while remaining outside the athlete-facing M0 requirement.

Record the natural plate and a deliberately distinctive marker rigidly aligned with the bar axis in
the same video:

                    same decoded frames
                           |
             +-------------+-------------+
             |                           |
       natural plate                 marker
       candidate                     reference
             |                           |
             +-------------+-------------+
                           |
                 exact-timestamp compare

A robust marker tracker can use deliberately simple colour/geometry processing and provide a
same-video secondary reference with minimal identity ambiguity.

This experiment can answer an important diagnostic question:

- if the marker trajectory is highly stable while natural plate estimates are noisy, localization
  remains the dominant problem;
- if both produce similar downstream velocity error, sampling/exposure/calibration/filtering or the
  independent physical-reference definition is probably limiting.

The marker is not independent physical ground truth merely because it is easy to see. M0 gate
validation still requires the independent-reference contract in M0_REFERENCE_STUDY.md.

## 10. YOLO26: useful research localizer, not the metrology layer

As of this review, Ultralytics documents YOLO26n detection at 640 px as approximately 2.4M fused
parameters, 5.5 GFLOPs, 38.9 ms CPU ONNX and 1.7 ms T4 TensorRT. The family supports detection,
instance segmentation and pose/keypoint tasks and exports to mobile-relevant formats.

Sources:
- https://docs.ultralytics.com/models/yolo26/
- https://docs.ultralytics.com/modes/export/

However, there are four constraints for OpenBar:

1. COCO's 80 pretrained detection classes do not include a barbell or weight plate. A useful model
   therefore requires a custom dataset and fine-tuning/training.
   Source: https://docs.ultralytics.com/datasets/detect/coco/
2. the P2/stride-4 YOLO26 architecture is published as YAML only; Ultralytics states that no
   scale-specific P2 pretrained weights are released. It is a training experiment, not a free
   drop-in localization upgrade.
3. a detector box centre is not a physical bar-axis estimator. Segmentation followed by geometric
   refinement or a custom centre keypoint is more relevant than direct box-centre velocity.
4. Ultralytics code/models are AGPL-3.0 by default and the vendor directs proprietary/commercial
   users to commercial licensing. That is not an uncomplicated fit with OpenBar's current MIT
   dependency policy.

Sources:
- https://github.com/ultralytics/ultralytics/blob/main/ultralytics/cfg/models/26/yolo26-p2.yaml
- https://www.ultralytics.com/license

Therefore YOLO26 is permitted only as an isolated research challenger under the current plan. It
must not enter a production dependency/model path without a separate licensing/dependency decision.

The most relevant experimental variants are:

- YOLO26n detect -> ROI only;
- YOLO26n segmentation -> contour -> plate-specific geometry;
- custom YOLO26n pose/keypoint -> bar-axis centre.

Primary evaluation must be OpenBar localization/displacement/kinematic error. COCO-style mAP is
secondary.

## 11. Recommended M0 research architecture

The research candidate should look like this:

    manual target seed
            |
            v
      coarse localizer
    (existing tracker first)
            |
            v
        small ROI
        /       \
       /         \
      v           v
 absolute plate   relative image
 metrology        registration
      |           |
      +-----+-----+
            |
            v
 deterministic robust
 global trajectory fit
            |
            v
 tracker-prediction-v1
            |
            v
 existing analyze --observations
            |
            v
 calibration/filter/kinematics

This preserves the current architecture boundary: research gathers observations; openbar-core
continues to own authoritative physical measurement semantics.

## 12. Decision priorities

The research order should be:

1. add measurement-oriented diagnostics so relative motion and temporal jitter can be evaluated;
2. build the controlled-marker same-video reference to separate target-identification error from
   downstream error;
3. evaluate an independent plate-specific absolute estimator from raw ROI pixels;
4. evaluate sub-pixel relative registration;
5. fuse both evidence types over the complete clip;
6. only if coarse localization/reacquisition is a demonstrated bottleneck, evaluate a learned
   localizer such as YOLO26;
7. freeze at most two candidates on development evidence and return to #57 held-out evaluation;
8. use the independent physical/reference study and Pixel 8 runtime gate before production
   promotion.

## 13. What this analysis does not change

This document does not:

- revise the provisional 3 px or kinematic gates;
- select a production tracker;
- claim the marker is physical ground truth;
- reopen or rewrite held-out evidence;
- change PlateDiameterCalibration@1;
- introduce dynamic frame-by-frame scale;
- add interpolation for lost samples;
- change the canonical kinematics method;
- add YOLO/Ultralytics/OpenCV as a production dependency;
- add automatic plate detection to M0;
- promote a mobile/UI design.

The correct outcome of any experiment remains REJECT or DEFER when the evidence does not justify
additional complexity.
