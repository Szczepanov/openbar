//! Timestamp-authoritative video frame source for M0 (ADR-0006, issue #40).
//!
//! Decodes the first video stream through external FFmpeg processes into display-oriented
//! 8-bit luma frames whose timestamps are the probed presentation timestamps. Media concerns
//! stay here; `openbar-tracking` only ever sees `FrameSample` values.

mod error;
mod ffmpeg;
mod probe;
mod raw;

pub use error::MediaError;

use crate::sha256::file_sha256_hex;
use openbar_tracking::{FrameSample, GrayFrame};
pub use probe::TimeBase;
use probe::VideoProbe;
use raw::RawFrameLayout;
use serde::Serialize;
use std::path::{Path, PathBuf};

/// Bumped whenever decoding arguments, pairing or timestamp semantics change.
pub const FRAME_SOURCE_VERSION: &str = "1";
pub const FRAME_SOURCE_ID: &str = "ffmpeg-process-gray";

/// Inclusive time window on the decoded media timeline.
#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct TimeRange {
    pub start_s: f64,
    pub end_s: f64,
}

impl TimeRange {
    pub fn try_new(start_s: f64, end_s: f64) -> Result<Self, MediaError> {
        if !start_s.is_finite() || !end_s.is_finite() || start_s < 0.0 || end_s < start_s {
            return Err(MediaError::InvalidRange { start_s, end_s });
        }
        Ok(Self { start_s, end_s })
    }

