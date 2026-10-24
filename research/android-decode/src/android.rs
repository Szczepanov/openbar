//! In-process `AMediaExtractor` + `AMediaCodec` setup (NDK `libmediandk`) for ByteBuffer or
//! `AImageReader` Surface output; the output loop is in `android_loop`.
//!
//! Every NDK handle is owned by an RAII wrapper so codec, extractor and formats are released on
//! all paths.

use crate::android_image::IMAGE_FORMAT_YUV_420_888;
use crate::collect::FrameMode;
use crate::decode::{
    CodecHints, DecodeError, GeometrySource, HintReadback, OutputInfo, OutputPath, RangeSource,
    SourceInfo,
};
use crate::image_layout::parse_luma_plane;
use crate::raster::{CropRect, LumaRange, PlaneLayout, Rotation};
use crate::timing::Window;
use ndk_sys as ffi;
use std::ffi::{CStr, CString};
use std::fs::File;
use std::os::fd::AsRawFd;
use std::os::raw::c_char;
use std::path::Path;
use std::ptr;
use std::time::{Duration, Instant};

/// MediaFormat key strings. The NDK's `AMEDIAFORMAT_KEY_*` symbols are only exported from
/// API 28 stubs (Termux links an older one), and the strings are the stable public key names.
const KEY_MIME: &CStr = c"mime";
const KEY_WIDTH: &CStr = c"width";
const KEY_HEIGHT: &CStr = c"height";
const KEY_ROTATION: &CStr = c"rotation-degrees";
const KEY_DURATION: &CStr = c"durationUs";
const KEY_COLOR_FORMAT: &CStr = c"color-format";
const KEY_STRIDE: &CStr = c"stride";
const KEY_SLICE_HEIGHT: &CStr = c"slice-height";
const KEY_COLOR_RANGE: &CStr = c"color-range";
const KEY_DISPLAY_CROP: &CStr = c"crop";
const KEY_IMAGE_DATA: &CStr = c"image-data";
const KEY_PRIORITY: &CStr = c"priority";
const KEY_OPERATING_RATE: &CStr = c"operating-rate";

/// `MediaCodecInfo.CodecCapabilities` colour formats.
const COLOR_FORMAT_YUV420_PLANAR: i32 = 19;
const COLOR_FORMAT_YUV420_SEMIPLANAR: i32 = 21;
const COLOR_FORMAT_YUV420_FLEXIBLE: i32 = 0x7F42_0888;

/// Input is polled without blocking; output waits briefly while input is still being fed so
/// free input buffers are refilled quickly, and longer once only draining remains.
const INPUT_TIMEOUT_US: i64 = 0;
pub(crate) const OUTPUT_TIMEOUT_FEEDING_US: i64 = 1_000;
pub(crate) const OUTPUT_TIMEOUT_DRAINING_US: i64 = 10_000;
pub(crate) const STALL_LIMIT: Duration = Duration::from_secs(5);
/// Samples scanned from the start of the track to find the stream's first presentation time.
const ORIGIN_SCAN_LIMIT: usize = 1024;
/// Poll interval while waiting for the last rendered images after decoding stops.
pub(crate) const IMAGE_POLL: Duration = Duration::from_micros(200);

pub struct DecodeOptions {
    pub window: Window,
    /// Used only when the decoder does not report `color-range`; recorded as an assumption.
    pub assume_range: Option<LumaRange>,
    pub frame_mode: FrameMode,
    pub output_path: OutputPath,
    pub hints: CodecHints,
}

pub(crate) fn check(call: &'static str, status: ffi::media_status_t) -> Result<(), DecodeError> {
    if status == ffi::media_status_t::AMEDIA_OK {
        Ok(())
    } else {
        Err(DecodeError::Media {
            call,
            status: i64::from(status.0),
        })
    }
}

pub(crate) struct Format(pub(crate) *mut ffi::AMediaFormat);

impl Format {
    fn non_null(call: &'static str, raw: *mut ffi::AMediaFormat) -> Result<Self, DecodeError> {
        if raw.is_null() {
            Err(DecodeError::Media { call, status: -1 })
        } else {
            Ok(Self(raw))
        }
    }

    fn int32(&self, key: *const c_char) -> Option<i32> {
        let mut value = 0;
        // SAFETY: self.0 is a live format; key is an NDK-provided static C string.
        unsafe { ffi::AMediaFormat_getInt32(self.0, key, &mut value) }.then_some(value)
    }

