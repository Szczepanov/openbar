# Existing VBT development recording audit

## Goal and boundaries

Continue after the adjusted #103 merge without new human work. Audit the eight existing VBT
development clips using retained CSRT and SAM 2.1 bplus-circle predictions and canonical analyses.
No new seeding, model run, label generation, filter selection or held-out processing. Tracker
agreement is a consistency diagnostic, never independent accuracy or physical-reference truth.

## Ordered work and evidence

1. Validate the manifest/development roles, media hashes and display-oriented FFprobe metadata.
2. Validate existing seeds, prediction contracts and canonical Analysis schemas. Verify raw
   coordinates/confidence/loss and timestamps match retained predictions; align frame indices to
   actual decoded PTS. Record any refusal instead of silently repairing an input.
3. Exclude initialization. Compare centres on exact common tracked timestamps and displacement
   only on consecutive decoded frames, with no losses and gaps <=0.2 s. Retain missing/lost support.
4. Render each existing canonical Analysis through the Rust diagnostic consumer. Retain private
   reports, source/input hashes and plots. Compare current session seed content with retained
   legacy seeds separately, so stale configurations are visible rather than combined.
5. Leave a runnable synthetic self-check and repeat the frozen-input audit. Publish only aggregate
   findings under docs/analysis; keep the bounded audit script and trajectories private.

Stop after inventory/alignment/consistency assessment of these eight clips. Existing sparse-label
accuracy results remain the separate three-clip report. No MAE, false-track or validated-velocity
claim is possible on these unlabelled streams. Any discovered stale session lineage or numerical
failure must be diagnosed before recommending a tracker change.
