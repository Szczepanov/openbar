# VBT seed tool (#84) and filter window in seconds (#85) — implementation plan

Status: done. WP-A (#84) merged in PR #89; WP-B (#85) merged in PR #90. The only open item is
the owner's private-clip trial (seed before the first rep, then `track.py`, then
`analyze --observations`).
Related: #84, #85, #79, #86, #87, `docs/plans/VBT_WORKFLOW_PLAN.md`, ADR-0006, ADR-0007

## 0. How to use this plan

The two issues are **independent**. Run them as two branches, two PRs, and two agent sessions,
which can work in parallel:

| WP | Issue | Language | Branch | Touches Rust? | Touches `openbar-core`? |
|----|-------|----------|--------|---------------|-------------------------|
| A | #84 seed tool | Python (stdlib) + HTML text + docs | `feat/84-seed-tool` | no | no |
| B | #85 `--filter-window-s` | Rust + docs | `feat/85-filter-window-s` | yes | yes (`filtering.rs`) |

Each WP below has the same layout: findings the issue doesn't state, decisions, ordered steps,
tests, validation commands, stop conditions, and a kickoff prompt for the agent.

Shared rules for both (from AGENTS.md / CLAUDE.md):

- Read the files in "Read first" before editing. `analyze.rs` is ~2200 lines; use offset/limit.
- No new dependencies. Python stays stdlib-only (CI runs Python 3.14).
- No schema, `*_VERSION`, golden or `validation/fixtures/` change is expected in either WP. If one
  turns out to be needed, **stop and ask**. Don't regenerate `analysis-v1.golden.json`.
- Put the doc changes in the same PR as the code.
- Report the commands you ran and their exit codes. Don't claim a pass you didn't observe.

---

## WP-A — #84 Seed tool: `manual-target-seed-v1` from one clicked frame

### A.1 Current state (verified)

- `validation/tools/label_package.py` builds a grid package. `--step-s` is **required**,
  `--start-s` defaults to `0.0`, and `--include-frame` can be repeated. Frame selection goes through
  `select_frames()` and extraction through `extract()`, with PTS alignment checked by
  `require_aligned()`. `metadata()` writes `metadata.json` with `annotated_at: "FILL-AT-IMPORT"`.
- `metadata.json` has **no rotation field**. It records only display-oriented `width_px`/`height_px`
  and `rotation_applied: true`. Rotation comes from the manifest's `fixture.video.rotation_deg`,
  which `require_fixture_probe_match()` checks against the probe (`% 360`) when the package is built.
- `validation/tools/annotations.py` has the subcommands `validate`, `import-csv` and
  `repeatability`. `import_csv()` parses the CSV, then calls `validate_annotation()`, which rejects
  the `FILL-AT-IMPORT` placeholder (`annotated_at` must be ISO-8601 with a timezone). So `seed`
  cannot simply call `import_csv()` on a fresh package's metadata.
- In the page (`label_page.html`), `csvRows()` writes `timestamp_s` with `toFixed(6)` and
  x/y/radius with `toFixed(2)`. Download is blocked until every labelled frame has a quality of
  1/2/3. **So the seed workflow is: click the centre, Shift+click the rim, press `1`/`2`/`3`, then
  download.** The issue's step 2 leaves out the quality key.
- The page text claims the tracker seed is made at import. That claim is false in two places:
  `label_page.html` lines ~63–64 and `docs/validation/ANNOTATION.md` lines ~196–198. The AGENTS.md
  list of `annotations.py` subcommands also needs `seed` added.
- In Rust, `manual_seed.rs` rejects a centre outside `[0,w)×[0,h)` (`validate_center_in_frame`)
  **and** a circle whose bounding box leaves `[0,w]×[0,h]` (`validate_target_in_frame`). The issue
  lists only the first check. The tool must enforce both, or `analyze --seed` would reject its
  output.
- `analyze` matches the seed time to a decoded frame within `SEED_TIMESTAMP_TOLERANCE_S = 0.0005`
  (`analyze.rs:45`). A 6-decimal CSV timestamp is well inside that.
