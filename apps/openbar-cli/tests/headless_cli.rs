use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn binary() -> &'static str {
    env!("CARGO_BIN_EXE_openbar-cli")
}

fn repo_path(relative: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .join(relative)
}

fn scratch(name: &str) -> PathBuf {
    std::env::temp_dir().join(format!("openbar-cli-it-{}-{name}", std::process::id()))
}

fn ffmpeg_available() -> bool {
    let available = ["ffmpeg", "ffprobe"].iter().all(|tool| {
        Command::new(tool)
            .arg("-version")
            .output()
            .is_ok_and(|output| output.status.success())
    });
    if !available {
        assert!(
            std::env::var_os("OPENBAR_REQUIRE_FFMPEG").is_none(),
            "OPENBAR_REQUIRE_FFMPEG is set but ffmpeg/ffprobe are not on PATH"
        );
        eprintln!("SKIPPED: ffmpeg/ffprobe not on PATH; CLI media integration test not run");
    }
    available
}

fn analyze_fixture(output: &Path) -> Command {
    analyze_fixture_with_plate(output, "0.45")
}

fn analyze_fixture_with_plate(output: &Path, plate_diameter_m: &str) -> Command {
    let mut command = Command::new(binary());
    command
        .arg("analyze")
        .arg("--manifest")
        .arg(repo_path("validation/fixtures/public/manifest.json"))
        .arg("--fixture")
        .arg("synthetic-clean-side-12")
        .arg("--video")
        .arg(repo_path(
            "validation/fixtures/public/synthetic-clean-side-12.mp4",
        ))
        .arg("--seed")
        .arg(repo_path(
            "validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json",
        ))
        .arg("--plate-diameter-m")
        .arg(plate_diameter_m)
        .arg("--tracker")
        .arg("template")
        .arg("--filter")
        .arg("raw")
        .arg("--kinematics-max-gap-s")
        .arg("0.2")
        .arg("--kinematics-min-confidence")
        .arg("0")
        .arg("--diagnostics")
        .arg("normal")
        .arg("--output")
        .arg(output);
    command
}

