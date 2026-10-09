//! Diagnostic renderer CLI for issue #13.
//!
//! The renderer consumes validated canonical analysis output. Source media is optional and is
//! used only as a verified background frame; no tracking/filtering/calibration/kinematics runs
//! here.

use crate::cli_error::{CliError, CliResult};
use crate::diagnostic_svg::{render_svg, SourceFrame, RENDERER_ID, RENDERER_VERSION};
use crate::media::{FrameSourceOptions, MediaError, ProbedVideo, TimeRange as MediaTimeRange};
use openbar_core::analysis::{Analysis, TrackingState};
use openbar_tracking::GrayscaleImage;
use std::collections::BTreeMap;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write as _;
use std::path::{Path, PathBuf};

const USAGE: &str = "Usage: openbar-cli render --analysis <analysis.json> --output <report.svg>\n\
     [--video <source-video>] [--frame-timestamp-s <s>] [--force]\n\
\n\
Produces a deterministic diagnostic SVG from canonical analysis-v1 data.\n\
When --video is supplied, the source hash/display geometry/rotation and selected canonical\n\
frame identity are verified before the frame is embedded in the report.";

#[derive(Debug)]
struct Args {
    analysis: PathBuf,
    video: Option<PathBuf>,
    frame_timestamp_s: Option<f64>,
    output: PathBuf,
    force: bool,
}

pub fn run_cli() -> CliResult<()> {
    let Some(args) = parse_args(env::args().skip(2).collect())? else {
        println!("{USAGE}");
        return Ok(());
    };
    run(&args)
}

fn run(args: &Args) -> CliResult<()> {
    let analysis_json = fs::read_to_string(&args.analysis).map_err(|error| {
        CliError::invalid_input(format!(
            "failed to read canonical analysis '{}': {error}",
            args.analysis.display()
        ))
    })?;
    let analysis = Analysis::from_json(&analysis_json).map_err(|error| {
        CliError::invalid_input(format!(
            "failed to validate canonical analysis '{}': {error}",
            args.analysis.display()
        ))
    })?;

    let source_frame = args
        .video
        .as_deref()
        .map(|video| load_source_frame(&analysis, video, args.frame_timestamp_s))
        .transpose()?;
    let report = render_svg(&analysis, &args.analysis, source_frame.as_ref());
    write_report(&args.output, &report, args.force)?;

    let lost = analysis
        .raw_observations()
        .iter()
        .filter(|sample| sample.tracking_state == TrackingState::Lost)
        .count();
    let low_confidence = analysis
        .raw_observations()
        .iter()
        .filter(|sample| sample.tracking_state == TrackingState::LowConfidence)
        .count();
    eprintln!(
        "status={} output={} renderer={RENDERER_ID}@{RENDERER_VERSION} lost={} low_confidence={} source_frame={}",
        if lost > 0 || low_confidence > 0 {
            "warning"
        } else {
            "success"
        },
        args.output.display(),
        lost,
        low_confidence,
        source_frame.is_some()
    );
    Ok(())
}

fn parse_args(args: Vec<String>) -> CliResult<Option<Args>> {
    if args
        .iter()
        .any(|value| matches!(value.as_str(), "--help" | "-h"))
    {
        return Ok(None);
    }

    let mut values = BTreeMap::new();
    let mut force = false;
    let mut index = 0usize;
    while index < args.len() {
        let flag = args[index].as_str();
        if flag == "--force" {
            if force {
                return Err(CliError::invalid_input("--force was given more than once"));
            }
            force = true;
            index += 1;
            continue;
        }
        if !matches!(
            flag,
            "--analysis" | "--video" | "--frame-timestamp-s" | "--output"
        ) {
            return Err(CliError::invalid_input(format!(
                "unknown render argument '{flag}'"
            )));
        }
        let value = args
            .get(index + 1)
            .ok_or_else(|| CliError::invalid_input(format!("{flag} requires a value")))?;
        if values.insert(flag, value.as_str()).is_some() {
            return Err(CliError::invalid_input(format!(
                "{flag} was given more than once"
            )));
        }
        index += 2;
    }

    let analysis =
        PathBuf::from(values.remove("--analysis").ok_or_else(|| {
            CliError::invalid_input("render requires --analysis <analysis.json>")
        })?);
    let output = PathBuf::from(
        values
            .remove("--output")
            .ok_or_else(|| CliError::invalid_input("render requires --output <report.svg>"))?,
    );
    if output
        .extension()
        .and_then(|value| value.to_str())
        .is_none_or(|extension| !extension.eq_ignore_ascii_case("svg"))
    {
        return Err(CliError::invalid_input(
            "render output must use the .svg extension",
        ));
    }

    let video = values.remove("--video").map(PathBuf::from);
    let frame_timestamp_s = values
        .remove("--frame-timestamp-s")
        .map(|value| {
            value.parse::<f64>().map_err(|_| {
                CliError::invalid_input(format!(
                    "--frame-timestamp-s value '{value}' is not a valid number"
                ))
            })
        })
        .transpose()?;
    if let Some(timestamp_s) = frame_timestamp_s {
        if !timestamp_s.is_finite() || timestamp_s < 0.0 {
            return Err(CliError::invalid_input(
                "--frame-timestamp-s must be finite and non-negative",
            ));
        }
        if video.is_none() {
            return Err(CliError::invalid_input(
                "--frame-timestamp-s requires --video <source-video>",
            ));
        }
    }

    Ok(Some(Args {
        analysis,
        video,
        frame_timestamp_s,
        output,
        force,
    }))
}