- The committed seed `validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json`
  has `selection_confidence: 1.0`. To reproduce it field for field (an acceptance criterion), the
  tool needs a way to emit that field.

### A.2 Decisions (recommended defaults; the agent may proceed with them)

1. **Single-frame selection:** add `--frame-index <n>` and `--at-s <t>` (nearest decoded frame) as
   a mutually exclusive group with `--step-s`. Exactly one of the three is required. Change
   `--start-s` to default `None` so its presence can be detected. `--start-s`/`--end-s` are rejected
   with `--frame-index`/`--at-s`, and so is `--include-frame`. Implement through the existing
   `select_frames()`: `select_frames(ts, ts[n], ts[n], 1.0, [n])` or `select_frames(ts, t, t, 1.0, [])`.
   That adds no second decode path or validation path.
2. **Seed rotation** comes from `manifest fixture.video.rotation_deg % 360`, which must be one of
   {0, 90, 180, 270}. The package build already checked this value against the probe.
   **`coordinate_space`** is `"display_top_left"`, emitted only if the metadata's `coordinate_system`
   equals the package constants (`decoded_display_pixels`, `top_left`, `right`, `down`,
   `rotation_applied: true`). Any other value fails closed. Neither value is hard-coded blindly.
3. **`selection_confidence`:** add an optional `--selection-confidence <0..1>` flag. When it is
   absent, omit the field. Do **not** map label quality to a confidence: that would invent a
   probability (AGENTS.md "confidence is never silently promoted").
4. **CSV parsing:** extract the row-parsing loop of `import_csv()` into a pure
   `_read_csv_samples(csv_path) -> list[dict]`. `import_csv()` keeps its behaviour, and the existing
   tests guard that. `seed` reuses the parser and does its own validation, so it doesn't depend on
   `annotated_at`.
5. **Candidate rule:** take the first row in timestamp order with `annotation_state == "labelled"`, a
   `center_px`, and a `target_size_px.radius_px`. Diameter and bounds rows don't qualify; the page
   never writes them. Rows must still pass per-row checks: strictly increasing timestamps, a valid
   state/visibility/quality combination, and a labelled row with quality high/medium/low. Factor
   these checks out of `validate_annotation()` if that's cleaner, or reuse them as they are.
   `frame_index` is required on the chosen row.
6. **Output:** use the committed seed's key order (`schema_version`, `fixture_id`,
   `seed{timestamp_s, frame_index, target{center{x_px,y_px}, radius_px}, coordinate_space,
   source_rotation_deg, [selection_confidence], notes}`), `indent=2`, a trailing `\n`, written with
   `newline="\n"`. Do **not** use `_write()`, whose `sort_keys=True` would reorder keys. Refuse an
   existing `--output` unless `--force` is given, so hand-written private seeds can't be clobbered.
7. **`notes`:** `"Manual seed from label CSV sha256=<hex>; annotator_id=<id>; tool=annotations.py seed."`
   Do not include the CSV path or filename: the same CSV bytes must give the same seed bytes.

### A.3 Ordered steps

1. **Read first:** `label_package.py`, `annotations.py`, `label_page.html` (lines 55–75, 287–330),
   `docs/validation/MANUAL_TARGET_SEED.md`, `docs/validation/ANNOTATION.md` (lines 160–215),
   `manual_seed.rs` lines 479–565, ADR-0007, and `validation/tests/test_label_package.py`
   (`PackageBuildTests`, `test_page_format_csv_imports_with_generated_metadata`).
2. **Tests first (red):** add the tests in A.4 to `validation/tests/test_annotations.py` and
   `validation/tests/test_label_package.py`.
3. **`label_package.py`:**
   - In `parser()`, add a required mutually exclusive group (`--step-s`, `--frame-index`, `--at-s`)
     and set `--start-s` to `default=None`.
   - In `build()`, branch on the mode. Grid mode must keep exactly today's behaviour, including the
     `notes` text with `start_s=0.0`. Single-frame mode validates `0 <= n < len(ts)` (via
     `select_frames`), rejects grid-only flags, and writes `notes` as
     `"Single frame <n> at <t:.6f> s for a manual target seed."`.
   - `TOOL` version stays `"3"`, because grid output is unchanged. Bump it only if the grid package
     bytes change, and if they do, stop and say why.
