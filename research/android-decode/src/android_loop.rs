//! The output loop of the in-process decoder: feeds samples, inspects every output buffer in
//! presentation order and emits in-window frames from either output path.

use crate::android::{
    check, configure_codec, feed_input, open_video_track, read_hints, read_output_info,
    read_surface_output, secs, stream_origin_us, Codec, DecodeOptions, InputStep, IMAGE_POLL,
    OUTPUT_TIMEOUT_DRAINING_US, OUTPUT_TIMEOUT_FEEDING_US, STALL_LIMIT,
};
use crate::android_image::ImageReader;
use crate::collect::{DisplayFrame, FrameSink, WindowCollector};
use crate::decode::{DecodeError, DecodeReport, OutputInfo, OutputPath, StageTimings};
use crate::raster::{LumaRange, Rotation};
use crate::timing::{pts_us_to_seconds, push_strictly_increasing, Placement, Window};
use ndk_sys as ffi;
use std::collections::VecDeque;
use std::path::Path;
use std::time::Instant;

/// Copies the luma plane of a dequeued ByteBuffer output, bounds-checked against the codec buffer.
fn copy_codec_luma(
    codec: &Codec,
    index: usize,
    info: &ffi::AMediaCodecBufferInfo,
    out: &OutputInfo,
) -> Result<Vec<u8>, DecodeError> {
    let mut capacity = 0usize;
    // SAFETY: index is a dequeued output buffer, valid until the caller releases it.
    let base = unsafe { ffi::AMediaCodec_getOutputBuffer(codec.raw, index, &mut capacity) };
    if base.is_null() {
        return Err(DecodeError::Media {
            call: "AMediaCodec_getOutputBuffer",
            status: -1,
        });
    }
    let (offset, size) = (info.offset, info.size);
    let out_of_bounds = DecodeError::OutputBufferOutOfBounds {
        offset,
        size,
        capacity,
    };
    let in_bounds = offset >= 0
        && (offset as usize)
            .checked_add(size as usize)
            .is_some_and(|end| end <= capacity);
    if !in_bounds {
        return Err(out_of_bounds);
    }
    // SAFETY: [offset, offset+size) was checked to lie inside the codec buffer.
    let plane = unsafe { std::slice::from_raw_parts(base.add(offset as usize), size as usize) };
    let plane = plane.get(out.y_offset..).ok_or(out_of_bounds)?;
    // One sequential copy out of codec memory (often uncached); everything after reads the copy.
    Ok(plane[..out.layout.extent().min(plane.len())].to_vec())
}

/// What to do with a dequeued output buffer once it has been inspected.
#[derive(Debug, Clone, Copy)]
struct OutputStep {
    /// Release to the Surface (ImageReader path, in-window frames only).
    render: bool,
    stop: bool,
}

struct DecodeLoop<'a> {
    codec: &'a Codec,
    reader: Option<&'a ImageReader>,
    decoder_name: String,
    assume_range: Option<LumaRange>,
    rotation: Rotation,
    window: Window,
    origin_us: i64,
    output: Option<OutputInfo>,
    /// Every sized output, including pre-roll and the frame that ends the window, so a decoder
    /// that does not emit in presentation order fails instead of losing frames.
    all_output_pts_us: Vec<i64>,
    preroll_discarded: u64,
    /// Rendered in-window buffers still waiting for their Surface image, oldest first.
    pending: VecDeque<(i64, f64)>,
    collector: WindowCollector,
    timings: StageTimings,
}