fn load_source_frame(
    analysis: &Analysis,
    path: &Path,
    requested_timestamp_s: Option<f64>,
) -> CliResult<SourceFrame> {
    let expected_hash = source_hash_for_overlay(analysis)?;
    let probed = ProbedVideo::open(path).map_err(classify_media_error)?;
    if !expected_hash.eq_ignore_ascii_case(probed.source_sha256()) {
        return Err(CliError::media(format!(
            "source video SHA-256 {} does not match canonical analysis value {expected_hash}",
            probed.source_sha256()
        )));
    }

    let stream = probed.stream();
    let video = analysis.video();
    if (stream.display_width_px, stream.display_height_px)
        != (video.display_width_px, video.display_height_px)
    {
        return Err(CliError::unsupported(format!(
            "source video display size {}x{} does not match canonical analysis size {}x{}",
            stream.display_width_px,
            stream.display_height_px,
            video.display_width_px,
            video.display_height_px
        )));
    }
    if stream.rotation_deg != video.source_rotation_deg {
        return Err(CliError::unsupported(format!(
            "source video rotation {} does not match canonical analysis rotation {}",
            stream.rotation_deg, video.source_rotation_deg
        )));
    }

    let timestamp_s = requested_timestamp_s.unwrap_or_else(|| nearest_seed_observation(analysis));
    if !video.trim.contains(timestamp_s) {
        return Err(CliError::invalid_input(format!(
            "render frame timestamp {timestamp_s} s lies outside canonical selected range [{}, {}]",
            video.trim.start_s, video.trim.end_s
        )));
    }
    let canonical = analysis
        .raw_observations()
        .iter()
        .find(|sample| sample.timestamp_s == timestamp_s)
        .ok_or_else(|| {
            CliError::invalid_input(format!(
                "render frame timestamp {timestamp_s} s is not present in canonical raw_observations"
            ))
        })?;

    let range = MediaTimeRange::try_new(timestamp_s, timestamp_s).map_err(classify_media_error)?;
    let frame_bytes = u64::from(video.display_width_px) * u64::from(video.display_height_px);
    let clip = probed
        .decode(FrameSourceOptions {
            selection: Some(range),
            max_frame_bytes: frame_bytes,
        })
        .map_err(|error| match error {
            MediaError::EmptySelection { .. } => CliError::media(format!(
                "canonical raw-observation timestamp {timestamp_s} s was not present in the verified source video"
            )),
            other => classify_media_error(other),
        })?;
    let frame = clip
        .frames
        .first()
        .ok_or_else(|| CliError::media("source-frame decode returned no frame"))?;
    if clip.frames.len() != 1 || frame.timestamp_s != timestamp_s {
        return Err(CliError::media(format!(
            "source-frame decode did not preserve canonical timestamp {timestamp_s} s"
        )));
    }
    if canonical
        .frame_index
        .is_some_and(|expected| expected != frame.frame_index)
    {
        return Err(CliError::media(format!(
            "decoded source frame index {} does not match canonical frame index {:?} at {timestamp_s} s",
            frame.frame_index, canonical.frame_index
        )));
    }

    Ok(SourceFrame {
        timestamp_s: frame.timestamp_s,
        frame_index: frame.frame_index,
        source_path: path.to_path_buf(),
        png_data_uri: grayscale_png_data_uri(&frame.image),
    })
}

