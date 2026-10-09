use std::fmt;

pub type CliResult<T> = Result<T, CliError>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CliErrorKind {
    Internal,
    InvalidInput,
    Media,
    Unsupported,
    SeedCalibration,
    Tracking,
    Output,
    Benchmark,
}

impl CliErrorKind {
    pub const fn exit_code(self) -> i32 {
        match self {
            Self::Internal => 1,
            Self::InvalidInput => 2,
            Self::Media => 3,
            Self::Unsupported => 4,
            Self::SeedCalibration => 5,
            Self::Tracking => 6,
            Self::Output => 7,
            Self::Benchmark => 8,
        }
    }

    pub const fn label(self) -> &'static str {
        match self {
            Self::Internal => "internal",
            Self::InvalidInput => "invalid-input",
            Self::Media => "media",
            Self::Unsupported => "unsupported",
            Self::SeedCalibration => "seed-calibration",
            Self::Tracking => "tracking",
            Self::Output => "output",
            Self::Benchmark => "benchmark",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CliError {
    kind: CliErrorKind,
    message: String,
}

impl CliError {
    pub fn new(kind: CliErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }

    pub fn invalid_input(message: impl Into<String>) -> Self {
        Self::new(CliErrorKind::InvalidInput, message)
    }

    pub fn media(message: impl Into<String>) -> Self {
        Self::new(CliErrorKind::Media, message)
    }

    pub fn unsupported(message: impl Into<String>) -> Self {
        Self::new(CliErrorKind::Unsupported, message)
    }

    pub fn seed_calibration(message: impl Into<String>) -> Self {
        Self::new(CliErrorKind::SeedCalibration, message)
    }

    pub fn tracking(message: impl Into<String>) -> Self {
        Self::new(CliErrorKind::Tracking, message)
    }

    pub fn output(message: impl Into<String>) -> Self {
        Self::new(CliErrorKind::Output, message)
    }

    pub fn benchmark(message: impl Into<String>) -> Self {
        Self::new(CliErrorKind::Benchmark, message)
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self::new(CliErrorKind::Internal, message)
    }

    pub const fn kind(&self) -> CliErrorKind {
        self.kind
    }

    pub const fn exit_code(&self) -> i32 {
        self.kind.exit_code()
    }
}

impl fmt::Display for CliError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for CliError {}

impl From<fmt::Error> for CliError {
    fn from(error: fmt::Error) -> Self {
        Self::internal(format!("formatting error: {error}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exit_codes_are_stable_and_distinct() {
        let kinds = [
            CliErrorKind::Internal,
            CliErrorKind::InvalidInput,
            CliErrorKind::Media,
            CliErrorKind::Unsupported,
            CliErrorKind::SeedCalibration,
            CliErrorKind::Tracking,
            CliErrorKind::Output,
            CliErrorKind::Benchmark,
        ];
        let mut codes = kinds
            .iter()
            .copied()
            .map(CliErrorKind::exit_code)
            .collect::<Vec<_>>();
        codes.sort_unstable();
        codes.dedup();
        assert_eq!(codes.len(), kinds.len());
        assert_eq!(CliErrorKind::InvalidInput.exit_code(), 2);
        assert_eq!(CliErrorKind::Output.exit_code(), 7);
    }
}
