# #79 agreement study: execution status and collection readiness

Study identity: `owner-vbt-agreement-79-v1`.
Protocol: [VBT_AGREEMENT_PREREGISTRATION.md](../plans/VBT_AGREEMENT_PREREGISTRATION.md). Frozen
content `75bc5f16853987469b76b1996894ec220726ff2d` (2026-10-09 17:20:54 +02:00). PR #166's head
`c7a8a11` only turned the #1032 reference into a link, and the PR landed on `main` as `c6a58b5`.
This note executes that protocol. It does not change, reinterpret or amend it.

**Status: PENDING, collection not started (checked 2026-10-10).**
No study recording, WL Analysis export, human confirmation, collection lock, pairs file or
formal CLI run exists. No paired outcome has been calculated or inspected. Nothing here is a
PASS, a FAIL or a partial result.

## 1. Readiness

| Requirement (preregistration section) | State | Evidence |
|---|---|---|
| Frozen preregistration committed before pairing | done | `75bc5f1` (content), on `main` as `c6a58b5` via #166 |
| Pinned OpenBar baseline, clean isolated checkout | ready | Detached worktree at `742225dc21d0d947db0ddf472218cd4dc442dc24`, `git status --porcelain` empty before and after build and tests |
| Pinned CLI binary | ready | `cargo build --locked --release -p openbar-cli` exit 0; `openbar-cli.exe` SHA-256 `8561b1c1e1f9099e8a53b3ece1578192d73a94c5c2f0d5c80e7a7f0d6d1d7cc6` (2,196,480 bytes) |
| Research and measurement code unchanged since baseline | confirmed | `git diff 742225d c6a58b5 -- research crates apps validation` is empty; only three docs changed |
| Research interpreter | ready | `research/opencv-tracking/.venv` ([VBT_RESEARCH_RUN_PLAN.md](../plans/VBT_RESEARCH_RUN_PLAN.md)): Python 3.11.9, `numpy==2.2.6`, `opencv-contrib-python==4.12.0.88`; `cv2.TrackerCSRT.create` resolves (the factory `track.py` uses) |
| Pinned consumer, clean isolated checkout | ready | Detached worktree at `a74cb9dca24864af9f348bf9853fb6fe4f475521` (#1032 head). Its tree `2d25567a…` is identical to squash merge `3f3dea45` on consumer `main`, so the pinned code is what landed. Node 22.23.2, npm 11.18.0, `npm ci` exit 0 |
| Consumer agreement CLI and parsers | ready | `evidence:velocity-agreement` present; versions `openbar-analysis-v2` (parameterized tracker), `wl-analysis-csv-v2`, `concentric-segmentation-v2`, report `velocity-agreement-v1`; default `--min-overlap` 0.5 |
| FFmpeg / ffprobe | ready | 9.0.2 (gyan.dev full build) |
| Novelty exclusion set | ready (private) | 34 unique SHA-256: 13 retained development videos hashed (all already recorded in manifests), 28 recorded development manifest hashes, 6 recorded #57 held-out manifest hashes. Held-out media were **not opened**; their files were skipped by name. Digest of the sorted, newline-terminated hash list: `2c8cbc0275b11b94a5e89494d0190ec9a121d7c43c38fdd0679a2f26176894ea` |
| Plate physical diameter measured and locked | **missing** | No measurement is on record. Owner action before session 1 |
| Stick marker separation verified at 1.30 m | **missing** | No verification with resolution and repeated checks is on record. Owner action before session 1 |
| Fixed camera setup recorded | **missing** | Model, mode, height and plate-plane distance not recorded. Owner action before session 1 |
| WL Analysis version and export settings | **missing** | Record at the first export (`not_visible` is allowed for the version) |
| Recordings (18 slots) | **0 / 18** | See section 2 |
| Human-confirmed seeds, runs, assessments, WL exports | **0 / 18** | Blocked on recordings |
| Collection-lock note | not created | Must wait until collection completes or is explicitly concluded |
| Per-lift CLI reports, double run, hand-check `S1-SQ-1` | not run | Blocked; running them now would manufacture results |

Gate runs observed for this note:

- Pinned OpenBar worktree, research interpreter: research workflow tests (`-m unittest discover -s research/vbt-workflow/tests -p "test_*.py"`) ran 347 tests, OK, 4 skipped. Three of the skips are the opt-in `OPENBAR_VBT_E2E=1` end-to-end tests; the fourth is a symlink test that Windows refused (WinError 1314). Validation tests ran 211, OK. `schema_check.py` passed (13 documents). The public synthetic `analyze` smoke ran twice and produced byte-identical output (`61ffcf3f…a572`).
- Pinned consumer worktree: the 9 focused files (agreement CLI, `velocityAgreement`, OpenBar and WL parsers and importers, concentric segmentation and its golden, SHA-256) passed with 172/172 tests on two consecutive runs. The first attempt exited 1 after a vitest worker failed to start ("Worker exited unexpectedly … during starting state"), with 148/148 of the tests it ran passing. The two reruns of unchanged code were clean. This is recorded as an infrastructure flake, not a code failure. #1032's CLI and hash set passed 31/31. A synthetic agreement-CLI smoke, built from the consumer's own test-fixture builders, produced byte-identical JSON and Markdown across repeated runs, refused an existing basename and aborted on malformed pairs JSON. That smoke is a tooling check, not a study outcome.
- OS and CPU: Windows 11 Pro 10.0.26300, Intel Core i5-13600K. Rust 1.98.1, selected by `rust-toolchain.toml`.

## 2. Input inventory (no outcomes calculated)

Searched for study inputs created after the freeze:

- every `validation/private/` tree in the owner's main checkout and all local worktrees;
- the full user profile (excluding `AppData`, toolchain caches, `node_modules`, `target`, `.git`) for `*.mp4`, `*.mov`, `*.m4v` and `*.csv`, and the second local drive for videos (six directory levels);
- the owner's Google Drive, by metadata (videos and CSVs created after the freeze).

Nothing was found apart from tracked public fixtures and unrelated sample data. The newest retained private video is from 2026-10-03, and no file under the main checkout's `validation/private/` was modified after the freeze. Every retained clip belongs to the development, trial or held-out sets, which the preregistration excludes. None was substituted.

18-slot accounting, in frozen slot order. `pending` means the slot has not been recorded yet. It is not a failure, and it becomes a recorded failure only if collection is concluded without that slot.

| Slot | Lift | Load | Status | Stage | Reason |
|---|---|---:|---|---|---|
| S1-SQ-1 | back_squat | 40 kg | pending | collection | not recorded as of 2026-10-10 |
| S1-SQ-2 | back_squat | 40 kg | pending | collection | not recorded as of 2026-10-10 |
| S1-SN-1 | snatch | 30 kg | pending | collection | not recorded as of 2026-10-10 |
| S1-SN-2 | snatch | 30 kg | pending | collection | not recorded as of 2026-10-10 |
| S1-CL-1 | clean | 40 kg | pending | collection | not recorded as of 2026-10-10 |
| S1-CL-2 | clean | 40 kg | pending | collection | not recorded as of 2026-10-10 |
| S2-SQ-1 | back_squat | 40 kg | pending | collection | not recorded as of 2026-10-10 |
| S2-SQ-2 | back_squat | 40 kg | pending | collection | not recorded as of 2026-10-10 |
| S2-SN-1 | snatch | 30 kg | pending | collection | not recorded as of 2026-10-10 |
| S2-SN-2 | snatch | 30 kg | pending | collection | not recorded as of 2026-10-10 |
| S2-CL-1 | clean | 40 kg | pending | collection | not recorded as of 2026-10-10 |
| S2-CL-2 | clean | 40 kg | pending | collection | not recorded as of 2026-10-10 |
| S3-SQ-1 | back_squat | 40 kg | pending | collection | not recorded as of 2026-10-10 |
| S3-SQ-2 | back_squat | 40 kg | pending | collection | not recorded as of 2026-10-10 |
| S3-SN-1 | snatch | 30 kg | pending | collection | not recorded as of 2026-10-10 |
| S3-SN-2 | snatch | 30 kg | pending | collection | not recorded as of 2026-10-10 |
| S3-CL-1 | clean | 40 kg | pending | collection | not recorded as of 2026-10-10 |
| S3-CL-2 | clean | 40 kg | pending | collection | not recorded as of 2026-10-10 |

Counts: 18 planned videos, 54 planned reps. Recorded 0, enrolled 0, failed 0, pending 18.

## 3. Owner collection checklist

Everything below restates the frozen protocol as actions. Where it differs, the preregistration
wins. Items marked *(operational)* are how this execution carries out a preregistered rule, not
additional protocol. A private, git-ignored log template with these fields is in
`validation/private/vbt/study-79/COLLECTION_LOG.template.md` inside the pinned worktree.

### Once, before session 1 (locks)

1. Measure the plate's physical diameter and record the value. Use 0.45 m only if the plate really
   measures 0.45 m. This value is locked from session 1 on. *(Operational: record the instrument
   resolution and repeat the measurement.)*
2. Verify that the stick's two markers are 1.30 m apart. Record the measurement resolution and the
   repeated length check. *(Operational: at least two repeats.)*
3. Fix one phone, lens, orientation, zoom and focus mode, at 1080p and nominal 60 fps. Record the
   model and mode, the camera height (target about 1.0 m) and the distance to the plate plane
   (target about 2.0 m).
4. Note the WL Analysis app version or build (`not_visible` if not shown), the export settings and
   the plate-scale choice.

### Each session (three different days)

5. Before recording, recheck that the framing matches the locked setup. A geometry change is a
   protocol failure for the affected slots, not a reason to re-shoot.
6. Record in this order: SQ1, SQ2 (back squat 40 kg), SN1, SN2 (snatch 30 kg), CL1, CL2 (clean
   without jerk 40 kg). One video per slot, three attempted reps each.
7. In every video: hold the stick upright in the plate face/sleeve plane, still for at least 2 s
   before rep 1, with the plate and both markers visible. Keep still intervals before and after
   the reps. For squats, capture the standing start and the first descent. No jerk on cleans.
8. The first attempt for a slot is the enrolled video. Do not re-record, add reps or swap in a
   better take. Keep, transfer and process every enrolled recording, including missed lifts or a
   missing stick. Log the slot, stage and reason; the pipeline, not a judgement on the spot,
   decides whether the slot is analyzable. A slot with no video file at all is a recording-stage
   failure. A session that cannot do the protocol is recorded as incomplete; do not change the load.
9. Immediately after each recording, write the slot ID next to the original file name in the log.
10. **Before opening the clip in WL Analysis or any OpenBar output**, write each video's three
    attempted-rep start times from the video itself. Use a plain video player; WL Analysis shows
    velocities. These times feed the seed-before-rep-1 check and the hand-check.
11. Transfer the original files byte-for-byte: no trimming, re-encoding, stabilizing or
    re-compressing "optimized" sharing.
12. In WL Analysis, analyse the same full original clip and export the full per-frame CSV. Do not
    pick rep windows by hand. Keep the CSV next to the log under `validation/private/vbt/study-79/`.
    A WL Analysis or tooling crash on unchanged input is an infrastructure failure and may be
    retried with the same input and tools. Log it.

### After each session (existing tooling, from the pinned worktree root)

13. Check novelty before anything else: SHA-256 every new video and confirm none appears in
    `validation/private/vbt/study-79/novelty-exclusion-v1.sha256-list.txt`. A match is a retained
    protocol failure (`duplicate_of_excluded_clip`), not a re-enrollment. *(Operational: this set
    holds the retained development hashes the preregistration names, plus the recorded #57
    held-out manifest hashes, so it is stricter, not different.)* The check is needed because
    `ingest` only skips videos already in the checkout's own manifest, and the pinned checkout's
    manifest starts empty.
14. Run the existing session workflow with the pinned binary. Pass every enrolled original from the
    session. Use `--at-s NAME=SECONDS` if frame 0 is not inside the still interval with the plate
    and markers visible. On the session page, set each clip's exercise to `back_squat`, `snatch`
    or `clean` to match its slot (never `other`), and confirm or adjust the plate centre/rim and
    both stick markers.

    ```powershell
    $py = "<main checkout>/research/opencv-tracking/.venv/Scripts/python.exe"
    & $py research/vbt-workflow/vbt_session.py ingest --session study79-s1 --inbox <folder with this session's enrolled originals>
    # Open the session page, confirm every clip, download the session CSV.
    & $py research/vbt-workflow/vbt_session.py run --session study79-s1 --csv <downloaded session CSV> --plate-diameter-m <locked value> --stick-length-m 1.30 --tracker-policy csrt-all-v1 --preset vbt-sg-0.15s-v1 --openbar-cli target/release/openbar-cli.exe
    & $py research/vbt-workflow/vbt_session.py status --session study79-s1
    ```

    Use one confirmed CSV per session. After `run` has produced output, do not re-confirm seeds,
    `ingest --force` or `run --force`. Retry an interruption only with `run --resume` on unchanged
    inputs. Mark a clip `skipped` on the page only if its seed truly cannot be confirmed; log it as
    a `confirmation`-stage failure with the reason. Never skip a clip because it looks bad.
15. Create a #111 assessment for every produced analysis with `assess_clip.py --fixture-id
    <id> --run-record <final per-clip run record> --openbar-cli target/release/openbar-cli.exe
    --output <new file>`, stored as `<fixture-id>.assessment-v1.json`
    ([VBT_CLIP_ASSESSMENT.md](../validation/VBT_CLIP_ASSESSMENT.md)). None of this writes to the
    recommender.
16. Do not open the session's `report.html` velocity numbers or the WL Analysis CSV values to
    judge clips. Mechanical status, completeness and confirmation are the only enrollment gates.

## 4. Remaining execution, in order

Once collection is complete or explicitly concluded, the following steps run exactly as the
preregistration specifies. Nothing here changes them:

1. Write the per-lift pairs files from analyzable slots only. If a lift has no analyzable slot, do
   not invoke the CLI for it; record unavailable statistics and all six slots as failures.
2. Build the private UTF-8 inventory in slot order, hashing the exact bytes of each input listed in
   the preregistration's input-lock section, including the pairs files, plus every failed or
   missing slot with its stage and reason. Check that seed time precedes the video-verified
   first-rep time, that processing is `complete` and all mechanical checks are `valid` for each
   analyzable slot, and that recording dates fall on three distinct days after the freeze.
3. Commit a collection-lock note with the inventory's aggregate SHA-256, slot counts and the freeze
   references (`75bc5f1`/`c6a58b5`, `742225d`, `a74cb9d`, the CLI SHA-256 and exclusion digest
   above) **before** the first CLI invocation.
4. From the pinned consumer `app/`, run each lift twice to new basenames, without `--force`.
   Compare the JSON and Markdown bytes, then rehash all inputs.
5. Hand-check `S1-SQ-1`, all three reps. If it cannot be checked, record that failure; do not
   substitute another slot.
6. Commit the aggregate-only report and apply the frozen criterion. Record the reviewed decision
   under #79 and update [VBT_WORKFLOW_PLAN.md](../plans/VBT_WORKFLOW_PLAN.md) step 4. Coordination
   issue #110 is updated only if its own requirements are met; the preregistration does not
   require it.

## 5. Boundaries held in this note

No outcomes, threshold, configuration, segmentation, pairing or tooling change. No replacement or
development clip, and no #57 held-out media opened. No private media, CSVs, analyses or
absolute paths committed. No database write, source switch, eligibility promotion, production
tracker selection, #58 closure or #80/M1 authorization. A future PASS would still require a
separate, reviewed consumer policy before any source switch.
