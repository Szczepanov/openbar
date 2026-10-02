# VBT Reference Experiments — Findings & Decision Records

Status: completed research findings  
Related: `docs/plans/VBT_REFERENCE_EXPERIMENTS_IMPLEMENTATION_PLAN.md`, `docs/analysis/VBT_OPEN_SOURCE_REFERENCE_REVIEW.md`, ADR-0004, ADR-0008, #57, #58

---

## 1. Executive Summary

This document records the empirical findings, data distributions, and explicit decision records from executing the first phase of the [VBT reference experiments implementation plan](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/plans/VBT_REFERENCE_EXPERIMENTS_IMPLEMENTATION_PLAN.md).

| Phase | Investigation | Findings | Decision |
|---|---|---|---|
| **Phase 1** | Coordinate transforms | Display-oriented coordinate invariant documented per ADR-0007; no active tracker emits preprocessed coordinates. | **COMPLETED (DOCS ONLY)** |
| **Phase 2 (P2.0)** | Real-timestamp survey | Strict tick-exact regularity fails all real clips due to ~0.1 ms sensor clock jitter; frozen 1% low-jitter rule qualifies 80.0% of dev and 83.3% of validation phone clips while rejecting VFR/dropped frames. | **GO (CONTINUE TO P2.1–P2.4)** |
| **Phase 2 (P2.1–P2.4)** | Butterworth challenger | Zero-phase forward-backward digital Butterworth implemented in pure Rust; 20-candidate dev grid evaluated; winning config selected and tested on held-out scenarios. | **CONTINUE RESEARCH (DEFER PROMOTION)** |
| **Phase 3 (P3.0)** | Multi-frame calibration oracle | Annotator aiming rings systematically shrink by 3%–12% during motion blur compared to setup seed; no production tracker measures size; no reference proves S1 > S0. | **REJECT ESTIMATOR PROMOTION / KEEP DIAGNOSTIC** |

---

## 2. Phase 1 — Coordinate Transform Display Invariant

Per §8 of the implementation plan, no current tracking or inference path emits transformed coordinates. The display-coordinate invariant was formally recorded in [TRACKER_EXPERIMENTS.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/validation/TRACKER_EXPERIMENTS.md):

> All tracker observations, seeds, annotations and bounds are in decoded display-oriented pixel coordinates after rotation, per ADR-0007. A tracker that runs on a transformed frame must map its output back before emitting observations.

---

## 3. Phase 2 — Real-Timestamp Applicability Survey (P2.0)

### 3.1 Objective & Survey Method
A digital Butterworth filter assumes regular sampling spacing. The survey investigated whether real smartphone video captures satisfy this assumption, or whether phone variable-frame-rate (VFR) mechanics invalidate the filter.

authoritative decoded frame presentation timestamps ($PTS$) were probed through the ADR-0006 FFmpeg boundary across all available development clips in `validation/fixtures/public/` and `validation/private/media/`.

### 3.2 Development Clips Empirical Distribution

| Fixture ID | Media / Device | Nominal FPS | Frames | Median $\Delta t$ (s) | Tick Span (1/90000 tb) | Max Rel Dev ($\%$) | CV ($\%$) | Anomalies / Drops |
|---|---|---|---|---|---|---|---|---|
| `synthetic-clean-side-12` | First principles | 12 | 12 | 0.083333 | 0 | 0.000% | 0.000% | 0 |
| `self-snatch-ohs-side-001` | Android 13 CFR | 30 | 320 | 0.033333 | 0 | 0.000% | 0.000% | 0 |
| `self-hang-snatch-side-002` | Xiaomi Android 11 | 30.13 | 1348 | 0.033189 | 970 | 16.773% | 1.565% | 640 (VFR drift) |
| `self-back-squat-side-002` | Android 2026 | 30 | 450 | 0.033333 | 3204 | 106.500% | 5.010% | 1 dropped frame (clip start) |
| `self-clean-jerk-side-002` | Android 2026 | 60 | 749 | 0.016667 | 1491 | 98.800% | 4.593% | 1 dropped frame (clip start) |
| `self-snatch-side-002` | Android 2026 | 60 | 813 | 0.016667 | 1491 | 98.800% | 3.662% | 1 dropped frame (clip start) |

### 3.3 Contiguous Tracked Lift Segments (Seed to End)

When evaluating the actual contiguous tracked segments from the manual seed through lift completion:

- `self-back-squat-side-002` (307 frames): ticks 2991..3009 ($\pm 9$ ticks, 0.2 ms jitter), **Max Rel Dev = 0.333%**, **CV = 0.085%**.
- `self-clean-jerk-side-002` (637 frames): ticks 1491..1509 ($\pm 9$ ticks, 0.2 ms jitter), **Max Rel Dev = 0.600%**, **CV = 0.150%**.
- `self-snatch-side-002` (700 frames): ticks 1491..1509 ($\pm 9$ ticks, 0.2 ms jitter), **Max Rel Dev = 0.600%**, **CV = 0.130%**.
- `self-hang-snatch-side-002` (708 frames): ticks 2518..3488, **Max Rel Dev = 16.773%**, **CV = 1.672%** (persistent VFR).

### 3.4 Rule Applicability & Frozen Policy

1. **Strict Rule** ($\text{tick span} \le 1$): Only synthetic video passed (1/5 seeded dev clips, 20.0%). Real phone CMOS sensors exhibit slight hardware timing jitter of $\approx 0.1$ ms ($\pm 9$ ticks in a 90 kHz time base), failing the strict tick-exact rule even on high-quality recordings.
2. **Frozen Low-Jitter Rule**:
   - Minimum sample count: $\ge 2$
   - No dropped/missing frame: $\max(\Delta t) < 1.5 \times \text{median}(\Delta t)$ and $\min(\Delta t) > 0.5 \times \text{median}(\Delta t)$
   - Maximum relative deviation from median: $\frac{\max |\Delta t - \text{median}|}{\text{median}} \le 1.0\%$
   - Coefficient of variation: $\text{CV} \le 0.5\%$
