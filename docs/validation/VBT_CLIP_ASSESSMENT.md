# Research VBT clip assessment v1

Issue #111 is the first slice under #110. `assess_clip.py` reads one retained
`analyze_lift.py` workflow-v3/run-record-v2 and writes a separate, deterministic
`vbt-clip-assessment-v1` artifact. It never changes the video, seed, prediction, run record,
or canonical Analysis. The wire contract is `validation/schema/vbt-clip-assessment-v1.schema.json`;
readers must reject other schema/implementation versions and unknown fields. The committed
example describes absent evidence, not a successful measurement.

```powershell
cargo build --locked -p openbar-cli
python research/vbt-workflow/assess_clip.py --fixture-id vbt-<hash-prefix> `
  --run-record validation/private/vbt/analyses/<clip>.run-record.json `
  --openbar-cli target/debug/openbar-cli.exe --output target/clip.assessment-v1.json
```

Use the run record for the selected tracker. The explicit fixture id allows an incomplete
assessment even when its run record is absent. Recorded input paths must use the exact canonical,
repository-relative spelling emitted by workflow-v3; absolute, escaping or normalized-equivalent
spellings are not recognized as that run-record contract. Recorded outputs must be sibling
filenames. The output must be a new file; existing files are refused. Exit 0 means an assessment
was written, including rejected/unknown results. It does not mean the clip passed. No automatic
session integration or consumer writes occur.

## Four independent conclusions

| Field | States | Evidence and limits |
|---|---|---|
| `processing` | `complete`, `incomplete`, `unknown` | Recognized run and every declared output present with its recorded hash. A missing output or hash mismatch is incomplete; an unrecognized run is unknown. Completion says nothing about measurement validity. |
| `mechanical` | `valid`, `invalid`, `unknown` | All four checks must be valid. Any invalid check makes the conclusion invalid; otherwise a missing/unavailable check makes it unknown. |
| `experiment_suitability` | `unknown`, `rejected` | Mechanical invalidity rejects the clip. Otherwise suitability stays unknown: this first slice has no predeclared experiment criteria or support-evidence ingestion. |
| `accuracy` | `not_established` | This first slice accepts no accuracy verdict. No reviewed independent physical evidence is supplied. |

The wire schema enforces the mechanical aggregate above rather than trusting a writer-supplied
summary: `valid` requires every mechanical check to be valid, `invalid` requires at least one
invalid check, and `unknown` requires no invalid check plus at least one unknown check. Successful
processing/check states carry no failure reason; incomplete, invalid or unknown states carry an
explicit reason.

The tool intentionally has no eligibility or accuracy promotion switch. #112 must supply
predeclared experiment-specific criteria and review their evidence before any later version can
emit suitability. A later accuracy verdict requires a cited, reviewed, independently obtained
reference, applicable recording conditions, protocol and uncertainty; #53/#58/#59 study harnesses,
#79 WL agreement, tracker consensus, confidence, and coverage alone do not provide that evidence.
Unknown support remains unknown. A mechanically valid clip may track the wrong object.

## Mechanical checks and binding

- `run_binding`: known record/workflow versions, expected fixture and tracker, canonical
  repository-relative input paths, required output set, and hashes of the exact retained output
  bytes (including circle geometry when declared).
- `source_binding`: video and seed hashes, the manifest entry's canonical JSON hash, fixture
  binding, the canonical source hash, retained prediction hash in tracker provenance, and
  canonical manual seed matching the supplied seed through the Rust CLI's `--seed` reader,
  including canonical `f32` confidence normalization. The manifest's whole-file hash is retained
  for audit, but unrelated entries need not match a historical whole-file hash.
- `canonical_analysis`: `openbar-cli validate-analysis --analysis ...` calls
  `Analysis::from_json`. Rust is authoritative for version/fields, finite values, strictly
  increasing timestamps, loss without measurement/bounds, required calibration and provenance,
  and derived-layer consistency. Python does not reimplement those rules.
