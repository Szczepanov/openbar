//! Typed view of `ffprobe` output and the PTS -> seconds contract (ADR-0006).

use super::MediaError;
use serde::{Deserialize, Serialize};

/// Arguments placed before the input path. The entry list is part of the frame-source contract.
pub const PROBE_ARGS: [&str; 8] = [
    "-v",
    "error",
    "-select_streams",
    "v:0",
    "-show_entries",
    "stream=index,codec_name,pix_fmt,color_range,color_transfer,width,height,sample_aspect_ratio,time_base,start_pts:stream_side_data=rotation:frame=pts",
    "-of",
    "json",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct TimeBase {
    pub num: u64,
    pub den: u64,
}

impl TimeBase {
    fn parse(value: &str) -> Result<Self, MediaError> {
        let invalid = || MediaError::InvalidTimeBase {
            value: value.to_owned(),
        };
        let (num, den) = value.split_once('/').ok_or_else(invalid)?;
        let num = num.trim().parse::<u64>().map_err(|_| invalid())?;
        let den = den.trim().parse::<u64>().map_err(|_| invalid())?;
        if num == 0 || den == 0 {
            return Err(invalid());
        }
        Ok(Self { num, den })
    }
}

/// Probed first video stream plus per-frame presentation timestamps in decode output order.
#[derive(Debug, Clone, PartialEq)]
pub struct VideoProbe {
    pub stream_index: u32,
    pub codec_name: String,
    pub pix_fmt: String,
    pub color_range: Option<String>,
    pub color_transfer: Option<String>,
    pub coded_width_px: u32,
    pub coded_height_px: u32,
    pub time_base: TimeBase,
    pub start_pts: i64,
    /// Counter-clockwise display rotation (FFmpeg convention), normalised to 0/90/180/270.
    pub rotation_deg: u16,
    pub frame_pts: Vec<i64>,
}

#[derive(Debug, Deserialize)]
struct RawProbe {
    #[serde(default)]
    streams: Vec<RawStream>,
    #[serde(default)]
    frames: Vec<RawFrame>,
}

#[derive(Debug, Deserialize)]
struct RawStream {
    index: u32,
    codec_name: Option<String>,
    width: Option<u32>,
    height: Option<u32>,
    pix_fmt: Option<String>,
    sample_aspect_ratio: Option<String>,
    color_range: Option<String>,
    color_transfer: Option<String>,
    time_base: Option<String>,
    start_pts: Option<i64>,
    #[serde(default)]
    side_data_list: Vec<RawSideData>,
}

#[derive(Debug, Deserialize)]
struct RawSideData {
    rotation: Option<f64>,
}

#[derive(Debug, Deserialize)]
struct RawFrame {
    pts: Option<i64>,
}

impl VideoProbe {
    pub fn parse(json: &str) -> Result<Self, MediaError> {
        let raw: RawProbe =
            serde_json::from_str(json).map_err(|error| MediaError::InvalidProbeOutput {
                detail: error.to_string(),
            })?;
        let stream = raw
            .streams
            .into_iter()
            .next()
            .ok_or(MediaError::NoVideoStream)?;

        validate_sample_aspect_ratio(stream.sample_aspect_ratio.as_deref())?;
        let rotation_deg = normalize_rotation(
            stream
                .side_data_list
                .iter()
                .find_map(|side_data| side_data.rotation),
        )?;
        let coded_width_px = stream
            .width
            .ok_or(MediaError::MissingStreamField { field: "width" })?;
        let coded_height_px = stream
            .height
            .ok_or(MediaError::MissingStreamField { field: "height" })?;
        if coded_width_px == 0 || coded_height_px == 0 {
            return Err(MediaError::InvalidDimensions {
                width_px: coded_width_px,
                height_px: coded_height_px,
            });
        }
        let time_base = TimeBase::parse(
            stream
                .time_base
                .as_deref()
                .ok_or(MediaError::MissingStreamField { field: "time_base" })?,
        )?;
        let start_pts = stream
            .start_pts
            .ok_or(MediaError::MissingStreamField { field: "start_pts" })?;
        let frame_pts = raw
            .frames
            .iter()
            .enumerate()
            .map(|(index, frame)| frame.pts.ok_or(MediaError::MissingPts { index }))
            .collect::<Result<Vec<_>, _>>()?;

        Ok(Self {
            stream_index: stream.index,
            codec_name: stream.codec_name.ok_or(MediaError::MissingStreamField {
                field: "codec_name",
            })?,
            pix_fmt: stream
                .pix_fmt
                .ok_or(MediaError::MissingStreamField { field: "pix_fmt" })?,
            color_range: stream.color_range,
            color_transfer: stream.color_transfer,
            coded_width_px,
            coded_height_px,
            time_base,
            start_pts,
            rotation_deg,
            frame_pts,
        })
    }

    /// Display-oriented dimensions after the rotation metadata is applied.
    pub fn display_dimensions(&self) -> (u32, u32) {
        if self.rotation_deg % 180 == 90 {
            (self.coded_height_px, self.coded_width_px)
        } else {
            (self.coded_width_px, self.coded_height_px)
        }
    }

    /// `(pts - start_pts) * time_base` per frame, in seconds from the video stream start.
    pub fn timestamps_s(&self) -> Result<Vec<f64>, MediaError> {
        if self.frame_pts.is_empty() {
            return Err(MediaError::NoFrames);
        }
        let mut timestamps = Vec::with_capacity(self.frame_pts.len());
        for (index, &pts) in self.frame_pts.iter().enumerate() {
            if index > 0 && pts <= self.frame_pts[index - 1] {
                return Err(MediaError::NonIncreasingPts {
                    index,
                    previous: self.frame_pts[index - 1],
                    current: pts,
                });
            }
            let ticks = i128::from(pts) - i128::from(self.start_pts);
            if ticks < 0 {
                return Err(MediaError::PtsBeforeMediaStart {
                    index,
                    pts,
                    start_pts: self.start_pts,
                });
            }
            // Integer numerator first, one rounding step in the final division.
            let numerator = ticks * i128::from(self.time_base.num);
            timestamps.push(numerator as f64 / self.time_base.den as f64);
        }
        Ok(timestamps)
    }
}

fn validate_sample_aspect_ratio(value: Option<&str>) -> Result<(), MediaError> {
    match value {
        // "0:1" is FFmpeg's "unknown", which it treats as square pixels.
        None | Some("1:1") | Some("0:1") => Ok(()),
        Some(other) => Err(MediaError::UnsupportedSampleAspectRatio {
            value: other.to_owned(),
        }),
    }
}

fn normalize_rotation(value: Option<f64>) -> Result<u16, MediaError> {
    let Some(value) = value else {
        return Ok(0);
    };
    if !value.is_finite() || value.abs() > 360.0 || value.fract() != 0.0 || value % 90.0 != 0.0 {
        return Err(MediaError::UnsupportedRotation { value });
    }
    Ok((value as i64).rem_euclid(360) as u16)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn probe_json(stream_extra: &str, frames: &str) -> String {
        format!(
            r#"{{"programs":[],"stream_groups":[],"streams":[{{"index":1,"codec_name":"h264","width":1280,"height":720,"pix_fmt":"yuvj420p","color_range":"tv","time_base":"1/90000","start_pts":0{stream_extra}}}],"frames":[{frames}]}}"#
        )
    }

    fn probe_with(stream_extra: &str, frames: &str) -> Result<VideoProbe, MediaError> {
        VideoProbe::parse(&probe_json(stream_extra, frames))
    }

    #[test]
    fn parses_rotated_vfr_stream_like_real_phone_footage() {
        let probe = probe_with(
            r#","sample_aspect_ratio":"1:1","side_data_list":[{"rotation":90}]"#,
            r#"{"pts":0,"side_data_list":[{}]},{"pts":2985},{"pts":5970}"#,
        )
        .expect("probe parses");
        assert_eq!(
            probe,
            VideoProbe {
                stream_index: 1,
                codec_name: "h264".to_owned(),
                pix_fmt: "yuvj420p".to_owned(),
                color_range: Some("tv".to_owned()),
                color_transfer: None,
                coded_width_px: 1280,
                coded_height_px: 720,
                time_base: TimeBase {
                    num: 1,
                    den: 90_000
                },
                start_pts: 0,
                rotation_deg: 90,
                frame_pts: vec![0, 2985, 5970],
            }
        );
        assert_eq!(probe.display_dimensions(), (720, 1280));
        assert_eq!(
            probe.timestamps_s().expect("valid timestamps"),
            vec![0.0, 2985.0 / 90_000.0, 5970.0 / 90_000.0]
        );
    }

    #[test]
    fn normalizes_ffmpeg_rotation_signs() {
        for (reported, expected) in [
            ("0", 0u16),
            ("90", 90),
            ("-90", 270),
            ("180", 180),
            ("-180", 180),
            ("270", 270),
            ("-270", 90),
            ("360", 0),
        ] {
            let probe = probe_with(
                &format!(r#","side_data_list":[{{"rotation":{reported}}}]"#),
                r#"{"pts":0}"#,
            )
            .expect("rotation parses");
            assert_eq!(probe.rotation_deg, expected, "reported {reported}");
        }
        let unrotated = probe_with("", r#"{"pts":0}"#).expect("no side data parses");
        assert_eq!(unrotated.rotation_deg, 0);
        assert_eq!(unrotated.display_dimensions(), (1280, 720));
    }

    #[test]
    fn rejects_non_right_angle_rotation() {
        assert_eq!(
            probe_with(r#","side_data_list":[{"rotation":45}]"#, r#"{"pts":0}"#),
            Err(MediaError::UnsupportedRotation { value: 45.0 })
        );
        assert_eq!(
            probe_with(r#","side_data_list":[{"rotation":90.5}]"#, r#"{"pts":0}"#),
            Err(MediaError::UnsupportedRotation { value: 90.5 })
        );
        assert_eq!(
            probe_with(r#","side_data_list":[{"rotation":1e300}]"#, r#"{"pts":0}"#),
            Err(MediaError::UnsupportedRotation { value: 1e300 })
        );
    }

    #[test]
    fn rejects_non_square_pixels_but_accepts_unknown_ratio() {
        assert_eq!(
            probe_with(r#","sample_aspect_ratio":"4:3""#, r#"{"pts":0}"#),
            Err(MediaError::UnsupportedSampleAspectRatio {
                value: "4:3".to_owned()
            })
        );
        assert!(probe_with(r#","sample_aspect_ratio":"0:1""#, r#"{"pts":0}"#).is_ok());
    }

    #[test]
    fn timestamps_are_relative_to_stream_start_pts() {
        let json = probe_json("", r#"{"pts":1500},{"pts":4500}"#)
            .replace(r#""start_pts":0"#, r#""start_pts":1500"#)
            .replace("1/90000", "1001/30000");
        let probe = VideoProbe::parse(&json).expect("probe parses");
        assert_eq!(
            probe.timestamps_s().expect("valid timestamps"),
            vec![0.0, 3000.0 * 1001.0 / 30_000.0]
        );
    }

    #[test]
    fn rejects_timestamp_errors_instead_of_synthesizing_them() {
        assert_eq!(
            probe_with("", r#"{"pts":0},{}"#),
            Err(MediaError::MissingPts { index: 1 })
        );
        assert_eq!(
            probe_with("", r#"{"pts":0},{"pts":3000},{"pts":3000}"#)
                .expect("parses")
                .timestamps_s(),
            Err(MediaError::NonIncreasingPts {
                index: 2,
                previous: 3000,
                current: 3000
            })
        );
        let early = probe_json("", r#"{"pts":-3000},{"pts":0}"#);
        assert_eq!(
            VideoProbe::parse(&early).expect("parses").timestamps_s(),
            Err(MediaError::PtsBeforeMediaStart {
                index: 0,
                pts: -3000,
                start_pts: 0
            })
        );
        assert_eq!(
            probe_with("", "").expect("parses").timestamps_s(),
            Err(MediaError::NoFrames)
        );
    }

    #[test]
    fn rejects_missing_or_invalid_stream_metadata() {
        assert_eq!(
            VideoProbe::parse(r#"{"streams":[],"frames":[]}"#),
            Err(MediaError::NoVideoStream)
        );
        assert_eq!(
            VideoProbe::parse(&probe_json("", "").replace(r#","start_pts":0"#, "")),
            Err(MediaError::MissingStreamField { field: "start_pts" })
        );
        for value in ["0/1", "1/0", "1:90000", "x/90000"] {
            assert_eq!(
                VideoProbe::parse(&probe_json("", "").replace("1/90000", value)),
                Err(MediaError::InvalidTimeBase {
                    value: value.to_owned()
                })
            );
        }
        assert_eq!(
            VideoProbe::parse(&probe_json("", "").replace(r#""width":1280"#, r#""width":0"#)),
            Err(MediaError::InvalidDimensions {
                width_px: 0,
                height_px: 720
            })
        );
        assert!(matches!(
            VideoProbe::parse("not json"),
            Err(MediaError::InvalidProbeOutput { .. })
        ));
    }
}
