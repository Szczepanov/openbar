//! Parse the `image-data` (`MediaImage2`) blob a decoder attaches to a flexible-YUV output format.
//!
//! With `COLOR_FormatYUV420Flexible` in ByteBuffer mode the buffer layout is not implied by the
//! colour format; Codec2 decoders describe it exactly in `image-data`. Only the luma plane is
//! needed, and it is accepted only when it is plain 8-bit samples with unit column increment.
//! Layout (frameworks/native `HardwareAPI.h`, all fields 32-bit little-endian):
//! `mType, mNumPlanes, mWidth, mHeight, mBitDepth, mBitDepthAllocated`, then per plane
//! `mOffset, mColInc, mRowInc, mHorizSubsampling, mVertSubsampling`.

use std::fmt;

const MEDIA_IMAGE_TYPE_YUV: u32 = 1;
const HEADER_FIELDS: usize = 6;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImageLayoutError(pub String);

impl fmt::Display for ImageLayoutError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "unsupported image-data layout: {}", self.0)
    }
}

impl std::error::Error for ImageLayoutError {}

/// Luma plane geometry from `MediaImage2`, in bytes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LumaPlane {
    pub offset: usize,
    pub row_stride: usize,
    pub width_px: usize,
    pub height_px: usize,
}

pub fn parse_luma_plane(blob: &[u8]) -> Result<LumaPlane, ImageLayoutError> {
    let word = |index: usize| -> Result<u32, ImageLayoutError> {
        blob.get(index * 4..index * 4 + 4)
            .map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
            .ok_or_else(|| ImageLayoutError(format!("{} bytes is too short", blob.len())))
    };
    let (kind, planes, width, height, depth, allocated) =
        (word(0)?, word(1)?, word(2)?, word(3)?, word(4)?, word(5)?);
    let y = HEADER_FIELDS;
    let (offset, col_inc, row_inc, h_sub, v_sub) = (
        word(y)?,
        word(y + 1)? as i32,
        word(y + 2)? as i32,
        word(y + 3)?,
        word(y + 4)?,
    );
    let reject = |what: String| Err(ImageLayoutError(what));
    if kind != MEDIA_IMAGE_TYPE_YUV {
        return reject(format!("type {kind} is not YUV"));
    }
    if planes < 3 {
        return reject(format!("{planes} planes"));
    }
    if depth != 8 || allocated != 8 {
        return reject(format!("bit depth {depth}/{allocated} is not 8/8"));
    }
    if col_inc != 1 || h_sub != 1 || v_sub != 1 {
        return reject(format!(
            "luma col_inc {col_inc}, subsampling {h_sub}x{v_sub} is not packed full resolution"
        ));
    }
    if width == 0 || height == 0 || row_inc < width as i32 {
        return reject(format!("luma row_inc {row_inc} for {width}x{height}"));
    }
    Ok(LumaPlane {
        offset: offset as usize,
        row_stride: row_inc as usize,
        width_px: width as usize,
        height_px: height as usize,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn blob(words: &[u32]) -> Vec<u8> {
        words.iter().flat_map(|w| w.to_le_bytes()).collect()
    }

    // NV12 1280x720 with a 1280 stride and 736-row slice: Y at 0, UV interleaved after it.
    fn nv12() -> Vec<u32> {
        vec![
            1, 3, 1280, 720, 8, 8, // header
            0, 1, 1280, 1, 1, // Y
            942_080, 2, 1280, 2, 2, // U
            942_081, 2, 1280, 2, 2, // V
        ]
    }

    #[test]
    fn parses_packed_luma_plane() {
        assert_eq!(
            parse_luma_plane(&blob(&nv12())),
            Ok(LumaPlane {
                offset: 0,
                row_stride: 1280,
                width_px: 1280,
                height_px: 720,
            })
        );
    }

    #[test]
    fn rejects_non_yuv_deep_or_strided_luma_and_short_blobs() {
        let cases: Vec<(usize, u32)> = vec![(0, 2), (4, 10), (5, 16), (7, 2), (8, 640), (9, 2)];
        for (index, value) in cases {
            let mut words = nv12();
            words[index] = value;
            assert!(
                parse_luma_plane(&blob(&words)).is_err(),
                "word {index} = {value} should be rejected"
            );
        }
        assert!(parse_luma_plane(&blob(&nv12()[..8])).is_err());
    }
}