    pub fn contains(self, timestamp_s: f64) -> bool {
        timestamp_s >= self.start_s && timestamp_s <= self.end_s
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FrameSourceOptions {
    pub selection: Option<TimeRange>,
    pub max_frame_bytes: u64,
}

pub struct DecodedFrame {
    pub timestamp_s: f64,
    pub frame_index: u64,
    pub image: GrayFrame,
}

pub struct DecodedClip {
    pub frames: Vec<DecodedFrame>,
    pub provenance: FrameSourceProvenance,
}

impl DecodedClip {
    pub fn frame_samples(&self) -> Vec<FrameSample<'_>> {
        self.frames
            .iter()
            .map(|frame| FrameSample {
                timestamp_s: frame.timestamp_s,
                frame_index: Some(frame.frame_index),
                image: &frame.image,
            })
            .collect()
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct FrameSourceProvenance {
    pub id: &'static str,
    pub version: &'static str,
    pub prober_version: String,
    pub decoder_version: String,
    pub probe_args: Vec<String>,
    pub decode_args: Vec<String>,
    pub source_sha256: String,
    pub stream: StreamProvenance,
    pub probed_frame_count: usize,
    pub selected_frame_count: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub selected_range_s: Option<TimeRange>,
    pub first_selected_timestamp_s: f64,
    pub last_selected_timestamp_s: f64,
    pub decoder_diagnostics: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct StreamProvenance {
    pub stream_index: u32,
    pub codec_name: String,
    pub pix_fmt: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub color_range: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub color_transfer: Option<String>,
    pub coded_width_px: u32,
    pub coded_height_px: u32,
    pub display_width_px: u32,
    pub display_height_px: u32,
    pub rotation_deg: u16,
    pub rotation_convention: &'static str,
    pub time_base: TimeBase,
    pub start_pts: i64,
}

/// A probed, hashed source whose timestamps already passed validation, ready to decode.
pub struct ProbedVideo {
    path: PathBuf,
    probe: VideoProbe,
    timestamps_s: Vec<f64>,
    source_sha256: String,
    prober_version: String,
    decoder_version: String,
}

impl ProbedVideo {
    /// Identifies the tools, hashes the file and validates its timeline without decoding pixels,
    /// so callers can reject the wrong file before paying for a full decode.
    pub fn open(path: &Path) -> Result<Self, MediaError> {
        let prober_version = ffmpeg::tool_version(ffmpeg::FFPROBE)?;
        let decoder_version = ffmpeg::tool_version(ffmpeg::FFMPEG)?;
        let source_sha256 = file_sha256_hex(path).map_err(|error| MediaError::Io {
            context: format!("failed to read '{}'", path.display()),
            detail: error.to_string(),
        })?;
        let probe = ffmpeg::probe(path)?;
        let timestamps_s = probe.timestamps_s()?;
        Ok(Self {
            path: path.to_path_buf(),
            probe,
            timestamps_s,
            source_sha256,
            prober_version,
            decoder_version,
        })
    }

    pub fn source_sha256(&self) -> &str {
        &self.source_sha256
    }

    /// The authoritative, validated PTS timeline; no pixel decode or FPS reconstruction.
    pub fn timestamps_s(&self) -> &[f64] {
        &self.timestamps_s
    }

    pub fn stream(&self) -> StreamProvenance {
        let (display_width_px, display_height_px) = self.probe.display_dimensions();
        stream_provenance(&self.probe, display_width_px, display_height_px)
    }

    /// Decodes the selected frames, failing closed on any timing or layout disagreement.
    pub fn decode(self, options: FrameSourceOptions) -> Result<DecodedClip, MediaError> {
        let (display_width_px, display_height_px) = self.probe.display_dimensions();
        let layout = RawFrameLayout {
            width_px: display_width_px,
            height_px: display_height_px,
        };
        check_selection_budget(&self.timestamps_s, &layout, options)?;

        let (frames, decoder_diagnostics) =
            ffmpeg::decode(&self.path, &layout, &self.timestamps_s, options.selection)?;
        let (Some(first), Some(last)) = (frames.first(), frames.last()) else {
            return Err(MediaError::NoFrames);
        };
        let (first_selected_timestamp_s, last_selected_timestamp_s) =
            (first.timestamp_s, last.timestamp_s);

        let provenance = FrameSourceProvenance {
            id: FRAME_SOURCE_ID,
            version: FRAME_SOURCE_VERSION,
            stream: self.stream(),
            prober_version: self.prober_version,
            decoder_version: self.decoder_version,
            probe_args: ffmpeg::probe_args_for_provenance(),
            decode_args: ffmpeg::decode_args_for_provenance(),
            source_sha256: self.source_sha256,
            probed_frame_count: self.timestamps_s.len(),
            selected_frame_count: frames.len(),
            selected_range_s: options.selection,
            first_selected_timestamp_s,
            last_selected_timestamp_s,
            decoder_diagnostics,
        };
        Ok(DecodedClip { frames, provenance })
    }
}

fn check_selection_budget(
    timestamps_s: &[f64],
    layout: &RawFrameLayout,
    options: FrameSourceOptions,
) -> Result<(), MediaError> {
    let selected_count = timestamps_s
        .iter()
        .filter(|&&timestamp_s| {
            options
                .selection
                .is_none_or(|range| range.contains(timestamp_s))
        })
        .count();
    if selected_count == 0 {
        return Err(match options.selection {
            Some(range) => MediaError::EmptySelection {
                start_s: range.start_s,
                end_s: range.end_s,
            },
            None => MediaError::NoFrames,
        });
    }
    let required_bytes = (selected_count as u64).saturating_mul(layout.frame_bytes() as u64);
    if required_bytes > options.max_frame_bytes {
        return Err(MediaError::FrameMemoryBudgetExceeded {
            required_bytes,
            budget_bytes: options.max_frame_bytes,
        });
    }
    Ok(())
}

fn stream_provenance(
    probe: &VideoProbe,
    display_width_px: u32,
    display_height_px: u32,
) -> StreamProvenance {
    StreamProvenance {
        stream_index: probe.stream_index,
        codec_name: probe.codec_name.clone(),
        pix_fmt: probe.pix_fmt.clone(),
        color_range: probe.color_range.clone(),
        color_transfer: probe.color_transfer.clone(),
        coded_width_px: probe.coded_width_px,
        coded_height_px: probe.coded_height_px,
        display_width_px,
        display_height_px,
        rotation_deg: probe.rotation_deg,
        rotation_convention: "ffmpeg_display_matrix_counter_clockwise",
        time_base: probe.time_base,
        start_pts: probe.start_pts,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use openbar_tracking::GrayscaleImage;
    use std::path::PathBuf;
    use std::process::Command;

    const SYNTHETIC_SHA256: &str =
        "a175d350c96db3df1771c1eb141a017eaed012ae6bbd6cda739510b244113096";
    const UNLIMITED: u64 = u64::MAX;

    fn repo_path(relative: &str) -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .join(relative)
    }

    fn synthetic_clip() -> PathBuf {
        repo_path("validation/fixtures/public/synthetic-clean-side-12.mp4")
    }

    /// FFmpeg is an external prerequisite. CI sets `OPENBAR_REQUIRE_FFMPEG=1`, so a missing
    /// install fails there instead of silently skipping.
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
            eprintln!("SKIPPED: ffmpeg/ffprobe not on PATH; decode test not run");
        }
        available
    }

    fn decode_video(path: &Path, options: FrameSourceOptions) -> Result<DecodedClip, MediaError> {
        ProbedVideo::open(path)?.decode(options)
    }

    fn decode(path: &Path, selection: Option<TimeRange>) -> Result<DecodedClip, MediaError> {
        decode_video(
            path,
            FrameSourceOptions {
                selection,
                max_frame_bytes: UNLIMITED,
            },
        )
    }

    fn scratch_file(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("openbar-media-{}-{name}", std::process::id()))
    }

    fn pixels(frame: &DecodedFrame) -> Vec<u8> {
        let (width, height) = (frame.image.width_px(), frame.image.height_px());
        (0..height)
            .flat_map(|y| (0..width).map(move |x| (x, y)))
            .map(|(x, y)| frame.image.intensity(x, y))
            .collect()
    }

    #[test]
    fn decodes_synthetic_fixture_with_probed_timestamps() {
        if !ffmpeg_available() {
            return;
        }
        let clip = decode(&synthetic_clip(), None).expect("synthetic fixture decodes");

        let timestamps = clip
            .frames
            .iter()
            .map(|frame| frame.timestamp_s)
            .collect::<Vec<_>>();
        assert_eq!(
            timestamps,
            (0..12).map(|k| f64::from(k) / 12.0).collect::<Vec<_>>()
        );
        assert_eq!(
            clip.frames
                .iter()
                .map(|frame| frame.frame_index)
                .collect::<Vec<_>>(),
            (0..12).collect::<Vec<u64>>()
        );
        assert!(clip
            .frames
            .iter()
            .all(|frame| (frame.image.width_px(), frame.image.height_px()) == (320, 240)));

        let provenance = &clip.provenance;
        assert_eq!(provenance.source_sha256, SYNTHETIC_SHA256);
        assert_eq!(
            (
                provenance.probed_frame_count,
                provenance.selected_frame_count
            ),
            (12, 12)
        );
        assert_eq!(provenance.stream.rotation_deg, 0);
        assert_eq!(
            provenance.stream.time_base,
            TimeBase {
                num: 1,
                den: 12_288
            }
        );
        assert!(provenance.decode_args.contains(&"<input>".to_owned()));
        assert!(provenance.decoder_version.starts_with("ffmpeg version"));
    }

    #[test]
    fn decoded_timestamps_match_committed_annotation_within_its_tolerance() {
        if !ffmpeg_available() {
            return;
        }
        let annotation: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(repo_path(
                "validation/fixtures/public/annotations/synthetic-clean-side-12.annotation-v1.json",
            ))
            .expect("annotation is readable"),
        )
        .expect("annotation is JSON");
        let tolerance_s = annotation["timebase"]["decoder_match_tolerance_s"]
            .as_f64()
            .expect("tolerance is a number");
        let clip = decode(&synthetic_clip(), None).expect("synthetic fixture decodes");

        let samples = annotation["samples"].as_array().expect("samples array");
        assert!(!samples.is_empty());
        for sample in samples {
            let annotated_s = sample["timestamp_s"].as_f64().expect("timestamp");
            let nearest_s = clip
                .frames
                .iter()
                .map(|frame| (frame.timestamp_s - annotated_s).abs())
                .fold(f64::INFINITY, f64::min);
            assert!(
                nearest_s <= tolerance_s,
                "annotation at {annotated_s}s has no decoded frame within {tolerance_s}s"
            );
        }
    }

