"""Research ChArUco experiment (#177); never modifies canonical measurements.

Uses OpenCV 4.12 documented APIs, not a production calibration method.
"""
import argparse
import hashlib
import importlib.metadata
import itertools
import json
import math
from pathlib import Path
import platform
import sys

import cv2
import numpy as np

VERSION = 1
ROOT = Path(__file__).resolve().parents[2]
POSITIONS = ("centre", "left", "right", "top", "bottom")
OPTIONS = dict(min_fit_views=10, min_corners=12, min_hull_fraction=0.01,
               min_span_fraction=0.6, min_centroid_cells=3, min_tilt_range_deg=15.,
               max_rms_px=2., min_homography_rank_ratio=1e-5,
               flags=0, iterations=100, epsilon=1e-9, threads=1, rng_seed=0,
               charuco_min_markers=2, charuco_try_refine_markers=False,
               detector_corner_refinement_method=0, board_legacy_pattern=False)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def strict_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ValueError(f"{label}: fields must be exactly {keys}")


def number(value, label, minimum=0, positive=False):
    try:
        finite = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{label}: expected finite number")
    if value < minimum or (positive and value == minimum):
        raise ValueError(f"{label}: outside valid range")
    return value


def integer(value, label, minimum):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label}: expected integer >= {minimum}")


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: expected nonempty string")


def load_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError(f"nonfinite JSON value: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=invalid)


def make_board(config, require_physical=True):
    strict_keys(config, "squares_x squares_y square_length_m marker_length_m dictionary "
                "measured_square_length_m square_length_tolerance_m flatness_verified print_scale_verified", "board")
    for field in ("squares_x", "squares_y"):
        integer(config[field], field, 3)
    for field in ("square_length_m", "marker_length_m", "measured_square_length_m"):
        number(config[field], field, positive=True)
    number(config["square_length_tolerance_m"], "square_length_tolerance_m", positive=True)
    if config["square_length_tolerance_m"] >= config["square_length_m"]:
        raise ValueError("board: square tolerance must be smaller than square")
    if config["marker_length_m"] >= config["square_length_m"]:
        raise ValueError("board: marker must be smaller than square")
    if abs(config["measured_square_length_m"] - config["square_length_m"]) > config["square_length_tolerance_m"]:
        raise ValueError("board: measured print scale differs from declared geometry")
    if any(type(config[field]) is not bool for field in ("flatness_verified", "print_scale_verified")):
        raise ValueError("board: physical verification flags must be booleans")
    if require_physical and (config["flatness_verified"] is not True or config["print_scale_verified"] is not True):
        raise ValueError("board: physical verification required")
    dictionary = config["dictionary"]
    if not isinstance(dictionary, str) or not dictionary.startswith("DICT_") or not hasattr(cv2.aruco, dictionary):
        raise ValueError("board: unknown dictionary")
    board = cv2.aruco.CharucoBoard((config["squares_x"], config["squares_y"]),
                                  config["square_length_m"], config["marker_length_m"],
                                  cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary)))
    board.setLegacyPattern(OPTIONS["board_legacy_pattern"])
    return board


def validate_camera(camera):
    strict_keys(camera, "device lens width_px height_px orientation zoom focus stabilization mode", "camera")
    integer(camera["width_px"], "width_px", 1)
    integer(camera["height_px"], "height_px", 1)
    number(camera["zoom"], "zoom", positive=True)
    for field in ("device", "lens", "orientation", "focus", "stabilization", "mode"):
        text(camera[field], field)


