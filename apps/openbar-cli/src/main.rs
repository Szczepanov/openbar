mod benchmark;

fn main() {
    if let Err(error) = benchmark::run_cli() {
        eprintln!("error: {error}");
        std::process::exit(2);
    }
}
