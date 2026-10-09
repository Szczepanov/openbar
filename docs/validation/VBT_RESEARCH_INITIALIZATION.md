# Research VBT machine initialization v1 (#113)

`vbt_session.py init-research` is an opt-in, research-only command. It turns the seed-frame
suggestions of one already-ingested #95 session into a machine-initialization record,
`machine-init.json`, for each clip whose suggestions pass the checks below. It exists because the
[proceed decision](../analysis/VBT_INITIALIZATION_DECISION.md) allows an opt-in, separately named
mode beside the unchanged human-confirmation path. It does not claim that the suggester is accurate:
a machine-initialized session is research evidence to be assessed, not a measurement to be trusted.

The command runs no tracking, analysis or calibration. It writes nothing except
`machine-init.json` in the session folder. It writes no seed, no session CSV, no personal manifest,
no `session.html`, no `session-input.csv` and no `session-record.json`. The separate research command
`run-research` (see [Research run](#research-run)) tracks and analyzes the initialized clips. The module is
`research/vbt-workflow/session_machine_init.py`; it is standard library only and needs neither
FFmpeg nor OpenCV.

```powershell
research/opencv-tracking/.venv/Scripts/python.exe research/vbt-workflow/vbt_session.py init-research `
  --session 2026-10-03 --profile validation/private/vbt/profiles/snatch-lab.json
```

The session must already exist from `ingest`. The profile path may be anywhere readable. Re-running
with the same inputs prints `unchanged`. Add `--force` only to replace a record whose content
differs. When no clip is initialized, the command still exits 0 and prints
`no clip initialized; use the #95 confirmation page for every clip`.

## Profile contract

The profile is one JSON object with exactly these keys. A missing or unknown key is refused, and
the message names it.

```json
{
  "format": "openbar-research-vbt-init-profile",
  "format_version": 1,
  "profile_id": "snatch-lab",
  "exercise": "snatch",
  "plate_diameter_m": 0.45,
  "stick_length_m": 1.30
}
```

| Key | Rule |
|---|---|
| `format` | exactly `openbar-research-vbt-init-profile` |
| `format_version` | the integer `1` (a boolean, string or `1.0` is refused) |
| `profile_id` | a string matching the session id pattern (`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`) |
| `exercise` | one of `snatch`, `clean`, `back_squat`; `other` is refused as ambiguous |
| `plate_diameter_m` | a finite JSON number greater than 0 (not a boolean, string, or an integer too large for a float) |
| `stick_length_m` | a finite JSON number greater than 0; carried because `run` requires `--stick-length-m` |

The JSON must be strict: `NaN`, `Infinity` and duplicate keys are refused. An invalid profile fails
the whole command with `error: ...` on stderr and exit code 1, and nothing is written.

## Per-clip evaluation

Clips are evaluated in session order. Every applicable reason is collected, then sorted. No
confidence threshold of any kind is applied: confidence is checked only for validity.

| Reason code | Applies when |
|---|---|
| `plate_suggestion_missing` | the plate suggestion status is not `suggested` |
| `stick_suggestion_missing` | the stick suggestion status is not `suggested` |
| `exercise_conflict` | the clip's registered exercise is set and differs from the profile exercise |
| `suggestion_confidence_invalid` | a suggested suggestion's confidence is missing, a boolean, non-numeric, non-finite, or outside [0, 1] |
| `suggestion_provenance_invalid` | a suggested suggestion's `id` or `method` is not a non-empty string, or its `parameters` is not an object |
| `plate_geometry_invalid` | a suggested plate's centre or radius is missing, non-finite or non-numeric; the radius is not positive; the centre is outside [0, width) x [0, height); or the circle is not inside [0, width] x [0, height] |
| `stick_geometry_invalid` | a suggested stick marker is missing, non-finite or non-numeric, or outside [0, width) x [0, height); or the two markers are at most 2.0 px apart |

The suggestion-level checks (confidence, provenance and geometry) apply only to a suggestion whose
status is `suggested`. The geometry rules are the ones `session_contract` applies to a confirmed
clip, replicated rather than imported. A clip with no reason is `initialized`. Any other clip is
`rejected`, with its reasons and no values. A rejected clip falls back to the #95 confirmation page,
and the command says so.

A session state whose structure is damaged (for example a non-object `suggestions`, a missing
`plate` or `stick`, or a non-integer `width_px`, `height_px` or `frame_index`) is not repaired and
not rejected per clip. The command fails with `error: ...` and exit code 1, and writes nothing.

## Record contract

`machine-init.json` is written with sorted keys, two-space indentation, no `NaN` and a trailing
newline, through a temporary file and an atomic replace. It holds no timestamp, random id, absolute
path, original file name or profile path.

```json
{
  "format": "openbar-research-vbt-machine-init", "format_version": 1,
  "origin": "machine", "human_confirmed": false, "research_only": true, "consumer_eligible": false,
  "session_id": "2026-10-03", "page_id": "<session page id>",
  "inputs": {"session_state_sha256": "<session.json bytes>", "profile_sha256": "<profile bytes>"},
  "profile": {"profile_id": "snatch-lab", "exercise": "snatch", "plate_diameter_m": 0.45, "stick_length_m": 1.3},
  "implementation": {"name": "session_machine_init", "version": 1, "source_sha256": "<this module>"},
  "suggester_environment": {},
  "clips": [{"clip_index": 0, "fixture_id": "vbt-...", "source_video_sha256": "...", "package_id": "...",
             "frame_index": 0, "timestamp_s": 0.0, "width_px": 1080, "height_px": 1920,
             "outcome": "initialized", "reasons": [],
             "items": {"plate_center": {"origin": "machine", "values": {"center_x_px": 0.0, "center_y_px": 0.0},
                                        "suggestion": {"id": "...", "method": "...", "parameters": {},
                                                       "confidence": 0.8}}}}],
  "summary": {"initialized": 1, "rejected": 0}
}
```

- `items` holds the four session items (`plate_center`, `plate_radius`, `stick_low`, `stick_high`),
  named as in `session_contract.ITEMS`. A rejected clip has `items: {}`.
- Every item has `origin: "machine"`. The record has no `status` or `statuses` key at any depth.
  The words accepted, adjusted, manual and confirmed never appear as a status.
- `values` are the suggester's raw numbers, not rounded. Consumers must use the recorded values
  as they are, without re-rounding to the 2-decimal CSV precision of #95.
- `suggestion.parameters` is the suggester's parameters object, as recorded. It is never defaulted to
  `null`: a suggested suggestion without a parameters object is rejected with
  `suggestion_provenance_invalid`.
- `implementation.source_sha256` is the SHA-256 of this module's raw bytes. It is stable across
  checkouts because `.gitattributes` forces LF for `*.py`, so it does not depend on the platform's
  line endings. It is computed, never hard-coded.
- `IMPLEMENTATION_VERSION` must be bumped whenever a reason rule changes, and `RECORD_VERSION`
  (`format_version`) whenever the record shape changes, so a record carries the rules and shape
  that produced it.

## Writing and idempotence

The complete record text is computed in memory before anything touches the disk. Before the file is
written, the writer runs the same verification the reader runs (see below) on those bytes. A failure
there writes nothing and leaves any existing record untouched.

| Existing `machine-init.json` | Without `--force` | With `--force` |
|---|---|---|
| absent | written (`wrote`) | written (`wrote`) |
| identical bytes | `unchanged`, exit 0, file not rewritten | `unchanged`, exit 0 |
| different bytes | refused, exit 1, nothing written | replaced atomically (`replaced`) |

A leftover `.machine-init.json.tmp` from a crashed run is overwritten by the next run and does not
block it. Because the record is written through a temporary file and an atomic replace, a crash
never leaves a partial record.

## Reader guarantees

`session_machine_init.load_machine_init(directory, profile_bytes) -> dict` is the only reader. It
takes the directory and the profile bytes, and nothing else. It does not accept a caller-supplied
session.

- It loads `session.json` from disk itself, with `run`'s format and page-id check plus stricter
  structural checks. The state hash is computed from the bytes on disk.
- It validates `profile_bytes` with the same profile validator and requires their SHA-256 to equal
  `inputs.profile_sha256`. The profile must therefore be re-supplied, and the next slice (`run`) must
  take `--profile` for the same reason. A record cannot be read against a different profile.
- It checks, in order and each with a specific message: the four research flags (`origin` is
  `machine`, `human_confirmed` is `false`, `research_only` is `true`, `consumer_eligible` is
  `false`); no `status` or `statuses` key at any depth; the exact key sets; the format and version;
  `session_id` and `page_id` against the current session; `inputs.session_state_sha256` against the
  on-disk `session.json`, so a record goes stale after `ingest --force`; every item has
  `origin: "machine"`.
- Finally it rebuilds the complete record from the session state, the validated profile and the
  implementation, and requires the file's bytes to equal the rebuilt record's bytes exactly. The one
  exception is `implementation.source_sha256`: the recorded value is copied in after a format check
  (64 lowercase hex digits). Byte equality closes the remaining gaps: an edited field, a changed
  `suggester_environment`, JSON type confusion (`false` for `0`, `1080.0` for `1080`), a summary
  whose values differ in type, and an integer where a float is written.

The reader does not check `implementation.source_sha256` against the current module. An edit to the
module therefore does not invalidate existing records, but a change to the reason rules or the record
shape must come with a bump of `IMPLEMENTATION_VERSION` (see above).

## Research run

`vbt_session.py run-research` is the opt-in, research-only consumer of `machine-init.json`
(`research/vbt-workflow/session_machine_run.py`, standard library only). It tracks and analyzes every
`initialized` clip and writes every output under `<session>/machine-run/`. Rejected clips are not run;
the command names them and points to the #95 confirmation page.

```powershell
research/opencv-tracking/.venv/Scripts/python.exe research/vbt-workflow/vbt_session.py run-research `
  --session 2026-10-03 --profile validation/private/vbt/profiles/snatch-lab.json `
  --tracker-policy csrt-all-v1 --preset vbt-sg-0.15s-v1
```

- **Inputs.** The record is read only through `load_machine_init`, so the profile must be the one
  the record was written with. Plate diameter, stick length and exercise come only from that profile.
  `--plate-diameter-m`, `--stick-length-m`, `--manifest`, `--csv` and `--watch` are refused.
  `--tracker-policy`, `--gpu-python`, `--openbar-cli`, `--preset` and the explicit filter/kinematics
  flags follow the same rules as `run`. A record with no initialized clip is refused.
- **Outputs**, all under `machine-run/`:
  - `manifest.json`: a session-local research manifest, rebuilt on every run.
  - `seeds/<fixture>.machine-origin-seed.json`.
  - `scale/<fixture>.scale-reference.csv` and `scale-packages/<fixture>/` (a byte copy of the label
    package `metadata.json` plus `reference-config.json`).
  - `analyses/<fixture>.<tracker>.*` from `analyze_lift.py run`.
  - `scale-report/`.
  - `machine-run-record.json`.
- **Never written:** the personal manifest, `seeds/`, `scale/`, `analyses/`, `packages/`,
  `session.json`, `session.html`, `session-input.csv`, `report.html`, `session-record.json` and
  `machine-init.json`. A machine run therefore never binds a clip's exercise in the personal manifest
  and never hides a video from a later `ingest`. The #95 `run` neither reads nor removes
  `machine-run/`.
- **Seed.** The tracker and analyzer accept only `manual-target-seed-v1`, and the canonical
  `Analysis` stores it as `manual_seed`, which #113 does not change. The research seed is therefore a
  schema-valid `manual-target-seed-v1`. Its plate centre and radius are the recorded values,
  unrounded. It has no `selection_confidence`, because that means human confidence. Its `notes`,
  which the canonical analysis carries, begin `MACHINE-ORIGIN research seed` and say it is not a
  manual selection, not human-confirmed and not consumer-eligible. The notes also give the
  `machine-init.json` and profile hashes and the plate suggestion id, method and suggester
  confidence. Any analysis whose seed notes begin that way is machine-origin. Do not import such an
  analysis into the recommender.
- **Scale clicks** carry the recorded stick values in `repr` form, not the 2-decimal #95 form.
- **Record** (`openbar-research-vbt-machine-run-record` v1, sorted keys, written last):
  - `origin: machine`, `human_confirmed: false`, `research_only: true`, `consumer_eligible: false`;
  - `inputs` with the SHA-256 of `session.json`, the profile and `machine-init.json`;
  - the profile, the tracker configuration and the per-clip seed, click, analysis and run-record
    hashes, with each clip's `origin: machine`;
  - the rejected clips with their reasons, the removed stale outputs, the scale report and the git
    state.

  It has no `status` or `statuses` key at any depth (checked before writing), no timestamp, no
  absolute path and no `original_name` field. Each video is identified by its repository media path and
  SHA-256; the media path keeps the imported file's name and stays under the git-ignored
  `validation/private/`.
- **Restart and idempotence.** All input, profile, media and output-existence checks run before the first write; the status-key
  check and the input-change guard run last and only prevent the record from being written. Then:
  - The record is deleted first and written last, so a folder without it is incomplete.
  - Existing outputs are refused without `--force`.
  - `--force` removes this command's outputs for clips that are no longer initialized, and rebuilds
    the research manifest.
  - A same-input `--force` rerun is byte-identical once no stale outputs remain (the run that removes
    stale outputs lists them in `removed_stale_outputs`).
  - An interrupted run leaves no record, and its `--force` rerun reproduces a clean run.
  - If `session.json`, `machine-init.json` or the profile changes during the run, no record is written.
  - A re-ingest makes `machine-init.json` stale until `init-research` runs again.

## Limits

- `init-research` runs no tracking or kinematics; `run-research` does, with outputs under
  `machine-run/` only. Neither changes the canonical `Analysis`.
- Neither command writes a #95 seed, CSV, the personal manifest, `session.html`, `session-input.csv`
  or `session-record.json`.
- No confidence threshold and no automatic acceptance. Confidence is validated for range only.
- No #57 held-out data, no tracker or default selection, and no consumer write.
- A record is not consumer-eligible and cannot be promoted to human-confirmed by serialization,
  import or restart. The #95 CSV contract refuses `machine` as a status.
- Rejected clips use the #95 confirmation page, where a person confirms or places each item.

## Tests

`research/vbt-workflow/tests/test_session_machine_init.py` covers the record contract, profile
refusals (including integers too large for a float), each reason code alone and in combination,
provenance, malformed session structure, idempotence and restart, the writer's self-check (a status
key inside `parameters` writes nothing), isolation (only `machine-init.json` is written), the
fail-closed reader and its promotion and forgery attempts, and the session-integrity refusals. It runs
with fakes under the research venv.

`research/vbt-workflow/tests/test_session_machine_run.py` covers the research run. It runs with the
#95 fakes and checks:

- the refused flags and missing, promoted or stale inputs;
- the GPU policy rules;
- the machine-origin seed, the unrounded click CSV, the mirrored scale package and the
  session-local manifest;
- the record contract;
- that nothing is written outside `machine-run/`;
- byte-identical `--force` reruns, interruption and restart, mid-run input changes and stale-output
  removal;
- that the #95 `run` still works before and after a research run.

The synthetic public fixture has no stick, so `init-research` rejects it. There is no
`OPENBAR_VBT_E2E` test for this command.