def validate_manifest(value):
    strict_keys(value, "schema_version board camera views references preregistration", "manifest")
    if type(value["schema_version"]) is not int or value["schema_version"] != VERSION:
        raise ValueError("unsupported schema_version")
    make_board(value["board"])
    validate_camera(value["camera"])
    registration = value["preregistration"]
    strict_keys(registration, "frozen_before_capture protocol_id minimum_relative_improvement "
                "minimum_absolute_improvement maximum_position_regression", "preregistration")
    if registration["frozen_before_capture"] is not True:
        raise ValueError("preregistration must be frozen before capture")
    text(registration["protocol_id"], "protocol_id")
    for field in ("minimum_relative_improvement", "minimum_absolute_improvement", "maximum_position_regression"):
        number(registration[field], field, positive=True)
        if registration[field] >= 1:
            raise ValueError(f"{field}: must be < 1")
    ids, view_hashes, reference_hashes = set(), set(), set()
    for collection in ("views", "references"):
        if not isinstance(value[collection], list):
            raise ValueError(f"{collection}: expected list")
        for item in value[collection]:
            keys = "id path sha256 role camera"
            if collection == "references":
                keys += " position known_length_m length_tolerance_m point_a_px point_b_px endpoint_tolerance_px plane_verified"
            strict_keys(item, keys, collection)
            for field in ("id", "path"):
                text(item[field], field)
            path = Path(item["path"])
            if path.is_absolute() or path.drive or ".." in path.parts:
                raise ValueError("image path must be relative and remain within the manifest directory")
            sha = item["sha256"]
            if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                raise ValueError("invalid sha256")
            if item["id"] in ids:
                raise ValueError("duplicate input id")
            ids.add(item["id"])
            if item["camera"] != value["camera"]:
                raise ValueError("camera settings mismatch; profile reuse invalid")
            hashes = view_hashes if collection == "views" else reference_hashes
            if sha in hashes:
                raise ValueError("duplicate input hash; repeated captures must be independent")
            hashes.add(sha)
            if collection == "views":
                if item["role"] not in ("fit", "held_out"):
                    raise ValueError("invalid view role")
            else:
                if item["role"] not in ("anchor", "check") or item["position"] not in POSITIONS:
                    raise ValueError("invalid reference role/position")
                validate_reference(item, value["camera"])
    if view_hashes & reference_hashes:
        raise ValueError("fitting/held-out board and independent references overlap")
    anchors = [r for r in value["references"] if r["role"] == "anchor"]
    if len(anchors) > 1 or (anchors and anchors[0]["position"] != "centre"):
        raise ValueError("exactly one central scene anchor permitted")


def validate_reference(item, camera):
    for field in ("known_length_m", "length_tolerance_m", "endpoint_tolerance_px"):
        number(item[field], field, positive=True)
    if item["length_tolerance_m"] >= item["known_length_m"] or item["plane_verified"] is not True:
        raise ValueError("reference: physical length/scene plane verification invalid")
    for field in ("point_a_px", "point_b_px"):
        point = item[field]
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"{field}: expected two coordinates")
        for coordinate, limit in zip(point, (camera["width_px"], camera["height_px"])):
            number(coordinate, field)
            if coordinate >= limit:
                raise ValueError("reference outside display-oriented pixel window")
    points = np.array([item["point_a_px"], item["point_b_px"]])
    if np.linalg.norm(points[1] - points[0]) <= 2 * math.sqrt(2) * item["endpoint_tolerance_px"]:
        raise ValueError("reference separation insufficient for endpoint tolerance")
    midpoint = points.mean(axis=0) / [camera["width_px"], camera["height_px"]]
    x, y = midpoint
    regions = dict(centre=1/3 <= x <= 2/3 and 1/3 <= y <= 2/3,
                   left=x < 1/3, right=x > 2/3, top=y < 1/3, bottom=y > 2/3)
    if not regions[item["position"]]:
        raise ValueError("reference position does not match endpoint midpoint")


def detect_view(image, board, identity, role):
    parameters = cv2.aruco.CharucoParameters()
    parameters.minMarkers = OPTIONS["charuco_min_markers"]
    parameters.tryRefineMarkers = OPTIONS["charuco_try_refine_markers"]
    detector = cv2.aruco.DetectorParameters()
    detector.cornerRefinementMethod = OPTIONS["detector_corner_refinement_method"]
    corners, ids, _, _ = cv2.aruco.CharucoDetector(board, parameters, detector).detectBoard(image)
    result = dict(id=identity, role=role, image_points_px=[], object_points_m=[], corner_ids=[])
    if ids is None or corners is None:
        return dict(result, rejection_reason="no_charuco_corners")
    result["corner_ids"] = ids.ravel().tolist()
    result["image_points_px"] = corners.reshape(-1, 2).tolist()
    if len(set(result["corner_ids"])) != len(ids) or board.checkCharucoCornersCollinear(ids):
        return dict(result, rejection_reason="duplicate_or_collinear_corners")
    objects, points = board.matchImagePoints(corners, ids)
    result.update(image_points_px=points.reshape(-1, 2).tolist(),
                  object_points_m=objects.reshape(-1, 3).tolist())
    reason = view_rejection(result, image.shape[1::-1])
    if reason:
        result["rejection_reason"] = reason
    return result


