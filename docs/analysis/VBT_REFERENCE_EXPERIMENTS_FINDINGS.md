# VBT Reference Experiments — Findings & Decision Records

Status: completed research findings  
Related: `docs/plans/VBT_REFERENCE_EXPERIMENTS_IMPLEMENTATION_PLAN.md`, `docs/analysis/VBT_OPEN_SOURCE_REFERENCE_REVIEW.md`, ADR-0004, ADR-0008, #57, #58

---

## 1. Executive Summary

This document records the empirical findings, data distributions, and explicit decision records from executing Phases 1–3 of the [VBT reference experiments implementation plan](../plans/VBT_REFERENCE_EXPERIMENTS_IMPLEMENTATION_PLAN.md).

| Phase | Investigation | Findings | Decision |
|---|---|---|---|
| **Phase 1** | Coordinate transforms | Display-oriented coordinate invariant documented per ADR-0007; no active tracker emits preprocessed coordinates. | **COMPLETED (DOCS ONLY)** |
| **Phase 2 (P2.0)** | Real-timestamp survey | Decoded container video PTS shows ~0.1 ms sensor clock jitter; frozen 1% low-jitter rule qualifies 80.0% of dev and 83.3% of validation phone clips while rejecting VFR. Note: real tracker qualification is lower because single-frame tracking dropouts violate continuity. | **GO (CONTINUE TO P2.1–P2.4)** |
| **Phase 2 (P2.1–P2.4)** | Butterworth challenger | Zero-phase forward-backward digital Butterworth implemented in pure Rust; 20-candidate grid evaluated. Winner sits at lower grid boundary (4.0 Hz) due to smoothing bias, suffering 29.6% peak velocity attenuation; rejects short gaps. | **DO NOT PROMOTE / RETAIN AS RESEARCH CHALLENGER** |
| **Phase 3 (P3.0)** | Multi-frame calibration oracle | Deliberately fitted per-frame plate diameters disagree with stationary setup seeds by 3%–12%. Without an independent physical reference, cause is unverified (blur vs out-of-plane motion vs perspective vs annotator bias). | **DEFER (INCONCLUSIVE) / FORWARD SCALE UNCERTAINTY TO #58** |

---

## 2. Phase 1 — Coordinate Transform Display Invariant

Per §8 of the implementation plan, no current tracking or inference path emits transformed coordinates. The display-coordinate invariant was formally recorded in [`TRACKER_EXPERIMENTS.md`](../validation/TRACKER_EXPERIMENTS.md):

> All tracker observations, seeds, annotations and bounds are in decoded display-oriented pixel coordinates after rotation, per ADR-0007. A tracker that runs on a transformed frame must map its output back before emitting observations.

---

## 3. Phase 2 — Real-Timestamp Applicability Survey (P2.0)

### 3.1 Objective & Survey Method
A digital Butterworth filter assumes regular sampling spacing. The survey investigated whether real smartphone video captures satisfy this assumption, or whether phone variable-frame-rate (VFR) mechanics invalidate the filter.

Authoritative decoded frame presentation timestamps ($PTS$) were probed through the ADR-0006 FFmpeg boundary across all available development and validation clips in `validation/fixtures/public/` and `validation/private/media/`.

### 3.2 Empirical Distributions Across Cohorts

#### Development Cohort (N = 6 clips, 5 seeded phone recordings)

| Fixture ID | Media / Device | Nominal FPS | Frames | Median $\Delta t$ (s) | Tick Span (1/90000 tb) | Max Rel Dev ($\%$) | CV ($\%$) | Anomalies / Drops |
|---|---|---|---|---|---|---|---|---|
| `synthetic-clean-side-12` | First principles | 12 | 12 | 0.083333 | 0 | 0.000% | 0.000% | 0 |
| `self-snatch-ohs-side-001` | Android 13 CFR | 30 | 320 | 0.033333 | 0 | 0.000% | 0.000% | 0 |
| `self-hang-snatch-side-002` | Xiaomi Android 11 | 30.13 | 1348 | 0.033189 | 970 | 16.773% | 1.565% | 640 (VFR drift) |
| `self-back-squat-side-002` | Android 2026 | 30 | 450 | 0.033333 | 3204 | 106.500% | 5.010% | 1 dropped frame (clip start) |
| `self-clean-jerk-side-002` | Android 2026 | 60 | 749 | 0.016667 | 1491 | 98.800% | 4.593% | 1 dropped frame (clip start) |
| `self-snatch-side-002` | Android 2026 | 60 | 813 | 0.016667 | 1491 | 98.800% | 3.662% | 1 dropped frame (clip start) |

#### Validation Cohort (N = 6 clips, 6 seeded phone recordings)

