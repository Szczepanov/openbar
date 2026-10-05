"""Research-only robust fusion of absolute centres and measured relative edges.

Conservative v1 policy: only valid tracked absolute observations receive coordinates;
lost frames, missing relative edges, and intervals above ``max_gap_s`` split solves.
Relative-only evidence never fills a lost frame. Times must be strictly increasing,
and a relative constraint must join consecutive samples by their actual timestamps.

The objective is sum(wG * Huber(||C-G||)) +
sum(wD * Huber(||C[i]-C[i-1]-D||)). There is NO smoothness term.
Absolute evidence uses its configured weight times max(confidence,
confidence_weight_floor); the floor keeps zero-confidence tracked absolute observations
anchored numerically without promoting their output confidence. Relative evidence uses
its raw confidence, so a zero-confidence edge has zero influence on the solve. Eight
fixed IRLS iterations are the default; the deterministic tridiagonal solver uses no
randomness or convergence-based stopping. Exactly consistent evidence is returned
unchanged because its objective is already zero. All output-affecting parameters live
in FUSION_CONFIG and should accompany prediction implementation provenance.

Confidence is an algorithm-specific diagnostic. It starts from absolute confidence,
is reduced by absolute residual disagreement, and applies each incident relative-edge
disagreement in proportion to that edge's confidence. It is not a calibrated
probability and never exceeds the absolute confidence. Inputs are validated without
mutation. Raw evidence belongs in caller sidecars.
"""

import math


FUSION_CONFIG = {
    "absolute_weight": 1.0,
    "relative_weight": 1.0,
    "huber_delta_px": 2.0,
    "irls_iterations": 8,
    "max_gap_s": 0.2,
    "confidence_weight_floor": 1e-6,
}


