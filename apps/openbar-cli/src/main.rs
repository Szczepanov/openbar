mod benchmark;
mod filter_experiment;
mod media;
mod sha256;
mod tracker_experiment;
mod tracker_run;

fn main() {
    let result = match std::env::args().nth(1).as_deref() {
        Some("tracker-experiment") => tracker_experiment::run_cli(),
        Some("tracker-run") => tracker_run::run_cli(),
        Some("filter-experiment") => filter_experiment::run_cli(),
        _ => benchmark::run_cli(),
    };

    if let Err(error) = result {
        eprintln!("error: {error}");
        std::process::exit(2);
    }
}