- `decoded_pts`: the same command with `--video` uses `ProbedVideo::open` to verify source hash,
  encoded/display geometry and rotation, then compares the seed and **every** raw observation to
  the validated PTS timeline. It shares the external-ingestion nearest-frame matcher and existing
  0.5 ms tolerance. Supplied frame indices must match and raw samples must map to distinct,
  advancing frames. No timestamp, loss, confidence or coordinate is rewritten.

PTS checking probes retained media without decoding pixels. It verifies the current source
timeline and canonical identities, not a new successful full pixel decode. The retained run
binds outputs from the existing verified `analyze --observations` ingestion. Sparse observations
and absent optional frame indices remain legal; the assessment does not invent completeness,
gap, error or confidence thresholds. Hashes bind bytes; they are not signed attestations of run
authenticity. The explicit current CLI executable is trusted as the validator; its hash and the
entire run-record hash are retained. Repeated assessment of unchanged files with the same
validator/path arguments produces identical JSON. No clock time or random id is emitted.

## Reasons and evidence needed to resolve them

| Reason codes | Resolution |
|---|---|
| `run_record_missing`, `run_output_missing`, `source_missing` | Restore the retained run/output/source inputs, or complete a human-confirmed run. Missing evidence never passes. |
| `run_record_invalid` | Supply a recognized, correctly bound workflow-v3/run-record-v2, including its canonical repository-relative input-path spellings. Other formats and future versions require explicit ingestion support. |
| `run_output_hash_mismatch`, `source_hash_mismatch`, `manifest_entry_hash_mismatch` | Recover the original exact files/entry or deliberately rerun from the correct inputs; never relabel a changed input as unchanged. |
| `source_binding_invalid`, `analysis_run_binding_mismatch`, `analysis_seed_mismatch` | Supply the fixture, seed, manifest, prediction and analysis from the same confirmed run. |
| `canonical_analysis_invalid` | Fix the producing pipeline/input against the authoritative canonical contract and regenerate; do not patch retained raw data to pass. |
| `source_pts_invalid` | Resolve the actual source/geometry/PTS/frame mismatch through authoritative ingestion. The reason includes source identity/geometry failures as well as timing. |
| `source_unsupported` | Use a supported recording format or independently implement/validate its media support. This is a rejection, not accuracy evidence. |
| `validator_unavailable`, `source_probe_unavailable` | Build the current CLI or restore readable media and FFmpeg/ffprobe. Tool/I/O failures and uninterpretable probe output remain unknown; confirmed missing/non-increasing PTS, invalid time base or geometry are rejected. |
| `canonical_analysis_not_verified`, `source_binding_not_verified` | Resolve the preceding check; PTS is not marked passed when its prerequisite is unresolved. |
| `evidence_changed_during_assessment` | Assess a stable retained input set. Every observed file is rehashed before returning. |
| `mechanical_invalid` | Resolve the named failed mechanical checks before judging suitability. |
| `experiment_criteria_not_predeclared`, `recording_support_not_established` | Predeclare the particular experiment and provide reviewed applicable support evidence in a later assessment slice. |
| `independent_accuracy_evidence_missing` | Obtain and review independently referenced accuracy evidence with its scope and uncertainty; no first-slice promotion exists. |

Keep #57 held-out data out of development labeling. This tooling adds no tracker, tuning,
dependency, evidence registry, canonical Analysis field, production default, or source-switch
decision. The #112 evaluation, recorded proceed/stop decision before #113, #114 local handoff,
and #79 agreement decision remain distinct work.

Tests: `test_assess_clip.py`, `test_assess_clip_contract.py`, `validate_analysis::tests`, and the
headless CLI validation process test cover complete/incomplete/invalid/unsupported/unknown cases,
binding, PTS, no promotion, contract-state consistency, unchanged source bytes, overwrite refusal,
and determinism.