4. **`annotations.py`:**
   - Extract `_read_csv_samples()` (pure refactor) and run the existing tests to confirm they stay
     green.
   - Add `build_seed(metadata, csv_path, manifest, selection_confidence) -> dict`, which:
     - checks the metadata fixture against the manifest (`_fixture`), and checks that
       `source_video_sha256` equals the manifest's `media.sha256`;
     - checks that the `coordinate_system` constants are as expected and that its dimensions equal
       `_display_size(fixture)`;
     - picks the candidate;
     - checks that the radius is finite and > 0, that the centre lies in `[0,w)×[0,h)`, that
       `x-r>=0, y-r>=0, x+r<=w, y+r<=h`, and that `selection_confidence` is finite and in `[0,1]`;
     - computes the CSV's SHA-256 from its raw bytes.
   - Add a `seed` subparser with `--manifest`, `--metadata`, `--csv`, `--output`,
     `[--selection-confidence]` and `[--force]`. Errors exit 2 through `AnnotationError`, like the
     other subcommands.
5. **`label_page.html`:** replace the "made at import" sentence. The tracker seed is the first frame
   with a centre and a radius. Build it with `annotations.py seed`, and for VBT clips place it before
   the first rep. Keep the `BEGIN/END csvRows` region untouched.
6. **Docs:**
   - `MANUAL_TARGET_SEED.md`: add a section on creating a seed with the three-command workflow
     (including the quality key), the fail-closed list, the rotation/coordinate derivation, and the
     note that VBT seeds must precede the first rep.
   - `ANNOTATION.md`: add the `--frame-index`/`--at-s` usage and fix the "at import" sentence.
   - AGENTS.md: add `seed` to the `annotations.py` subcommand list.

### A.4 Tests (deterministic; stdlib `unittest`)

In `test_annotations.py` (no FFmpeg needed; build the sidecar with
`label_package.metadata(fixture, (320, 240), "seed", "…")`):

- `test_seed_reproduces_committed_synthetic_seed_except_notes`: write the CSV row
  `0.000000,,0,labelled,visible,high,100.00,190.00,24.00,,,,,,` and run with
  `selection_confidence=1.0`. The output must equal the committed seed dict with `notes` removed from
  both.
- `test_seed_is_byte_identical_for_same_csv` (two runs, compare bytes) and
  `test_seed_notes_record_csv_sha256_and_annotator`.
- `test_seed_picks_first_labelled_row_with_centre_and_radius`: an earlier labelled row with no
  radius, and an earlier `not_annotated` row, are both skipped.
- Fail-closed, one test each, with the error message asserted:
  - no candidate;
  - radius `0`, `-1`, `nan`, `inf`;
  - centre x = width, centre y = height, centre x = -0.1;
  - circle crossing the left edge and the bottom edge;
  - metadata `fixture_id` not in the manifest;
  - metadata hash ≠ manifest hash;
  - wrong `coordinate_system.space` / dimensions;
  - `selection_confidence` `1.5` / `nan`;
  - `--output` exists without `--force`;
  - malformed CSV header.
- `test_seed_output_passes_schema_check`: run `schema_check.py`'s validation entry point (import the
  module) on the generated seed against `manual-target-seed-v1.schema.json`.
- The existing `import-csv` tests must stay green after the parser extraction.

In `test_label_package.py`:

- No FFmpeg: the parser rejects `--frame-index` combined with `--step-s`, `--at-s` combined with
  `--start-s`, and a call with no mode at all.
- No FFmpeg: `select_frames` single-frame calls return `[n]`, and an out-of-range `n` raises.
- In `PackageBuildTests` (FFmpeg-gated): `test_single_frame_package_matches_grid_frame_bytes`. Build
  the grid package from the existing test (`step 0.25`, `include_frame [1]`) and a `--frame-index 3`
  package, then compare `frames/frame_000003.png` byte for byte. The page config has exactly one
  frame, and `metadata.json` passes `import_csv` once `annotated_at` is filled.