fn source_hash_for_overlay(analysis: &Analysis) -> CliResult<&str> {
    analysis
        .identity()
        .source_sha256
        .as_deref()
        .ok_or_else(|| {
            CliError::invalid_input(
                "render --video requires canonical identity.source_sha256 so the source frame can be verified",
            )
        })
}

fn nearest_seed_observation(analysis: &Analysis) -> f64 {
    let seed_s = analysis.manual_seed().timestamp_s();
    analysis
        .raw_observations()
        .iter()
        .min_by(|left, right| {
            (left.timestamp_s - seed_s)
                .abs()
                .total_cmp(&(right.timestamp_s - seed_s).abs())
        })
        .map_or(seed_s, |sample| sample.timestamp_s)
}

fn classify_media_error(error: MediaError) -> CliError {
    match error {
        MediaError::UnsupportedRotation { .. }
        | MediaError::UnsupportedSampleAspectRatio { .. } => {
            CliError::unsupported(error.to_string())
        }
        MediaError::InvalidRange { .. }
        | MediaError::EmptySelection { .. }
        | MediaError::FrameMemoryBudgetExceeded { .. } => {
            CliError::invalid_input(error.to_string())
        }
        _ => CliError::media(error.to_string()),
    }
}

fn write_report(path: &Path, report: &str, force: bool) -> CliResult<()> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent).map_err(|error| {
                CliError::output(format!(
                    "failed to create render output directory '{}': {error}",
                    parent.display()
                ))
            })?;
        }
    }

    let mut options = OpenOptions::new();
    options.write(true);
    if force {
        options.create(true).truncate(true);
    } else {
        options.create_new(true);
    }
    let mut file = options.open(path).map_err(|error| {
        let hint = if !force && error.kind() == std::io::ErrorKind::AlreadyExists {
            " (use --force to replace it)"
        } else {
            ""
        };
        CliError::output(format!(
            "failed to open render output '{}': {error}{hint}",
            path.display()
        ))
    })?;
    file.write_all(report.as_bytes()).map_err(|error| {
        CliError::output(format!(
            "failed to write render output '{}': {error}",
            path.display()
        ))
    })
}

fn grayscale_png_data_uri(image: &dyn GrayscaleImage) -> String {
    format!(
        "data:image/png;base64,{}",
        base64_encode(&encode_png(image))
    )
}

fn encode_png(image: &dyn GrayscaleImage) -> Vec<u8> {
    let width = image.width_px();
    let height = image.height_px();
    let mut raw = Vec::with_capacity((width as usize + 1) * height as usize);
    for y in 0..height {
        raw.push(0);
        for x in 0..width {
            raw.push(image.intensity(x, y));
        }
    }

    let mut zlib = vec![0x78, 0x01];
    let blocks = raw.len().div_ceil(65_535);
    for (index, block) in raw.chunks(65_535).enumerate() {
        zlib.push(u8::from(index + 1 == blocks));
        let length = block.len() as u16;
        zlib.extend_from_slice(&length.to_le_bytes());
        zlib.extend_from_slice(&(!length).to_le_bytes());
        zlib.extend_from_slice(block);
    }
    zlib.extend_from_slice(&adler32(&raw).to_be_bytes());

    let mut png = vec![0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a];
    let mut header = Vec::with_capacity(13);
    header.extend_from_slice(&width.to_be_bytes());
    header.extend_from_slice(&height.to_be_bytes());
    header.extend_from_slice(&[8, 0, 0, 0, 0]);
    append_png_chunk(&mut png, *b"IHDR", &header);
    append_png_chunk(&mut png, *b"IDAT", &zlib);
    append_png_chunk(&mut png, *b"IEND", &[]);
    png
}

fn append_png_chunk(output: &mut Vec<u8>, kind: [u8; 4], data: &[u8]) {
    output.extend_from_slice(&(data.len() as u32).to_be_bytes());
    output.extend_from_slice(&kind);
    output.extend_from_slice(data);
    let mut crc_input = Vec::with_capacity(4 + data.len());
    crc_input.extend_from_slice(&kind);
    crc_input.extend_from_slice(data);
    output.extend_from_slice(&crc32(&crc_input).to_be_bytes());
}

