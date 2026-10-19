//! Platform-independent decode result types, errors and the deterministic JSON report.

use crate::raster::{LumaRange, PlaneLayout, RasterError, Rotation};
use crate::timing::{TimingError, Window};
use std::fmt;
use std::fmt::Write as _;

/// Bumped whenever the report shape or frame semantics change.
pub const REPORT_VERSION: u32 = 1;
pub const FRAME_SOURCE_ID: &str = "android-mediacodec-gray";
/// Codec configuration hints (speed only, not output): best-effort priority, unbounded rate.
pub const CODEC_PRIORITY: i32 = 1;
pub const CODEC_OPERATING_RATE: f32 = 32_767.0;

#[derive(Debug)]
pub enum DecodeError {
    Io(std::io::Error),
    /// An NDK media call returned a non-OK status or a null handle.
    Media {
        call: &'static str,
        status: i64,
    },
    NoVideoTrack,
    /// Extractor stopped returning samples before the end of the track.
    ExtractorReadFailed {
        sample_time_us: i64,
    },
    /// A compressed sample carries a negative time (edit-list pre-roll); never clamped.
    NegativeSampleTime {
        time_us: i64,
    },
    OutputBufferOutOfBounds {
        offset: i32,
        size: i32,
        capacity: usize,
    },
    /// Display size changed inside the window; the packed dump would be misaligned.
    GeometryChanged {
        first: (usize, usize),
        now: (usize, usize),
    },
    /// Decoder output is not a layout whose first plane is a plain 8-bit Y plane.
    UnsupportedColorFormat {
        value: i32,
    },
    ImageLayout(crate::image_layout::ImageLayoutError),
    MissingOutputFormat,
    /// Decoder did not report `color-range` and no assumption was allowed.
    UnknownColorRange,
    Raster(RasterError),
    Timing(TimingError),
    EmptySelection {
        start_s: f64,
        end_s: f64,
    },
    Stalled {
        idle_ms: u128,
    },
    Unsupported(&'static str),
}

impl fmt::Display for DecodeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(error) => write!(f, "i/o error: {error}"),
            Self::Media { call, status } => write!(f, "{call} failed with status {status}"),
            Self::NoVideoTrack => write!(f, "container has no video/* track"),
            Self::ExtractorReadFailed { sample_time_us } => write!(
                f,
                "extractor failed to read the sample at {sample_time_us} us before end of track"
            ),
            Self::NegativeSampleTime { time_us } => write!(
                f,
                "sample time {time_us} us is negative; refusing to clamp it to a fake timestamp"
            ),
            Self::OutputBufferOutOfBounds {
                offset,
                size,
                capacity,
            } => write!(
                f,
                "output buffer range offset {offset} size {size} exceeds capacity {capacity}"
            ),
            Self::GeometryChanged { first, now } => write!(
                f,
                "display size changed inside the window from {}x{} to {}x{}",
                first.0, first.1, now.0, now.1
            ),
            Self::UnsupportedColorFormat { value } => write!(
                f,
                "decoder output colour format {value} (0x{value:X}) is not I420 (19), NV12 (21) or flexible YUV with image-data; refusing to guess the Y plane layout"
            ),
            Self::MissingOutputFormat => {
                write!(f, "decoder produced a frame before reporting its output format")
            }
            Self::UnknownColorRange => write!(
                f,
                "decoder did not report color-range; pass --assume-range to record an explicit assumption"
            ),
            Self::Raster(error) => write!(f, "{error}"),
            Self::ImageLayout(error) => write!(f, "{error}"),
            Self::Timing(error) => write!(f, "{error}"),
            Self::EmptySelection { start_s, end_s } => {
                write!(f, "no decoded frame lies inside [{start_s}, {end_s}] s")
            }
            Self::Stalled { idle_ms } => {
                write!(f, "decoder made no progress for {idle_ms} ms")
            }
            Self::Unsupported(reason) => write!(f, "{reason}"),
        }
    }
}

impl std::error::Error for DecodeError {}

impl From<std::io::Error> for DecodeError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

impl From<RasterError> for DecodeError {
    fn from(error: RasterError) -> Self {
        Self::Raster(error)
    }
}

impl From<TimingError> for DecodeError {
    fn from(error: TimingError) -> Self {
        Self::Timing(error)
    }
}

/// How the luma range used for expansion was established.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RangeSource {
    Decoder,
    Assumed,
}

/// Whether a plane geometry value came from the decoder or from the width/height fallback.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GeometrySource {
    Decoder,
    /// Taken from the `image-data` (`MediaImage2`) description of a flexible-YUV buffer.
    ImageData,
    Fallback,
}

