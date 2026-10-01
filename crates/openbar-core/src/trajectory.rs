#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PixelObservation {
    pub timestamp_s: f64,
    pub x_px: f64,
    pub y_px: f64,
    pub confidence: f32,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MetricPositionSample {
    pub timestamp_s: f64,
    pub x_m: f64,
    pub y_m: f64,
    pub confidence: f32,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct KinematicSample {
    pub timestamp_s: f64,
    pub x_m: f64,
    pub y_m: f64,
    pub vx_mps: Option<f64>,
    pub vy_mps: Option<f64>,
    pub confidence: f32,
}