    fn float(&self, key: *const c_char) -> Option<f32> {
        let mut value = 0.0;
        // SAFETY: as above.
        unsafe { ffi::AMediaFormat_getFloat(self.0, key, &mut value) }.then_some(value)
    }

    fn int64(&self, key: *const c_char) -> Option<i64> {
        let mut value = 0;
        // SAFETY: as above.
        unsafe { ffi::AMediaFormat_getInt64(self.0, key, &mut value) }.then_some(value)
    }

    fn string(&self, key: *const c_char) -> Option<String> {
        let mut value: *const c_char = ptr::null();
        // SAFETY: the returned pointer is owned by the format and valid until it is deleted or
        // changed; it is copied immediately.
        let found = unsafe { ffi::AMediaFormat_getString(self.0, key, &mut value) };
        (found && !value.is_null()).then(|| {
            unsafe { CStr::from_ptr(value) }
                .to_string_lossy()
                .into_owned()
        })
    }

    fn buffer(&self, key: *const c_char) -> Option<Vec<u8>> {
        let mut data: *mut std::os::raw::c_void = ptr::null_mut();
        let mut size = 0usize;
        // SAFETY: the returned bytes are owned by the format and copied immediately.
        let found = unsafe { ffi::AMediaFormat_getBuffer(self.0, key, &mut data, &mut size) };
        (found && !data.is_null())
            .then(|| unsafe { std::slice::from_raw_parts(data as *const u8, size) }.to_vec())
    }

    fn crop(&self) -> Result<Option<CropRect>, DecodeError> {
        let (mut left, mut top, mut right, mut bottom) = (0, 0, 0, 0);
        // SAFETY: as above. KEY_DISPLAY_CROP is API 28+; inclusive right/bottom.
        let found = unsafe {
            ffi::AMediaFormat_getRect(
                self.0,
                KEY_DISPLAY_CROP.as_ptr(),
                &mut left,
                &mut top,
                &mut right,
                &mut bottom,
            )
        };
        if !found {
            return Ok(None);
        }
        Ok(Some(CropRect::try_from_android([
            left, top, right, bottom,
        ])?))
    }
}

impl Drop for Format {
    fn drop(&mut self) {
        // SAFETY: the format was returned to us by the NDK and is deleted exactly once.
        unsafe { ffi::AMediaFormat_delete(self.0) };
    }
}

pub(crate) struct Extractor(pub(crate) *mut ffi::AMediaExtractor);

impl Drop for Extractor {
    fn drop(&mut self) {
        // SAFETY: created by AMediaExtractor_new and deleted exactly once.
        unsafe { ffi::AMediaExtractor_delete(self.0) };
    }
}

pub(crate) struct Codec {
    pub(crate) raw: *mut ffi::AMediaCodec,
    started: bool,
}

impl Drop for Codec {
    fn drop(&mut self) {
        // SAFETY: created by AMediaCodec_createDecoderByType; stopped and deleted exactly once.
        unsafe {
            if self.started {
                ffi::AMediaCodec_stop(self.raw);
            }
            ffi::AMediaCodec_delete(self.raw);
        }
    }
}

impl Codec {
    pub(crate) fn name(&self) -> String {
        let mut name: *mut c_char = ptr::null_mut();
        // SAFETY: getName allocates a string we must hand back with releaseName (API 28+).
        unsafe {
            if ffi::AMediaCodec_getName(self.raw, &mut name) != ffi::media_status_t::AMEDIA_OK
                || name.is_null()
            {
                return "unknown".to_owned();
            }
            let owned = CStr::from_ptr(name).to_string_lossy().into_owned();
            ffi::AMediaCodec_releaseName(self.raw, name);
            owned
        }
    }
}

pub(crate) fn secs(since: Instant) -> f64 {
    since.elapsed().as_secs_f64()
}