### A.5 Validation commands

```bash
python -m unittest discover -v -s validation/tests -p 'test_*.py'
python validation/tools/schema_check.py
python validation/tools/label_package.py --manifest validation/fixtures/public/manifest.json --fixture synthetic-clean-side-12 --frame-index 0 --annotator-id seed --output-dir target/seed-pkg
python validation/tools/annotations.py seed --manifest validation/fixtures/public/manifest.json --metadata target/seed-pkg/metadata.json --csv target/seed.csv --selection-confidence 1.0 --output target/seed.json
python validation/tools/schema_check.py --schema validation/schema/manual-target-seed-v1.schema.json target/seed.json
cargo run --locked -p openbar-cli -- analyze --manifest validation/fixtures/public/manifest.json --fixture synthetic-clean-side-12 --seed target/seed.json --plate-diameter-m 0.45 --tracker template --filter raw --kinematics-max-gap-s 0.2 --kinematics-min-confidence 0 --output target/analyze-seedtool.json --force
```

Write `target/seed.csv` by hand with the A.4 row; the browser step is manual. Running `analyze`
should give output identical to the AGENTS.md analyze smoke except for `manual_seed.notes`. Diff the
two files to confirm. The manual private-clip check (seed before the first rep, then `track.py`,
then `analyze --observations`) is for the owner. List it as unchecked in the PR test plan.

### A.6 Stop conditions

- A schema, the `ManualTargetSeed` contract, or `annotation-v1` would need to change.
- Grid-mode package bytes change (frames, `metadata.json` or `index.html` for the same args).
- Single-frame PNG bytes differ from the grid PNG bytes. Investigate the FFmpeg select path; don't
  add a second decoder invocation style.

### A.7 Kickoff prompt (WP-A)

> Implement GitHub issue #84 on branch `feat/84-seed-tool`, following
> `docs/plans/VBT_SEED_TOOL_AND_FILTER_WINDOW_PLAN.md` section WP-A exactly (decisions A.2,
> steps A.3, tests A.4). Python stdlib only. Write the A.4 tests first and confirm they fail, then
> implement. Run every A.5 command and report each exit code. Don't change any schema, Rust code or
> fixture. Stop and ask if an A.6 condition triggers. Commit as `feat(validation): …` with
> `Refs #84`.

---

## WP-B — #85 `analyze --filter-window-s`

> **Done:** merged in PR #90 (`add3a84`) as planned below. Note for #79 pre-registration: the merged
> rule gives 30 fps and 60 fps windows the **same timestamp span** `(N − 1) / fps` only for some
> durations. 0.15 s (5/9 samples, span 0.133 s), 0.2 s (7/13), 0.267 s (9/17) and 0.4 s (13/25)
> match exactly. 0.1, 0.25 and 0.3 s differ by one 30 fps frame (33 ms). Pre-register a matching
> value; 0.15 s is the one used in the #79 trial.

### B.1 Current state (verified)

- `crates/openbar-core/src/filtering.rs`: `FilterConfig::{MovingAverage{window, max_gap_s},
  SavitzkyGolay{window, polynomial_order, max_gap_s}}` takes `window: usize` (a sample count, odd,
  ≥ 1, and > order for SG). `FilterConfig::provenance()` is the **only** producer of filter
  provenance. It inserts `window`, `polynomial_order` and `max_gap_s` into a `BTreeMap`.
  `FILTER_VERSION = "1"`.
- `apps/openbar-cli/src/analyze.rs`: `parse_filter_config()` (~L747) builds a `FilterConfig` **at
  parse time** and validates it before media I/O by calling `apply_filter(&[], config)`. `Args.filter`
  is a `FilterConfig`. `run()` calls `apply_filter(&calibrated_samples, args.filter)` (~L276).
- The duration resolution needs the measured fps, which exists only **after decode**:
  `measured_fps(&clip)` (~L1009) is `(frames-1)/(last-first)` over the *selected* frames. It returns
  `None` for ≤ 1 frame or zero duration. It is the same value written to
  `video.frame_rate.measured_fps`.
