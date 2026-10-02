//! Deterministic measurement primitives for OpenBar.
//!
//! This crate is intentionally independent from UI and ML training code.

pub mod analysis;
pub mod benchmark;
pub mod calibration;
pub mod filtering;
pub mod kinematics;
pub mod manual_seed;
pub mod math;
pub mod recording_support;
#[cfg(test)]
pub mod test_utils;
pub mod trajectory;

pub use manual_seed::SpatialFrameReference;
