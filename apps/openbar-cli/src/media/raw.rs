//! Pairs a raw `gray` byte stream with probed timestamps, strictly by decode order.

use super::{DecodedFrame, MediaError, TimeRange};
use openbar_tracking::GrayFrame;
use std::borrow::Cow;
use std::io::{ErrorKind, Read};

pub struct RawFrameLayout {
    pub width_px: u32,
    pub height_px: u32,
}

impl RawFrameLayout {
    pub fn frame_bytes(&self) -> usize {
        self.width_px as usize * self.height_px as usize
    }
}

/// Reads every frame from `reader`, keeps those whose timestamp lies in `selection`
/// (inclusive), and fails closed when the stream and the probe disagree on frame count.
pub fn read_gray_frames<R: Read>(
    mut reader: R,
    layout: &RawFrameLayout,
    timestamps_s: &[f64],
    selection: Option<TimeRange>,
) -> Result<Vec<DecodedFrame>, MediaError> {
    let frame_bytes = layout.frame_bytes();
    let mut buffer = vec![0u8; frame_bytes];
    let mut frames = Vec::new();
    let mut decoded = 0usize;

    loop {
        let received = read_up_to(&mut reader, &mut buffer)?;
        if received == 0 {
            break;
        }
        if received < frame_bytes {
            return Err(MediaError::TruncatedFrame {
                index: decoded,
                received_bytes: received,
                expected_bytes: frame_bytes,
            });
        }
        if let Some(&timestamp_s) = timestamps_s.get(decoded) {
            if selection.is_none_or(|range| range.contains(timestamp_s)) {
                let image = GrayFrame::try_new(layout.width_px, layout.height_px, buffer.clone())
                    .map_err(|error| MediaError::InvalidFrame {
                    index: decoded,
                    detail: error.to_string(),
                })?;
                frames.push(DecodedFrame {
                    timestamp_s,
                    frame_index: decoded as u64,
                    image,
                });
            }
        }
        decoded += 1;
    }

    if decoded != timestamps_s.len() {
        return Err(MediaError::FrameCountMismatch {
            probed: timestamps_s.len(),
            decoded,
        });
    }
    Ok(frames)
}

/// Fills `buffer` unless the stream ends first; returns the number of bytes read.
fn read_up_to<R: Read>(reader: &mut R, buffer: &mut [u8]) -> Result<usize, MediaError> {
    let mut filled = 0usize;
    while filled < buffer.len() {
        match reader.read(&mut buffer[filled..]) {
            Ok(0) => break,
            Ok(read) => filled += read,
            Err(error) if error.kind() == ErrorKind::Interrupted => {}
            Err(error) => {
                return Err(MediaError::Io {
                    context: Cow::Borrowed("failed to read decoded frames from ffmpeg"),
                    detail: error.to_string(),
                })
            }
        }
    }
    Ok(filled)
}

#[cfg(test)]
mod tests {
    use super::*;
    use openbar_tracking::GrayscaleImage;

    const LAYOUT: RawFrameLayout = RawFrameLayout {
        width_px: 3,
        height_px: 2,
    };

    fn stream(frame_count: u8) -> Vec<u8> {
        (0..frame_count)
            .flat_map(|frame| (0..6u8).map(move |pixel| frame * 10 + pixel))
            .collect()
    }

    fn summary(frames: &[DecodedFrame]) -> Vec<(f64, u64, u8)> {
        frames
            .iter()
            .map(|frame| {
                (
                    frame.timestamp_s,
                    frame.frame_index,
                    frame.image.intensity(2, 1),
                )
            })
            .collect()
    }

    #[test]
    fn pairs_frames_with_timestamps_in_decode_order() {
        let frames = read_gray_frames(&stream(3)[..], &LAYOUT, &[0.0, 0.031, 0.07], None)
            .expect("stream matches probe");
        assert_eq!(
            summary(&frames),
            vec![(0.0, 0, 5), (0.031, 1, 15), (0.07, 2, 25)]
        );
    }

    #[test]
    fn keeps_only_frames_inside_inclusive_selection_but_reads_whole_stream() {
        let range = TimeRange::try_new(0.031, 0.07).expect("valid range");
        let frames = read_gray_frames(
            &stream(4)[..],
            &LAYOUT,
            &[0.0, 0.031, 0.07, 0.1],
            Some(range),
        )
        .expect("stream matches probe");
        assert_eq!(summary(&frames), vec![(0.031, 1, 15), (0.07, 2, 25)]);
    }

    #[test]
    fn rejects_frame_count_mismatch_in_both_directions() {
        assert_eq!(
            read_gray_frames(&stream(2)[..], &LAYOUT, &[0.0, 0.1, 0.2], None).map(|_| ()),
            Err(MediaError::FrameCountMismatch {
                probed: 3,
                decoded: 2
            })
        );
        assert_eq!(
            read_gray_frames(&stream(3)[..], &LAYOUT, &[0.0, 0.1], None).map(|_| ()),
            Err(MediaError::FrameCountMismatch {
                probed: 2,
                decoded: 3
            })
        );
    }

    #[test]
    fn rejects_truncated_trailing_frame() {
        let mut bytes = stream(2);
        bytes.truncate(10);
        assert_eq!(
            read_gray_frames(&bytes[..], &LAYOUT, &[0.0, 0.1], None).map(|_| ()),
            Err(MediaError::TruncatedFrame {
                index: 1,
                received_bytes: 4,
                expected_bytes: 6
            })
        );
    }

    #[test]
    fn reassembles_frames_from_short_reads() {
        struct OneByte<'a>(&'a [u8]);
        impl Read for OneByte<'_> {
            fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
                let Some((&first, rest)) = self.0.split_first() else {
                    return Ok(0);
                };
                buf[0] = first;
                self.0 = rest;
                Ok(1)
            }
        }
        let bytes = stream(2);
        let frames = read_gray_frames(OneByte(&bytes), &LAYOUT, &[0.0, 0.1], None)
            .expect("short reads are reassembled");
        assert_eq!(summary(&frames), vec![(0.0, 0, 5), (0.1, 1, 15)]);
    }
}
