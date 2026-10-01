# M0 kinematic metric semantics

Issue: #11

## Purpose

M0 publishes only kinematic quantities whose inputs, timing semantics, continuity rules, units,
and quality behaviour are explicit.

The authoritative implementation is:

`crates/openbar-core/src/kinematics.rs`

The canonical coordinate convention comes from plate calibration:

- X: horizontal image-plane displacement, +X to the right;
- Y: vertical image-plane displacement, +Y up;
- position units: metres;
- velocity units: metres per second.

These remain image-plane measurements under the M0 scalar plate-diameter geometry model. A valid
numeric result does not override calibration warnings or make unsupported perspective/camera
geometry physically valid.

## Position and displacement

Calibrated `x_m` and `y_m` are displacement from the plate centre at the calibration reference
sample. They are derived from calibrated/filtered position samples, never from nominal FPS or frame
index.

`axis_displacement` is:

```text
displacement = position_last - position_first
```

for one explicitly named axis. It is available only when the supplied series is continuous under
the configured gap rule and every contributing sample meets the configured confidence threshold.

## Velocity method

M0 velocity method identity is:

```text
implementation = backward-difference
version        = 1
```

For sample `i > 0`:

```text
vx[i] = (x[i] - x[i-1]) / (t[i] - t[i-1])
vy[i] = (y[i] - y[i-1]) / (t[i] - t[i-1])
```

The derivative belongs to the segment ending at `t[i]`.

### Timestamp behaviour

- decoded/authoritative timestamps are used directly;
- irregular spacing is supported;
- timestamps must be finite, non-negative, and strictly increasing;
- nominal FPS is never substituted for timestamp deltas.

### First and last sample behaviour

- the first sample has no backward segment, so `vx_mps` and `vy_mps` are `null`/missing;
- the final sample may have a velocity if the final segment is supported;
- no forward extrapolation is performed.

### Gap behaviour

`KinematicsConfig.max_gap_s` is a required positive continuity threshold.

If:

```text
t[i] - t[i-1] > max_gap_s
```

the derivative at sample `i` is unavailable. OpenBar does not divide displacement by the long gap
and present the result as supported motion.

No interpolation is performed by this method.

### Confidence behaviour

`KinematicsConfig.min_confidence` is explicit and must be within `[0, 1]`.

A derivative is unavailable when either endpoint confidence is below that threshold. Otherwise the
derived sample confidence is:

```text
min(confidence[i-1], confidence[i])
```

This prevents a numeric derivative from erasing weaker endpoint evidence.

### Provenance

`derive_kinematic_trajectory` returns the canonical `KinematicTrajectory` with:

- input layer: calibrated or filtered;
- implementation: `backward-difference`;
- version: `1`;
- `max_gap_s`;
- `min_confidence`;
- sample-aligned position and velocity values.

The method/configuration therefore travels with the derived layer in canonical analysis JSON.

## Range of motion

For an explicitly named axis:

```text
ROM = max(position) - min(position)
```

over the supplied series.

At least two supported position samples are required. A single observation is insufficient evidence
for a movement range and therefore returns unavailable rather than a numeric zero.

ROM is unavailable if the supplied series contains an unsupported continuity gap or any sample below
the configured confidence threshold. Returned metric confidence is the minimum confidence across all
contributing samples.

This conservative rule avoids reporting an apparently precise ROM when an unobserved span could
contain the true extremum.

## Explicit metric intervals

Mean and peak axis velocity require a `MetricInterval { start_s, end_s }`.

M0 interval boundaries must exactly match authoritative sample timestamps. OpenBar does not silently
interpolate a phase boundary. Interpolation/resampling, if introduced later, requires its own
versioned method and validation.

The interval is closed in position space, but derivatives are included only for segments wholly
inside the interval. The derivative that ends at the interval start is excluded because it begins
before the interval.

## Mean axis velocity

For an explicit interval with fully supported continuity:

```text
mean_axis_velocity =
    (position_at_end - position_at_start)
    / (end_s - start_s)
```

This is equivalent to the time-weighted mean of the backward-difference segments inside the
interval and remains correct for irregular timestamps.

If any segment inside the interval is unsupported, the metric is unavailable rather than averaging
only the surviving fragments.

The returned confidence is the minimum confidence across the interval.

Product labels such as **mean concentric velocity** must not be used until a separately validated
process defines the concentric interval boundaries.

## Peak axis velocity

For an explicit, fully supported interval, `peak_axis_velocity` is the maximum **signed component**
for the named axis across derivative segments wholly inside the interval.

For vertical M0 coordinates this means maximum +Y velocity, where +Y is upward.

Edge rules:

- no derivative crossing the interval start may win the peak;
- a segment ending exactly at the interval end is eligible;
- if any required segment is unsupported, the peak is unavailable;
- samples outside the interval are not differentiated, so they can neither win nor fail the peak;
- ties retain the earliest encountered sample because only a strictly larger value replaces the
  current peak.

The returned timestamp is the authoritative timestamp at the end of the winning segment. Metric
confidence is the minimum confidence across the complete interval, not merely the winning sample.

### Peak velocity vs peak speed

The filter experiment retains an older diagnostic named `peak_speed`, computed as the Euclidean
magnitude `hypot(vx, vy)`. That diagnostic is useful for filter attenuation studies but is **not**
the canonical product definition of signed vertical or horizontal peak velocity.

Reports and UI must not label Euclidean peak speed as vertical peak velocity.

## Benchmark semantics

Where calibrated reference trajectories are available,
`openbar_core::benchmark::evaluate_filter_case` now reports:

- horizontal ROM absolute error;
- vertical ROM absolute error;
- mean X velocity absolute error over the full explicit reference interval;
- mean Y velocity absolute error over the full explicit reference interval;
- peak X velocity absolute error when peak metrics are applicable;
- peak Y velocity absolute error when peak metrics are applicable;
- pointwise velocity MAE/RMSE over supported derivative segments;
- existing Euclidean peak-speed attenuation/timing diagnostics.

The same canonical `backward-difference@1` continuity rule is used for reference and evaluated
trajectories.

The provisional engineering gates remain:

| Metric | Initial engineering target |
| --- | ---: |
| ROM MAE | < 0.01 m |
| Mean velocity MAE | < 0.05 m/s |
| Peak velocity MAE | < 0.10 m/s |

These are engineering gates, not scientific validity claims. Device-to-device comparison is valid
only when axis, movement interval/phase, filtering, smoothing, timestamp boundaries, and metric
definition match.

Correlation alone is not evidence of agreement.

## Validation status

| Quantity | M0 status |
| --- | --- |
| Calibrated X/Y position | implemented; calibration/geometry support still governs validity |
| X/Y backward-difference velocity | implemented with deterministic analytic tests |
| X/Y displacement | implemented with explicit continuity/confidence rules |
| Horizontal/vertical ROM | implemented with deterministic analytic tests |
| Mean axis velocity over explicit interval | implemented with deterministic analytic tests |
| Peak signed axis velocity over explicit interval | implemented with deterministic analytic tests |
| Mean concentric velocity | unavailable until concentric boundaries are explicitly defined and validated |
| Automatic phase-specific metrics | unavailable in issue #11 |
| Acceleration | not a validated M0 production metric |
| Force / power | out of scope; barbell-derived values must not be presented as whole-body output |

Synthetic analytic tests validate implementation semantics, including constant, linear,
irregular-time, quadratic, long-gap, low-confidence, ROM, interval, and peak-edge cases. They do not
replace external/reference validation across real recording conditions.