pub(crate) fn open_video_track(
    path: &Path,
) -> Result<(File, Extractor, Format, SourceInfo), DecodeError> {
    let file = File::open(path)?;
    let length = file.metadata()?.len() as i64;
    // SAFETY: plain constructor; null is checked.
    let raw = unsafe { ffi::AMediaExtractor_new() };
    if raw.is_null() {
        return Err(DecodeError::Media {
            call: "AMediaExtractor_new",
            status: -1,
        });
    }
    let extractor = Extractor(raw);
    // SAFETY: fd stays open for the extractor's lifetime because `file` is returned with it.
    check("AMediaExtractor_setDataSourceFd", unsafe {
        ffi::AMediaExtractor_setDataSourceFd(extractor.0, file.as_raw_fd(), 0, length)
    })?;

    // SAFETY: extractor is live.
    let tracks = unsafe { ffi::AMediaExtractor_getTrackCount(extractor.0) };
    for index in 0..tracks {
        let format = Format::non_null("AMediaExtractor_getTrackFormat", unsafe {
            ffi::AMediaExtractor_getTrackFormat(extractor.0, index)
        })?;
        let Some(mime) = format.string(KEY_MIME.as_ptr()) else {
            continue;
        };
        if !mime.starts_with("video/") {
            continue;
        }
        check("AMediaExtractor_selectTrack", unsafe {
            ffi::AMediaExtractor_selectTrack(extractor.0, index)
        })?;
        let dimension = |key| {
            format
                .int32(key)
                .and_then(|v| u32::try_from(v).ok())
                .ok_or(DecodeError::Unsupported("video track has no width/height"))
        };
        let info = SourceInfo {
            mime,
            coded_width_px: dimension(KEY_WIDTH.as_ptr())?,
            coded_height_px: dimension(KEY_HEIGHT.as_ptr())?,
            rotation: Rotation::from_android_cw(format.int32(KEY_ROTATION.as_ptr()).unwrap_or(0))?,
            duration_us: format.int64(KEY_DURATION.as_ptr()),
        };
        return Ok((file, extractor, format, info));
    }
    Err(DecodeError::NoVideoTrack)
}

pub(crate) fn configure_codec(
    source: &SourceInfo,
    track_format: &Format,
    hints: CodecHints,
    surface: Option<*mut ffi::ANativeWindow>,
) -> Result<Codec, DecodeError> {
    let mime = CString::new(source.mime.as_str())
        .map_err(|_| DecodeError::Unsupported("mime contains NUL"))?;
    // SAFETY: mime is a valid C string; null is checked.
    let raw = unsafe { ffi::AMediaCodec_createDecoderByType(mime.as_ptr()) };
    if raw.is_null() {
        return Err(DecodeError::Media {
            call: "AMediaCodec_createDecoderByType",
            status: -1,
        });
    }
    let mut codec = Codec {
        raw,
        started: false,
    };
    // SAFETY: track_format is live; the surface (if any) is owned by a reader that outlives the
    // codec; no crypto, decoder flags.
    unsafe {
        match surface {
            None => {
                ffi::AMediaFormat_setInt32(
                    track_format.0,
                    KEY_COLOR_FORMAT.as_ptr(),
                    COLOR_FORMAT_YUV420_FLEXIBLE,
                );
            }
            // The coded orientation was read from the track before configure and is applied
            // when the frame is built, exactly as on the ByteBuffer path. Zero here keeps the
            // codec from attaching a Surface transform, which an ImageReader would not apply.
            Some(_) => ffi::AMediaFormat_setInt32(track_format.0, KEY_ROTATION.as_ptr(), 0),
        }
        // Hints only: they may change speed, never output. Unset keys are left to the codec.
        if let Some(priority) = hints.priority {
            ffi::AMediaFormat_setInt32(track_format.0, KEY_PRIORITY.as_ptr(), priority);
        }
        if let Some(rate) = hints.operating_rate {
            ffi::AMediaFormat_setFloat(track_format.0, KEY_OPERATING_RATE.as_ptr(), rate);
        }
        check(
            "AMediaCodec_configure",
            ffi::AMediaCodec_configure(
                codec.raw,
                track_format.0,
                surface.unwrap_or(ptr::null_mut()),
                ptr::null_mut(),
                0,
            ),
        )?;
        check("AMediaCodec_start", ffi::AMediaCodec_start(codec.raw))?;
    }
    codec.started = true;
    Ok(codec)
}

/// What the started codec reports back for the hint keys. Codecs need not echo hints, so an
/// absent key is recorded as `null`, not as "ignored".
pub(crate) fn read_hints(codec: &Codec) -> Result<HintReadback, DecodeError> {
    let rate = |format: &Format| {
        format
            .float(KEY_OPERATING_RATE.as_ptr())
            .or_else(|| format.int32(KEY_OPERATING_RATE.as_ptr()).map(|v| v as f32))
    };
    // SAFETY: codec is started; returned formats are owned and deleted by `Format`.
    let input = Format::non_null("AMediaCodec_getInputFormat", unsafe {
        ffi::AMediaCodec_getInputFormat(codec.raw)
    })?;
    let output = Format::non_null("AMediaCodec_getOutputFormat", unsafe {
        ffi::AMediaCodec_getOutputFormat(codec.raw)
    })?;
    Ok(HintReadback {
        input_priority: input.int32(KEY_PRIORITY.as_ptr()),
        input_operating_rate: rate(&input),
        output_priority: output.int32(KEY_PRIORITY.as_ptr()),
        output_operating_rate: rate(&output),
    })
}

