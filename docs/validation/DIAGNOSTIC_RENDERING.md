# Diagnostic Rendering

Issue #13 adds deterministic diagnostic rendering to the M0 headless pipeline. The renderer is a
debugging/validation surface, not the future product UI.

## Command

```bash
cargo run --locked -p openbar-cli -- render \
  --analysis target/analyze-smoke.json \
  --video validation/fixtures/public/synthetic-clean-side-12.mp4 \
  --output target/render-success.svg
```

`--video` is optional. Without it the report still renders trajectories and numeric plots in the
canonical display coordinate system. `--frame-timestamp-s` may be supplied only with `--video`
and must identify a timestamp present in canonical `raw_observations`. Existing outputs are not
overwritten unless `--force` is given.

## Measurement boundary

Rendering consumes a validated `openbar_core::analysis::Analysis`. It does not run a tracker,
filter, calibration, interpolation, or kinematics implementation. It therefore cannot change the
numeric analysis it displays.

The report contains:

- a spatial display-coordinate overlay with manual seed, raw path, optional filtered path, sampled
  tracker bounds, tracked/low-confidence markers, and an explicit loss timeline;
- X/Y calibrated position versus authoritative timestamp;
- X/Y velocity versus authoritative timestamp when canonical kinematics exist;
- confidence and tracking state versus authoritative timestamp;
- analysis/pipeline/render provenance.

Filtered metric positions are inverse-projected through the recorded plate scale only to place the
already-persisted filtered points in the spatial diagnostic view. That is a rendering coordinate
transform, not recalibration or refiltering.

## Missing-data semantics

A canonical `lost` observation breaks trajectory and velocity paths. The renderer never connects
a line across a recorded loss and never synthesizes a replacement coordinate. Low-confidence
observations remain visible. If filtered or kinematic layers are absent, the report says so instead
of inventing them.

## Source-frame verification

When `--video` is supplied, the existing ADR-0006 FFmpeg process adapter is reused. A canonical
`identity.source_sha256` is required so the renderer cannot place a plausible but unrelated
same-sized video behind the trajectory. Before a frame is embedded, the renderer verifies:

1. source SHA-256 against canonical analysis identity;
2. display width/height;
3. source rotation;
4. selected presentation timestamp against canonical raw observations;
5. decoded frame index against the canonical frame index.

The selected decoded grayscale frame is encoded into a deterministic, dependency-free PNG and
embedded into the SVG. Rendering therefore adds no new Cargo or native dependency. FFmpeg remains
the existing external runtime prerequisite only when a source frame is requested.

## Reproducibility

The SVG records:

- renderer id/version (`diagnostic-svg@1`);
- source analysis path and schema version;
- source id/hash;
- pipeline version and Git commit when recorded;
- optional source-video path, selected timestamp, and frame index.

For identical canonical input, arguments, and source frame bytes, report bytes are deterministic.
Tests also assert that rendering leaves the canonical analysis file unchanged.

## CI evidence

CI produces two diagnostic artifacts:

- `target/render-success.svg`: the public synthetic fixture after the canonical `analyze` smoke,
  including an embedded verified source frame and persisted filtered/kinematic layers;
- `target/render-failure.svg`: the canonical golden analysis containing an explicit lost sample,
  a low-confidence sample, and intentionally absent filtered/kinematic layers.

These artifacts are uploaded with the existing M0 smoke evidence on Ubuntu.
