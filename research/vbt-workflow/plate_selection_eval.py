"""Frozen plate-selection executor (#178): validate a freeze record, then run the baseline once.

Research only. ``validate`` reads the record, manifests and hashes; it decodes nothing. ``run``
repeats every check, then decodes one frame per clip at the record's fixed time and executes the
unchanged ``suggest_plate`` (``plate-hough-edge-v1``). It writes raw observations and timings
only: labels are hashed, never read, so truth cannot reach the candidate. Scoring is a separate
step. Rules: docs/validation/PLATE_SELECTION_FREEZE_RECORD.md and PLATE_SELECTION_SPLIT.md.

The executor fails closed: any check below that does not pass stops it before a frame is decoded
or a run directory is created.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "tools"))
import held_out_guard  # noqa: E402
import label_package  # noqa: E402  (shared probe/decode contract with the labelling tool)
import manifest_roles  # noqa: E402  (shared session rule)

EXECUTOR_ID = "plate-selection-executor-v1"
METHOD_ID = "plate-hough-edge-v1"
FROZEN_TAG, EXPLORATION_TAG = "split-frozen-eval", "split-exploration"
SUGGEST_PATH = "research/vbt-workflow/vbt_suggest.py"
SELF_PATH = "research/vbt-workflow/plate_selection_eval.py"
GUARD_PATH = "validation/tools/held_out_guard.py"
# Code that decides the frame, the session rule or the recorded output; pinned to code_commit.
PINNED_PATHS = (SUGGEST_PATH, SELF_PATH, "research/vbt-workflow/hough_observe.py",
                "validation/tools/label_package.py", "validation/tools/manifest_roles.py")
OTHER_MANIFESTS = ("validation/private/vbt/manifest.json",)  # role-checked when present
PRIVATE_RUNS = Path("validation") / "private" / "frozen-eval" / "runs"
STARTED, CLIP_LOG, OBSERVATIONS = "started.json", "clips.jsonl", "observations.json"
EXPECTED_POLICIES = {"select", "refuse_ambiguous", "refuse_no_target", "refuse_unsupported",
                     "refuse_no_pre_movement", "indeterminate"}
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER = re.compile(r"<[A-Za-z_]")  # "<PENDING>", "<sha256>"; prose such as "x < 3" passes

# Required keys, mirroring the template in PLATE_SELECTION_FREEZE_RECORD.md.
REQUIRED: dict[str, Any] = {
    "record_version": None, "freeze_id": None, "supersedes": None, "created_utc": None,
    "created_by": None,
    "authority": {"plan_path": None, "plan_commit": None, "split_doc_commit": None,
                  "owner_decision_url": None, "owner_decision_record": None,
                  "allowed_entry_point": None},
    "inputs": {"manifest_path": None, "manifest_sha256": None, "sessions": None, "clips": None,
               "labels_csv": {"path": None, "sha256": None}, "synthetic_inputs": None},
    "reserved_exclusion": {"tag": None, "clips_in_record": None, "guard_module": None,
                           "guard_commit": None},
    "candidates": None,
    "search_rules": {"frame_search_horizon_s": None, "timestamp_sampling": None,
                     "target_identity_rule": None, "tie_and_ambiguity_rule": None,
                     "refusal_rules": None, "per_clip_timeout_s": None, "resource_limits": None},
    "scoring": {"outcome_definitions": None, "count_once_before_correction": None,
                "intervals": None, "strata": None, "geometry_reference": None,
                "latency": {"cold_definition": None, "warm_definition": None, "repeats": None,
                            "stages_recorded": None}},
    "decision_limits": {"mode": None, "numeric_limits": None, "rationale": None},
    "environment": {"os": None, "hardware": None, "python": None, "opencv": None,
                    "ffmpeg": None, "dependency_lock": None},
    "statement": None,
}
CLIP_KEYS = ("fixture_id", "session_id", "role", "media_sha256", "purpose", "challenge",
             "expected_policy", "decision_frame_pts_s", "label_files", "adjudication")
CANDIDATE_KEYS = ("name", "method_id", "code_commit", "entry_point", "parameters",
                  "parameters_sha256", "model", "added_rules")


class FreezeError(RuntimeError):
    """The record or its inputs do not permit a frozen run."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parameters_sha256(parameters: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(parameters, sort_keys=True).encode("utf-8")).hexdigest()


def _reject_constant(name: str) -> None:
    raise FreezeError(f"record contains non-finite value {name}")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)