def view_rejection(view, size):
    points = np.asarray(view["image_points_px"], dtype=np.float64)
    objects = np.asarray(view["object_points_m"], dtype=np.float64)
    if points.ndim != 2 or points.shape[1:] != (2,) or len(points) < OPTIONS["min_corners"]:
        return "insufficient_corners"
    if objects.shape != (len(points), 3) or not np.isfinite(points).all() or not np.isfinite(objects).all():
        return "invalid_correspondences"
    if np.any(points < 0) or np.any(points >= size) or np.any(objects[:, 2] != 0):
        return "invalid_coordinate_window_or_board_plane"
    if np.linalg.matrix_rank(objects[:, :2] - objects[:, :2].mean(axis=0)) < 2:
        return "collinear_corners"
    if cv2.contourArea(cv2.convexHull(points.astype(np.float32))) / (size[0] * size[1]) < OPTIONS["min_hull_fraction"]:
        return "insufficient_corner_coverage"
    return None


def homography_constraints(objects, points):
    homography, _ = cv2.findHomography(objects[:, :2], points, method=0)
    if homography is None or not np.isfinite(homography).all():
        raise ValueError("invalid_homography")
    def constraint(i, j):
        a, b = homography[:, i], homography[:, j]
        return np.array([a[0]*b[0], a[0]*b[1]+a[1]*b[0], a[1]*b[1],
                         a[2]*b[0]+a[0]*b[2], a[2]*b[1]+a[1]*b[2], a[2]*b[2]])
    rows = [constraint(0, 1), constraint(0, 0) - constraint(1, 1)]
    return [row / np.linalg.norm(row) for row in rows]


def fit_calibration(views, size):
    cv2.setNumThreads(1)
    cv2.setRNGSeed(0)
    accepted, rejected, reasons = [], [], []
    for view in sorted(views, key=lambda v: v["id"]):
        if view["role"] != "fit":
            continue
        reason = view.get("rejection_reason") or view_rejection(view, size)
        if reason:
            rejected.append(dict(id=view["id"], reason=reason))
        else:
            accepted.append(view)
    report = dict(status="rejected", rejected_views=rejected,
                  fit_view_ids=[v["id"] for v in accepted], rejection_reasons=reasons)
    if len(accepted) < OPTIONS["min_fit_views"]:
        reasons.append("insufficient_fit_views")
        return report
    all_points = np.concatenate([v["image_points_px"] for v in accepted])
    span = np.ptp(all_points, axis=0) / size
    cells = {tuple(np.minimum((np.mean(v["image_points_px"], axis=0) / size * 3).astype(int), 2)) for v in accepted}
    report["coverage"] = dict(span_fraction=span.tolist(), centroid_cells=len(cells))
    if np.any(span < OPTIONS["min_span_fraction"]) or len(cells) < OPTIONS["min_centroid_cells"]:
        reasons.append("inadequate_image_coverage")
    objects = [np.asarray(v["object_points_m"], np.float32) for v in accepted]
    points = [np.asarray(v["image_points_px"], np.float32) for v in accepted]
    try:
        constraints = np.array(list(itertools.chain.from_iterable(homography_constraints(o, p) for o, p in zip(objects, points))))
        # Column scaling makes this preliminary rank check insensitive to pixel/metre units.
        constraints /= np.maximum(np.linalg.norm(constraints, axis=0), 1e-15)
        singular = np.linalg.svd(constraints, compute_uv=False)
        report["homography_rank_ratio"] = float(singular[-2] / singular[0])
        if report["homography_rank_ratio"] < OPTIONS["min_homography_rank_ratio"]:
            reasons.append("degenerate_view_geometry")
        if reasons:
            return report
        rms, camera, distortion, rotations, translations, intrinsic_std, extrinsic_std, errors = cv2.calibrateCameraExtended(
            objects, points, size, None, None, flags=OPTIONS["flags"],
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, OPTIONS["iterations"], OPTIONS["epsilon"]))
        arrays = [camera, distortion, intrinsic_std, extrinsic_std, errors, *rotations, *translations]
        if not math.isfinite(rms) or any(not np.isfinite(a).all() for a in arrays):
            reasons.append("nonfinite_calibration")
            return report
        tilts, diagnostics = [], []
        for view, obj, img, rotation, translation, error in zip(accepted, objects, points, rotations, translations, errors.ravel()):
            matrix = cv2.Rodrigues(rotation)[0]
            tilts.append(math.degrees(math.acos(float(np.clip(abs(matrix[2, 2]), 0, 1)))))
            projected = cv2.projectPoints(obj, rotation, translation, camera, distortion)[0].reshape(-1, 2)
            diagnostics.append(dict(id=view["id"], rvec=rotation.ravel().tolist(), tvec_m=translation.ravel().tolist(),
                                    rms_px=float(error), residuals_px=(projected - img).tolist()))
        report.update(camera_matrix=camera.tolist(), distortion=distortion.ravel().tolist(), rms_px=float(rms),
                      intrinsic_std=intrinsic_std.ravel().tolist(), extrinsic_std=extrinsic_std.ravel().tolist(),
                      fit_diagnostics=diagnostics, tilt_range_deg=max(tilts)-min(tilts))
        if report["tilt_range_deg"] < OPTIONS["min_tilt_range_deg"]:
            reasons.append("insufficient_pose_diversity")
        if rms > OPTIONS["max_rms_px"] or np.any(errors > OPTIONS["max_rms_px"]):
            reasons.append("excessive_fit_reprojection_error")
        if (camera[0, 0] <= 0 or camera[1, 1] <= 0 or not 0 <= camera[0, 2] < size[0]
                or not 0 <= camera[1, 2] < size[1] or any(t[2, 0] <= 0 for t in translations)):
            reasons.append("implausible_camera_or_pose")
        if not reasons:
            report["status"] = "accepted"
    except (cv2.error, ValueError, np.linalg.LinAlgError) as error:
        reasons.append(f"calibration_solver_failed: {error}")
    return report