impl DecodeLoop<'_> {
    fn format_changed(&mut self) -> Result<(), DecodeError> {
        self.output = Some(match self.reader {
            None => read_output_info(self.codec, &self.decoder_name, self.assume_range)?,
            Some(_) => read_surface_output(self.codec, &self.decoder_name, self.assume_range)?,
        });
        Ok(())
    }

    fn add_copy_time(&mut self, since: Instant) {
        let copy_s = secs(since);
        self.timings.copy_s += copy_s;
        self.timings.repack_s += copy_s;
    }

    fn inspect(
        &mut self,
        index: usize,
        info: &ffi::AMediaCodecBufferInfo,
        sink: &mut FrameSink<'_>,
    ) -> Result<OutputStep, DecodeError> {
        let end_of_stream = info.flags & ffi::AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM != 0;
        let keep = OutputStep {
            render: false,
            stop: end_of_stream,
        };
        if info.size <= 0 {
            return Ok(keep);
        }
        let pts_us = info.presentationTimeUs;
        push_strictly_increasing(&mut self.all_output_pts_us, pts_us)?;
        let timestamp_s = pts_us_to_seconds(pts_us, self.origin_us)?;
        match self.window.place(timestamp_s) {
            Placement::Before => {
                self.preroll_discarded += 1;
                Ok(keep)
            }
            Placement::After => Ok(OutputStep {
                render: false,
                stop: true,
            }),
            Placement::Inside => {
                let out = self
                    .output
                    .clone()
                    .ok_or(DecodeError::MissingOutputFormat)?;
                if self.reader.is_some() {
                    self.pending.push_back((pts_us, timestamp_s));
                    return Ok(OutputStep {
                        render: true,
                        stop: end_of_stream,
                    });
                }
                let copy_started = Instant::now();
                let plane = copy_codec_luma(self.codec, index, info, &out)?;
                self.add_copy_time(copy_started);
                self.collector.accept(
                    plane,
                    &out,
                    self.rotation,
                    pts_us,
                    timestamp_s,
                    &mut self.timings,
                    sink,
                )?;
                Ok(keep)
            }
        }
    }

    /// Pairs queued Surface images with rendered buffers, in order. With `wait`, polls until every
    /// rendered buffer has its image or the stall limit passes.
    fn take_images(&mut self, wait: bool, sink: &mut FrameSink<'_>) -> Result<(), DecodeError> {
        let Some(reader) = self.reader else {
            return Ok(());
        };
        let mut last_progress = Instant::now();
        loop {
            let Some(image) = reader.try_acquire()? else {
                if !wait || self.pending.is_empty() {
                    return Ok(());
                }
                if last_progress.elapsed() > STALL_LIMIT {
                    return Err(DecodeError::Stalled {
                        idle_ms: last_progress.elapsed().as_millis(),
                    });
                }
                std::thread::sleep(IMAGE_POLL);
                continue;
            };
            let image_ns = image.timestamp_ns()?;
            let (pts_us, timestamp_s) =
                self.pending.pop_front().ok_or(DecodeError::Unsupported(
                    "Surface delivered an image with no rendered buffer pending",
                ))?;
            if i128::from(image_ns) != i128::from(pts_us) * 1000 {
                return Err(DecodeError::ImageTimestampMismatch {
                    expected_us: pts_us,
                    image_ns,
                });
            }
            let copy_started = Instant::now();
            let (plane, layout) = image.copy_luma()?;
            drop(image);
            self.add_copy_time(copy_started);
            let out = OutputInfo {
                layout,
                ..self
                    .output
                    .clone()
                    .ok_or(DecodeError::MissingOutputFormat)?
            };
            self.collector.accept(
                plane,
                &out,
                self.rotation,
                pts_us,
                timestamp_s,
                &mut self.timings,
                sink,
            )?;
            last_progress = Instant::now();
        }
    }
}

