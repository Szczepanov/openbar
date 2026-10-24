//! External `ffprobe` / `ffmpeg` processes (ADR-0006). FFmpeg is invoked, never linked.

use super::luma::LumaTransform;
use super::probe::{VideoProbe, PROBE_ARGS};
use super::raw::{read_gray_frames, RawFrameLayout};
use super::{DecodedFrame, MediaError, TimeRange};
use std::io::{BufReader, Read};
use std::path::Path;
use std::process::{Command, Stdio};
use std::thread;

pub const FFPROBE: &str = "ffprobe";
pub const FFMPEG: &str = "ffmpeg";

/// Placeholder recorded in provenance instead of the machine-specific input path.
pub const INPUT_PLACEHOLDER: &str = "<input>";

const DECODE_INPUT_ARGS: [&str; 6] = [
    "-nostdin",
    "-hide_banner",
    "-v",
    "error",
    // Explicit although it is the default: display orientation is part of the contract.
    "-autorotate",
    "-i",
];

const DECODE_MAP_ARGS: [&str; 6] = [
    "-map",
    "0:v:0",
    "-fps_mode",
    "passthrough",
    "-enc_time_base",
    "demux",
];

/// After the luma filter, which already yields 8-bit gray, so no colour conversion remains.
const DECODE_FORMAT_ARGS: [&str; 5] = ["-pix_fmt", "gray", "-f", "rawvideo", "-"];

/// Output arguments: gray is an explicit function of the Y plane (ADR-0006, Amendment 1).
fn decode_output_args(transform: LumaTransform) -> impl Iterator<Item = &'static str> {
    DECODE_MAP_ARGS
        .into_iter()
        .chain(["-vf", transform.filter()])
        .chain(DECODE_FORMAT_ARGS)
}

pub fn probe_args_for_provenance() -> Vec<String> {
    PROBE_ARGS
        .iter()
        .copied()
        .chain(["-i", INPUT_PLACEHOLDER])
        .map(str::to_owned)
        .collect()
}

pub fn decode_args_for_provenance(transform: LumaTransform) -> Vec<String> {
    DECODE_INPUT_ARGS
        .iter()
        .copied()
        .chain([INPUT_PLACEHOLDER])
        .chain(decode_output_args(transform))
        .map(str::to_owned)
        .collect()
}

/// First line of `<tool> -version`, which identifies the exact FFmpeg build.
pub fn tool_version(tool: &'static str) -> Result<String, MediaError> {
    let output = Command::new(tool)
        .args(["-hide_banner", "-version"])
        .stdin(Stdio::null())
        .output()
        .map_err(|error| MediaError::ToolUnavailable {
            tool,
            detail: error.to_string(),
        })?;
    if !output.status.success() {
        return Err(MediaError::ToolFailed {
            tool,
            status: output.status.to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        });
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    Ok(stdout.lines().next().unwrap_or_default().trim().to_owned())
}

pub fn probe(path: &Path) -> Result<VideoProbe, MediaError> {
    // `-i` keeps a path that starts with '-' from being parsed as an option.
    let output = Command::new(FFPROBE)
        .args(PROBE_ARGS)
        .arg("-i")
        .arg(path)
        .stdin(Stdio::null())
        .output()
        .map_err(|error| MediaError::ToolUnavailable {
            tool: FFPROBE,
            detail: error.to_string(),
        })?;
    if !output.status.success() {
        return Err(MediaError::ToolFailed {
            tool: FFPROBE,
            status: output.status.to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        });
    }
    let stdout =
        String::from_utf8(output.stdout).map_err(|error| MediaError::InvalidProbeOutput {
            detail: error.to_string(),
        })?;
    VideoProbe::parse(&stdout)
}

/// Decodes every frame, keeps the selected ones, and returns them with ffmpeg's stderr lines.
pub fn decode(
    path: &Path,
    layout: &RawFrameLayout,
    timestamps_s: &[f64],
    selection: Option<TimeRange>,
    transform: LumaTransform,
) -> Result<(Vec<DecodedFrame>, Vec<String>), MediaError> {
    let mut child = Command::new(FFMPEG)
        .args(DECODE_INPUT_ARGS)
        .arg(path)
        .args(decode_output_args(transform))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| MediaError::ToolUnavailable {
            tool: FFMPEG,
            detail: error.to_string(),
        })?;

    // Drain stderr concurrently so a chatty decoder cannot block on a full pipe. Bytes, not
    // UTF-8: a non-UTF-8 message must not stop the draining or lose the explanation.
    let mut stderr = child.stderr.take().expect("stderr is piped");
    let stderr_reader = thread::spawn(move || {
        let mut bytes = Vec::new();
        let _ = stderr.read_to_end(&mut bytes);
        String::from_utf8_lossy(&bytes).into_owned()
    });

    let stdout = child.stdout.take().expect("stdout is piped");
    let read_result = read_gray_frames(
        BufReader::with_capacity(1 << 20, stdout),
        layout,
        timestamps_s,
        selection,
    );
    // Count and truncation errors are found at end of stream, when ffmpeg has stopped writing.
    // Only an error raised while frames are still flowing requires stopping the decoder.
    let killed = matches!(
        read_result,
        Err(MediaError::Io { .. } | MediaError::InvalidFrame { .. })
    ) && child.kill().is_ok();

    let status = child.wait().map_err(|error| MediaError::Io {
        context: "failed to wait for ffmpeg".into(),
        detail: error.to_string(),
    })?;
    let stderr_text = stderr_reader.join().unwrap_or_default();

    // A decoder that failed on its own explains any truncation or frame-count symptom.
    if !killed && !status.success() {
        return Err(MediaError::ToolFailed {
            tool: FFMPEG,
            status: status.to_string(),
            stderr: stderr_text,
        });
    }
    match read_result {
        Err(error) if !stderr_text.trim().is_empty() => Err(MediaError::WithDecoderOutput {
            error: Box::new(error),
            stderr: stderr_text,
        }),
        Err(error) => Err(error),
        Ok(frames) => Ok((frames, normalized_diagnostics(&stderr_text, path))),
    }
}

