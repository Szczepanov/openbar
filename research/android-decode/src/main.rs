//! `openbar-android-decode`: research prototype for #258.
//!
//! Decodes a video window in-process with Android `AMediaCodec` and writes a deterministic report
//! (frame count, PTS, geometry, decoder identity) plus stage timings. `--dump-gray` writes the
//! packed display-oriented luma frames in FFmpeg `-f rawvideo -pix_fmt gray` layout so
//! `compare.py` can check parity with the ADR-0006 FFmpeg path.

// On host builds the decoder module is compiled out, so its consumers of the shared types are too.
#![cfg_attr(not(target_os = "android"), allow(dead_code))]

#[cfg(target_os = "android")]
mod android;
mod decode;
mod image_layout;
mod raster;
mod timing;

use raster::LumaRange;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const USAGE: &str = "usage: openbar-android-decode --input <video> --report <json> \
[--start-s <s>] [--end-s <s>] [--dump-gray <raw>] [--assume-range full|limited]";

#[derive(Debug, PartialEq)]
struct Args {
    input: PathBuf,
    report: PathBuf,
    start_s: Option<f64>,
    end_s: Option<f64>,
    dump_gray: Option<PathBuf>,
    assume_range: Option<LumaRange>,
}

fn parse_args(raw: &[String]) -> Result<Args, String> {
    let mut input = None;
    let mut report = None;
    let mut start_s = None;
    let mut end_s = None;
    let mut dump_gray = None;
    let mut assume_range = None;
    let mut iter = raw.iter();
    while let Some(flag) = iter.next() {
        let mut value = || {
            iter.next()
                .cloned()
                .ok_or_else(|| format!("{flag} needs a value"))
        };
        let seconds = |text: String| {
            text.parse::<f64>()
                .map_err(|_| format!("{flag} expects seconds, got {text:?}"))
        };
        match flag.as_str() {
            "--input" => input = Some(PathBuf::from(value()?)),
            "--report" => report = Some(PathBuf::from(value()?)),
            "--start-s" => start_s = Some(seconds(value()?)?),
            "--end-s" => end_s = Some(seconds(value()?)?),
            "--dump-gray" => dump_gray = Some(PathBuf::from(value()?)),
            "--assume-range" => {
                assume_range = Some(match value()?.as_str() {
                    "full" => LumaRange::Full,
                    "limited" => LumaRange::Limited,
                    other => {
                        return Err(format!(
                            "--assume-range expects full|limited, got {other:?}"
                        ))
                    }
                })
            }
            other => return Err(format!("unknown argument {other:?}")),
        }
    }
    Ok(Args {
        input: input.ok_or("--input is required")?,
        report: report.ok_or("--report is required")?,
        start_s,
        end_s,
        dump_gray,
        assume_range,
    })
}

fn validate_output_paths(input: &Path, report: &Path, dump: Option<&Path>) -> std::io::Result<()> {
    let paths = [Some(input), Some(report), dump]
        .into_iter()
        .flatten()
        .map(|path| match path.canonicalize() {
            Ok(resolved) => Ok((resolved, Some(std::fs::metadata(path)?))),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                if path
                    .symlink_metadata()
                    .is_ok_and(|metadata| metadata.is_symlink())
                {
                    return Err(error);
                }
                let parent = path
                    .parent()
                    .filter(|p| !p.as_os_str().is_empty())
                    .unwrap_or(Path::new("."));
                Ok((
                    parent.canonicalize()?.join(path.file_name().ok_or(error)?),
                    None,
                ))
            }
            Err(error) => Err(error),
        })
        .collect::<std::io::Result<Vec<_>>>()?;
    for (index, (path, metadata)) in paths.iter().enumerate() {
        for (other, other_metadata) in &paths[index + 1..] {
            #[cfg(unix)]
            let same_file = {
                use std::os::unix::fs::MetadataExt;
                metadata
                    .as_ref()
                    .zip(other_metadata.as_ref())
                    .is_some_and(|(a, b)| a.dev() == b.dev() && a.ino() == b.ino())
            };
            #[cfg(not(unix))]
            let same_file = {
                let _ = (metadata, other_metadata);
                false
            };
            if path == other || same_file {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidInput,
                    "input, report and gray dump must be distinct files",
                ));
            }
        }
    }
    Ok(())
}