/// Decodes the window and hands every in-window frame to `on_frame` in presentation order.
pub fn decode(
    path: &Path,
    options: &DecodeOptions,
    mut on_frame: impl FnMut(&DisplayFrame) -> Result<(), DecodeError>,
) -> Result<DecodeReport, DecodeError> {
    let started = Instant::now();
    let mut timings = StageTimings::default();

    let (_file, extractor, track_format, source) = open_video_track(path)?;
    timings.extractor_open_s = secs(started);

    let configure_started = Instant::now();
    // Declared before the codec, so it is dropped after it: the codec renders into its window.
    let reader = match options.output_path {
        OutputPath::ByteBuffer => None,
        OutputPath::ImageReader => Some(ImageReader::new(
            source.coded_width_px,
            source.coded_height_px,
        )?),
    };
    let codec = configure_codec(
        &source,
        &track_format,
        options.hints,
        reader.as_ref().map(ImageReader::window),
    )?;
    let decoder_name = codec.name();
    let hint_readback = read_hints(&codec)?;
    timings.codec_configure_s = secs(configure_started);

    let window = options.window;
    let origin_us = stream_origin_us(&extractor)?;
    // Always seek: the origin scan advanced the extractor.
    // SAFETY: extractor is live with a selected track.
    check("AMediaExtractor_seekTo", unsafe {
        ffi::AMediaExtractor_seekTo(
            extractor.0,
            origin_us + window.seek_us(),
            ffi::SeekMode::AMEDIAEXTRACTOR_SEEK_PREVIOUS_SYNC,
        )
    })?;

    let loop_started = Instant::now();
    let mut state = DecodeLoop {
        codec: &codec,
        reader: reader.as_ref(),
        decoder_name,
        assume_range: options.assume_range,
        rotation: source.rotation,
        window,
        origin_us,
        output: None,
        all_output_pts_us: Vec::new(),
        preroll_discarded: 0,
        pending: VecDeque::new(),
        collector: WindowCollector::new(options.frame_mode, started),
        timings,
    };
    let sink: &mut FrameSink<'_> = &mut on_frame;
    let mut input_done = false;
    let mut last_progress = Instant::now();

    loop {
        state.take_images(false, sink)?;
        // Fill every free input buffer, so the decoder is never starved between outputs.
        while !input_done {
            match feed_input(&codec, &extractor)? {
                InputStep::Fed => {}
                InputStep::NoFreeBuffer => break,
                InputStep::EndOfStream => input_done = true,
            }
        }
        let mut info = ffi::AMediaCodecBufferInfo {
            offset: 0,
            size: 0,
            presentationTimeUs: 0,
            flags: 0,
        };
        let timeout_us = if input_done {
            OUTPUT_TIMEOUT_DRAINING_US
        } else {
            OUTPUT_TIMEOUT_FEEDING_US
        };
        // SAFETY: codec is started; info is a valid out-pointer.
        let index =
            unsafe { ffi::AMediaCodec_dequeueOutputBuffer(codec.raw, &mut info, timeout_us) };
        if index == ffi::AMEDIACODEC_INFO_OUTPUT_FORMAT_CHANGED as isize {
            state.format_changed()?;
            last_progress = Instant::now();
            continue;
        }
        if index == ffi::AMEDIACODEC_INFO_TRY_AGAIN_LATER as isize
            || index == ffi::AMEDIACODEC_INFO_OUTPUT_BUFFERS_CHANGED as isize
        {
            if last_progress.elapsed() > STALL_LIMIT {
                return Err(DecodeError::Stalled {
                    idle_ms: last_progress.elapsed().as_millis(),
                });
            }
            continue;
        }
        if index < 0 {
            return Err(DecodeError::Media {
                call: "AMediaCodec_dequeueOutputBuffer",
                status: index as i64,
            });
        }
        last_progress = Instant::now();
        let index = index as usize;
        let step = state.inspect(index, &info, sink);
        let render = matches!(step, Ok(OutputStep { render: true, .. }));
        // SAFETY: return the buffer to the codec exactly once, whatever happened above.
        let released = unsafe { ffi::AMediaCodec_releaseOutputBuffer(codec.raw, index, render) };
        let step = step?;
        if render {
            check("AMediaCodec_releaseOutputBuffer(render)", released)?;
        }
        if step.stop {
            break;
        }
    }
    state.take_images(true, sink)?;

    let DecodeLoop {
        collector,
        mut timings,
        preroll_discarded,
        all_output_pts_us,
        ..
    } = state;
    timings.decode_loop_s = secs(loop_started);
    drop(codec);
    drop(reader);
    timings.total_s = secs(started);

    let collected = collector.finish().ok_or(DecodeError::EmptySelection {
        start_s: window.start_s,
        end_s: window.end_s,
    })?;
    Ok(DecodeReport {
        source,
        output: collected.output,
        frame_mode: options.frame_mode,
        output_path: options.output_path,
        hints: options.hints,
        hint_readback,
        decoded_output_count: all_output_pts_us.len() as u64,
        window,
        origin_us,
        display_width_px: collected.display_width_px,
        display_height_px: collected.display_height_px,
        presentation_time_us: collected.presentation_time_us,
        timestamps_s: collected.timestamps_s,
        preroll_discarded,
        timings,
    })
}
