//! Turns copied in-window luma planes into frames and keeps the window bookkeeping.
//!
//! Shared by the ByteBuffer and `AImageReader` output paths so both apply the same frame mode,
//! the same fail-closed geometry and format checks, and the same PTS history.

use crate::decode::{retain_window_output, DecodeError, OutputInfo, StageTimings};
use crate::raster::{to_contract_luma, LumaFrame, Rotation};
use crate::view::{scan_checksum, DisplayGray, DisplayLumaView};
use std::borrow::Cow;
use std::time::Instant;

/// How an in-window frame is handed downstream.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FrameMode {
    /// Materialised display raster: rotate + range-expand every pixel (the #258 baseline).
    Packed,
    /// Zero-copy `DisplayLumaView`: only the luma copy; rotation and LUT applied per lookup.
    View,
    /// `View`, then every pixel read once through `intensity` (timed as `scan_s`): the bound for
    /// a consumer that touches the whole frame instead of a tracking window.
    ViewScan,
}

impl FrameMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Packed => "packed",
            Self::View => "view",
            Self::ViewScan => "view-scan",
        }
    }
}

pub enum DisplayFrame {
    Packed(LumaFrame),
    View(DisplayLumaView),
}

impl DisplayFrame {
    pub fn image(&self) -> &dyn DisplayGray {
        match self {
            Self::Packed(frame) => frame,
            Self::View(view) => view,
        }
    }

    /// FFmpeg `-pix_fmt gray` layout for `--dump-gray`; a view is materialised only here.
    pub fn packed_pixels(&self) -> Cow<'_, [u8]> {
        match self {
            Self::Packed(frame) => Cow::Borrowed(&frame.pixels),
            Self::View(view) => Cow::Owned(view.to_packed().pixels),
        }
    }
}

pub type FrameSink<'a> = dyn FnMut(&DisplayFrame) -> Result<(), DecodeError> + 'a;

/// Everything the report needs about the emitted window.
#[derive(Debug, Clone, PartialEq)]
pub struct CollectedWindow {
    pub display_width_px: usize,
    pub display_height_px: usize,
    pub output: OutputInfo,
    pub presentation_time_us: Vec<i64>,
    pub timestamps_s: Vec<f64>,
}

pub struct WindowCollector {
    mode: FrameMode,
    started: Instant,
    display: Option<(usize, usize)>,
    window_output: Option<OutputInfo>,
    presentation_time_us: Vec<i64>,
    timestamps_s: Vec<f64>,
}

impl WindowCollector {
    pub fn new(mode: FrameMode, started: Instant) -> Self {
        Self {
            mode,
            started,
            display: None,
            window_output: None,
            presentation_time_us: Vec::new(),
            timestamps_s: Vec::new(),
        }
    }

    /// Builds the frame from an already-copied plane, checks it against the window so far and
    /// hands it to `sink`. Build time goes to `repack_s`, sink time to `sink_s`.
    #[allow(clippy::too_many_arguments)]
    pub fn accept(
        &mut self,
        plane: Vec<u8>,
        out: &OutputInfo,
        rotation: Rotation,
        pts_us: i64,
        timestamp_s: f64,
        timings: &mut StageTimings,
        sink: &mut FrameSink<'_>,
    ) -> Result<(), DecodeError> {
        let build_started = Instant::now();
        let frame =
            match self.mode {
                FrameMode::Packed => DisplayFrame::Packed(to_contract_luma(
                    &plane,
                    &out.layout,
                    rotation,
                    out.luma_range,
                )?),
                FrameMode::View | FrameMode::ViewScan => DisplayFrame::View(
                    DisplayLumaView::try_new(plane, &out.layout, rotation, out.luma_range)?,
                ),
            };
        timings.repack_s += build_started.elapsed().as_secs_f64();
        if self.mode == FrameMode::ViewScan {
            let scan_started = Instant::now();
            std::hint::black_box(scan_checksum(frame.image()));
            timings.scan_s += scan_started.elapsed().as_secs_f64();
        }
        if self.timestamps_s.is_empty() {
            timings.first_frame_s = self.started.elapsed().as_secs_f64();
        }
        let image = frame.image();
        let size_now = (image.width_px() as usize, image.height_px() as usize);
        match self.display {
            Some(first) if first != size_now => {
                return Err(DecodeError::GeometryChanged {
                    first,
                    now: size_now,
                })
            }
            _ => self.display = Some(size_now),
        }
        retain_window_output(&mut self.window_output, out)?;

        let sink_started = Instant::now();
        sink(&frame)?;
        timings.sink_s += sink_started.elapsed().as_secs_f64();

        self.presentation_time_us.push(pts_us);
        self.timestamps_s.push(timestamp_s);
        Ok(())
    }