fn adler32(data: &[u8]) -> u32 {
    let (mut a, mut b) = (1u32, 0u32);
    for &byte in data {
        a = (a + u32::from(byte)) % 65_521;
        b = (b + a) % 65_521;
    }
    (b << 16) | a
}

fn crc32(data: &[u8]) -> u32 {
    let mut crc = 0xffff_ffffu32;
    for &byte in data {
        crc ^= u32::from(byte);
        for _ in 0..8 {
            let mask = 0u32.wrapping_sub(crc & 1);
            crc = (crc >> 1) ^ (0xedb8_8320 & mask);
        }
    }
    !crc
}

fn base64_encode(input: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut output = String::with_capacity(input.len().div_ceil(3) * 4);
    for chunk in input.chunks(3) {
        let a = chunk[0];
        let b = chunk.get(1).copied().unwrap_or(0);
        let c = chunk.get(2).copied().unwrap_or(0);
        output.push(TABLE[(a >> 2) as usize] as char);
        output.push(TABLE[(((a & 3) << 4) | (b >> 4)) as usize] as char);
        output.push(if chunk.len() > 1 {
            TABLE[(((b & 15) << 2) | (c >> 6)) as usize] as char
        } else {
            '='
        });
        output.push(if chunk.len() > 2 {
            TABLE[(c & 63) as usize] as char
        } else {
            '='
        });
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strings(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_owned()).collect()
    }

    #[test]
    fn render_requires_svg_output_and_video_for_frame_timestamp() {
        let missing =
            parse_args(strings(&["--analysis", "a.json"])).expect_err("missing output must fail");
        assert!(missing.to_string().contains("--output"));

        let bad_extension =
            parse_args(strings(&["--analysis", "a.json", "--output", "report.txt"]))
                .expect_err("non-SVG output must fail");
        assert!(bad_extension.to_string().contains(".svg"));

        let missing_video = parse_args(strings(&[
            "--analysis",
            "a.json",
            "--output",
            "report.svg",
            "--frame-timestamp-s",
            "1.0",
        ]))
        .expect_err("frame timestamp without video must fail");
        assert!(missing_video.to_string().contains("requires --video"));
    }

    #[test]
    fn source_frame_overlay_requires_recorded_source_hash() {
        let mut value: serde_json::Value = serde_json::from_str(include_str!(
            "../../../crates/openbar-core/tests/fixtures/analysis-v1.golden.json"
        ))
        .unwrap();
        value["identity"]
            .as_object_mut()
            .expect("identity object")
            .remove("source_sha256");
        let analysis = Analysis::from_json(&serde_json::to_string(&value).unwrap())
            .expect("hashless analysis");
        let error = source_hash_for_overlay(&analysis).expect_err("source hash must be required");
        assert!(error.to_string().contains("identity.source_sha256"));
    }

    #[test]
    fn bench_parse_args_performance() {
        let sample_args = strings(&[
            "--analysis",
            "a.json",
            "--video",
            "video.mp4",
            "--frame-timestamp-s",
            "12.34",
            "--output",
            "report.svg",
            "--force",
        ]);

        // Warm up
        for _ in 0..1_000 {
            let _ = parse_args(sample_args.clone());
        }

        let start = std::time::Instant::now();
        let iterations = 200_000;
        for _ in 0..iterations {
            let _ = parse_args(sample_args.clone());
        }
        let elapsed = start.elapsed();
        println!(
            "BENCHMARK_PARSE_ARGS: {:?} total for {} iterations ({:.2?} / iter)",
            elapsed,
            iterations,
            elapsed / iterations
        );
    }

    #[test]
    fn png_and_base64_encoding_are_deterministic() {
        let frame = openbar_tracking::GrayFrame::try_new(2, 1, vec![0, 255]).unwrap();
        let png = encode_png(&frame);
        assert!(png.starts_with(&[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a]));
        assert!(png.windows(4).any(|window| window == b"IHDR"));
        assert!(png.windows(4).any(|window| window == b"IDAT"));
        assert!(png.windows(4).any(|window| window == b"IEND"));
        assert_eq!(encode_png(&frame), png);
        assert_eq!(base64_encode(b"foobar"), "Zm9vYmFy");
    }
}