3. **Qualification Rate**:
   - Development seeded clips: **4/5 (80.0%) pass** (`synthetic-clean-side-12`, `self-back-squat-side-002`, `self-clean-jerk-side-002`, `self-snatch-side-002`).
   - Held-out validation clips: **5/6 (83.3%) pass** (`self-back-squat-side-001`, `self-clean-jerk-side-001`, `self-hang-snatch-side-003`, `self-clean-pull-side-003`, `self-clean-side-004`).
   - VFR clips (Xiaomi 16.8% jitter) and frames with drops fail closed.

**P2.0 Decision: GO.** A substantial majority of representative phone captures satisfy the frozen regularity criteria.

---

## 4. Phase 2 — Butterworth Implementation & Grid Benchmark (P2.1–P2.4)

### 4.1 Filter Design
An independent digital zero-phase Butterworth low-pass filter was implemented in `crates/openbar-core/src/filtering.rs`:
- Cascaded biquad direct form II architecture with bilinear transform and frequency pre-warping.
- Two-pass forward-backward zero-phase application ($M=2$) eliminating group delay and phase lag.
- Explicit support for Winter cutoff correction: $C = (2^{1/(2M)} - 1)^{1/(2 N_d)}$.
- Reflected endpoint padding ($3 \times \text{order}$) eliminating boundary transients.
- Fail-closed timestamp regularity validation (`FilterError::UnsupportedTimestampJitter`).

### 4.2 Predeclared Development Grid & Results
A 20-candidate grid was evaluated across 3 seeds per development scenario:
- Design orders: 2 (effective 4th-order roll-off), 4 (effective 8th-order roll-off).
- Cutoff conventions: Winter corrected vs uncorrected single-pass.
- Cutoff frequencies: 4.0, 6.0, 8.0, 10.0, 12.0 Hz.
- Gap threshold: 0.05 s.

Selected Winner (minimizing mean development velocity RMSE):
- **Implementation**: `zero-phase-butterworth-research@1`
- **Parameters**: `design_order=4`, `effective_order=8`, `cutoff_hz=4.0`, `cutoff_convention="uncorrected_single_pass"`.
- Development velocity RMSE: **0.0384 m/s**, position RMSE: **0.00165 m**.

### 4.3 Held-Out Validation Performance

| Scenario | Butterworth (Eff. Order 8, 4Hz) Vel RMSE (m/s) | Savitzky-Golay (Win 7, Ord 2) Vel RMSE (m/s) | Kalman Vel RMSE (m/s) | Raw Identity Vel RMSE (m/s) |
|---|---|---|---|---|
| `sharp-peak` | 0.1396 m/s (att: 0.296, shift: -0.033s) | **0.1136 m/s** (att: 0.005, shift: -0.017s) | 0.2101 m/s | 0.3423 m/s |
| `clip-boundaries` | 0.1192 m/s | **0.0665 m/s** | 0.2200 m/s | 0.3921 m/s |
| `short-missing-span` | *unsupported (gap)* | **0.0702 m/s** | 0.0971 m/s | 0.2971 m/s |
| `long-loss-span` | **0.0312 m/s** | 0.1200 m/s | 0.1758 m/s | 0.4060 m/s |

### 4.4 Decision Record
**Decision: CONTINUE RESEARCH (DEFER PROMOTION).**
- Butterworth satisfies zero phase delay and shows strong velocity smoothing on long loss spans.
- However, Savitzky-Golay demonstrates superior peak preservation (0.5% attenuation vs 29.6% attenuation on sharp velocity bursts) and handles short gaps/irregular timestamps natively.
- Promotion to a production default requires independent physical reference evidence (#58).

---

## 5. Phase 3 — Multi-Frame Calibration Robustness (P3.0)

### 5.1 Oracle Study Findings
Examined human-labelled per-sample plate diameters across 10 annotated lifts:
- In clips where the annotator dynamically adjusted the aiming ring, the median lift diameter ($S_1$) was **3.2% to 12.5% smaller** than the initial stationary seed diameter ($S_0$).
- Cause: motion blur during high-speed barbell pulls softens outer plate edges; human annotators fit the ring to the high-contrast core.
- Consequence: using multi-frame median diameter under motion blur systematically scales up velocity and ROM by $+3\%$ to $+12.5\%$.
- Furthermore, no production tracker currently estimates plate size.

### 5.2 Decision Record
**Decision: REJECT ESTIMATOR PROMOTION.**
Multi-frame diameter averaging without motion-blur deconvolution inflates calibration scale error. `PlateDiameterCalibration@1` remains the authoritative production standard. Diameter variation will be retained strictly as a diagnostic signal for geometry/blur quality (P3.5).

---

## 6. How to Reproduce

All research artifacts can be deterministically reproduced using the repository toolchain:

```bash
# 1. Run workspace tests
cargo test --locked --workspace --all-targets --all-features

# 2. Run media timestamp regularity survey
python research/vbt-experiments/timestamp_survey.py

# 3. Run calibration oracle study
python research/vbt-experiments/calibration_oracle_study.py

# 4. Run Butterworth challenger benchmark grid
cargo run --locked -p openbar-cli -- filter-experiment --challenger butterworth --output target/research/butterworth/filter-experiment-butterworth.json

# 5. Run standard baseline filter experiment
cargo run --locked -p openbar-cli -- filter-experiment --output target/filter-experiment.json
```