def require_keys(document: Any, spec: dict[str, Any], where: str = "record") -> None:
    if not isinstance(document, dict):
        raise FreezeError(f"{where} must be an object")
    for key, child in spec.items():
        if key not in document:
            raise FreezeError(f"{where} is missing {key!r}")
        if child is not None:
            require_keys(document[key], child, f"{where}.{key}")


def find_placeholders(value: Any, where: str = "record") -> list[str]:
    """Paths of every string still written as a ``<...>`` placeholder (terminated or not)."""
    if isinstance(value, dict):
        return [hit for key, child in value.items() for hit in find_placeholders(child, f"{where}.{key}")]
    if isinstance(value, list):
        return [hit for index, child in enumerate(value) for hit in find_placeholders(child, f"{where}[{index}]")]
    if isinstance(value, str) and PLACEHOLDER.search(value):
        return [where]
    return []


def require_text(value: Any, where: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise FreezeError(f"{where} must be a non-empty string")


def require_number(value: Any, where: str, low: float, high: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) \
            or not low <= value <= high:
        raise FreezeError(f"{where} must be a finite number in [{low}, {high}]")
    return float(value)


def git_file_matches(repo: Path, commit: str, path: str) -> bool:
    """True when ``commit`` is a commit and ``path`` is committed there and unchanged in the tree.

    Line endings follow git's own conversion (``core.autocrlf``), as git itself compares them.
    """
    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)

    if git("rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}").returncode != 0:
        return False
    if git("cat-file", "-e", f"{commit}:{path}").returncode != 0:
        return False
    status = git("status", "--porcelain", "--", path)
    return (git("diff", "--quiet", "--no-ext-diff", "--no-textconv", commit, "--", path).returncode == 0
            and status.returncode == 0 and not status.stdout.strip())


def git_commit_exists(repo: Path, commit: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}"],
                          capture_output=True, check=False).returncode == 0


def default_plate_parameters() -> dict[str, Any]:
    import vbt_suggest  # needs OpenCV; imported only when a candidate is checked or run

    return asdict(vbt_suggest.PlateParams())


def validate_header(record: dict[str, Any]) -> None:
    if type(record["record_version"]) is not int or record["record_version"] != 1:
        raise FreezeError(f"unsupported record_version {record['record_version']!r}")
    status = record.get("status")
    if isinstance(status, str) and status.upper().startswith("DRAFT"):
        raise FreezeError("record is a DRAFT")
    placeholders = find_placeholders(record)
    if placeholders:
        raise FreezeError(f"unfilled placeholders: {', '.join(placeholders)}")
    for key in ("freeze_id", "created_utc", "created_by", "statement"):
        require_text(record[key], key)
    for key, value in record["environment"].items():
        require_text(value, f"environment.{key}")
    authority = record["authority"]
    if not any(isinstance(authority[key], str) and authority[key].strip()
               for key in ("owner_decision_url", "owner_decision_record")):
        raise FreezeError("authority needs the owner decision URL or record")
    if authority["allowed_entry_point"] != SELF_PATH:
        raise FreezeError(f"allowed_entry_point must be {SELF_PATH}")
    for key in ("plan_commit", "split_doc_commit"):
        if not isinstance(authority[key], str) or not COMMIT.match(authority[key]):
            raise FreezeError(f"authority.{key} must be a full 40-hex commit")


def validate_rules(record: dict[str, Any]) -> None:
    exclusion = record["reserved_exclusion"]
    if (exclusion["tag"] != held_out_guard.RESERVED_TAG or exclusion["clips_in_record"] != []
            or exclusion["guard_module"] != GUARD_PATH):
        raise FreezeError("reserved_exclusion must name split-reserved-57 and the guard, with no clips")
    if record["decision_limits"]["mode"] != "descriptive":
        raise FreezeError("only decision_limits.mode = descriptive is supported")
    rules = record["search_rules"]
    if rules["frame_search_horizon_s"] != 0.0 or isinstance(rules["frame_search_horizon_s"], bool):
        raise FreezeError("this executor implements only the single fixed-time rule (horizon 0.0 s)")
    require_number(rules["per_clip_timeout_s"], "per_clip_timeout_s", 1e-3, 3600)
    repeats = record["scoring"]["latency"]["repeats"]
    if type(repeats) is not int or not 1 <= repeats <= 100:
        raise FreezeError("latency.repeats must be an integer in [1, 100]")


