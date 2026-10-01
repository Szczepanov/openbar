//! Stable render integration boundary for issue #12.
//!
//! The renderer itself belongs to issue #13. This command deliberately validates and consumes
//! canonical analysis JSON, then fails explicitly rather than recomputing measurement data or
//! creating a placeholder artifact.

use crate::cli_error::{CliError, CliResult};
use openbar_core::analysis::Analysis;
use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::PathBuf;

const USAGE: &str =
    "Usage: openbar-cli render --analysis <analysis.json> --output <diagnostic-artifact>\n\
     Validates canonical analysis-v1 input. Diagnostic rendering is implemented by issue #13.";

pub fn run_cli() -> CliResult<()> {
    let Some((analysis_path, output_path)) = parse_args(env::args().skip(2).collect())? else {
        println!("{USAGE}");
        return Ok(());
    };

    let content = fs::read_to_string(&analysis_path).map_err(|error| {
        CliError::invalid_input(format!(
            "failed to read canonical analysis '{}': {error}",
            analysis_path.display()
        ))
    })?;
    Analysis::from_json(&content).map_err(|error| {
        CliError::invalid_input(format!(
            "failed to validate canonical analysis '{}': {error}",
            analysis_path.display()
        ))
    })?;

    Err(CliError::render_unavailable(format!(
        "canonical analysis '{}' is valid, but diagnostic rendering is not implemented yet; issue #13 owns the renderer boundary and '{}' was not created",
        analysis_path.display(),
        output_path.display()
    )))
}

fn parse_args(args: Vec<String>) -> CliResult<Option<(PathBuf, PathBuf)>> {
    if args.iter().any(|value| matches!(value.as_str(), "--help" | "-h")) {
        return Ok(None);
    }
    let mut values = BTreeMap::new();
    let mut index = 0usize;
    while index < args.len() {
        let flag = args[index].as_str();
        if !matches!(flag, "--analysis" | "--output") {
            return Err(CliError::invalid_input(format!(
                "unknown render argument '{flag}'"
            )));
        }
        let value = args
            .get(index + 1)
            .ok_or_else(|| CliError::invalid_input(format!("{flag} requires a value")))?;
        if values.insert(flag.to_owned(), value.clone()).is_some() {
            return Err(CliError::invalid_input(format!(
                "{flag} was given more than once"
            )));
        }
        index += 2;
    }

    let analysis = values
        .remove("--analysis")
        .ok_or_else(|| CliError::invalid_input("render requires --analysis <analysis.json>"))?;
    let output = values
        .remove("--output")
        .ok_or_else(|| CliError::invalid_input("render requires --output <path>"))?;
    Ok(Some((PathBuf::from(analysis), PathBuf::from(output))))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn render_requires_canonical_input_and_output_paths() {
        let error = parse_args(vec!["--analysis".to_owned(), "a.json".to_owned()])
            .expect_err("missing output must fail");
        assert!(error.to_string().contains("--output"));
    }

    #[test]
    fn render_rejects_unknown_flags() {
        let error = parse_args(vec!["--format".to_owned(), "png".to_owned()])
            .expect_err("unknown option must fail");
        assert!(error.to_string().contains("unknown render argument"));
    }
}