impl GeometrySource {
    fn as_str(self) -> &'static str {
        match self {
            Self::Decoder => "decoder",
            Self::ImageData => "image-data",
            Self::Fallback => "fallback",
        }
    }
}

/// Track metadata read from the extractor before decoding.
#[derive(Debug, Clone, PartialEq)]
pub struct SourceInfo {
    pub mime: String,
    pub coded_width_px: u32,
    pub coded_height_px: u32,
    pub rotation: Rotation,
    pub duration_us: Option<i64>,
}

/// Decoder output geometry and pixel format, from `INFO_OUTPUT_FORMAT_CHANGED`.
#[derive(Debug, Clone, PartialEq)]
pub struct OutputInfo {
    pub decoder_name: String,
    pub color_format: i32,
    pub color_range_raw: Option<i32>,
    pub luma_range: LumaRange,
    pub range_source: RangeSource,
    pub stride_source: GeometrySource,
    pub crop_source: GeometrySource,
    /// Byte offset of the luma plane inside each output buffer.
    pub y_offset: usize,
    pub layout: PlaneLayout,
}

/// Wall-clock stage timings. Diagnostics only, never measurement output.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct StageTimings {
    pub extractor_open_s: f64,
    pub codec_configure_s: f64,
    pub first_frame_s: f64,
    pub decode_loop_s: f64,
    /// Part of `repack_s`: the sequential copy of the luma plane out of codec memory.
    pub copy_s: f64,
    pub repack_s: f64,
    pub sink_s: f64,
    pub total_s: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct DecodeReport {
    pub source: SourceInfo,
    pub output: OutputInfo,
    pub window: Window,
    /// Earliest PTS of the stream; timestamps are relative to it, like FFmpeg `start_pts`.
    pub origin_us: i64,
    pub display_width_px: usize,
    pub display_height_px: usize,
    pub presentation_time_us: Vec<i64>,
    pub timestamps_s: Vec<f64>,
    pub preroll_discarded: u64,
    pub timings: StageTimings,
}

impl DecodeReport {
    /// Deterministic JSON: fixed key order, no wall-clock dates, timings kept in their own object.
    pub fn to_json(&self) -> String {
        let mut out = String::new();
        let source = &self.source;
        let output = &self.output;
        let crop = output.layout.crop;
        let _ = write!(
            out,
            concat!(
                "{{\n",
                "  \"report_version\": {},\n",
                "  \"frame_source_id\": \"{}\",\n",
                "  \"source\": {{\n",
                "    \"mime\": {},\n",
                "    \"coded_width_px\": {},\n",
                "    \"coded_height_px\": {},\n",
                "    \"rotation_cw_deg\": {},\n",
                "    \"rotation_ffmpeg_ccw_deg\": {},\n",
                "    \"duration_us\": {}\n",
                "  }},\n",
                "  \"decoder\": {{\n",
                "    \"name\": {},\n",
                "    \"priority_hint\": {},\n",
                "    \"operating_rate_hint\": {:?},\n",
                "    \"color_format\": {},\n",
                "    \"color_range_raw\": {},\n",
                "    \"luma_range\": \"{}\",\n",
                "    \"luma_range_source\": \"{}\",\n",
                "    \"stride\": {},\n",
                "    \"slice_height\": {},\n",
                "    \"stride_source\": \"{}\",\n",
                "    \"y_offset\": {},\n",
                "    \"crop\": [{}, {}, {}, {}],\n",
                "    \"crop_source\": \"{}\"\n",
                "  }},\n",
                "  \"window\": {{ \"start_s\": {}, \"end_s\": {} }},\n",
                "  \"origin_us\": {},\n",
                "  \"display_width_px\": {},\n",
                "  \"display_height_px\": {},\n",
                "  \"frame_count\": {},\n",
                "  \"preroll_discarded\": {},\n",
                "  \"presentation_time_us\": [{}],\n",
                "  \"timestamps_s\": [{}],\n",
            ),
            REPORT_VERSION,
            FRAME_SOURCE_ID,
            json_string(&source.mime),
            source.coded_width_px,
            source.coded_height_px,
            source.rotation.clockwise_deg(),
            source.rotation.ffmpeg_ccw_deg(),
            json_opt(source.duration_us),
            json_string(&output.decoder_name),
            CODEC_PRIORITY,
            CODEC_OPERATING_RATE,
            output.color_format,
            json_opt(output.color_range_raw),
            output.luma_range.as_str(),
            match output.range_source {
                RangeSource::Decoder => "decoder",
                RangeSource::Assumed => "assumed",
            },
            output.layout.stride,
            output.layout.slice_height,
            output.stride_source.as_str(),
            output.y_offset,
            crop.left,
            crop.top,
            crop.right,
            crop.bottom,
            output.crop_source.as_str(),
            json_f64(self.window.start_s),
            json_f64(self.window.end_s),
            self.origin_us,
            self.display_width_px,
            self.display_height_px,
            self.timestamps_s.len(),
            self.preroll_discarded,
            join(self.presentation_time_us.iter().map(i64::to_string)),
            join(self.timestamps_s.iter().map(|&t| json_f64(t))),
        );
        let t = &self.timings;
        let _ = write!(
            out,
            concat!(
                "  \"timings\": {{\n",
                "    \"extractor_open_s\": {},\n",
                "    \"codec_configure_s\": {},\n",
                "    \"first_frame_s\": {},\n",
                "    \"decode_loop_s\": {},\n",
                "    \"copy_s\": {},\n",
                "    \"repack_s\": {},\n",
                "    \"sink_s\": {},\n",
                "    \"total_s\": {}\n",
                "  }}\n",
                "}}\n",
            ),
            json_f64(t.extractor_open_s),
            json_f64(t.codec_configure_s),
            json_f64(t.first_frame_s),
            json_f64(t.decode_loop_s),
            json_f64(t.copy_s),
            json_f64(t.repack_s),
            json_f64(t.sink_s),
            json_f64(t.total_s),
        );
        out
    }
}