def validate_clip(clip: Any) -> None:
    require_keys(clip, dict.fromkeys(CLIP_KEYS), f"clip {clip.get('fixture_id') if isinstance(clip, dict) else '?'}")
    name = clip["fixture_id"]
    require_text(name, "clip.fixture_id")
    require_text(clip["session_id"], f"{name}: session_id")
    if clip["role"] != FROZEN_TAG:
        raise FreezeError(f"{name}: role must be {FROZEN_TAG}")
    if clip["expected_policy"] not in EXPECTED_POLICIES:
        raise FreezeError(f"{name}: unknown expected_policy {clip['expected_policy']!r}")
    require_number(clip["decision_frame_pts_s"], f"{name}: decision_frame_pts_s", 0.0, 86400.0)
    hashes = [clip["media_sha256"], *(label.get("sha256") for label in clip["label_files"]
                                      if isinstance(label, dict))]
    if sorted(label.get("annotator") for label in clip["label_files"] if isinstance(label, dict)) != ["a", "b"] \
            or len(clip["label_files"]) != 2:
        raise FreezeError(f"{name}: label_files must hold exactly the passes a and b")
    adjudication = clip["adjudication"]
    if not isinstance(adjudication, dict) or not isinstance(adjudication.get("unresolved"), bool):
        raise FreezeError(f"{name}: adjudication needs path, sha256 and a boolean unresolved")
    if adjudication.get("path") is not None:
        hashes.append(adjudication.get("sha256"))
    if not all(isinstance(value, str) and SHA256.match(value) for value in hashes):
        raise FreezeError(f"{name}: every media, label and adjudication hash must be 64-hex sha256")


def validate_structure(record: dict[str, Any]) -> None:
    require_keys(record, REQUIRED)
    validate_header(record)
    validate_rules(record)
    clips = record["inputs"]["clips"]
    if not isinstance(clips, list) or not clips:
        raise FreezeError("inputs.clips must be a non-empty list")
    for clip in clips:
        validate_clip(clip)
    ids = [clip["fixture_id"] for clip in clips]
    if len(set(ids)) != len(ids):
        raise FreezeError("inputs.clips lists a fixture twice")
    listed = {session.get("session_id") for session in record["inputs"]["sessions"] if isinstance(session, dict)}
    if listed != {clip["session_id"] for clip in clips} or len(listed) != len(record["inputs"]["sessions"]):
        raise FreezeError("inputs.sessions must list exactly the clips' sessions, once each")
    candidates = record["candidates"]
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise FreezeError("exactly one candidate is supported")
    candidate = candidates[0]
    require_keys(candidate, dict.fromkeys(CANDIDATE_KEYS), "candidate")
    if candidate["method_id"] != METHOD_ID or candidate["entry_point"] != SUGGEST_PATH \
            or candidate["model"] is not None or candidate["added_rules"] != "none":
        raise FreezeError(f"the candidate must be the unchanged {METHOD_ID} baseline in {SUGGEST_PATH}")
    for value, where in ((candidate["code_commit"], "code_commit"),
                         (record["reserved_exclusion"]["guard_commit"], "guard_commit")):
        if not isinstance(value, str) or not COMMIT.match(value):
            raise FreezeError(f"{where} must be a full 40-hex commit")


def frozen_sessions_crossing(fixtures: list[dict[str, Any]], sessions: set[str]) -> list[dict[str, Any]]:
    by_id = {fixture.get("id"): fixture for fixture in fixtures}
    return [violation for violation in manifest_roles.check(fixtures)["violations"]
            if violation.get("session") in sessions
            or (violation.get("fixture") in by_id and manifest_roles.session_of(by_id[violation["fixture"]]) in sessions)]