- `parse_f64` accepts `"NaN"`, `"inf"` and `"-inf"`, so the finiteness check has to be explicit.
- The analysis schema's `parameters` is a free-form map of `parameter_value`, so a `window_s`
  float fits without a schema change. `Analysis::validate` re-verifies only the kinematics method,
  not filter parameters.
- `filter_experiment.rs` builds `FilterConfig::{MovingAverage, SavitzkyGolay}` directly (6 sites).
  `filtering.rs` tests have about 20 more.
- `validation/tools/tracker_filter_selection.py` turns frozen filter parameters into CLI flags
  through `FILTER_PARAMETER_FLAGS` and raises on unknown keys. It reads `filter-experiment` output,
  which never contains `window_s`, so it needs no change. It fails closed if one ever appears.

### B.2 Decisions (recommended; the agent may proceed with them)

1. **Conversion lives in core** as a pure function:

   ```rust
   pub const DURATION_WINDOW_TOLERANCE_SAMPLES: f64 = 1.0;
   pub fn resolve_window_samples(window_s: f64, measured_fps: f64, min_window: usize)
       -> Result<usize, FilterError>
   ```

   - Reject a `window_s` or `measured_fps` that is non-finite or ≤ 0 (new `FilterError` variants:
     `InvalidWindowDuration { window_s }`, `InvalidMeasuredFps { measured_fps }`).
   - `requested = window_s * measured_fps`. Take the nearest odd integer
     `2 * floor((requested - 1) / 2 + 0.5 + 1e-9) + 1`. **Ties round up** to the larger odd, and
     the `1e-9` keeps `0.1 × 60 = 6.000…01` and `5.999…9` on the same side.
   - Clamp up to `min_window`: 1 for moving average, and the smallest odd > `polynomial_order` for
     Savitzky–Golay.
   - Fail closed if `|resolved - requested| > DURATION_WINDOW_TOLERANCE_SAMPLES`
     (`WindowDurationUnresolvable { window_s, measured_fps, resolved_window }`). Nearest-odd rounding
     never exceeds 1 sample, so in practice only a clamp can trigger this. A request for 0.05 s at
     30 fps with SG order 3 (1.5 samples → would clamp to 5) is rejected.
2. **Provenance:** add a provenance-only field `window_s: Option<f64>` to both variants. `provenance()`
   inserts `"window_s"` as a `Float` **only when `Some`**, so `--filter-window` output stays
   byte-identical. `validate()` rejects `Some` when the value is non-finite or ≤ 0. `apply_filter`
   ignores it. Add constructors that set both fields together, so `window` and `window_s` cannot
   disagree when they come from the CLI:
   `FilterConfig::moving_average_for_duration(window_s, measured_fps, max_gap_s)` and
   `FilterConfig::savitzky_golay_for_duration(window_s, measured_fps, polynomial_order, max_gap_s)`.
   *Alternative (documented, not chosen):* keep `FilterConfig` unchanged and let the CLI add
   `window_s` to the provenance afterwards. That splits provenance ownership between core and CLI.
   `measured_fps` is not duplicated into the filter parameters, because it is already in
   `video.frame_rate.measured_fps`.
3. **No version bump:** the filter maths and the existing parameters are unchanged, and the new key
   appears only when the new flag is used. If a reviewer disagrees, **stop and ask**; don't bump
   `FILTER_VERSION` on your own.
4. **CLI deferred resolution:** `Args.filter` becomes a CLI-private enum:

   ```rust
   enum FilterRequest {
       Fixed(FilterConfig),
       MovingAverageDuration { window_s: f64, max_gap_s: f64 },
       SavitzkyGolayDuration { window_s: f64, polynomial_order: usize, max_gap_s: f64 },
   }
   ```

   - **At parse time** (still before media I/O), check that `window_s` is finite and > 0. Validate
     the rest by building the duration config at a nominal rate that resolves to the minimum window
     and calling `apply_filter(&[], …)`, which checks the order limit and the gap.
   - **In `run()`**, resolve right after `validate_seed(...)` and before tracking, so a bad request
     fails before the expensive work. `measured_fps(&clip) == None` gives invalid-input (exit 2),
     with a message that `--filter-window-s` needs at least two selected frames with increasing
     timestamps.