| Fixture ID | Media / Device | Nominal FPS | Frames | Median $\Delta t$ (s) | Tick Span (1/90000 tb) | Max Rel Dev ($\%$) | CV ($\%$) | Anomalies / Drops |
|---|---|---|---|---|---|---|---|---|
| `self-back-squat-side-001` | Android 2026 | 30 | 450 | 0.033333 | 2991 | 99.400% | 4.750% | 1 dropped frame (clip start) |
| `self-clean-jerk-side-001` | Android 2026 | 30 | 436 | 0.033333 | 3007 | 99.933% | 5.245% | 1 dropped frame (clip start) |
| `self-hang-snatch-side-003` | Android 2026 | 30 | 330 | 0.033333 | 2991 | 99.400% | 5.732% | 1 dropped frame (clip start) |
| `self-clean-pull-side-003` | Android 2026 | 60 | 706 | 0.016667 | 1491 | 98.800% | 4.838% | 1 dropped frame (clip start) |
| `self-clean-side-004` | Android 2026 | 30 | 510 | 0.033333 | 4131 | 137.400% | 6.070% | 1 dropped frame (clip start) |
| `self-clean-side-005` | Android 2026 | 60 | 867 | 0.016667 | 1508 | 99.933% | 4.829% | 1 dropped frame in tracked span |

### 3.3 Contiguous Tracked Lift Segments (Seed to End)

When evaluating the actual contiguous tracked segments from the manual seed through lift completion:

- In the development cohort, 4 of 5 seeded phone clips exhibit hardware jitter $\le \pm 9$ ticks ($\le 0.6\%$ relative deviation), passing the frozen 1% low-jitter threshold.
- In the validation cohort, 5 of 6 seeded phone clips exhibit jitter $\le 0.6\%$, passing the threshold.
- High-jitter VFR captures (`self-hang-snatch-side-002`, 16.8% jitter) and clips with in-segment dropped frames (`self-clean-side-005`, frame gap $\approx 33.3$ ms at 60 fps) fail closed.

### 3.4 Container Frame PTS vs Real Tracker Output Qualification

> [!IMPORTANT]
> **Container Frame PTS vs Tracker Observation Streams:**
> The survey measured container frame PTS regularity. In an end-to-end tracking pipeline, a tracker frequently drops tracking or loses confidence for single isolated frames.
> At 60 fps, an observation dropout creates a timestamp gap $\Delta t \ge 33.3\text{ ms} > 1.5 \times 16.7\text{ ms} = 25\text{ ms}$. Under the Butterworth regularity constraint ($\Delta t \in [0.5 \times \text{median}, 1.5 \times \text{median}]$), any single lost frame rejects the entire segment. Real-world end-to-end tracker qualification will therefore be significantly lower than the container PTS qualification rate (80%–83%).

---

## 4. Phase 2 — Butterworth Implementation & Grid Benchmark (P2.1–P2.4)

### 4.1 Filter Design
An independent digital zero-phase Butterworth low-pass filter was implemented in `crates/openbar-core/src/filtering/butterworth_experimental.rs`:
- Cascaded biquad direct form II architecture with bilinear transform and frequency pre-warping.
- Two-pass forward-backward zero-phase application ($M=2$) eliminating group delay and phase lag.
- Winter dual-pass cutoff correction formula:
  $$C = (2^{1/M} - 1)^{\frac{1}{2 N_d}} = (\sqrt{2} - 1)^{\frac{1}{2 N_d}}$$
  where $N_d$ is the design order ($C \approx 0.802$ for $N_d=2$; $C \approx 0.8955$ for $N_d=4$).
- Validated via steady-state sinusoidal frequency response unit test confirming $-3.0\text{ dB}$ attenuation ($0.7071 \pm 0.015$) at the stated cutoff with correction, and $-6.0\text{ dB}$ ($0.500 \pm 0.015$) without correction.
- Reflected endpoint padding ($3 \times \text{order}$) eliminating boundary transients.
- Fail-closed timestamp regularity validation (`FilterError::UnsupportedTimestampJitter`).

### 4.2 Predeclared Development Grid & Boundary Winner
A 20-candidate grid was evaluated across 3 seeds per development scenario:
- Design orders: 2 (effective 4th-order roll-off), 4 (effective 8th-order roll-off).
- Cutoff conventions: Winter corrected vs uncorrected single-pass.
- Cutoff frequencies: 4.0, 6.0, 8.0, 10.0, 12.0 Hz.
- Gap threshold: 0.05 s.

Selected Candidate (minimizing mean development velocity RMSE on supported scenarios):
- **Implementation**: `zero-phase-butterworth-research@1`
- **Parameters**: `design_order=4`, `effective_order=8`, `cutoff_hz=4.0`, `cutoff_convention="uncorrected_single_pass"`.
- Development velocity RMSE: **0.0311 m/s**, position RMSE: **0.00180 m**.
- Supported development scenarios: 9 / 12 (75.0%). The 3 `irregular-timestamps` scenarios failed closed with `UnsupportedTimestampJitter`.