def _number(value, name, *, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return result


def _point(value, name, *, nonnegative=False):
    if not isinstance(value, dict) or set(value) != {"x_px", "y_px"}:
        raise ValueError(f"{name} must contain only x_px and y_px")
    return tuple(
        _number(value[key], f"{name}.{key}", minimum=0 if nonnegative else None)
        for key in ("x_px", "y_px")
    )


def _config(config):
    if not isinstance(config, dict) or set(config) - set(FUSION_CONFIG):
        raise ValueError("unknown fusion configuration keys or non-object config")
    result = {**FUSION_CONFIG, **config}
    iterations = result["irls_iterations"]
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 1:
        raise ValueError("irls_iterations must be a positive integer")
    for key in FUSION_CONFIG:
        if key == "irls_iterations":
            continue
        result[key] = _number(result[key], key)
        if result[key] <= 0:
            raise ValueError(f"{key} must be positive")
    if result["confidence_weight_floor"] > 1:
        raise ValueError("confidence_weight_floor must be <= 1")
    return result


def _validate(absolute_samples, relative_samples):
    if not isinstance(absolute_samples, list) or not isinstance(relative_samples, list):
        raise ValueError("absolute_samples and relative_samples must be lists")
    times, positions, confidences = [], [], []
    for sample in absolute_samples:
        if not isinstance(sample, dict) or set(sample) - {
            "timestamp_s", "state", "center_px", "confidence"
        }:
            raise ValueError("invalid absolute sample fields")
        timestamp = _number(sample.get("timestamp_s"), "timestamp_s", minimum=0)
        if times and timestamp <= times[-1]:
            raise ValueError("absolute timestamps must be strictly increasing")
        state = sample.get("state")
        if state == "lost":
            if "center_px" in sample or "confidence" in sample:
                raise ValueError("lost samples forbid coordinates and confidence")
            position, confidence = None, None
        elif state == "tracked":
            position = _point(sample.get("center_px"), "center_px", nonnegative=True)
            confidence = _number(sample.get("confidence"), "confidence", minimum=0, maximum=1)
        else:
            raise ValueError("absolute state must be tracked or lost")
        times.append(timestamp)
        positions.append(position)
        confidences.append(confidence)

    indexes = {timestamp: index for index, timestamp in enumerate(times)}
    edges, destinations = {}, set()
    last_relative_time = None
    for sample in relative_samples:
        if not isinstance(sample, dict) or set(sample) - {
            "timestamp_s", "previous_timestamp_s", "delta_px", "confidence"
        } or not {"timestamp_s", "previous_timestamp_s", "delta_px"} <= set(sample):
            raise ValueError("invalid relative sample fields")
        timestamp = _number(sample["timestamp_s"], "relative timestamp_s", minimum=0)
        if last_relative_time is not None and timestamp <= last_relative_time:
            raise ValueError("relative timestamps must be strictly increasing")
        last_relative_time = timestamp
        if timestamp not in indexes or timestamp in destinations:
            raise ValueError("unmatched or duplicate relative destination")
        destinations.add(timestamp)
        index = indexes[timestamp]
        previous = sample["previous_timestamp_s"]
        delta = sample["delta_px"]
        confidence = sample.get("confidence")
        if delta is None:
            if confidence is not None:
                raise ValueError("missing relative delta forbids confidence")
            observation = None
        else:
            observation = (
                _point(delta, "delta_px"),
                _number(confidence, "relative confidence", minimum=0, maximum=1),
            )
        if previous is None:
            if index != 0 or observation is not None:
                raise ValueError("only a missing first relative sample may have no previous time")
            continue
        previous = _number(previous, "previous_timestamp_s", minimum=0)
        if index == 0 or previous != times[index - 1]:
            raise ValueError("relative endpoints must match consecutive absolute timestamps")
        if observation is not None:
            edges[index] = observation
    return times, positions, confidences, edges


def _agreement(residual, delta):
    if not math.isfinite(residual):
        raise ValueError("non-finite fusion residual")
    return 1.0 if residual <= delta else delta / residual


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _edge_residual(previous, current, delta):
    return math.hypot(
        current[0] - previous[0] - delta[0],
        current[1] - previous[1] - delta[1],
    )


def _tridiagonal(diagonal, off_diagonal, rhs):
    """Fixed-order Thomas elimination for the positive definite normal system."""
    diagonal, rhs = diagonal[:], rhs[:]
    for index in range(1, len(diagonal)):
        if not math.isfinite(diagonal[index - 1]) or diagonal[index - 1] <= 0:
            raise ValueError("fusion system is numerically singular")
        factor = off_diagonal[index - 1] / diagonal[index - 1]
        diagonal[index] -= factor * off_diagonal[index - 1]
        rhs[index] -= factor * rhs[index - 1]
    solution = [0.0] * len(diagonal)
    for index in range(len(diagonal) - 1, -1, -1):
        if not math.isfinite(diagonal[index]) or diagonal[index] <= 0:
            raise ValueError("fusion system is numerically singular")
        remainder = rhs[index]
        if index + 1 < len(diagonal):
            remainder -= off_diagonal[index] * solution[index + 1]
        solution[index] = remainder / diagonal[index]
        if not math.isfinite(solution[index]):
            raise ValueError("non-finite fusion solution")
    return solution


def _solve(positions, confidences, edges, config):
    centres = positions[:]
    floor = config["confidence_weight_floor"]
    for _ in range(config["irls_iterations"]):
        absolute_weights = [
            config["absolute_weight"] * max(confidence, floor)
            * _agreement(_distance(centre, position), config["huber_delta_px"])
            for centre, position, confidence in zip(centres, positions, confidences)
        ]
        relative_weights = [
            config["relative_weight"] * confidence
            * _agreement(_edge_residual(centres[i], centres[i + 1], delta), config["huber_delta_px"])
            for i, (delta, confidence) in enumerate(edges)
        ]
        diagonal = absolute_weights[:]
        for i, weight in enumerate(relative_weights):
            diagonal[i] += weight
            diagonal[i + 1] += weight
        axes = []
        for axis in (0, 1):
            rhs = [weight * position[axis] for weight, position in zip(absolute_weights, positions)]
            for i, ((delta, _), weight) in enumerate(zip(edges, relative_weights)):
                rhs[i] -= weight * delta[axis]
                rhs[i + 1] += weight * delta[axis]
            axes.append(_tridiagonal(diagonal, [-weight for weight in relative_weights], rhs))
        centres = list(zip(*axes))
    return centres


def fuse(absolute_samples: list, relative_samples: list, config: dict) -> list:
    """Return fresh canonical tracker samples; reject invalid evidence/configuration.

    ``center_px`` and ``delta_px`` are mappings with x_px/y_px. Relative records
    carry timestamp_s, previous_timestamp_s, delta_px and confidence; a missing
    delta has null/absent confidence. A first missing record may have null previous
    time. An empty relative list is valid and preserves absolute-only positions.
    Valid measured edges adjacent to lost frames or across max_gap_s are ignored,
    never used to bridge them. Negative fitted positions are emitted as lost,
    rather than clamped to fabricated display coordinates.
    """
    settings = _config(config)
    times, positions, confidences, edges = _validate(absolute_samples, relative_samples)
    output = [{"timestamp_s": time, "state": "lost"} for time in times]
    start = 0
    while start < len(times):
        if positions[start] is None:
            start += 1
            continue
        end = start + 1
        while (
            end < len(times)
            and positions[end] is not None
            and end in edges
            and times[end] - times[end - 1] <= settings["max_gap_s"]
        ):
            end += 1
        local_edges = [edges[index] for index in range(start + 1, end)]
        if end == start + 1 or all(
            _edge_residual(positions[index - 1], positions[index], edges[index][0]) == 0
            for index in range(start + 1, end)
        ):
            centres = positions[start:end]
        else:
            centres = _solve(positions[start:end], confidences[start:end], local_edges, settings)
        for offset, centre in enumerate(centres):
            index = start + offset
            if min(centre) < 0:
                continue
            agreement = _agreement(_distance(centre, positions[index]), settings["huber_delta_px"])
            for edge_index in (offset - 1, offset):
                if 0 <= edge_index < len(local_edges):
                    delta, edge_confidence = local_edges[edge_index]
                    edge_agreement = _agreement(
                        _edge_residual(centres[edge_index], centres[edge_index + 1], delta),
                        settings["huber_delta_px"],
                    )
                    agreement *= 1.0 - edge_confidence * (1.0 - edge_agreement)
            output[index] = {
                "timestamp_s": times[index],
                "state": "tracked",
                "center_px": {"x_px": centre[0], "y_px": centre[1]},
                "confidence": confidences[index] * agreement,
            }
        start = end
    return output