#[cfg(target_os = "android")]
fn run(args: Args) -> Result<(), Box<dyn std::error::Error>> {
    use std::fs::File;
    use std::io::{BufWriter, Write};

    let window = timing::Window::try_new(args.start_s, args.end_s)?;
    validate_output_paths(&args.input, &args.report, args.dump_gray.as_deref())?;
    let options = android::DecodeOptions {
        window,
        assume_range: args.assume_range,
    };
    let mut dump = args
        .dump_gray
        .as_ref()
        .map(|path| File::create(path).map(BufWriter::new))
        .transpose()?;
    let report = android::decode(&args.input, &options, |frame| {
        if let Some(writer) = dump.as_mut() {
            writer.write_all(&frame.pixels)?;
        }
        Ok(())
    })?;
    if let Some(mut writer) = dump {
        writer.flush()?;
    }
    std::fs::write(&args.report, report.to_json())?;
    eprintln!(
        "{} frames {}x{} via {} in {:.3} s",
        report.timestamps_s.len(),
        report.display_width_px,
        report.display_height_px,
        report.output.decoder_name,
        report.timings.total_s
    );
    Ok(())
}

#[cfg(not(target_os = "android"))]
fn run(_args: Args) -> Result<(), Box<dyn std::error::Error>> {
    Err(Box::new(decode::DecodeError::Unsupported(
        "AMediaCodec decoding needs an Android build (target_os = \"android\")",
    )))
}

fn main() -> ExitCode {
    let raw: Vec<String> = std::env::args().skip(1).collect();
    let args = match parse_args(&raw) {
        Ok(args) => args,
        Err(message) => {
            eprintln!("error: {message}\n{USAGE}");
            return ExitCode::from(2);
        }
    };
    match run(args) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("error: {error}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strings(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| (*s).to_owned()).collect()
    }

    #[test]
    fn output_paths_cannot_alias_input_or_each_other() {
        let root =
            std::env::temp_dir().join(format!("openbar-decode-paths-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let input = root.join("input.mp4");
        let report = root.join("report.json");
        let dump = root.join("dump.gray");
        std::fs::write(&input, b"original clip").unwrap();
        assert!(validate_output_paths(&input, &report, Some(&dump)).is_ok());
        assert!(validate_output_paths(&input, &input, None).is_err());
        assert!(validate_output_paths(&input, &report, Some(&input)).is_err());
        assert!(validate_output_paths(&input, &report, Some(&report)).is_err());
        #[cfg(unix)]
        {
            let link = root.join("input-link.mp4");
            std::fs::hard_link(&input, &link).unwrap();
            assert!(validate_output_paths(&input, &report, Some(&link)).is_err());
            std::fs::remove_file(link).unwrap();
            std::os::unix::fs::symlink(&report, &dump).unwrap();
            assert!(validate_output_paths(&input, &report, Some(&dump)).is_err());
            std::fs::remove_file(&dump).unwrap();
        }
        assert_eq!(std::fs::read(&input).unwrap(), b"original clip");
        std::fs::remove_file(input).unwrap();
        std::fs::remove_dir(root).unwrap();
    }

    #[test]
    fn parses_full_argument_set() {
        let args = parse_args(&strings(&[
            "--input",
            "clip.mp4",
            "--report",
            "r.json",
            "--start-s",
            "1.5",
            "--end-s",
            "7.25",
            "--dump-gray",
            "f.gray",
            "--assume-range",
            "limited",
        ]))
        .unwrap();
        assert_eq!(
            args,
            Args {
                input: PathBuf::from("clip.mp4"),
                report: PathBuf::from("r.json"),
                start_s: Some(1.5),
                end_s: Some(7.25),
                dump_gray: Some(PathBuf::from("f.gray")),
                assume_range: Some(LumaRange::Limited),
            }
        );
    }

    #[test]
    fn rejects_missing_unknown_and_malformed_arguments() {
        assert_eq!(
            parse_args(&strings(&["--report", "r.json"])),
            Err("--input is required".to_owned())
        );
        assert!(parse_args(&strings(&["--bogus"])).is_err());
        assert!(parse_args(&strings(&["--input"])).is_err());
        assert!(parse_args(&strings(&[
            "--input",
            "a",
            "--report",
            "b",
            "--start-s",
            "x"
        ]))
        .is_err());
        assert!(parse_args(&strings(&[
            "--input",
            "a",
            "--report",
            "b",
            "--assume-range",
            "tv"
        ]))
        .is_err());
    }
}