> [!WARNING]
> **Grid Boundary Effect:**
> The selected winner (4.0 Hz) lies exactly on the lower boundary of the evaluated grid [4.0, 12.0] Hz. Because development scenarios feature smooth polynomial trajectories corrupted by Gaussian noise, decreasing the cutoff monotonically improves velocity RMSE by aggressively suppressing high-frequency noise. However, this heavy smoothing imposes a severe penalty on high-frequency velocity bursts.

### 4.3 Held-Out Validation Performance

| Scenario | Butterworth (Eff. Order 8, 4Hz) | Savitzky-Golay (Win 7, Ord 2) | Kalman | Raw Identity |
|---|---|---|---|---|
| `sharp-peak` | 0.1396 m/s (att: **29.6%**, shift: -0.033s) | **0.1136 m/s** (att: 0.5%, shift: -0.017s) | 0.2101 m/s | 0.3423 m/s |
| `clip-boundaries` | 0.1192 m/s | **0.0665 m/s** | 0.2200 m/s | 0.3921 m/s |
| `short-missing-span` | **UNSUPPORTED** (relative dev 1.40 > 0.01 threshold) | **0.0702 m/s** | 0.0971 m/s | 0.2971 m/s |
| `long-loss-span` | **0.0312 m/s** (2 segments) | 0.1200 m/s | 0.1758 m/s | 0.4060 m/s |

### 4.4 Decision Record
**Decision: DO NOT PROMOTE (RETAIN AS RESEARCH CHALLENGER).**
- Butterworth achieves zero phase delay and low velocity RMSE on long loss spans when timestamps are regular.
- However, Savitzky-Golay demonstrates dramatically better peak preservation (0.5% attenuation vs 29.6% attenuation on dynamic peaks) and handles short dropouts and irregular timestamps without failing closed.
- The 4.0 Hz winner reflects artificial smoothing bias on smooth polynomial synthetic signals rather than general fitness for explosive barbell dynamics.
- Production promotion is rejected. Future evaluation requires independent physical reference evidence (#58).

---

## 5. Phase 3 — Multi-Frame Calibration Robustness (P3.0)

### 5.1 Oracle Study Findings
Examined human-labelled per-sample plate diameters across 9 primary annotated lifts:
- Annotation tool mechanics: the annotation tool automatically carries the aiming ring radius forward across frames unless the annotator deliberately resizes it.
- When isolating frames where the annotator actively adjusted the ring, the median dynamic diameter ($S_{1,\text{fitted}}$) was **0% to 9.6% smaller** than the initial stationary seed diameter ($S_0$) across clips (overall diameter variation up to 12.5%).
- **Absence of physical ground truth**: without an independent physical reference distance or physical diameter measurement during the lift, it is impossible to determine whether the stationary seed diameter or dynamic per-frame diameter is closer to the true physical scale.
- **Competing hypotheses**: speculative explanations (such as motion blur softening edges) cannot be confirmed without physical reference data. Out-of-plane bar trajectory (bar moving toward or away from the camera), camera perspective foreshortening, and visual annotator edge placement bias are equally plausible explanations.

### 5.2 Decision Record
**Decision: DEFER (INCONCLUSIVE) / RETAIN PlateDiameterCalibration@1.**
- Inconclusive without independent physical reference ground truth.
- `PlateDiameterCalibration@1` remains the authoritative production standard.
- The 3%–12% scale discrepancy is documented and forwarded directly to #58 as an empirical calibration uncertainty signal that physical ground truth must evaluate.

---

## 6. Strategic Priority: Re-entry Blockers (#57 and #58)

Phases 1–3 provided valuable empirical boundaries (timestamp regularity constraints, smoothing bias warnings, and calibration scale uncertainty). However, **neither filter challenger tuning nor multi-frame calibration moves the critical-path blockers for M0 re-entry**.

Per ADR-0008 (NO-GO / REWORK) and the implementation plan's sequencing rules (§6 and §18):
- **#57 (Tracker selection & evidence freeze)** outranks further filter tuning.
- **#58 (Independent physical reference ground truth)** outranks calibration estimator redesign.

All subsequent engineering effort should focus directly on #57 and #58.

---

## 7. How to Reproduce

All research artifacts can be deterministically reproduced using the repository toolchain:

```bash
# 1. Run workspace tests
cargo test --locked --workspace --all-targets --all-features

# 2. Run media timestamp regularity survey across development and validation cohorts
python research/vbt-experiments/timestamp_survey.py

# 3. Run calibration oracle study
python research/vbt-experiments/calibration_oracle_study.py

# 4. Run Butterworth challenger benchmark grid
cargo run --locked -p openbar-cli -- filter-experiment --challenger butterworth --output target/research/butterworth/filter-experiment-butterworth.json

# 5. Run standard baseline filter experiment
cargo run --locked -p openbar-cli -- filter-experiment --output target/filter-experiment.json
```