fn join(items: impl Iterator<Item = String>) -> String {
    items.collect::<Vec<_>>().join(", ")
}

fn json_opt<T: fmt::Display>(value: Option<T>) -> String {
    value.map_or_else(|| "null".to_owned(), |v| v.to_string())
}

/// Finite values print with Rust's shortest round-trip form; infinity (open window end) is `null`.
fn json_f64(value: f64) -> String {
    if value.is_finite() {
        format!("{value:?}")
    } else {
        "null".to_owned()
    }
}

fn json_string(value: &str) -> String {
    let mut out = String::with_capacity(value.len() + 2);
    out.push('"');
    for ch in value.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if u32::from(c) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", u32::from(c));
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::raster::CropRect;

    fn report() -> DecodeReport {
        DecodeReport {
            source: SourceInfo {
                mime: "video/avc".to_owned(),
                coded_width_px: 1920,
                coded_height_px: 1080,
                rotation: Rotation::Cw90,
                duration_us: Some(5_970_000),
            },
            output: OutputInfo {
                decoder_name: "c2.mtk.avc.decoder".to_owned(),
                color_format: 21,
                color_range_raw: Some(2),
                luma_range: LumaRange::Limited,
                range_source: RangeSource::Decoder,
                stride_source: GeometrySource::Decoder,
                crop_source: GeometrySource::Fallback,
                y_offset: 0,
                layout: PlaneLayout {
                    stride: 1920,
                    slice_height: 1088,
                    crop: CropRect::full(1920, 1080),
                },
            },
            window: Window::try_new(Some(0.5), None).unwrap(),
            origin_us: 66_666,
            display_width_px: 1080,
            display_height_px: 1920,
            presentation_time_us: vec![500_000, 533_333],
            timestamps_s: vec![0.5, 0.533333],
            preroll_discarded: 3,
            timings: StageTimings {
                total_s: 0.25,
                ..StageTimings::default()
            },
        }
    }

    #[test]
    fn report_json_is_deterministic_and_records_assumptions() {
        let json = report().to_json();
        assert_eq!(json, report().to_json());
        for needle in [
            "\"report_version\": 1",
            "\"mime\": \"video/avc\"",
            "\"rotation_cw_deg\": 90",
            "\"rotation_ffmpeg_ccw_deg\": 270",
            "\"luma_range\": \"limited\"",
            "\"luma_range_source\": \"decoder\"",
            "\"crop\": [0, 0, 1919, 1079]",
            "\"stride_source\": \"decoder\"",
            "\"crop_source\": \"fallback\"",
            "\"origin_us\": 66666",
            "\"window\": { \"start_s\": 0.5, \"end_s\": null }",
            "\"frame_count\": 2",
            "\"presentation_time_us\": [500000, 533333]",
            "\"timestamps_s\": [0.5, 0.533333]",
            "\"total_s\": 0.25",
        ] {
            assert!(json.contains(needle), "missing {needle} in\n{json}");
        }
    }

    #[test]
    fn json_strings_are_escaped() {
        assert_eq!(json_string("a\"b\\c\n"), "\"a\\\"b\\\\c\\u000a\"");
    }

    #[test]
    fn decode_errors_have_actionable_messages() {
        let message = DecodeError::UnsupportedColorFormat { value: 0x7FA3_0C04 }.to_string();
        assert!(message.contains("0x7FA30C04"), "{message}");
        assert!(DecodeError::UnknownColorRange
            .to_string()
            .contains("--assume-range"));
    }
}