5. **Variable frame rate:** `measured_fps` is the mean over the selection. On variable-frame-rate
   clips, the resolved window covers a duration that varies around `window_s`. Document this. Don't
   implement a time-based variable window, because that's out of scope ("no filter maths change").

### B.3 Ordered steps

1. **Read first:** `filtering.rs` lines 1–420 (config, provenance, validate, `apply_filter`), the
   `analyze.rs` ranges ~40–75 (USAGE), ~420–520 (`parse_args`), ~560–590 (known flags), ~740–860
   (`parse_filter_config`), ~200–320 (`run`), ~1000–1020 (`measured_fps`) and the tests from ~1660,
   then `docs/validation/CLI_PIPELINE.md` lines ~115–130 and
   `docs/validation/FILTER_EXPERIMENTS.md`.
2. **Capture the baseline before any edit** (for the byte-identical check):

   ```bash
   cargo run --locked -p openbar-cli -- analyze <smoke args, but> --filter savitzky-golay --filter-window 9 --filter-polynomial-order 2 --filter-max-gap-s 0.2 --output target/pre85-sg9.json
   cargo run --locked -p openbar-cli -- analyze <smoke args, but> --filter moving-average --filter-window 9 --filter-max-gap-s 0.2 --output target/pre85-ma9.json
   ```

3. **Core tests first (red), in `filtering.rs` `mod tests`:**
   - `duration_window_covers_same_time_at_30_and_60_fps`: for each `window_s` in
     {0.1, 0.15, 0.2, 0.3}, check `|w_lo/fps_lo - w_hi/fps_hi| <= 1/fps_lo` (one frame of the
     slower rate). Run it for 30/60 and for 29.97/59.94.
   - `duration_window_examples`: 0.15 s @ 60 → 9; 0.15 s @ 30 → 5; 0.3 s @ 30 → 9;
     0.1 s @ 60 → 7 (tie up); 0.25 s @ 12 → 3.
   - `duration_window_clamps_within_tolerance` (MA: 0.02 s @ 30 → 1; SG order 2: 0.1 s @ 30 = 3 → 3)
     and `duration_window_rejects_clamp_beyond_tolerance` (SG order 3, 0.05 s @ 30).
   - `duration_window_rejects_invalid_inputs`: `window_s` 0, -0.1, NaN, ±∞, and fps 0, NaN.
   - `provenance_records_window_s_only_when_requested`: compare the whole `ImplementationProvenance`
     value for both cases.
   - `validate_rejects_non_finite_window_s`.
4. **Implement core:** the new field, the error variants with `Display`, the resolver, the
   constructors, and `validate`/`provenance`. Update every `FilterConfig::{MovingAverage,
   SavitzkyGolay}` literal (`filtering.rs` tests, `filter_experiment.rs`) with `window_s: None`.
5. **CLI tests first (red), in `analyze.rs` `mod tests`:**
   - Parse: `--filter-window-s` with `raw` and with `kalman` gives an error naming the flag. Both
     window flags given, or neither, gives an error. `--filter-window-s` with `0`, `-0.1`, `NaN` or
     `inf` is rejected before media I/O. A valid duration request parses to the expected
     `FilterRequest`. Existing tests that assert `parsed.filter == FilterConfig::Raw` move to
     `FilterRequest::Fixed(FilterConfig::Raw)`.
   - FFmpeg-gated end-to-end, following `end_to_end_public_fixture_is_deterministic_and_refuses_overwrite`:
     `--filter savitzky-golay --filter-window-s 0.25 --filter-polynomial-order 2` on
     `synthetic-clean-side-12` (12 fps → window 3). Check that the filtered samples equal those of a
     `--filter-window 3` run, that the parameters are `{window: 3, window_s: 0.25, polynomial_order:
     2, max_gap_s: …}`, and that two runs are byte-identical.
   - Resolution failure: the resolver gives invalid-input when `measured_fps` is `None`. Unit-test
     it on the resolve helper. Add an end-to-end case only if the `--selection` flags can select a
     single frame.
