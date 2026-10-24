//! Canonical gray = an explicit per-pixel function of the decoded Y plane (ADR-0006, Amendment 1).
//!
//! `-pix_fmt gray` alone was observed to convert through RGB when the source matrix is BT.709
//! (FFmpeg 8.1.3 and 9.0.2; docs/analysis/FFMPEG_GRAY_CONVERSION_FINDINGS.md), which leaks chroma
//! into luma and changes between FFmpeg releases. The decode graph therefore extracts
//! the Y plane and, for limited-range frames, applies the documented expansion as a lookup table.

use super::MediaError;
use serde::Serialize;

/// 8-bit planar YUV layouts whose first plane is Y; anything else fails closed.
const SUPPORTED_PIX_FMTS: [&str; 6] = [
    "yuv420p", "yuvj420p", "yuv422p", "yuvj422p", "yuv444p", "yuvj444p",
];

/// Limited-range expansion `clip(round((Y - 16) * 255 / 219), 0, 255)`. No input code sits on a
/// rounding tie, so FFmpeg's `round` and integer half-up rounding agree on every code.
const LIMITED_TO_FULL_FILTER: &str = "extractplanes=y,lut=c0='clip(round((val-16)*255/219),0,255)'";
const IDENTITY_FILTER: &str = "extractplanes=y";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LumaTransform {
    /// Full-range frames: gray is the decoded Y plane unchanged.
    Identity,
    /// Limited-range (or unspecified, non-`yuvj`) frames: Y expanded from 16-235 to 0-255.
    LimitedToFull,
}

impl LumaTransform {
    /// Chooses the transform from the frame-level range ffprobe reports, which decides over the
    /// stream tag. Unspecified frame range falls back to the pixel format's default (full for
    /// `yuvj*`, limited otherwise), unless a known stream tag contradicts that default: then the
    /// range is ambiguous and decoding fails closed rather than guessing.
    pub fn select(
        pix_fmt: &str,
        frame_color_range: Option<&str>,
        stream_color_range: Option<&str>,
    ) -> Result<Self, MediaError> {
        if !SUPPORTED_PIX_FMTS.contains(&pix_fmt) {
            return Err(MediaError::UnsupportedPixelFormat {
                pix_fmt: pix_fmt.to_owned(),
            });
        }
        let default = if pix_fmt.starts_with("yuvj") {
            Self::Identity
        } else {
            Self::LimitedToFull
        };
        match (frame_color_range, stream_color_range) {
            (Some("pc"), _) => Ok(Self::Identity),
            (Some("tv"), _) => Ok(Self::LimitedToFull),
            (_, Some(stream @ ("pc" | "tv")))
                if (stream == "pc") != (default == Self::Identity) =>
            {
                Err(MediaError::AmbiguousColorRange {
                    pix_fmt: pix_fmt.to_owned(),
                    stream_color_range: stream.to_owned(),
                })
            }
            _ => Ok(default),
        }
    }

    pub fn filter(self) -> &'static str {
        match self {
            Self::Identity => IDENTITY_FILTER,
            Self::LimitedToFull => LIMITED_TO_FULL_FILTER,
        }
    }
}

/// The single frame-level range shared by every probed frame; a stream that switches range
/// mid-clip cannot be given one documented transform, so it fails closed.
pub fn uniform_frame_color_range(ranges: &[Option<String>]) -> Result<Option<String>, MediaError> {
    let normalized = |value: &Option<String>| match value.as_deref() {
        None | Some("unknown") => None,
        Some(other) => Some(other.to_owned()),
    };
    let Some(first) = ranges.first() else {
        return Ok(None);
    };
    let first = normalized(first);
    for (index, range) in ranges.iter().enumerate().skip(1) {
        let current = normalized(range);
        if current != first {
            return Err(MediaError::MixedFrameColorRange {
                index,
                first: first.unwrap_or_else(|| "unknown".to_owned()),
                current: current.unwrap_or_else(|| "unknown".to_owned()),
            });
        }
    }
    Ok(first)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_range_decides_and_unspecified_follows_pixel_format() {
        let cases = [
            ("yuv420p", Some("tv"), None, LumaTransform::LimitedToFull),
            ("yuv420p", Some("pc"), Some("tv"), LumaTransform::Identity),
            ("yuv420p", None, None, LumaTransform::LimitedToFull),
            ("yuv420p", None, Some("tv"), LumaTransform::LimitedToFull),
            // Real phone clips: stream tagged tv, frames report pc.
            ("yuvj420p", Some("pc"), Some("tv"), LumaTransform::Identity),
            ("yuvj420p", Some("tv"), None, LumaTransform::LimitedToFull),
            ("yuvj420p", None, None, LumaTransform::Identity),
            ("yuvj420p", None, Some("pc"), LumaTransform::Identity),
            (
                "yuv444p",
                None,
                Some("unknown"),
                LumaTransform::LimitedToFull,
            ),
        ];
        for (pix_fmt, frame, stream, expected) in cases {
            assert_eq!(
                LumaTransform::select(pix_fmt, frame, stream),
                Ok(expected),
                "{pix_fmt} frame {frame:?} stream {stream:?}"
            );
        }
    }

    #[test]
    fn unspecified_frame_range_contradicted_by_stream_tag_fails_closed() {
        for (pix_fmt, stream) in [("yuv420p", "pc"), ("yuvj420p", "tv")] {
            assert_eq!(
                LumaTransform::select(pix_fmt, None, Some(stream)),
                Err(MediaError::AmbiguousColorRange {
                    pix_fmt: pix_fmt.to_owned(),
                    stream_color_range: stream.to_owned(),
                })
            );
        }
    }

    #[test]
    fn non_8bit_or_packed_formats_fail_closed() {
        for pix_fmt in ["yuv420p10le", "nv12", "rgb24", "gray"] {
            assert_eq!(
                LumaTransform::select(pix_fmt, Some("tv"), None),
                Err(MediaError::UnsupportedPixelFormat {
                    pix_fmt: pix_fmt.to_owned()
                })
            );
        }
    }

    #[test]
    fn filters_extract_y_and_expand_only_limited_range() {
        assert_eq!(LumaTransform::Identity.filter(), "extractplanes=y");
        assert_eq!(
            LumaTransform::LimitedToFull.filter(),
            "extractplanes=y,lut=c0='clip(round((val-16)*255/219),0,255)'"
        );
        assert_eq!(
            serde_json::to_value(LumaTransform::LimitedToFull).unwrap(),
            "limited_to_full"
        );
    }

    #[test]
    fn frame_ranges_must_agree() {
        let some = |s: &str| Some(s.to_owned());
        assert_eq!(uniform_frame_color_range(&[]), Ok(None));
        assert_eq!(
            uniform_frame_color_range(&[some("tv"), some("tv")]),
            Ok(some("tv"))
        );
        assert_eq!(
            uniform_frame_color_range(&[None, some("unknown")]),
            Ok(None)
        );
        assert_eq!(
            uniform_frame_color_range(&[some("pc"), some("pc"), some("tv")]),
            Err(MediaError::MixedFrameColorRange {
                index: 2,
                first: "pc".to_owned(),
                current: "tv".to_owned()
            })
        );
    }
}
