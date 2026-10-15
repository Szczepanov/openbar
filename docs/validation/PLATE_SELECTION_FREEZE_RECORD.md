# Plate-selection freeze record: template and rules

Parent plan: [AUTOMATIC_PLATE_SELECTION_PLAN.md](../plans/AUTOMATIC_PLATE_SELECTION_PLAN.md) (#178),
section "Freeze and scoring protocol". Roles: [PLATE_SELECTION_SPLIT.md](PLATE_SELECTION_SPLIT.md).
Date: 2026-10-14. Work type: **evidence preparation**.

This is a template and a rule set, not a schema or an actual completed freeze. The owner supplied
**PROCEED — Prompt 1 only** in chat, durably recorded in the parent plan; no issue comment is
asserted. The [Prompt 1 preparation protocol](../plans/HOUGH_TOP_K_PREPARATION.md) supplements
this private format with candidate-stage metrics, independent annotation/repeatability and
readiness gates. A freeze must be complete **before the first scored run**. Current stage is
**READY-BUT-NOT-SCORED** tooling/protocol; formal inputs and executor remain unavailable.

## Why it exists

The plan scores a candidate once on clips that the candidate never saw. That is only believable
if everything that could change the answer is fixed and hashed first: the clips and their roles,
the labels, the candidate code and settings, the search and refusal rules, and how results are
counted. The record is that proof. A run that cannot cite a complete record is not evidence.

## Rules

1. **One record per frozen candidate set.** Store it at
   `validation/private/frozen-eval/freeze/<freeze-id>.freeze-record.json`. It contains clip
   identities and labels, so it stays in the git-ignored private tree. Commit only its SHA-256
   and the non-private fields in the report that cites it.
2. **Complete or refused.** Every field below is required. A run harness must refuse a record with
   a missing field, an unfilled `<...>` placeholder, or a hash that does not match the file on disk.
3. **Immutable.** Never edit a record. A change makes a new record with a new `freeze_id` and
   `supersedes` pointing at the old one. The old record's runs stay valid only for the old record.
4. **Descriptive by default.** The plan chooses `decision_limits.mode = "descriptive"` for the
   initial baseline: no pass or promotion verdict. A numeric mode needs limits and a rationale
   written here before scoring, never derived from the scored split.
5. **Revision burns the split.** A candidate changed after unblinding makes every
   `split-frozen-eval` clip development-only. It needs a fresh frozen split and a new record.
6. **Reserved clips stay out.** `reserved_exclusion.clips_in_record` must be empty and no listed
   input may carry `split-reserved-57`. Exclusion is also enforced in code by
   `validation/tools/held_out_guard.py` (PR #193).
7. **Truth never reaches the candidate.** Movement-onset labels and expected policy are scoring
   inputs only. They are not passed to the candidate, not logged beside its output before
   scoring, and not used to choose the search horizon.

## Template

Placeholders are written `<like this>`. Copy the block, fill every one, then hash the file.

```json
{
  "record_version": 1,
  "freeze_id": "<plate-selection-freeze-YYYYMMDD-N>",
  "supersedes": null,
  "created_utc": "<ISO-8601 timestamp>",
  "created_by": "<owner or agent id>",

  "authority": {
    "plan_path": "docs/plans/AUTOMATIC_PLATE_SELECTION_PLAN.md",
    "plan_commit": "<git hash of the plan at freeze>",
    "split_doc_commit": "<git hash of PLATE_SELECTION_SPLIT.md at freeze>",
    "owner_decision_url": "<link to the PROCEED comment on #178>",
    "owner_decision_record": "<for Prompt 1: committed parent-plan path, commit, SHA-256, heading and supplied authorization attachment SHA-256; set owner_decision_url to null when no comment exists>",
    "allowed_entry_point": "<path of the approved research harness>"
  },

  "inputs": {
    "manifest_path": "validation/private/manifest.json",
    "manifest_sha256": "<sha256 of that file at freeze>",
    "sessions": [
      { "session_id": "<id>", "date": "<YYYY-MM-DD>", "place": "<text>", "lighting": "<text>" }
    ],
    "clips": [
      {
        "fixture_id": "<id>",
        "session_id": "<id>",
        "role": "<split-exploration | split-frozen-eval>",
        "media_sha256": "<sha256>",
        "purpose": "<development | boundary | ood>",
        "challenge": "<row from the collection table>",
        "expected_policy": "<select | refuse_ambiguous | refuse_no_target | refuse_unsupported | refuse_no_pre_movement>",
        "label_files": [
          { "annotator": "a", "path": "<path>", "sha256": "<sha256>" },
          { "annotator": "b", "path": "<path>", "sha256": "<sha256>" }
        ],
        "adjudication": { "path": "<path or null>", "sha256": "<sha256 or null>", "unresolved": false }
      }
    ],
    "labels_csv": { "path": "validation/private/frozen-eval/labels.csv", "sha256": "<sha256>" },
    "synthetic_inputs": "<list of synthetic cases scored separately, or []>"
  },

  "reserved_exclusion": {
    "tag": "split-reserved-57",
    "clips_in_record": [],
    "guard_module": "validation/tools/held_out_guard.py",
    "guard_commit": "<git hash>"
  },

  "candidates": [
    {
      "name": "<e.g. baseline-plate-hough-edge-v1>",
      "method_id": "<e.g. plate-hough-edge-v1>",
      "code_commit": "<git hash>",
      "entry_point": "<path>",
      "parameters": "<full parameter dump, e.g. every PlateParams field and value>",
      "parameters_sha256": "<sha256 of that dump>",
      "model": null,
      "added_rules": "<none, or the rule text if this is a separately named candidate>"
    }
  ],

  "search_rules": {
    "frame_search_horizon_s": "<seconds from clip start, fixed before scoring>",
    "timestamp_sampling": "<schedule, in authoritative PTS, never nominal FPS>",
    "target_identity_rule": "<visible outer rim of the outermost plate on the camera-facing end>",
    "tie_and_ambiguity_rule": "<what counts as ambiguous, and the deterministic tie-break>",
    "refusal_rules": ["<each reason the candidate may refuse>"],
    "per_clip_timeout_s": "<seconds>",
    "resource_limits": "<memory, CPU or GPU, wall-clock, tools allowed>"
  },

  "scoring": {
    "outcome_definitions": "plan section 'Freeze and scoring protocol', at plan_commit",
    "count_once_before_correction": true,
    "intervals": "95% Wilson",
    "strata": ["challenge", "session_id"],
    "geometry_reference": "label pass a/b adjudicated centre and radius",
    "latency": {
      "cold_definition": "<text>",
      "warm_definition": "<text>",
      "repeats": "<integer>",
      "stages_recorded": ["probe", "decode", "search", "preview_or_refusal"]
    }
  },

  "decision_limits": {
    "mode": "descriptive",
    "numeric_limits": null,
    "rationale": null
  },

  "environment": {
    "os": "<name and version>",
    "hardware": "<cpu, gpu, ram>",
    "python": "<version>",
    "opencv": "<version>",
    "ffmpeg": "<version>",
    "dependency_lock": "<path and sha256, or the pinned versions>"
  },

  "statement": "No scored run precedes this record. The candidates above are final for this freeze."
}
```

## Hashing

```bash
sha256sum validation/private/manifest.json validation/private/frozen-eval/labels.csv
sha256sum validation/private/frozen-eval/seeds/*.json
sha256sum validation/private/frozen-eval/freeze/<freeze-id>.freeze-record.json   # after saving
```

The last hash is the record's identity. Every scored run, raw proposal, per-clip score and
report cites it.

## Checks before the first scored run

An agent confirms each item, and the owner is told if any fails:

- [ ] Owner PROCEED is bound in `authority`: for Prompt 1 use the committed parent-plan decision
      and its hash/commit plus source attachment hash; URL is null if no issue comment exists.
- [ ] Every `<...>` placeholder is replaced and every hash matches the file on disk.
- [ ] Every Prompt 1 `split-frozen-eval` clip has two independent blind label passes and, where the
      passes disagree, an adjudication or `unresolved: true`.
- [ ] No clip is `split-reserved-57`, and no `split-exploration` clip is listed as frozen evaluation.
- [ ] All frozen-evaluation clips come from at least the session count the owner confirmed. If
      sessions are not independent, the report says "insufficient independent evidence".
- [ ] `candidates[].code_commit` is the committed code the run will execute (clean working tree).
- [ ] Search horizon, timestamp schedule, tie and refusal rules are written, and none were derived
      from onset labels or from any frozen-evaluation result.
- [ ] `decision_limits` is `descriptive`, or its limits and rationale predate any scoring.
- [ ] The Prompt 1 supplement is complete: independent centre/radius repeatability, frozen
      matching rule/Tc/Tr, precision rationale, stage metrics/denominators/failure and margin
      definitions, and the executor's fail-closed verification. See the linked protocol checklist.

## Not decided here

The numeric values (horizon, timeouts, repeat counts, sample-size minimum) and any audit sampling
rate are the owner's, set at freeze time. This template deliberately proposes none. The record
format is a research artifact; it changes no canonical schema, version, golden file or fixture.
The observation harness alone is not a freeze validator or scorer. A reviewed frozen-data
executor with complete-record/hash/role refusal is still required before real-data execution.
