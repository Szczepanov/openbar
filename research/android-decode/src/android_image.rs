//! `AImageReader` Surface output: the decoder renders into a YUV_420_888 reader and the luma plane
//! is read with `AImage_getPlaneData`.
//!
//! Each image is paired with the buffer that was rendered for it by timestamp: MediaCodec stamps a
//! Surface buffer released with `render = true` at `presentationTimeUs * 1000` ns. Images must come
//! back one per rendered buffer and in order; anything else is refused as a drop or reorder.

use crate::decode::DecodeError;
use crate::raster::{CropRect, PlaneLayout};
use ndk_sys as ffi;
use std::ptr;

/// Images the reader lets us hold at once. We hold one at a time (copy, then delete); the extra
/// slots keep the codec from waiting on a free Surface buffer while we copy.
const MAX_IMAGES: i32 = 4;
pub const IMAGE_FORMAT_YUV_420_888: i32 = ffi::AIMAGE_FORMATS::AIMAGE_FORMAT_YUV_420_888.0 as i32;

fn check(call: &'static str, status: ffi::media_status_t) -> Result<(), DecodeError> {
    if status == ffi::media_status_t::AMEDIA_OK {
        Ok(())
    } else {
        Err(DecodeError::Media {
            call,
            status: i64::from(status.0),
        })
    }
}

pub struct ImageReader {
    raw: *mut ffi::AImageReader,
    /// Owned by the reader; valid until `AImageReader_delete`.
    window: *mut ffi::ANativeWindow,
}

impl Drop for ImageReader {
    fn drop(&mut self) {
        // SAFETY: created by AImageReader_new and deleted exactly once; this also releases the
        // window. The codec using the window must be dropped first (see `decode`).
        unsafe { ffi::AImageReader_delete(self.raw) };
    }
}

impl ImageReader {
    pub fn new(width_px: u32, height_px: u32) -> Result<Self, DecodeError> {
        let (width, height) = (
            i32::try_from(width_px).map_err(|_| DecodeError::Unsupported("width exceeds i32"))?,
            i32::try_from(height_px).map_err(|_| DecodeError::Unsupported("height exceeds i32"))?,
        );
        let mut raw = ptr::null_mut();
        // SAFETY: out-pointer is valid; the reader is owned by `ImageReader` once non-null.
        check("AImageReader_new", unsafe {
            ffi::AImageReader_new(
                width,
                height,
                IMAGE_FORMAT_YUV_420_888,
                MAX_IMAGES,
                &mut raw,
            )
        })?;
        let mut reader = Self {
            raw,
            window: ptr::null_mut(),
        };
        // SAFETY: reader is live; the window stays owned by the reader.
        check("AImageReader_getWindow", unsafe {
            ffi::AImageReader_getWindow(reader.raw, &mut reader.window)
        })?;
        if reader.window.is_null() {
            return Err(DecodeError::Media {
                call: "AImageReader_getWindow",
                status: -1,
            });
        }
        Ok(reader)
    }

    pub fn window(&self) -> *mut ffi::ANativeWindow {
        self.window
    }

    /// The next queued image, or `None` when the decoder has not delivered one yet.
    pub fn try_acquire(&self) -> Result<Option<Image>, DecodeError> {
        let mut raw = ptr::null_mut();
        // SAFETY: reader is live; a returned image is owned by `Image`.
        let status = unsafe { ffi::AImageReader_acquireNextImage(self.raw, &mut raw) };
        if status == ffi::media_status_t::AMEDIA_IMGREADER_NO_BUFFER_AVAILABLE {
            return Ok(None);
        }
        check("AImageReader_acquireNextImage", status)?;
        if raw.is_null() {
            return Err(DecodeError::Media {
                call: "AImageReader_acquireNextImage",
                status: -1,
            });
        }
        Ok(Some(Image(raw)))
    }
}

pub struct Image(*mut ffi::AImage);

impl Drop for Image {
    fn drop(&mut self) {
        // SAFETY: acquired from the reader and deleted exactly once, returning it to the reader.
        unsafe { ffi::AImage_delete(self.0) };
    }
}

impl Image {
    fn int(
        &self,
        call: &'static str,
        get: unsafe extern "C" fn(*const ffi::AImage, *mut i32) -> ffi::media_status_t,
    ) -> Result<i32, DecodeError> {
        let mut value = 0;
        // SAFETY: image is live; value is a valid out-pointer.
        check(call, unsafe { get(self.0, &mut value) })?;
        Ok(value)
    }

    pub fn timestamp_ns(&self) -> Result<i64, DecodeError> {
        let mut value = 0;
        // SAFETY: as above.
        check("AImage_getTimestamp", unsafe {
            ffi::AImage_getTimestamp(self.0, &mut value)
        })?;
        Ok(value)
    }

    /// Copies the luma plane (crop and stride untouched) and returns it with its geometry.
    /// Fails closed unless the image is YUV_420_888 with unit luma pixel stride.
    pub fn copy_luma(&self) -> Result<(Vec<u8>, PlaneLayout), DecodeError> {
        let format = self.int("AImage_getFormat", ffi::AImage_getFormat)?;
        if format != IMAGE_FORMAT_YUV_420_888 {
            return Err(DecodeError::UnsupportedColorFormat { value: format });
        }
        let height = self.int("AImage_getHeight", ffi::AImage_getHeight)?;
        let (mut pixel_stride, mut row_stride) = (0, 0);
        let mut rect = ffi::AImageCropRect {
            left: 0,
            top: 0,
            right: 0,
            bottom: 0,
        };
        let (mut data, mut length) = (ptr::null_mut(), 0);
        // SAFETY: image is live; all out-pointers are valid. Plane data stays valid until the
        // image is deleted, which cannot happen while `self` is borrowed.
        unsafe {
            check(
                "AImage_getPlanePixelStride",
                ffi::AImage_getPlanePixelStride(self.0, 0, &mut pixel_stride),
            )?;
            check(
                "AImage_getPlaneRowStride",
                ffi::AImage_getPlaneRowStride(self.0, 0, &mut row_stride),
            )?;
            check(
                "AImage_getCropRect",
                ffi::AImage_getCropRect(self.0, &mut rect),
            )?;
            check(
                "AImage_getPlaneData",
                ffi::AImage_getPlaneData(self.0, 0, &mut data, &mut length),
            )?;
        }
        if pixel_stride != 1 {
            return Err(DecodeError::Unsupported(
                "AImage luma plane is not packed (pixel stride != 1)",
            ));
        }
        if data.is_null() || length <= 0 || row_stride <= 0 || height <= 0 {
            return Err(DecodeError::Unsupported("AImage luma plane is empty"));
        }
        // AImageCropRect right/bottom are exclusive; CropRect is inclusive like MediaFormat crop.
        let crop =
            CropRect::try_from_android([rect.left, rect.top, rect.right - 1, rect.bottom - 1])?;
        let layout = PlaneLayout {
            stride: row_stride as usize,
            slice_height: height as usize,
            crop,
        };
        // SAFETY: AImage_getPlaneData reported `length` readable bytes at `data`.
        let plane = unsafe { std::slice::from_raw_parts(data as *const u8, length as usize) };
        Ok((plane[..layout.extent().min(plane.len())].to_vec(), layout))
    }
}