def validate_inputs(record: dict[str, Any], repo_root: Path, allow_held_out: bool) -> dict[str, dict[str, Any]]:
    """Check every hash and role; return manifest fixtures keyed by id."""
    inputs = record["inputs"]
    manifest_path = repo_root / inputs["manifest_path"]
    if sha256_file(manifest_path) != inputs["manifest_sha256"]:
        raise FreezeError("manifest sha256 does not match the record")
    manifest = load_json(manifest_path)
    fixtures = {fixture["id"]: fixture for fixture in manifest["fixtures"]}
    sessions = {clip["session_id"] for clip in inputs["clips"]}
    others = [fixture for path in OTHER_MANIFESTS if (repo_root / path).exists()
              for fixture in load_json(repo_root / path)["fixtures"]]
    crossing = frozen_sessions_crossing([*fixtures.values(), *others], sessions)
    if crossing:
        raise FreezeError(f"a frozen session breaks the one-role-per-session rule: {crossing}")
    hashed = [(inputs["labels_csv"]["path"], inputs["labels_csv"]["sha256"])]
    for clip in inputs["clips"]:
        name, fixture = clip["fixture_id"], fixtures.get(clip["fixture_id"])
        if fixture is None:
            raise FreezeError(f"{name} is not in the manifest")
        tags = manifest_roles.tags_of(fixture)
        if held_out_guard.RESERVED_TAG in tags:
            raise FreezeError(f"{name} is {held_out_guard.RESERVED_TAG}; never run")
        if manifest_roles.roles_of(fixture) != [FROZEN_TAG]:
            raise FreezeError(f"{name} is not tagged {FROZEN_TAG} alone in the manifest")
        session = manifest_roles.session_of(fixture)
        if session is None or session != clip["session_id"]:
            raise FreezeError(f"{name}: session_id does not match the manifest session {session!r}")
        if fixture.get("purpose") != clip["purpose"]:
            raise FreezeError(f"{name}: purpose differs between record and manifest")
        if held_out_guard.is_held_out(fixture) and not allow_held_out:
            raise FreezeError(f"{name} is held-out; pass --allow-held-out inside the frozen run")
        if fixture["media"]["sha256"] != clip["media_sha256"]:
            raise FreezeError(f"{name}: media sha256 differs between record and manifest")
        hashed.append((fixture["media"]["repository_path"], clip["media_sha256"]))
        hashed.extend((label["path"], label["sha256"]) for label in clip["label_files"])
        if clip["adjudication"]["path"] is not None:
            hashed.append((clip["adjudication"]["path"], clip["adjudication"]["sha256"]))
    for path, expected in hashed:
        if sha256_file(repo_root / path) != expected:
            raise FreezeError(f"{path} does not match its sha256 in the record")
    return fixtures


def validate_code(record: dict[str, Any], code_root: Path,
                  file_matches: Callable[[Path, str, str], bool] = git_file_matches,
                  commit_exists: Callable[[Path, str], bool] = git_commit_exists) -> None:
    candidate = record["candidates"][0]
    if candidate["parameters"] != default_plate_parameters():
        raise FreezeError("candidate parameters differ from the unchanged PlateParams defaults")
    if parameters_sha256(candidate["parameters"]) != candidate["parameters_sha256"]:
        raise FreezeError("parameters_sha256 does not match the parameter dump")
    pinned = [(path, candidate["code_commit"]) for path in PINNED_PATHS]
    pinned.append((GUARD_PATH, record["reserved_exclusion"]["guard_commit"]))
    for path, commit in pinned:
        if not file_matches(code_root, commit, path):
            raise FreezeError(f"{path} is not the committed code at {commit}")
    for key in ("plan_commit", "split_doc_commit"):
        if not commit_exists(code_root, record["authority"][key]):
            raise FreezeError(f"authority.{key} is not a commit in this repository")


def load_record(record_path: Path, record_sha256: str) -> dict[str, Any]:
    if sha256_file(record_path) != record_sha256:
        raise FreezeError("record sha256 does not match --record-sha256")
    record = load_json(record_path)
    if not isinstance(record, dict):
        raise FreezeError("record must be a JSON object")
    return record


def validate(record_path: Path, record_sha256: str, repo_root: Path, allow_held_out: bool,
             file_matches: Callable[[Path, str, str], bool] = git_file_matches,
             commit_exists: Callable[[Path, str], bool] = git_commit_exists,
             code_root: Path = ROOT) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    try:
        record = load_record(record_path, record_sha256)
        validate_structure(record)
        fixtures = validate_inputs(record, repo_root, allow_held_out)
        validate_code(record, code_root, file_matches, commit_exists)
    except (KeyError, TypeError, AttributeError, OSError, ValueError) as error:
        raise FreezeError(f"record or inputs are malformed: {type(error).__name__}: {error}") from error
    return record, fixtures


def nearest_frame(timestamps_s: list[float], requested_s: float) -> int:
    """Index of the decoded frame nearest ``requested_s``; a tie takes the earlier frame.

    ``requested_s`` is seconds from the stream start, ``(pts - start_pts) * time_base``, as in
    ``label_package``. A time outside the decoded range is refused, never clamped.
    """
    if not timestamps_s:
        raise FreezeError("no decoded frames")
    if not math.isfinite(requested_s) or not timestamps_s[0] <= requested_s <= timestamps_s[-1]:
        raise FreezeError(f"requested time {requested_s!r} s is outside the decoded range "
                          f"{timestamps_s[0]}-{timestamps_s[-1]} s")
    right = bisect.bisect_left(timestamps_s, requested_s)
    candidates = [index for index in (right - 1, right) if 0 <= index < len(timestamps_s)]
    return min(candidates, key=lambda index: (abs(timestamps_s[index] - requested_s), index))


