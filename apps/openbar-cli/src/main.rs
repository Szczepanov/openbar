mod benchmark;
mod filter_experiment;
mod tracker_experiment;

fn main() {
    let result = match std::env::args().nth(1).as_deref() {
        Some("tracker-experiment") => tracker_experiment::run_cli(),
        Some("filter-experiment") => filter_experiment::run_cli(),
        _ => benchmark::run_cli(),
    };

    if let Err(error) = result {
        eprintln!("error: {error}");
        std::process::exit(2);
    }
}