fn assert_exit(output: &Output, code: i32, label: &str) {
    assert_eq!(
        output.status.code(),
        Some(code),
        "stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&output.stderr).contains(label),
        "expected stderr to contain {label:?}, got {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn analyze_process_is_deterministic_round_trips_and_refuses_overwrite() {
    if !ffmpeg_available() {
        return;
    }

    let output_a = scratch("deterministic-a.json");
    let output_b = scratch("deterministic-b.json");
    let render_output = scratch("render-diagnostic.svg");
    let _ = fs::remove_file(&output_a);
    let _ = fs::remove_file(&output_b);
    let _ = fs::remove_file(&render_output);

    let first = analyze_fixture(&output_a)
        .output()
        .expect("run first analyze");
    assert!(
        first.status.success(),
        "stdout={}\nstderr={}",
        String::from_utf8_lossy(&first.stdout),
        String::from_utf8_lossy(&first.stderr)
    );
    assert!(
        String::from_utf8_lossy(&first.stderr).contains("status=warning"),
        "unassessed calibration should remain visible"
    );

    let second = analyze_fixture(&output_b)
        .output()
        .expect("run second analyze");
    assert!(second.status.success());

    let bytes_a = fs::read(&output_a).expect("read first analysis");
    let bytes_b = fs::read(&output_b).expect("read second analysis");
    assert_eq!(bytes_a, bytes_b, "identical executions must be byte-stable");

    let json: Value = serde_json::from_slice(&bytes_a).expect("canonical JSON");
    assert_eq!(json["schema_version"], 1);
    assert_eq!(json["identity"]["fixture_id"], "synthetic-clean-side-12");
    assert!(json["derived"]["filtered"].is_object());
    assert!(json["derived"]["kinematics"].is_object());
    if let Some(commit) = option_env!("OPENBAR_GIT_COMMIT") {
        assert_eq!(json["provenance"]["pipeline"]["git_commit"], commit);
    }

    let overwrite = analyze_fixture(&output_a)
        .output()
        .expect("run overwrite rejection");
    assert_exit(&overwrite, 7, "status=failure error[output]");
    assert_eq!(
        fs::read(&output_a).expect("output survives rejected overwrite"),
        bytes_a
    );

    let analysis_before_render = fs::read(&output_a).expect("read analysis before render");
    let render = Command::new(binary())
        .arg("render")
        .arg("--analysis")
        .arg(&output_a)
        .arg("--video")
        .arg(repo_path(
            "validation/fixtures/public/synthetic-clean-side-12.mp4",
        ))
        .arg("--output")
        .arg(&render_output)
        .output()
        .expect("run diagnostic renderer");
    assert!(
        render.status.success(),
        "stdout={}\nstderr={}",
        String::from_utf8_lossy(&render.stdout),
        String::from_utf8_lossy(&render.stderr)
    );
    assert_eq!(
        fs::read(&output_a).expect("read analysis after render"),
        analysis_before_render,
        "rendering must not modify canonical analysis"
    );
    let svg = fs::read_to_string(&render_output).expect("read render artifact");
    assert!(svg.contains("renderer=diagnostic-svg@1"));
    assert!(svg.contains("data-layer=\"source-frame\""));
    assert!(svg.contains("data-layer=\"raw-trajectory\""));
    assert!(svg.contains("data-layer=\"filtered-trajectory\""));
    assert!(svg.contains("data-layer=\"velocity\""));

    let overwrite = Command::new(binary())
        .arg("render")
        .arg("--analysis")
        .arg(&output_a)
        .arg("--output")
        .arg(&render_output)
        .output()
        .expect("run render overwrite rejection");
    assert_exit(&overwrite, 7, "status=failure error[output]");

    let out_of_range = Command::new(binary())
        .arg("render")
        .arg("--analysis")
        .arg(&output_a)
        .arg("--video")
        .arg(repo_path(
            "validation/fixtures/public/synthetic-clean-side-12.mp4",
        ))
        .arg("--frame-timestamp-s")
        .arg("999")
        .arg("--output")
        .arg(scratch("render-out-of-range.svg"))
        .output()
        .expect("run out-of-range render");
    assert_exit(&out_of_range, 2, "status=failure error[invalid-input]");

    let _ = fs::remove_file(output_a);
    let _ = fs::remove_file(output_b);
    let _ = fs::remove_file(render_output);
}

#[test]
fn render_failure_fixture_is_deterministic_and_keeps_uncertainty_visible() {
    let analysis = repo_path("crates/openbar-core/tests/fixtures/analysis-v1.golden.json");
    let output_a = scratch("render-failure-a.svg");
    let output_b = scratch("render-failure-b.svg");
    let _ = fs::remove_file(&output_a);
    let _ = fs::remove_file(&output_b);

    for output in [&output_a, &output_b] {
        let render = Command::new(binary())
            .arg("render")
            .arg("--analysis")
            .arg(&analysis)
            .arg("--output")
            .arg(output)
            .output()
            .expect("render failure diagnostic");
        assert!(
            render.status.success(),
            "stdout={}\nstderr={}",
            String::from_utf8_lossy(&render.stdout),
            String::from_utf8_lossy(&render.stderr)
        );
        assert!(String::from_utf8_lossy(&render.stderr).contains("status=warning"));
    }

    let first = fs::read(&output_a).expect("read first failure report");
    let second = fs::read(&output_b).expect("read second failure report");
    assert_eq!(
        first, second,
        "same canonical input must render byte-identically"
    );
    let svg = String::from_utf8(first).expect("SVG must be UTF-8");
    assert!(svg.contains("data-state=\"lost\""));
    assert!(svg.contains("data-state=\"low_confidence\""));
    assert!(svg.contains("No filtered trajectory in canonical analysis."));
    assert!(svg.contains("No kinematic trajectory in canonical analysis."));

    let _ = fs::remove_file(output_a);
    let _ = fs::remove_file(output_b);
}

#[test]
fn render_rejects_malformed_canonical_analysis_without_creating_output() {
    let analysis = scratch("render-malformed.json");
    let output = scratch("render-malformed.svg");
    let _ = fs::remove_file(&analysis);
    let _ = fs::remove_file(&output);
    fs::write(&analysis, r#"{"schema_version":1}"#).expect("write malformed analysis");

    let render = Command::new(binary())
        .arg("render")
        .arg("--analysis")
        .arg(&analysis)
        .arg("--output")
        .arg(&output)
        .output()
        .expect("run malformed render");
    assert_exit(&render, 2, "status=failure error[invalid-input]");
    assert!(
        !output.exists(),
        "invalid analysis must not create a render artifact"
    );

    let _ = fs::remove_file(analysis);
}

#[test]
fn major_cli_failure_paths_have_stable_categories_and_nonzero_codes() {
    let valid_seed = repo_path(
        "validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json",
    );
    let missing_video = scratch("missing.mp4");
    let output_path = scratch("failure-output.json");
    let malformed_seed = scratch("malformed-seed.json");
    let malformed_benchmark = scratch("malformed-benchmark.json");
    let _ = fs::remove_file(&missing_video);
    let _ = fs::remove_file(&output_path);
    fs::write(&malformed_seed, "{not-json").expect("write malformed seed");
    fs::write(&malformed_benchmark, "{not-json").expect("write malformed benchmark");

    let fixture_plate_mismatch = analyze_fixture_with_plate(&output_path, "0.50")
        .output()
        .expect("run fixture plate mismatch");
    assert_exit(
        &fixture_plate_mismatch,
        5,
        "status=failure error[seed-calibration]",
    );
    assert!(
        String::from_utf8_lossy(&fixture_plate_mismatch.stderr).contains("load.plate_diameter_m")
    );

    let invalid_plate = Command::new(binary())
        .arg("analyze")
        .arg("--video")
        .arg(&missing_video)
        .arg("--seed")
        .arg(&valid_seed)
        .arg("--plate-diameter-m")
        .arg("0")
        .arg("--tracker")
        .arg("template")
        .arg("--filter")
        .arg("raw")
        .arg("--kinematics-max-gap-s")
        .arg("0.2")
        .arg("--kinematics-min-confidence")
        .arg("0")
        .arg("--output")
        .arg(&output_path)
        .output()
        .expect("run invalid plate");
    assert_exit(&invalid_plate, 5, "status=failure error[seed-calibration]");

    let invalid_seed = Command::new(binary())
        .arg("analyze")
        .arg("--video")
        .arg(&missing_video)
        .arg("--seed")
        .arg(&malformed_seed)
        .arg("--plate-diameter-m")
        .arg("0.45")
        .arg("--tracker")
        .arg("template")
        .arg("--filter")
        .arg("raw")
        .arg("--kinematics-max-gap-s")
        .arg("0.2")
        .arg("--kinematics-min-confidence")
        .arg("0")
        .arg("--output")
        .arg(&output_path)
        .output()
        .expect("run invalid seed");
    assert_exit(&invalid_seed, 5, "status=failure error[seed-calibration]");

    let invalid_filter = Command::new(binary())
        .arg("analyze")
        .arg("--video")
        .arg(&missing_video)
        .arg("--seed")
        .arg(&valid_seed)
        .arg("--plate-diameter-m")
        .arg("0.45")
        .arg("--tracker")
        .arg("template")
        .arg("--filter")
        .arg("moving-average")
        .arg("--filter-window")
        .arg("4")
        .arg("--filter-max-gap-s")
        .arg("0.2")
        .arg("--kinematics-max-gap-s")
        .arg("0.2")
        .arg("--kinematics-min-confidence")
        .arg("0")
        .arg("--output")
        .arg(&output_path)
        .output()
        .expect("run invalid filter");
    assert_exit(&invalid_filter, 2, "status=failure error[invalid-input]");

    let missing_media = Command::new(binary())
        .arg("analyze")
        .arg("--video")
        .arg(&missing_video)
        .arg("--seed")
        .arg(&valid_seed)
        .arg("--plate-diameter-m")
        .arg("0.45")
        .arg("--tracker")
        .arg("template")
        .arg("--filter")
        .arg("raw")
        .arg("--kinematics-max-gap-s")
        .arg("0.2")
        .arg("--kinematics-min-confidence")
        .arg("0")
        .arg("--output")
        .arg(&output_path)
        .output()
        .expect("run missing media");
    assert_exit(&missing_media, 3, "status=failure error[media]");

    let benchmark = Command::new(binary())
        .arg("benchmark")
        .arg("--suite")
        .arg(&malformed_benchmark)
        .output()
        .expect("run malformed benchmark");
    assert_exit(&benchmark, 8, "status=failure error[benchmark]");

    let legacy = Command::new(binary())
        .arg("tracker-experiment")
        .arg("--definitely-invalid")
        .output()
        .expect("run legacy harness argument failure");
    assert_eq!(legacy.status.code(), Some(2));
    let legacy_stderr = String::from_utf8_lossy(&legacy.stderr);
    assert!(legacy_stderr.contains("error:"));
    assert!(!legacy_stderr.contains("status=failure"));

    if ffmpeg_available() {
        assert_runtime_media_failures(&output_path);
    }

    let _ = fs::remove_file(malformed_seed);
    let _ = fs::remove_file(malformed_benchmark);
    let _ = fs::remove_file(output_path);
}

fn assert_runtime_media_failures(output_path: &Path) {
    let manifest = scratch("wrong-geometry-manifest.json");
    let wrong_manifest = serde_json::json!({
        "schema_version": 1,
        "fixtures": [{
            "id": "synthetic-clean-side-12",
            "video": {
                "width_px": 999,
                "height_px": 96,
                "rotation_deg": 0
            },
            "load": {
                "plate_diameter_m": 0.45
            }
        }]
    });
    fs::write(
        &manifest,
        format!(
            "{}\n",
            serde_json::to_string_pretty(&wrong_manifest).expect("serialize manifest")
        ),
    )
    .expect("write manifest");

    let unsupported = Command::new(binary())
        .arg("analyze")
        .arg("--manifest")
        .arg(&manifest)
        .arg("--fixture")
        .arg("synthetic-clean-side-12")
        .arg("--video")
        .arg(repo_path(
            "validation/fixtures/public/synthetic-clean-side-12.mp4",
        ))
        .arg("--seed")
        .arg(repo_path(
            "validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json",
        ))
        .arg("--plate-diameter-m")
        .arg("0.45")
        .arg("--tracker")
        .arg("template")
        .arg("--filter")
        .arg("raw")
        .arg("--kinematics-max-gap-s")
        .arg("0.2")
        .arg("--kinematics-min-confidence")
        .arg("0")
        .arg("--output")
        .arg(output_path)
        .output()
        .expect("run unsupported geometry");
    assert_exit(&unsupported, 4, "status=failure error[unsupported]");

    let tracking = Command::new(binary())
        .arg("analyze")
        .arg("--manifest")
        .arg(repo_path("validation/fixtures/public/manifest.json"))
        .arg("--fixture")
        .arg("synthetic-clean-side-12")
        .arg("--video")
        .arg(repo_path(
            "validation/fixtures/public/synthetic-clean-side-12.mp4",
        ))
        .arg("--seed")
        .arg(repo_path(
            "validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json",
        ))
        .arg("--plate-diameter-m")
        .arg("0.45")
        .arg("--tracker")
        .arg("contrast")
        .arg("--contrast-min-seed-contrast")
        .arg("1000000000")
        .arg("--filter")
        .arg("raw")
        .arg("--kinematics-max-gap-s")
        .arg("0.2")
        .arg("--kinematics-min-confidence")
        .arg("0")
        .arg("--output")
        .arg(output_path)
        .output()
        .expect("run tracking failure");
    assert_exit(&tracking, 6, "status=failure error[tracking]");

    let _ = fs::remove_file(manifest);
}
