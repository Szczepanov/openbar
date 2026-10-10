# Retained VBT suggestion evaluation rules, version 2 (#117)

Work type: **harden evaluator binding**. This version applies to `evaluate_suggestions_v2.py`
(report `format_version` 2). Freeze these rules by commit before running any batch that consumes
retained #111 assessments; the evaluator records that commit and this file's SHA-256, as in v1.

## Relationship to version 1

[Version 1](VBT_SUGGESTION_EVALUATION_RULES.md) and `evaluate_suggestions.py` are **frozen and
unchanged**. The published #112 batch ([results](VBT_SUGGESTION_EVALUATION_RESULTS.md)) records
the v1 rules commit, the v1 rules SHA-256 and the v1 evaluator SHA-256; editing either v1 file
would make that evidence unreproducible. All eight retained clips in that batch had no
assessment, so the optional assessment branch did not contribute to it, and it is not
regenerated or relabelled. Every rule in version 1 (frozen set, confirmation provenance,
denominators, discrepancies, limits) applies to version 2 unchanged, except as stated below.

## Assessment source-path binding (the only semantic change)

When `--assessments-dir` supplies a #111 assessment for a clip, the evaluator already requires
the schema, fixture id, and the SHA-256 of the current `video`, `seed` and `run_record` bytes to
equal both the retained session-record binding and the assessment's `sources.<name>.sha256`.
Version 2 additionally requires `sources.<name>.path` for each of `video`, `seed` and
`run_record` to equal, as an exact string, the canonical repository-relative path in the retained
session-record binding. Per `docs/validation/VBT_CLIP_ASSESSMENT.md`, absolute, escaping,
`./`-prefixed, backslash, case-differing or otherwise normalized-equivalent spellings are not
recognized; no normalization is applied before comparing.

The path check runs for all three sources before any hash check. A clip whose assessment records
any differing path, even with identical bytes, is excluded with the explicit reason
`assessment_source_path_mismatch` and stays in inventory and status counts like any other
exclusion. Other assessment binding failures keep the reason `assessment_binding_invalid`.
Correctly bound assessments are accepted exactly as in version 1.
