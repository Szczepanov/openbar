use std::borrow::Cow;
use std::fmt;

#[derive(Debug, Clone, PartialEq)]
pub enum MediaError {
    ToolUnavailable {
        tool: &'static str,
        detail: String,
    },
    ToolFailed {
        tool: &'static str,
        status: String,
        stderr: String,
    },
    InvalidProbeOutput {
        detail: String,
    },
    NoVideoStream,
    MissingStreamField {
        field: &'static str,
    },
    InvalidTimeBase {
        value: String,
    },
    UnsupportedRotation {
        value: f64,
    },
    UnsupportedSampleAspectRatio {
        value: String,
    },
    InvalidDimensions {
        width_px: u32,
        height_px: u32,
    },
    NoFrames,
    MissingPts {
        index: usize,
    },
    NonIncreasingPts {
        index: usize,
        previous: i64,
        current: i64,
    },
    PtsBeforeMediaStart {
        index: usize,
        pts: i64,
        start_pts: i64,
    },
    InvalidRange {
        start_s: f64,
        end_s: f64,
    },
    EmptySelection {
        start_s: f64,
        end_s: f64,
    },
    FrameMemoryBudgetExceeded {
        required_bytes: u64,
        budget_bytes: u64,
    },
    TruncatedFrame {
        index: usize,
        received_bytes: usize,
        expected_bytes: usize,
    },
    FrameCountMismatch {
        probed: usize,
        decoded: usize,
    },
    InvalidFrame {
        index: usize,
        detail: openbar_tracking::TrackerError,
    },
    Io {
        context: Cow<'static, str>,
        detail: String,
    },
    /// A frame-stream error together with whatever ffmpeg reported while producing it.
    WithDecoderOutput {
        error: Box<MediaError>,
        stderr: String,
    },
}

impl fmt::Display for MediaError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ToolUnavailable { tool, detail } => write!(
                f,
                "'{tool}' could not be started ({detail}); install FFmpeg and make '{tool}' available on PATH"
            ),
            Self::ToolFailed {
                tool,
                status,
                stderr,
            } => write!(f, "'{tool}' failed with {status}: {}", stderr.trim()),
            Self::InvalidProbeOutput { detail } => {
                write!(f, "ffprobe output could not be interpreted: {detail}")
            }
            Self::NoVideoStream => write!(f, "media contains no video stream"),
            Self::MissingStreamField { field } => {
                write!(f, "ffprobe did not report required video stream field '{field}'")
            }
            Self::InvalidTimeBase { value } => {
                write!(f, "video stream time base '{value}' is not a positive rational")
            }
            Self::UnsupportedRotation { value } => write!(
                f,
                "display rotation {value} degrees is unsupported; only multiples of 90 are accepted"
            ),
            Self::UnsupportedSampleAspectRatio { value } => write!(
                f,
                "sample aspect ratio {value} is unsupported; display pixels must be square (1:1)"
            ),
            Self::InvalidDimensions {
                width_px,
                height_px,
            } => write!(f, "video dimensions {width_px}x{height_px} are invalid"),
            Self::NoFrames => write!(f, "video stream contains no decodable frames"),
            Self::MissingPts { index } => write!(
                f,
                "frame {index} has no presentation timestamp; timestamps are never synthesized from frame rate"
            ),
            Self::NonIncreasingPts {
                index,
                previous,
                current,
            } => write!(
                f,
                "presentation timestamps must strictly increase, but frame {index} has pts {current} after {previous}"
            ),
            Self::PtsBeforeMediaStart {
                index,
                pts,
                start_pts,
            } => write!(
                f,
                "frame {index} has pts {pts} before the stream start pts {start_pts}"
            ),
            Self::InvalidRange { start_s, end_s } => write!(
                f,
                "selected range [{start_s}, {end_s}] s must be finite, non-negative and non-decreasing"
            ),
            Self::EmptySelection { start_s, end_s } => {
                write!(f, "no decoded frame lies inside the selected range [{start_s}, {end_s}] s")
            }
            Self::FrameMemoryBudgetExceeded {
                required_bytes,
                budget_bytes,
            } => write!(
                f,
                "selected frames need {required_bytes} bytes, above the {budget_bytes}-byte frame memory budget; select a narrower time range or raise the budget"
            ),
            Self::TruncatedFrame {
                index,
                received_bytes,
                expected_bytes,
            } => write!(
                f,
                "decoded frame {index} is truncated: received {received_bytes} of {expected_bytes} bytes"
            ),
            Self::FrameCountMismatch { probed, decoded } => write!(
                f,
                "ffprobe reported {probed} frames but ffmpeg decoded {decoded}; frames cannot be paired with timestamps"
            ),
            Self::InvalidFrame { index, detail } => {
                write!(f, "decoded frame {index} is invalid: {detail}")
            }
            Self::Io { context, detail } => write!(f, "{context}: {detail}"),
            Self::WithDecoderOutput { error, stderr } => {
                write!(f, "{error}; ffmpeg reported: {}", stderr.trim())
            }
        }
    }
}

impl std::error::Error for MediaError {}