    /// `None` when no frame fell inside the window.
    pub fn finish(self) -> Option<CollectedWindow> {
        let (display_width_px, display_height_px) = self.display?;
        Some(CollectedWindow {
            display_width_px,
            display_height_px,
            output: self.window_output?,
            presentation_time_us: self.presentation_time_us,
            timestamps_s: self.timestamps_s,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::decode::{GeometrySource, RangeSource};
    use crate::raster::{CropRect, LumaRange, PlaneLayout};

    fn output(width: usize, height: usize) -> OutputInfo {
        OutputInfo {
            decoder_name: "c2.test.avc.decoder".to_owned(),
            color_format: 21,
            color_range_raw: Some(2),
            luma_range: LumaRange::Limited,
            range_source: RangeSource::Decoder,
            stride_source: GeometrySource::Decoder,
            crop_source: GeometrySource::Decoder,
            y_offset: 0,
            layout: PlaneLayout {
                stride: width + 1,
                slice_height: height,
                crop: CropRect::full(width, height),
            },
        }
    }

    fn plane(width: usize, height: usize) -> Vec<u8> {
        (0..(width + 1) * height)
            .map(|i| (i * 13 % 256) as u8)
            .collect()
    }

    fn collect(mode: FrameMode) -> (Vec<Vec<u8>>, CollectedWindow) {
        let mut collector = WindowCollector::new(mode, Instant::now());
        let mut timings = StageTimings::default();
        let mut dumped = Vec::new();
        let out = output(5, 3);
        for (pts_us, timestamp_s) in [(0, 0.0), (33_333, 0.033333)] {
            collector
                .accept(
                    plane(5, 3),
                    &out,
                    Rotation::Cw90,
                    pts_us,
                    timestamp_s,
                    &mut timings,
                    &mut |frame: &DisplayFrame| {
                        dumped.push(frame.packed_pixels().into_owned());
                        Ok(())
                    },
                )
                .unwrap();
        }
        (dumped, collector.finish().unwrap())
    }

    #[test]
    fn view_and_packed_modes_dump_identical_rasters_and_bookkeeping() {
        let (packed_dump, packed) = collect(FrameMode::Packed);
        for mode in [FrameMode::View, FrameMode::ViewScan] {
            let (view_dump, view) = collect(mode);
            assert_eq!(packed_dump, view_dump, "{mode:?}");
            assert_eq!(packed, view, "{mode:?}");
        }
        assert_eq!((packed.display_width_px, packed.display_height_px), (3, 5));
        assert_eq!(packed.presentation_time_us, vec![0, 33_333]);
    }

    #[test]
    fn geometry_or_format_change_inside_window_fails_closed() {
        let mut collector = WindowCollector::new(FrameMode::View, Instant::now());
        let mut timings = StageTimings::default();
        let mut sink = |_: &DisplayFrame| Ok(());
        collector
            .accept(
                plane(5, 3),
                &output(5, 3),
                Rotation::Cw0,
                0,
                0.0,
                &mut timings,
                &mut sink,
            )
            .unwrap();
        assert!(matches!(
            collector.accept(
                plane(4, 3),
                &output(4, 3),
                Rotation::Cw0,
                1,
                0.1,
                &mut timings,
                &mut sink
            ),
            Err(DecodeError::GeometryChanged { .. })
        ));
        let mut full = output(5, 3);
        full.luma_range = LumaRange::Full;
        assert!(collector
            .accept(
                plane(5, 3),
                &full,
                Rotation::Cw0,
                2,
                0.2,
                &mut timings,
                &mut sink
            )
            .is_err());
        assert_eq!(collector.finish().unwrap().timestamps_s, vec![0.0]);
    }

    #[test]
    fn empty_window_has_nothing_to_report() {
        assert!(WindowCollector::new(FrameMode::Packed, Instant::now())
            .finish()
            .is_none());
    }
}
