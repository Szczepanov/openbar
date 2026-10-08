mod analyze;
mod benchmark;
mod cli_error;
mod diagnostic_svg;
mod external_observations;
mod filter_experiment;
mod kinematic_reference;
mod media;
mod render;
mod sha256;
mod tracker_experiment;
mod tracker_run;
mod validate_analysis;

use cli_error::{CliError, CliResult};

const USAGE: &str = "OpenBar M0 headless CLI\n\
Usage: openbar-cli <command> [options]\n\
\n\
Commands:\n\
  analyze             Decode, track, calibrate, filter and derive canonical analysis JSON\n\
  validate-analysis   Validate canonical semantics and optional source PTS\n\
  benchmark           Run the common benchmark harness\n\
  render              Render deterministic diagnostic SVG from canonical analysis\n\
  tracker-run         Decode a fixture and emit tracker prediction artifacts\n\
  tracker-experiment  Run deterministic tracker experiments\n\
  filter-experiment   Run deterministic filter experiments\n\
  kinematic-reference Evaluate independent physical/reference kinematic evidence\n\
\n\
Use 'openbar-cli <command> --help' for command-specific options.";

fn main() {
    match std::env::args().nth(1).as_deref() {
        // Preserve the pre-#12 behavior of the developer/validation harnesses. Their
        // coarse exit-code contract is intentionally not retrofitted in this PR.
        Some("tracker-experiment") => run_legacy(tracker_experiment::run_cli()),
        Some("tracker-run") => run_legacy(tracker_run::run_cli()),
        Some("filter-experiment") => run_legacy(filter_experiment::run_cli()),
        Some("kinematic-reference") => run_legacy(kinematic_reference::run_cli()),
        _ => {
            if let Err(error) = dispatch() {
                eprintln!("status=failure error[{}]: {error}", error.kind().label());
                std::process::exit(error.exit_code());
            }
        }
    }
}

fn run_legacy(result: Result<(), Box<dyn std::error::Error>>) {
    if let Err(error) = result {
        eprintln!("error: {error}");
        std::process::exit(2);
    }
}

fn dispatch() -> CliResult<()> {
    match std::env::args().nth(1).as_deref() {
        Some("analyze") => analyze::run_cli(),
        Some("benchmark") => benchmark::run_cli(),
        Some("render") => render::run_cli(),
        Some("validate-analysis") => validate_analysis::run_cli(),
        None | Some("--help") | Some("-h") => {
            println!("{USAGE}");
            Ok(())
        }
        Some(command) => Err(CliError::invalid_input(format!(
            "unknown command '{command}'\n\n{USAGE}"
        ))),
    }
}