6. **Implement CLI:** add the flag to the known-flag list, to the `reject_present_filter_options`
   calls for `raw`/`kalman`, and to the MA/SG branches. Add the `FilterRequest` and resolve it in
   `run()`. Update USAGE to `--filter-window <odd> | --filter-window-s <s>`. If the filter-parsing
   code grows past about 50 lines, move it into a new `apps/openbar-cli/src/filter_request.rs`
   rather than growing `analyze.rs`.
7. **Docs:**
   - `CLI_PIPELINE.md` (filter section): the flag, the conversion formula, tie and clamp rules, the
     1-sample tolerance, provenance keys `window` + `window_s`, measured fps over the selection, the
     variable-frame-rate caveat, and that there is still no default filter.
   - `FILTER_EXPERIMENTS.md`: the window is a sample count, and `analyze` can resolve it from
     seconds.
   - `TRACKER_FILTER_SELECTION.md`: frozen candidates stay sample counts, and `window_s` provenance
     is not a selection-tool input.
   - Optionally add a line to `VBT_WORKFLOW_PLAN.md` saying that #79 pre-registers the filter in
     seconds.

### B.4 Validation commands

```bash
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
cargo test --locked --workspace --all-targets --all-features
cargo test -p openbar-core analysis::tests::golden_json_is_stable
# both CLI smoke commands from AGENTS.md (tracker-run, analyze) + benchmark smoke
cargo run --locked -p openbar-cli -- analyze <same args as step 2> --output target/post85-sg9.json
cargo run --locked -p openbar-cli -- analyze <same args as step 2, MA> --output target/post85-ma9.json
cmp target/pre85-sg9.json target/post85-sg9.json
cmp target/pre85-ma9.json target/post85-ma9.json
cargo run --locked -p openbar-cli -- analyze <smoke args> --filter savitzky-golay --filter-window-s 0.25 --filter-polynomial-order 2 --filter-max-gap-s 0.2 --output target/post85-sgs.json
python validation/tools/schema_check.py --schema validation/schema/analysis-v1.schema.json target/post85-sgs.json
python validation/tools/schema_check.py
cargo run --locked -p openbar-cli -- filter-experiment --output target/filter-experiment.json
```

`cmp` must report no difference for both files. `git status` must show no change to
`analysis-v1.golden.json`, `validation/schema/` or `validation/fixtures/`.

### B.5 Stop conditions

- The golden test fails, or either `cmp` shows a difference.
- `schema_check` rejects the `window_s` output, meaning a schema change would be needed.
- A reviewer asks for a `FILTER_VERSION` or analysis schema bump.
- Making the resolution exact turns out to need filter-maths changes, such as a time-based window.

### B.6 Kickoff prompt (WP-B)

> Implement GitHub issue #85 on branch `feat/85-filter-window-s`, following
> `docs/plans/VBT_SEED_TOOL_AND_FILTER_WINDOW_PLAN.md` section WP-B exactly (decisions B.2, steps B.3).
> Capture the step-2 baseline outputs **before** editing anything. Write the core and CLI tests first
> and confirm they fail. Run every B.4 command and report command, working directory and exit code.
> Don't regenerate the golden, don't change any schema, and don't bump any `*_VERSION`. Stop and ask
> if a B.5 condition triggers. Commit as `feat(core,cli): …` with `Refs #85`.

---

## Integration notes

- #86 (one-command workflow) consumes both WPs. #87 reuses WP-A's single-frame page. #79
  pre-registration needs WP-B, plus WP-A for seeds before the first rep.
- After either PR merges, add a short "how to" line to `VBT_WORKFLOW_PLAN.md` if the PR didn't.
- Review: run a `code-reviewer` pass on each PR. Also run the repo `measurement-audit` skill on
  WP-B, because it touches provenance and the filter boundary in `openbar-core`.