    #[test]
    fn selection_is_inclusive_and_keeps_source_frame_indices() {
        if !ffmpeg_available() {
            return;
        }
        let range = TimeRange::try_new(0.25, 0.5).expect("valid range");
        let clip = decode(&synthetic_clip(), Some(range)).expect("range decodes");
        assert_eq!(
            clip.frames
                .iter()
                .map(|frame| (frame.frame_index, frame.timestamp_s))
                .collect::<Vec<_>>(),
            vec![(3, 0.25), (4, 4.0 / 12.0), (5, 5.0 / 12.0), (6, 0.5)]
        );
        assert_eq!(clip.provenance.probed_frame_count, 12);
        assert_eq!(clip.provenance.selected_frame_count, 4);
        assert_eq!(clip.provenance.selected_range_s, Some(range));
    }

    #[test]
    fn repeated_decoding_is_byte_identical() {
        if !ffmpeg_available() {
            return;
        }
        let first = decode(&synthetic_clip(), None).expect("first decode");
        let second = decode(&synthetic_clip(), None).expect("second decode");
        assert_eq!(first.provenance, second.provenance);
        assert_eq!(first.frames.len(), second.frames.len());
        for (left, right) in first.frames.iter().zip(&second.frames) {
            assert_eq!(left.timestamp_s, right.timestamp_s);
            assert_eq!(pixels(left), pixels(right));
        }
    }