def correct_points(points, camera, distortion):
    return cv2.undistortPoints(np.asarray(points, np.float64).reshape(-1, 1, 2),
                             np.asarray(camera, np.float64), np.asarray(distortion, np.float64),
                             P=np.asarray(camera, np.float64)).reshape(-1, 2)


def distance_interval(reference, transform):
    points = np.array([reference["point_a_px"], reference["point_b_px"]], dtype=float)
    tolerance = reference["endpoint_tolerance_px"]
    offsets = np.array(list(itertools.product((-tolerance, 0., tolerance), repeat=2)))
    distances = []
    for a, b in itertools.product(points[0] + offsets, points[1] + offsets):
        corrected = transform([a, b])
        distances.append(float(np.linalg.norm(corrected[1] - corrected[0])))
    nominal = transform(points)
    length = float(np.linalg.norm(nominal[1] - nominal[0]))
    if not all(math.isfinite(d) and d > 0 for d in distances):
        raise ValueError("reference transform has invalid separation")
    return length, min(distances), max(distances)


def compare_references(references, camera, distortion, registration):
    anchors = [r for r in references if r["role"] == "anchor"]
    report = dict(status="defer_missing_captures", missing_inputs=[], checks=[])
    if len(anchors) != 1:
        report["missing_inputs"].append("one_independent_central_scene_anchor")
        return report
    anchor = anchors[0]
    transforms = dict(raw=lambda p: np.asarray(p, float),
                      corrected=lambda p: correct_points(p, camera, distortion))
    scales = {}
    for name, transform in transforms.items():
        length, low, high = distance_interval(anchor, transform)
        known, tol = anchor["known_length_m"], anchor["length_tolerance_m"]
        scales[name] = (known / length, (known - tol) / high, (known + tol) / low)
    report["anchor_scales_m_per_px"] = {k: list(v) for k, v in scales.items()}
    for reference in sorted(references, key=lambda r: r["id"]):
        if reference["role"] != "check":
            continue
        row = dict(id=reference["id"], position=reference["position"],
                   raw_endpoints_px=[reference["point_a_px"], reference["point_b_px"]],
                   corrected_endpoints_px=correct_points([reference["point_a_px"], reference["point_b_px"]], camera, distortion).tolist())
        for name, transform in transforms.items():
            length, low, high = distance_interval(reference, transform)
            scale, scale_low, scale_high = scales[name]
            known, tol = reference["known_length_m"], reference["length_tolerance_m"]
            ratio_low, ratio_high = low * scale_low / (known + tol), high * scale_high / (known - tol)
            interval = [0. if ratio_low <= 1 <= ratio_high else min(abs(ratio_low-1), abs(ratio_high-1)),
                        max(abs(ratio_low-1), abs(ratio_high-1))]
            row[name] = dict(estimated_length_m=length*scale, signed_relative_error=length*scale/known-1,
                             absolute_relative_error=abs(length*scale/known-1),
                             absolute_relative_error_interval=interval, local_scale_m_per_px=known/length)
        report["checks"].append(row)
    checks = report["checks"]
    for position in POSITIONS:
        if sum(r["position"] == position for r in checks) < 2:
            report["missing_inputs"].append(f"two_independent_checks_at_{position}")
        orientations = set()
        for reference in references:
            if reference["role"] == "check" and reference["position"] == position:
                delta = np.abs(np.array(reference["point_b_px"]) - reference["point_a_px"])
                orientations.add("horizontal" if delta[0] > delta[1] else "vertical")
        if orientations != {"horizontal", "vertical"}:
            report["missing_inputs"].append(f"horizontal_and_vertical_checks_at_{position}")
    if not checks:
        return report
    for name in transforms:
        report[f"{name}_median_absolute_relative_error"] = float(np.median([r[name]["absolute_relative_error"] for r in checks]))
    report["by_position"] = {}
    for position in POSITIONS:
        rows = [r for r in checks if r["position"] == position]
        if rows:
            report["by_position"][position] = {name: dict(
                median_absolute_relative_error=float(np.median([r[name]["absolute_relative_error"] for r in rows])),
                local_scale_range_m_per_px=[min(r[name]["local_scale_m_per_px"] for r in rows), max(r[name]["local_scale_m_per_px"] for r in rows)])
                for name in transforms}
    raw, corrected = (report[f"{name}_median_absolute_relative_error"] for name in transforms)
    conservative_gain = float(np.median([r["raw"]["absolute_relative_error_interval"][0] for r in checks])
                              - np.median([r["corrected"]["absolute_relative_error_interval"][1] for r in checks]))
    report["conservative_improvement"] = conservative_gain
    useful = (raw > 0 and raw-corrected >= registration["minimum_absolute_improvement"]
              and (raw-corrected)/raw >= registration["minimum_relative_improvement"] and conservative_gain > 0
              and all(p["corrected"]["median_absolute_relative_error"] - p["raw"]["median_absolute_relative_error"]
                      <= registration["maximum_position_regression"] for p in report["by_position"].values()))
    if not report["missing_inputs"]:
        report["status"] = "use_for_tested_setup" if useful else "defer_no_useful_improvement"
    return report


