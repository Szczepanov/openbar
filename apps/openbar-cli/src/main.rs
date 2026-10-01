mod analyze;
mod benchmark;
mod cli_error;
mod filter_experiment;
mod media;
mod render;
mod sha256;
mod tracker_experiment;
mod tracker_run;

use cli_error::{CliError, CliResult};

const USAGE: &str = "OpenBar M0 headless CLI\n\
Usage: openbar-cli <command> [options]\n\
\n\
Commands:\n\
  analyze             Decode, track, calibrate, filter and derive canonical analysis JSON\n\
  benchmark           Run the common benchmark harness\n\
  render              Validate canonical analysis and enter the #13 renderer boundary\n\
  tracker-run         Decode a fixture and emit tracker prediction artifacts\n\
  tracker-experiment  Run deterministic tracker experiments\n\
  filter-experiment   Run deterministic filter experiments\n\
\n\
Use 'openbar-cli <command> --help' for command-specific options.";

fn main() {
    if let Err(error) = dispatch() {
        eprintln!("status=failure error[{}]: {error}", error.kind().label());
        std::process::exit(error.exit_code());
    }
}

fn dispatch() -> CliResult<()> {
    match std::env::args().nth(1).as_deref() {
        Some("analyze") => analyze::run_cli(),
        Some("benchmark") => benchmark::run_cli(),
        Some("render") => render::run_cli(),
        Some("tracker-experiment") => {
            tracker_experiment::run_cli().map_err(|error| CliError::tracking(error.to_string()))
        }
        Some("tracker-run") => {
            tracker_run::run_cli().map_err(|error| CliError::tracking(error.to_string()))
        }
        Some("filter-experiment") => {
            filter_experiment::run_cli().map_err(|error| CliError::internal(error.to_string()))
        }
        None | Some("--help") | Some("-h") => {
            println!("{USAGE}");
            Ok(())
        }
        Some(command) => Err(CliError::invalid_input(format!(
            "unknown command '{command}'\n\n{USAGE}"
        ))),
    }
}