/// Surface output: the codec format gives only the range; geometry comes from each `AImage`.
pub(crate) fn read_surface_output(
    codec: &Codec,
    decoder_name: &str,
    assume_range: Option<LumaRange>,
) -> Result<OutputInfo, DecodeError> {
    // SAFETY: codec is started; the returned format is owned and deleted by `Format`.
    let format = Format::non_null("AMediaCodec_getOutputFormat", unsafe {
        ffi::AMediaCodec_getOutputFormat(codec.raw)
    })?;
    let color_range_raw = format.int32(KEY_COLOR_RANGE.as_ptr());
    let (luma_range, range_source) = resolve_range(color_range_raw, assume_range)?;
    Ok(OutputInfo {
        decoder_name: decoder_name.to_owned(),
        color_format: IMAGE_FORMAT_YUV_420_888,
        color_range_raw,
        luma_range,
        range_source,
        stride_source: GeometrySource::Image,
        crop_source: GeometrySource::Image,
        y_offset: 0,
        // Replaced by each image's own plane geometry before use.
        layout: PlaneLayout {
            stride: 0,
            slice_height: 0,
            crop: CropRect::full(0, 0),
        },
    })
}

fn resolve_range(
    color_range_raw: Option<i32>,
    assume_range: Option<LumaRange>,
) -> Result<(LumaRange, RangeSource), DecodeError> {
    Ok(match LumaRange::from_android(color_range_raw) {
        Some(range) => (range, RangeSource::Decoder),
        None => (
            assume_range.ok_or(DecodeError::UnknownColorRange)?,
            RangeSource::Assumed,
        ),
    })
}