def held_out_diagnostics(views, fit):
    camera, distortion = np.array(fit["camera_matrix"]), np.array(fit["distortion"])
    results = []
    for view in views:
        if view["role"] != "held_out":
            continue
        if view.get("rejection_reason"):
            results.append(dict(id=view["id"], rejection_reason=view["rejection_reason"]))
            continue
        objects, points = np.array(view["object_points_m"], np.float32), np.array(view["image_points_px"], np.float32)
        try:
            ok, rotation, translation = cv2.solvePnP(objects, points, camera, distortion)
            if not ok or not np.isfinite(rotation).all() or not np.isfinite(translation).all() or translation[2, 0] <= 0:
                raise ValueError("invalid held-out pose")
            projected = cv2.projectPoints(objects, rotation, translation, camera, distortion)[0].reshape(-1, 2)
            residual = projected-points
            rms = float(np.sqrt(np.mean(np.sum(residual**2, axis=1))))
            if not math.isfinite(rms):
                raise ValueError("nonfinite held-out residual")
            results.append(dict(id=view["id"], rms_px=rms, residuals_px=residual.tolist(),
                                rvec=rotation.ravel().tolist(), tvec_m=translation.ravel().tolist()))
        except (cv2.error, ValueError) as error:
            results.append(dict(id=view["id"], rejection_reason=f"held_out_pose_failed: {error}"))
    return results


