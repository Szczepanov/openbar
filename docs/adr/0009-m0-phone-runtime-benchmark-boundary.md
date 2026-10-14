# ADR-0009: M0 phone runtime benchmark boundary

- Status: Accepted (amended 2026-10-14, see Amendment 1)
- Date: 2026-10-02
- Issue: #59
- Related: #12, #14, #16, #53, #57, #179

## Context

The provisional M0 performance gate is:

> offline processing faster than video duration on reference hardware.

Desktop timings exist, but ADR-0008 correctly left this gate **NOT MEASURABLE YET** because
desktop measurements are diagnostic only. The intended first product runtime is a phone, while the
current M0 decode harness uses external FFmpeg/FFprobe processes under ADR-0006.

Issue #59 needs a real phone-class execution boundary that can exercise the current Rust pipeline
without prematurely selecting Flutter, Android MediaCodec integration, a native decoder crate, or a
production mobile packaging architecture.

The runtime study must also remain downstream of the measurement evidence:

- #57 selects or rejects the tracker/filter candidate using held-out real video;
- #53 defines at least one practically useful supported recording envelope;
- #59 measures the frozen candidate on representative clips inside that envelope.

A fast synthetic/development run is not evidence that the product gate passes.

## Decision

### Reference hardware

The M0 reference phone is the **Xiaomi 14T Pro** (MediaTek Dimensity 9300+), using the 64-bit
Android ARM runtime. Its Android device codename is **rothko** (model 2407FPN8EG), read from the
owner's unit on 2026-10-14 (see Amendment 1).

This is a fixed physical-device target, not an emulator class or a desktop performance proxy.

The benchmark artifact records the observed model, device codename, ABI, Android release/SDK,
available memory and tool versions. A run that does not identify the expected reference device /
arm64 environment cannot satisfy the gate. The codename `rothko` is shared with the Redmi K70
Ultra, which uses the same board, so the observed model string is kept in the artifact for review.

Why this target:

- it is a concrete physical Android arm64 phone the project can actually measure on, rather than an
  abstract CPU class;
- freezing one device makes performance regressions comparable across pipeline revisions.

Limit: this is a high-end SoC. A pass on it says nothing about lower-tier phones. This ADR does
not define a minimum supported consumer phone. It defines the M0 evidence reference.

### Executable/runtime boundary

For #59, execute the existing **release-built native ARM64 Rust CLI** under Termux and keep the
ADR-0006 external FFmpeg/FFprobe process boundary.

Conceptually:

    phone media
      -> Termux ffprobe/ffmpeg process
      -> display-oriented grayscale frames + authoritative PTS
      -> existing openbar-tracking tracker
      -> openbar-core calibration/filter/kinematics
      -> canonical analysis

This is an **M0 benchmark runtime**, not the production mobile decoder architecture.

It intentionally avoids:

- Flutter;
- JNI/FFI product integration;
- MediaCodec/NDK decoder implementation;
- a new Rust video dependency;
- live camera or real-time tracking.

A native Android decoder may later replace the media adapter behind the same frame contract, but
only through its own issue/ADR after the #59 evidence identifies a need.

### Benchmark harness

validation/tools/phone_runtime_benchmark.py measures:

1. end-to-end release analyze wall time;
2. processing-time / selected-video-duration ratio;
3. peak observed process-tree RSS;
4. the deterministic decoded grayscale frame-buffer size implied by the selected frames;
5. decoder and tracker wall-time diagnostics, parsed from tracker-run;
6. battery capacity/temperature and maximum readable thermal-zone temperature before/after, where
   Android exposes them to the process;
7. complete observed reference-device/tool provenance.

The harness repeats each case three times by default and uses the median end-to-end ratio for the
gate. A failed representative run is not averaged away.

The stage split is diagnostic. The authoritative performance gate uses the complete analyze wall
time, because the user waits for the complete offline analysis rather than for an isolated tracker.

### Gate-eligible evidence

The harness may run on any machine for diagnostics, but a gate status can be assigned only when:

- the suite explicitly records that #57 has frozen the production tracker/filter candidate;
- the observed runtime matches the designated physical reference-phone environment;
- at least two cases are explicitly marked representative for the gate;
- those cases are non-synthetic real lifting clips;
- their recording-support status is supported, not warning/unknown;
- the frozen analysis pipeline completes all repeated runs;
- process-tree RSS can be measured.

The provisional target is evaluated per representative case:

    median(end-to-end processing wall time / selected media duration) < 1.0

Result semantics:

- **PASS** — every representative supported real case has median ratio < 1.0;
- **FAIL** — the frozen pipeline fails on a representative case, or any representative case has
  median ratio >= 1.0;
- **NOT MEASURABLE YET** — reference runtime, representative supported clips, or required runtime
  observations are missing.

The harness does not automatically emit **REVISED**. ADR-0008 requires a target revision to be
evidence-justified; changing the target remains an explicit engineering/product decision.

### Frozen configuration

The initial harness uses the current canonical #14 integration probe:

- template-sad-v1@1;
- raw-identity@1;
- backward-difference@1;
- max continuity gap 0.2 s;
- minimum kinematics confidence 0.0;
- ADR-0006 external FFmpeg/FFprobe decode.

This configuration is useful for exercising the phone boundary now. It is **not** promoted to the
production tracker/filter choice by this ADR.

Before final #59 gate evidence is accepted, #57 must provide the production candidate. If that
candidate differs, the harness constants and documentation must be updated in the same evidence
change so the benchmark remains frozen and reproducible.

## Current gate status

**NOT MEASURABLE YET.**

This ADR resolves the missing reference-hardware/runtime definition and provides an executable
measurement path. It does not manufacture the missing physical-phone evidence.

Final #59 gate evidence still requires:

1. the frozen candidate from #57;
2. representative clips inside a supported envelope from #53;
3. execution of the benchmark on the designated reference phone;
4. review of the resulting timing, memory and thermal/battery observations.

## ADR-0006 disposition

ADR-0006 remains suitable for this M0 phone experiment because it can be executed on the physical
reference phone and preserves the existing timestamp/frame contract.

The benchmark must not extrapolate desktop FFmpeg results to mobile.

If the reference-phone evidence shows external-process decode to be a material blocker, excessive memory
pressure from whole-clip buffering, or a runtime incompatibility, create a separate issue and ADR
before implementing a native decoder or streaming tracker boundary.

## Amendment 1 (2026-10-14, #179): reference phone

The original decision (2026-10-02) designated the **Google Pixel 8** (codename `shiba`). The
project owner does not have that device and does have a Xiaomi 14T Pro, so the reference phone is
now the **Xiaomi 14T Pro**.

No Pixel 8 evidence was ever collected and the gate was NOT MEASURABLE YET, so no recorded result is
invalidated. The original rationale that the reference should not be the newest flagship is not
carried over: the 14T Pro is a high-end phone, and the gate result applies to it only.

Consequences:

- `validation/tools/phone_runtime_benchmark.py` identifies the new device
  (`REFERENCE_MODEL`, `REFERENCE_DEVICE`). It still requires arm64, Android and Termux, and still
  fails closed: any other device is NOT MEASURABLE.
- The identity was read from the owner's unit with `getprop` on 2026-10-14: manufacturer Xiaomi,
  model 2407FPN8EG, device `rothko`, ABI `arm64-v8a`, Android 16 (SDK 36). The first harness
  artifact's `environment.observed` repeats it. If a later artifact disagrees, correct the constant
  and this ADR to the observed value in the same change. Any mismatch keeps `reference_match` false
  and the gate NOT MEASURABLE.
- The vendor Android build may hide battery or thermal readings from Termux. They are then `null`,
  as for any device, and the run is reviewed rather than inferred.
- Whether this phone is also the product reference, and whether a lower-tier phone is added, remains
  an owner decision in `docs/validation/ANDROID_LATENCY_EVIDENCE.md`.

## Consequences

### Positive

- #59 now has a reproducible physical-phone target and executable benchmark path.
- Performance evidence remains separate from deterministic canonical analysis.
- The existing validated Rust measurement contracts are exercised without adding Flutter or a new
  production media architecture.
- Decode and tracker costs can be diagnosed independently while the gate remains end-to-end.
- A future decoder change can be justified by measured evidence rather than assumption.

### Costs / limitations

- Termux + external FFmpeg is not the intended consumer packaging.
- Whole-selected-clip grayscale buffering can be memory-heavy and is deliberately measured.
- Some Android battery/thermal sysfs values may be inaccessible; unavailable readings remain null.
- Actual gate closure still depends on #57/#53 and access to the physical reference phone.

## Revisit

Revisit this ADR when:

- #57 selects a different tracker/filter configuration;
- #53 promotes representative real recording conditions to supported;
- reference-phone evidence assigns PASS or FAIL to the performance gate;
- external-process decode or frame buffering is shown to require an architecture follow-up;
- a later product milestone chooses the production Android media/runtime boundary.