    #[test]
    fn rejects_empty_selection_and_exceeded_memory_budget_before_decoding() {
        if !ffmpeg_available() {
            return;
        }
        let late = TimeRange::try_new(5.0, 6.0).expect("valid range");
        assert_eq!(
            decode(&synthetic_clip(), Some(late)).map(|_| ()),
            Err(MediaError::EmptySelection {
                start_s: 5.0,
                end_s: 6.0
            })
        );
        let frame_bytes = 320 * 240;
        assert_eq!(
            decode_video(
                &synthetic_clip(),
                FrameSourceOptions {
                    selection: None,
                    max_frame_bytes: 12 * frame_bytes - 1,
                },
            )
            .map(|_| ()),
            Err(MediaError::FrameMemoryBudgetExceeded {
                required_bytes: 12 * frame_bytes,
                budget_bytes: 12 * frame_bytes - 1
            })
        );
        assert!(decode_video(
            &synthetic_clip(),
            FrameSourceOptions {
                selection: None,
                max_frame_bytes: 12 * frame_bytes,
            },
        )
        .is_ok());
    }

    /// Pins ADR-0006's rotation contract: ffprobe's display-matrix rotation is
    /// counter-clockwise, and decoded frames are the coded frames turned by that angle.
    #[test]
    fn display_rotation_metadata_turns_frames_counter_clockwise() {
        if !ffmpeg_available() {
            return;
        }
        let reference = decode(&synthetic_clip(), None).expect("reference decodes");
        let source = &reference.frames[3];
        let (width, height) = (source.image.width_px(), source.image.height_px());

        for (rotation, rotation_str, filename) in [
            (90u16, "90", "rot90.mp4"),
            (180, "180", "rot180.mp4"),
            (270, "270", "rot270.mp4"),
        ] {
            let rotated_path = scratch_file(filename);
            let status = Command::new("ffmpeg")
                .args(["-nostdin", "-v", "error", "-y", "-display_rotation"])
                .arg(rotation_str)
                .arg("-i")
                .arg(synthetic_clip())
                .args(["-c", "copy"])
                .arg(&rotated_path)
                .status()
                .expect("ffmpeg runs");
            assert!(status.success(), "rotated fixture generation failed");

            let rotated = decode(&rotated_path, None).expect("rotated copy decodes");
            let _ = std::fs::remove_file(&rotated_path);
            assert_eq!(rotated.provenance.stream.rotation_deg, rotation);

            let frame = &rotated.frames[3];
            assert_eq!(frame.timestamp_s, source.timestamp_s);
            let expected_dimensions = if rotation == 180 {
                (width, height)
            } else {
                (height, width)
            };
            assert_eq!(
                (frame.image.width_px(), frame.image.height_px()),
                expected_dimensions
            );
            for y in 0..frame.image.height_px() {
                for x in 0..frame.image.width_px() {
                    let (source_x, source_y) = match rotation {
                        90 => (width - 1 - y, x),
                        180 => (width - 1 - x, height - 1 - y),
                        _ => (y, height - 1 - x),
                    };
                    assert_eq!(
                        frame.image.intensity(x, y),
                        source.image.intensity(source_x, source_y),
                        "rotation {rotation} pixel ({x}, {y})"
                    );
                }
            }
        }
    }