def run(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    value = load_json(manifest_path)
    validate_manifest(value)
    packages = {name: importlib.metadata.version(name) for name in ("opencv-contrib-python", "numpy")}
    if packages != {"opencv-contrib-python": "4.12.0.88", "numpy": "2.2.6"} or cv2.__version__ != "4.12.0":
        raise ValueError("use the pinned research/opencv-tracking environment")
    cv2.setNumThreads(1)
    cv2.setRNGSeed(0)
    board = make_board(value["board"])
    views, inputs = [], []
    size = (value["camera"]["width_px"], value["camera"]["height_px"])
    for item in sorted(value["views"] + value["references"], key=lambda v: v["id"]):
        path = (manifest_path.parent / item["path"]).resolve()
        if not path.is_relative_to(manifest_path.parent):
            raise ValueError("resolved image path leaves the manifest directory")
        if digest(path) != item["sha256"]:
            raise ValueError(f"input hash mismatch: {item['id']}")
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION)
        if image is None or image.shape[1::-1] != size:
            raise ValueError(f"input resolution/decode mismatch: {item['id']}")
        inputs.append(dict(id=item["id"], path=item["path"], sha256=item["sha256"]))
        if item in value["views"]:
            views.append(detect_view(image, board, item["id"], item["role"]))
    fit = fit_calibration(views, size)
    report = dict(schema_version=VERSION, implementation="openbar-research-charuco", method_version=VERSION,
                  recommendation="defer_missing_captures", missing_inputs=[], board=value["board"],
                  camera=value["camera"], preregistration=value["preregistration"], options=OPTIONS,
                  observations=views, reference_observations=value["references"], calibration=fit, inputs=inputs,
                  provenance=dict(manifest_sha256=digest(manifest_path), source_sha256=digest(__file__),
                                  requirements_sha256=digest(ROOT / "research/opencv-tracking/requirements.txt"),
                                  board_config_sha256=hashlib.sha256(json.dumps(value["board"], sort_keys=True, allow_nan=False).encode()).hexdigest(),
                                  python=platform.python_version(), platform=platform.platform(), packages=packages,
                                  opencv_version=cv2.__version__, opencv_build_sha256=hashlib.sha256(cv2.getBuildInformation().encode()).hexdigest()),
                  limitations=["Research quality gates are heuristics, not validated accuracy limits.",
                               "Endpoint perturbation ranges are sampled sensitivity, not confidence intervals.",
                               "Camera intrinsics do not establish scene scale or plate-centre ground truth."])
    if fit["status"] == "accepted":
        held = held_out_diagnostics(views, fit)
        comparison = compare_references(value["references"], fit["camera_matrix"], fit["distortion"], value["preregistration"])
        report.update(held_out_diagnostics=held, comparison=comparison)
        report["missing_inputs"] = comparison["missing_inputs"][:]
        if len(held) < 3:
            report["missing_inputs"].append("three_independent_held_out_board_views")
        held_failure = any(v.get("rejection_reason") or v["rms_px"] > OPTIONS["max_rms_px"] for v in held)
        report["recommendation"] = ("defer_missing_captures" if report["missing_inputs"] else
                                    "defer_held_out_quality" if held_failure else comparison["status"])
    else:
        report["missing_inputs"] = ["adequate_multiview_board_captures", "held_out_board_views",
                                    "independent_measured_anchor_and_repeated_reference_checks"]
        if views:
            report["recommendation"] = "defer_calibration_rejected"
    json.dumps(report, sort_keys=True, allow_nan=False)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("board", help="render a board; verify the physical print before capture")
    render.add_argument("--config", required=True, type=Path)
    render.add_argument("--output", required=True, type=Path)
    experiment = commands.add_parser("run", help="evaluate a preregistered research manifest")
    experiment.add_argument("--manifest", required=True, type=Path)
    experiment.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "board":
            config = load_json(args.config)
            board = make_board(config, require_physical=False)
            if args.output.suffix.lower() != ".png" or args.output.resolve() == args.config.resolve():
                raise ValueError("board output must be a separate PNG")
            image = board.generateImage((config["squares_x"]*100+60, config["squares_y"]*100+60), marginSize=30)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(args.output), image):
                raise ValueError("board image write failed")
        else:
            value = load_json(args.manifest)
            validate_manifest(value)
            protected = {args.manifest.resolve(), Path(__file__).resolve()}
            protected.update((args.manifest.parent / v["path"]).resolve() for v in value.get("views", []) + value.get("references", []))
            if args.output.resolve() in protected:
                raise ValueError("report output would overwrite a retained input")
            report = run(args.manifest)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
            print(report["recommendation"])
    except (ValueError, OSError, cv2.error) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