def decode_frame(media: Path, fixture: dict[str, Any], requested_s: float, work_dir: Path) -> tuple[Any, dict[str, Any]]:
    """Re-check the media hash, probe, then extract exactly one PTS-checked display-oriented frame."""
    import cv2  # research venv

    label_package.require_media_hash(fixture, media)
    started = time.perf_counter()
    probed = label_package.probe(media)
    label_package.require_fixture_probe_match(fixture, probed)
    probe_s = time.perf_counter() - started
    index = nearest_frame(probed["timestamps_s"], requested_s)
    started = time.perf_counter()
    [path] = label_package.extract(media, [index], [probed["pts"][index]], work_dir, fixture)
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    decode_s = time.perf_counter() - started
    if image is None:
        raise FreezeError(f"cannot read decoded frame {path.name}")
    return image, {"requested_time_s": requested_s, "frame_index": index, "pts": probed["pts"][index],
                   "actual_time_s": probed["timestamps_s"][index], "probe_s": probe_s, "decode_s": decode_s}


def run_clip(image: Any, repeats: int) -> dict[str, Any]:
    """Time the unchanged suggest_plate, then observe its stages once; no labels are available here.

    Cold is the first call on this frame in the run process (the first clip also pays library
    warm-up); warm is the median of ``repeats`` further calls. The observer runs last because its
    profile hook distorts timing.
    """
    import hough_observe
    import vbt_suggest

    started = time.perf_counter()
    final = vbt_suggest.suggest_plate(image)
    cold_s = time.perf_counter() - started
    warm = []
    for _ in range(repeats):
        started = time.perf_counter()
        if vbt_suggest.suggest_plate(image) != final:
            raise FreezeError("suggest_plate is not repeatable on this frame")
        warm.append(time.perf_counter() - started)
    observation = hough_observe.observe_plate(image)
    if observation["final"] != final:
        raise FreezeError("observation changed the baseline result")
    return {"final": final, "observation": observation,
            "latency": {"cold_search_s": cold_s, "warm_search_s": warm,
                        "warm_median_s": statistics.median(warm)}}


def current_environment() -> dict[str, str]:
    """Versions actually used by this run, beside the record's declared environment."""
    import cv2

    return {"python": sys.version.split()[0], "opencv": cv2.__version__,
            "ffmpeg": label_package.run(["ffmpeg", "-version"]).stdout.splitlines()[0]}


def run_identity(record: dict[str, Any], record_sha256: str) -> dict[str, Any]:
    """What ``started.json`` stores so a later run can tell whether it repeats this one."""
    candidate = record["candidates"][0]
    return {"executor": EXECUTOR_ID, "freeze_id": record["freeze_id"], "record_sha256": record_sha256,
            "supersedes": record["supersedes"], "method_id": candidate["method_id"],
            "code_commit": candidate["code_commit"], "parameters_sha256": candidate["parameters_sha256"],
            "media_sha256": sorted(clip["media_sha256"] for clip in record["inputs"]["clips"]),
            "environment": current_environment()}