/// ffmpeg log lines carry per-run context pointers (`[h264 @ 0x55d0...]`) and sometimes the
/// input path; both are removed so recorded diagnostics stay reproducible and machine-neutral.
fn normalized_diagnostics(stderr_text: &str, path: &Path) -> Vec<String> {
    let path_text = path.display().to_string();
    stderr_text
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(|line| strip_context_pointers(&line.replace(&path_text, INPUT_PLACEHOLDER)))
        .collect()
}

fn strip_context_pointers(line: &str) -> String {
    const MARKER: &str = " @ 0x";
    let mut result = String::with_capacity(line.len());
    let mut rest = line;
    while let Some(position) = rest.find(MARKER) {
        result.push_str(&rest[..position]);
        let after = &rest[position + MARKER.len()..];
        let hex_len = after
            .find(|character: char| !character.is_ascii_hexdigit())
            .unwrap_or(after.len());
        if hex_len == 0 {
            result.push_str(MARKER);
        }
        rest = &after[hex_len..];
    }
    result.push_str(rest);
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_per_run_context_pointers_only() {
        assert_eq!(
            strip_context_pointers("[h264 @ 0x55d0a1b2c3d0] concealing 12 errors"),
            "[h264] concealing 12 errors"
        );
        assert_eq!(
            strip_context_pointers("[a @ 0xff] [b @ 0x1] x"),
            "[a] [b] x"
        );
        assert_eq!(
            strip_context_pointers("not a pointer @ 0xzz and @ 0x"),
            "not a pointer @ 0xzz and @ 0x"
        );
        assert_eq!(strip_context_pointers("plain line"), "plain line");
    }

    #[test]
    fn normalized_diagnostics_hide_input_path_and_drop_blank_lines() {
        let path = Path::new("/data/clip.mp4");
        assert_eq!(
            normalized_diagnostics(
                "
[mov,mp4 @ 0x1a2b] /data/clip.mp4: moov atom late 

  second  
",
                path
            ),
            vec!["[mov,mp4] <input>: moov atom late", "second"]
        );
    }

    #[test]
    fn recorded_arguments_place_the_input_where_it_is_passed() {
        assert_eq!(
            probe_args_for_provenance()[PROBE_ARGS.len()..],
            ["-i", INPUT_PLACEHOLDER]
        );
        let decode = decode_args_for_provenance(LumaTransform::LimitedToFull);
        let input = decode
            .iter()
            .position(|arg| arg == INPUT_PLACEHOLDER)
            .expect("placeholder recorded");
        assert_eq!(decode[input - 1], "-i");
        assert_eq!(decode.last().map(String::as_str), Some("-"));
    }
}