pub(crate) fn read_output_info(
    codec: &Codec,
    decoder_name: &str,
    assume_range: Option<LumaRange>,
) -> Result<OutputInfo, DecodeError> {
    // SAFETY: codec is started; the returned format is owned and deleted by `Format`.
    let format = Format::non_null("AMediaCodec_getOutputFormat", unsafe {
        ffi::AMediaCodec_getOutputFormat(codec.raw)
    })?;
    let color_format = format
        .int32(KEY_COLOR_FORMAT.as_ptr())
        .ok_or(DecodeError::Unsupported(
            "output format has no color-format",
        ))?;
    // Flexible YUV in ByteBuffer mode carries its exact layout in `image-data`; anything else
    // must be a fixed planar/semi-planar format whose Y plane starts the buffer.
    let image_plane = match color_format {
        COLOR_FORMAT_YUV420_PLANAR | COLOR_FORMAT_YUV420_SEMIPLANAR => None,
        COLOR_FORMAT_YUV420_FLEXIBLE => Some(
            parse_luma_plane(&format.buffer(KEY_IMAGE_DATA.as_ptr()).ok_or(
                DecodeError::UnsupportedColorFormat {
                    value: color_format,
                },
            )?)
            .map_err(DecodeError::ImageLayout)?,
        ),
        _ => {
            return Err(DecodeError::UnsupportedColorFormat {
                value: color_format,
            })
        }
    };
    let width = format
        .int32(KEY_WIDTH.as_ptr())
        .and_then(|v| usize::try_from(v).ok())
        .ok_or(DecodeError::Unsupported("output format has no width"))?;
    let height = format
        .int32(KEY_HEIGHT.as_ptr())
        .and_then(|v| usize::try_from(v).ok())
        .ok_or(DecodeError::Unsupported("output format has no height"))?;
    let positive = |key| {
        format
            .int32(key)
            .and_then(|v| usize::try_from(v).ok())
            .filter(|&v| v > 0)
    };
    // Fallbacks are recorded, never silent: a decoder that pads height to 1088 without a crop
    // shows up in the report and as a dimension mismatch in parity.
    let (stride, slice_height, stride_source, y_offset) = match image_plane {
        Some(plane) => (
            Some(plane.row_stride),
            Some(plane.height_px),
            GeometrySource::ImageData,
            plane.offset,
        ),
        None => {
            let stride = positive(KEY_STRIDE.as_ptr());
            let slice_height = positive(KEY_SLICE_HEIGHT.as_ptr());
            let source = if stride.is_some() && slice_height.is_some() {
                GeometrySource::Decoder
            } else {
                GeometrySource::Fallback
            };
            (stride, slice_height, source, 0)
        }
    };
    let crop = format.crop()?;
    let crop_source = if crop.is_some() {
        GeometrySource::Decoder
    } else {
        GeometrySource::Fallback
    };
    let stride = stride.unwrap_or(width);
    let slice_height = slice_height.unwrap_or(height);
    let crop = crop.unwrap_or_else(|| CropRect::full(width, height));

    let color_range_raw = format.int32(KEY_COLOR_RANGE.as_ptr());
    let (luma_range, range_source) = resolve_range(color_range_raw, assume_range)?;
    Ok(OutputInfo {
        decoder_name: decoder_name.to_owned(),
        color_format,
        color_range_raw,
        luma_range,
        range_source,
        stride_source,
        crop_source,
        y_offset,
        layout: PlaneLayout {
            stride,
            slice_height,
            crop,
        },
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum InputStep {
    Fed,
    NoFreeBuffer,
    EndOfStream,
}

/// Feeds one compressed sample (or EOS) if an input buffer is free.
pub(crate) fn feed_input(codec: &Codec, extractor: &Extractor) -> Result<InputStep, DecodeError> {
    // SAFETY: codec and extractor are live; buffer pointer/capacity come from the codec.
    unsafe {
        let index = ffi::AMediaCodec_dequeueInputBuffer(codec.raw, INPUT_TIMEOUT_US);
        if index < 0 {
            return Ok(InputStep::NoFreeBuffer);
        }
        let index = index as usize;
        let mut capacity = 0usize;
        let buffer = ffi::AMediaCodec_getInputBuffer(codec.raw, index, &mut capacity);
        if buffer.is_null() {
            return Err(DecodeError::Media {
                call: "AMediaCodec_getInputBuffer",
                status: -1,
            });
        }
        let size = ffi::AMediaExtractor_readSampleData(extractor.0, buffer, capacity);
        if size < 0 {
            // Only a genuine end of track becomes EOS; a read failure must not truncate silently.
            if ffi::AMediaExtractor_getSampleTrackIndex(extractor.0) != -1 {
                return Err(DecodeError::ExtractorReadFailed {
                    sample_time_us: ffi::AMediaExtractor_getSampleTime(extractor.0),
                });
            }
            check(
                "AMediaCodec_queueInputBuffer(eos)",
                ffi::AMediaCodec_queueInputBuffer(
                    codec.raw,
                    index,
                    0,
                    0,
                    0,
                    ffi::AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM,
                ),
            )?;
            return Ok(InputStep::EndOfStream);
        }
        let time_us = ffi::AMediaExtractor_getSampleTime(extractor.0);
        if time_us < 0 {
            return Err(DecodeError::NegativeSampleTime { time_us });
        }
        check(
            "AMediaCodec_queueInputBuffer",
            ffi::AMediaCodec_queueInputBuffer(
                codec.raw,
                index,
                0,
                size as usize,
                time_us as u64,
                0,
            ),
        )?;
        ffi::AMediaExtractor_advance(extractor.0);
        Ok(InputStep::Fed)
    }
}

/// Earliest presentation time in the first GOP: the stream origin, matching FFmpeg's
/// `start_pts`, so timestamps are `(pts - origin)` exactly as in `openbar-cli` `probe.rs`.
/// Reads sample metadata only (no decoding) and leaves the extractor to be re-seeked.
pub(crate) fn stream_origin_us(extractor: &Extractor) -> Result<i64, DecodeError> {
    let mut origin: Option<i64> = None;
    let mut sync_samples = 0;
    for _ in 0..ORIGIN_SCAN_LIMIT {
        // SAFETY: extractor is live with a selected track.
        let (track, time_us, flags) = unsafe {
            (
                ffi::AMediaExtractor_getSampleTrackIndex(extractor.0),
                ffi::AMediaExtractor_getSampleTime(extractor.0),
                ffi::AMediaExtractor_getSampleFlags(extractor.0),
            )
        };
        if track == -1 {
            break;
        }
        if time_us < 0 {
            return Err(DecodeError::NegativeSampleTime { time_us });
        }
        if flags & ffi::AMEDIAEXTRACTOR_SAMPLE_FLAG_SYNC != 0 {
            sync_samples += 1;
            if sync_samples > 1 {
                break;
            }
        }
        origin = Some(origin.map_or(time_us, |o| o.min(time_us)));
        // SAFETY: as above.
        if !unsafe { ffi::AMediaExtractor_advance(extractor.0) } {
            break;
        }
    }
    origin.ok_or(DecodeError::Unsupported("video track has no samples"))
}