def previous_runs(runs_root: Path, identity: dict[str, Any]) -> list[str]:
    """Earlier runs this one would repeat; a directory without a readable marker counts too.

    A run repeats an earlier one when it has the same record or freeze id, or runs the same
    candidate parameters on any of the same media, unless ``supersedes`` names that earlier freeze.
    """
    found = []
    for directory in sorted(path for path in runs_root.iterdir() if path.is_dir()) if runs_root.exists() else []:
        try:
            started = json.loads((directory / STARTED).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            started = None
        if not isinstance(started, dict):
            found.append(f"{directory.name} (no readable {STARTED})")  # fail closed
            continue
        same_freeze = (started.get("record_sha256") == identity["record_sha256"]
                       or started.get("freeze_id") == identity["freeze_id"])
        overlap = (started.get("parameters_sha256") == identity["parameters_sha256"]
                   and set(started.get("media_sha256") or []) & set(identity["media_sha256"]))
        if same_freeze or (overlap and identity["supersedes"] != started.get("freeze_id")):
            found.append(directory.name)
    return found


def serialisable(entry: dict[str, Any]) -> str:
    """One JSON line; a value that cannot be written becomes the clip's error, never a lost run."""
    try:
        return json.dumps(entry, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        return json.dumps({"fixture_id": entry["fixture_id"], "wall_s": entry.get("wall_s"),
                           "timed_out": entry.get("timed_out"),
                           "error": f"unserialisable result: {type(error).__name__}: {error}"}, sort_keys=True)


def run(record_path: Path, record_sha256: str, repo_root: Path, output_dir: Path, allow_held_out: bool,
        decode: Callable[..., tuple[Any, dict[str, Any]]] = decode_frame,
        execute: Callable[[Any, int], dict[str, Any]] = run_clip,
        file_matches: Callable[[Path, str, str], bool] = git_file_matches,
        commit_exists: Callable[[Path, str], bool] = git_commit_exists, code_root: Path = ROOT) -> Path:
    # Frames may only be written under the code checkout's validation/private (label_package rule);
    # every check runs before anything is created, so a refused run burns nothing.
    if repo_root.resolve() != code_root.resolve():
        raise FreezeError("run from the checkout that holds validation/private (frames stay private)")
    runs_root = (repo_root / PRIVATE_RUNS).resolve()
    if output_dir.exists():
        raise FreezeError(f"{output_dir} exists; a frozen candidate is run once")
    if output_dir.resolve().parent != runs_root:
        raise FreezeError(f"run output must be a new directory directly under {PRIVATE_RUNS.as_posix()}/")
    record, fixtures = validate(record_path, record_sha256, repo_root, allow_held_out,
                                file_matches, commit_exists, code_root)
    current = run_identity(record, record_sha256)
    earlier = previous_runs(runs_root, current)
    if earlier:
        raise FreezeError(f"this candidate already ran on these clips: {earlier}")
    rules, repeats = record["search_rules"], record["scoring"]["latency"]["repeats"]
    # Write the marker in a sibling scratch directory, then rename it into place, so the run
    # directory never exists without its marker.
    runs_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=runs_root.parent, prefix=".staging-"))
    (staging / STARTED).write_text(json.dumps(current, sort_keys=True) + "\n", encoding="utf-8")
    staging.rename(output_dir)
    lines = []
    with tempfile.TemporaryDirectory(dir=output_dir) as scratch, \
            (output_dir / CLIP_LOG).open("w", encoding="utf-8", newline="\n") as log:
        for clip in record["inputs"]["clips"]:
            fixture = fixtures[clip["fixture_id"]]
            started = time.perf_counter()
            entry: dict[str, Any] = {"fixture_id": clip["fixture_id"]}
            try:
                image, frame = decode(repo_root / fixture["media"]["repository_path"], fixture,
                                      float(clip["decision_frame_pts_s"]), Path(scratch) / clip["fixture_id"])
                entry.update(frame=frame, **execute(image, repeats))
            except Exception as error:  # noqa: BLE001 - a clip failure is an outcome, never hidden
                entry["error"] = f"{type(error).__name__}: {error}"
            # The limit is checked after the clip, not enforced by preemption; over-limit is an outcome.
            entry["wall_s"] = time.perf_counter() - started
            entry["timed_out"] = entry["wall_s"] > rules["per_clip_timeout_s"]
            line = serialisable(entry)
            log.write(line + "\n")
            log.flush()
            lines.append(line)
    document = {"executor": EXECUTOR_ID, "freeze_id": record["freeze_id"], "record_sha256": record_sha256,
                "candidate": record["candidates"][0]["name"], "clips": [json.loads(line) for line in lines]}
    out = output_dir / OBSERVATIONS
    out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("validate", "run"):
        command = commands.add_parser(name)
        command.add_argument("--record", type=Path, required=True)
        command.add_argument("--record-sha256", required=True)
        command.add_argument("--repo-root", type=Path, default=ROOT, help="checkout holding validation/private")
        command.add_argument("--allow-held-out", action="store_true", help="only inside the frozen run")
        if name == "run":
            command.add_argument("--output-dir", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "validate":
            record, _ = validate(args.record, args.record_sha256, args.repo_root, args.allow_held_out)
            print(f"freeze record {record['freeze_id']} is complete; {len(record['inputs']['clips'])} clips")
        else:
            print(run(args.record, args.record_sha256, args.repo_root, args.output_dir, args.allow_held_out))
    except FreezeError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