    #[test]
    fn reports_missing_tool_and_undecodable_input_explicitly() {
        assert!(matches!(
            ffmpeg::tool_version("openbar-test-missing-tool"),
            Err(MediaError::ToolUnavailable { .. })
        ));
        if !ffmpeg_available() {
            return;
        }
        let garbage = scratch_file("garbage.mp4");
        std::fs::write(&garbage, b"not a video").expect("scratch file is writable");
        let result = decode(&garbage, None);
        let _ = std::fs::remove_file(&garbage);
        assert!(
            matches!(
                result,
                Err(MediaError::ToolFailed {
                    tool: "ffprobe",
                    ..
                })
            ),
            "unexpected result: {:?}",
            result.map(|_| ())
        );
        assert!(matches!(
            decode(&repo_path("validation/fixtures/public/missing.mp4"), None),
            Err(MediaError::Io { .. })
        ));
    }

    fn synthetic_timestamps() -> Vec<f64> {
        (0..12).map(|k| f64::from(k) / 12.0).collect()
    }

    fn unwrap_decoder_output(error: MediaError) -> MediaError {
        match error {
            MediaError::WithDecoderOutput { error, .. } => *error,
            other => other,
        }
    }

    #[test]
    fn real_decode_fails_closed_when_probe_and_stream_disagree_on_count() {
        if !ffmpeg_available() {
            return;
        }
        let layout = RawFrameLayout {
            width_px: 320,
            height_px: 240,
        };
        let error = ffmpeg::decode(
            &synthetic_clip(),
            &layout,
            &synthetic_timestamps()[..11],
            None,
        )
        .map(|_| ())
        .expect_err("12 decoded frames cannot pair with 11 timestamps");
        assert_eq!(
            unwrap_decoder_output(error),
            MediaError::FrameCountMismatch {
                probed: 11,
                decoded: 12
            }
        );
    }

    #[test]
    fn real_decode_fails_closed_on_layout_that_does_not_divide_the_stream() {
        if !ffmpeg_available() {
            return;
        }
        // 12 x 320x240 bytes = 921600, which is 11 full 321x240 frames plus a partial one.
        let layout = RawFrameLayout {
            width_px: 321,
            height_px: 240,
        };
        let error = ffmpeg::decode(&synthetic_clip(), &layout, &synthetic_timestamps(), None)
            .map(|_| ())
            .expect_err("a wrong layout must not be accepted");
        assert_eq!(
            unwrap_decoder_output(error),
            MediaError::TruncatedFrame {
                index: 11,
                received_bytes: 921_600 - 11 * 321 * 240,
                expected_bytes: 321 * 240
            }
        );
    }

    #[test]
    fn probe_exposes_identity_before_decoding() {
        if !ffmpeg_available() {
            return;
        }
        let probed = ProbedVideo::open(&synthetic_clip()).expect("probe succeeds");
        assert_eq!(probed.source_sha256(), SYNTHETIC_SHA256);
        let stream = probed.stream();
        assert_eq!(
            (
                stream.coded_width_px,
                stream.coded_height_px,
                stream.display_width_px,
                stream.display_height_px,
                stream.rotation_deg
            ),
            (320, 240, 320, 240, 0)
        );
    }

    #[test]
    fn rejects_invalid_ranges() {
        for (start_s, end_s) in [
            (-0.1, 1.0),
            (2.0, 1.0),
            (f64::NAN, 1.0),
            (0.0, f64::INFINITY),
        ] {
            assert!(matches!(
                TimeRange::try_new(start_s, end_s),
                Err(MediaError::InvalidRange { .. })
            ));
        }
        assert!(TimeRange::try_new(1.0, 1.0).is_ok_and(|range| range.contains(1.0)));
    }
}
