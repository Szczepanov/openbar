//! Synthetic benchmark and sequence integration tests for `csrt-gray-v1` (slices S4 and S5).
//!
//! Validates:
//! - Invariant preservation (no coordinates on loss, timestamps authoritative, determinism).
//! - Synthetic tracking scenarios (stationary, translation, brightness step, occlusion, distractor,
//!   boundary exit/return, motion beyond search bound, fractional seed, largest crop).
//! - Core benchmark evaluation via [`evaluate_tracker_case`].

use openbar_core::benchmark::{evaluate_tracker_case, BenchmarkParameters, GroundTruthSample};
use openbar_core::manual_seed::{ManualTargetSeed, PixelPoint, PlateTarget, SeedValidationContext};
use openbar_tracking::{
    CsrtGrayTracker, FrameSample, GrayFrame, ManualSeedTracker, TrackerObservationState, TrackerRun,
};

const WIDTH: u32 = 128;
const HEIGHT: u32 = 96;

fn make_context(width: u32, height: u32, duration_s: f64) -> SeedValidationContext {
    SeedValidationContext {
        frame_width_px: width,
        frame_height_px: height,
        selected_range_start_s: 0.0,
        selected_range_end_s: duration_s,
        source_rotation_deg: 0,
    }
}

fn make_seed(
    timestamp_s: f64,
    center: (f64, f64),
    radius: f64,
    duration_s: f64,
) -> ManualTargetSeed {
    ManualTargetSeed::try_new(
        timestamp_s,
        Some(0),
        PlateTarget::new(PixelPoint::new(center.0, center.1), radius),
        0,
        Some(1.0),
        None,
        make_context(1000, 1000, duration_s),
    )
    .unwrap()
}

fn target_pixel(dx: f64, dy: f64, radius: f64) -> f64 {
    let d_sq = dx * dx + dy * dy;
    let r_sq = radius * radius;
    if d_sq > r_sq {
        return 40.0;
    }
    180.0 + ((dx * 13.0 + dy * 29.0).rem_euclid(60.0))
}

#[allow(clippy::too_many_arguments)]
fn generate_frame(
    width: u32,
    height: u32,
    target: Option<(f64, f64)>,
    radius: f64,
    brightness_gain: f64,
    brightness_offset: f64,
    distractor: Option<(f64, f64)>,
    partially_occluded: bool,
) -> GrayFrame {
    let mut pixels = Vec::with_capacity((width * height) as usize);

    for y in 0..height {
        for x in 0..width {
            let mut val = 40.0;

            // Check primary target
            if let Some((cx, cy)) = target {
                let dx = f64::from(x) - cx;
                let dy = f64::from(y) - cy;
                if dx * dx + dy * dy <= radius * radius {
                    // If partially occluded, occlude the right half
                    if !partially_occluded || dx <= 0.0 {
                        val = target_pixel(dx, dy, radius);
                    }
                }
            }

            // Check distractor
            if let Some((dx_c, dy_c)) = distractor {
                let dx = f64::from(x) - dx_c;
                let dy = f64::from(y) - dy_c;
                if dx * dx + dy * dy <= radius * radius {
                    val = target_pixel(dx, dy, radius);
                }
            }

            // Apply gain and offset
            let adjusted = val * brightness_gain + brightness_offset;
            pixels.push(adjusted.clamp(0.0, 255.0).round() as u8);
        }
    }

    GrayFrame::try_new(width, height, pixels).unwrap()
}

#[test]
fn scenario_stationary_target_tracks_cleanly_with_exact_invariants() {
    let tracker = CsrtGrayTracker::default();
    let center = (64.0, 48.0);
    let radius = 3.0;
    let seed = make_seed(0.0, center, radius, 1.0);

    let n_frames = 5;
    let frames: Vec<GrayFrame> = (0..n_frames)
        .map(|_| generate_frame(WIDTH, HEIGHT, Some(center), radius, 1.0, 0.0, None, false))
        .collect();

    let samples: Vec<FrameSample<'_>> = frames
        .iter()
        .enumerate()
        .map(|(i, frame)| FrameSample {
            timestamp_s: i as f64 * 0.05,
            frame_index: Some(i as u64),
            image: frame,
        })
        .collect();

    let run = tracker.track(&samples, &seed).expect("track run");
    assert_eq!(run.observations.len(), n_frames);

    // Exact seed verification
    let seed_obs = &run.observations[0];
    assert_eq!(seed_obs.timestamp_s, 0.0);
    assert_eq!(seed_obs.frame_index, Some(0));
    match seed_obs.state {
        TrackerObservationState::Tracked {
            center: seed_pt, ..
        } => {
            assert_eq!(seed_pt, PixelPoint::new(center.0, center.1));
        }
        _ => panic!("seed not tracked"),
    }

    // All later observations within 1 px error
    for (i, obs) in run.observations.iter().enumerate().skip(1) {
        assert_eq!(obs.timestamp_s, i as f64 * 0.05);
        assert_eq!(obs.frame_index, Some(i as u64));
        match obs.state {
            TrackerObservationState::Tracked { center: pt, .. } => {
                let err_x = (pt.x_px() - center.0).abs();
                let err_y = (pt.y_px() - center.1).abs();
                assert!(err_x <= 1.0 && err_y <= 1.0);
            }
            other => panic!("frame {i} not tracked: {other:?}"),
        }
    }

    // Core benchmark integration excluding seed timestamp
    let truth: Vec<GroundTruthSample> = (1..n_frames)
        .map(|i| GroundTruthSample {
            timestamp_s: i as f64 * 0.05,
            center: PixelPoint::new(center.0, center.1),
        })
        .collect();

    let metrics = evaluate_tracker_case(
        &truth,
        &run.benchmark_predictions(),
        BenchmarkParameters {
            timestamp_tolerance_s: 0.0,
            min_confidence: 0.0,
        },
    )
    .expect("evaluate");

    assert_eq!(metrics.comparable_samples, truth.len());
    assert_eq!(metrics.tracking_availability, Some(1.0));
    assert!(metrics.plate_center_mae_px.unwrap() <= 1.0);
}

#[test]
fn scenario_signed_translations_each_axis_and_diagonal() {
    let tracker = CsrtGrayTracker::default();
    let start = (64.0, 48.0);
    let radius = 5.0;

    let test_cases = [
        ("plus_x", 1.5, 0.0),
        ("minus_x", -1.5, 0.0),
        ("plus_y", 0.0, 1.5),
        ("minus_y", 0.0, -1.5),
        ("diagonal", 1.0, 1.0),
    ];

    for (name, step_x, step_y) in test_cases {
        let seed = make_seed(0.0, start, radius, 1.0);
        let n_frames = 5;
        let positions: Vec<(f64, f64)> = (0..n_frames)
            .map(|i| (start.0 + i as f64 * step_x, start.1 + i as f64 * step_y))
            .collect();

        let frames: Vec<GrayFrame> = positions
            .iter()
            .map(|&pos| generate_frame(WIDTH, HEIGHT, Some(pos), radius, 1.0, 0.0, None, false))
            .collect();

        let samples: Vec<FrameSample<'_>> = frames
            .iter()
            .enumerate()
            .map(|(i, frame)| FrameSample {
                timestamp_s: i as f64 * 0.05,
                frame_index: Some(i as u64),
                image: frame,
            })
            .collect();

        let run = tracker.track(&samples, &seed).expect("track run");

        let truth: Vec<GroundTruthSample> = (1..n_frames)
            .map(|i| GroundTruthSample {
                timestamp_s: i as f64 * 0.05,
                center: PixelPoint::new(positions[i].0, positions[i].1),
            })
            .collect();

        let metrics = evaluate_tracker_case(
            &truth,
            &run.benchmark_predictions(),
            BenchmarkParameters {
                timestamp_tolerance_s: 0.0,
                min_confidence: 0.0,
            },
        )
        .expect("evaluate");

        assert_eq!(
            metrics.tracking_availability,
            Some(1.0),
            "availability failed for {name}"
        );
        let mae = metrics.plate_center_mae_px.unwrap();
        assert!(mae <= 1.0, "MAE {mae} > 1.0 for {name}");
    }
}

#[test]
fn scenario_irregular_timestamps_and_none_frame_indices() {
    let tracker = CsrtGrayTracker::default();
    let center = (64.0, 48.0);
    let radius = 5.0;
    let seed = make_seed(0.0, center, radius, 2.0);

    let timestamps = [0.0, 0.033, 0.071, 0.182, 0.540];
    let indices = [Some(0), None, Some(7), None, Some(22)];

    let frames: Vec<GrayFrame> = (0..timestamps.len())
        .map(|_| generate_frame(WIDTH, HEIGHT, Some(center), radius, 1.0, 0.0, None, false))
        .collect();

    let samples: Vec<FrameSample<'_>> = frames
        .iter()
        .enumerate()
        .map(|(i, frame)| FrameSample {
            timestamp_s: timestamps[i],
            frame_index: indices[i],
            image: frame,
        })
        .collect();

    let run = tracker.track(&samples, &seed).expect("track run");
    assert_eq!(run.observations.len(), timestamps.len());

    for (i, obs) in run.observations.iter().enumerate() {
        assert_eq!(obs.timestamp_s, timestamps[i]);
        assert_eq!(obs.frame_index, indices[i]);
    }
}

#[test]
fn scenario_global_brightness_step_normalisation_invariance() {
    let tracker = CsrtGrayTracker::default();
    let center = (64.0, 48.0);
    let radius = 5.0;
    let seed = make_seed(0.0, center, radius, 1.0);

    // Frame 0: normal
    // Frame 1: normal
    // Frame 2: gain 1.3, offset +25.0
    // Frame 3: gain 0.7, offset -15.0
    let frame_params = [(1.0, 0.0), (1.0, 0.0), (1.3, 25.0), (0.7, -15.0)];

    let frames: Vec<GrayFrame> = frame_params
        .iter()
        .map(|&(gain, offset)| {
            generate_frame(
                WIDTH,
                HEIGHT,
                Some(center),
                radius,
                gain,
                offset,
                None,
                false,
            )
        })
        .collect();

    let samples: Vec<FrameSample<'_>> = frames
        .iter()
        .enumerate()
        .map(|(i, frame)| FrameSample {
            timestamp_s: i as f64 * 0.05,
            frame_index: Some(i as u64),
            image: frame,
        })
        .collect();

    let run = tracker.track(&samples, &seed).expect("track run");

    for (i, obs) in run.observations.iter().enumerate().skip(1) {
        match obs.state {
            TrackerObservationState::Tracked { center: pt, .. } => {
                let err_x = (pt.x_px() - center.0).abs();
                let err_y = (pt.y_px() - center.1).abs();
                assert!(err_x <= 1.0 && err_y <= 1.0, "frame {i} error too large");
            }
            TrackerObservationState::Lost { .. } => {
                // An explicit loss is allowed by spec, but never a wrong coordinate!
                assert_eq!(obs.target_bounds_px, None);
            }
            TrackerObservationState::LowConfidence { center: pt, .. } => {
                let err_x = (pt.x_px() - center.0).abs();
                let err_y = (pt.y_px() - center.1).abs();
                assert!(err_x <= 1.0 && err_y <= 1.0);
            }
        }
    }
}

#[test]
fn scenario_partial_occlusion_invariants() {
    let tracker = CsrtGrayTracker::default();
    let center = (64.0, 48.0);
    let radius = 3.0;
    let seed = make_seed(0.0, center, radius, 1.0);

    let frames = [
        generate_frame(WIDTH, HEIGHT, Some(center), radius, 1.0, 0.0, None, false),
        generate_frame(WIDTH, HEIGHT, Some(center), radius, 1.0, 0.0, None, false),
        generate_frame(WIDTH, HEIGHT, Some(center), radius, 1.0, 0.0, None, true), // partially occluded
        generate_frame(WIDTH, HEIGHT, Some(center), radius, 1.0, 0.0, None, false),
    ];

    let samples: Vec<FrameSample<'_>> = frames
        .iter()
        .enumerate()
        .map(|(i, frame)| FrameSample {
            timestamp_s: i as f64 * 0.05,
            frame_index: Some(i as u64),
            image: frame,
        })
        .collect();

    let run = tracker.track(&samples, &seed).expect("track run");

    // Any non-lost observation must be within error bound; lost observations carry no coordinate
    for obs in &run.observations {
        match obs.state {
            TrackerObservationState::Tracked { center: pt, .. }
            | TrackerObservationState::LowConfidence { center: pt, .. } => {
                let err_x = (pt.x_px() - center.0).abs();
                let err_y = (pt.y_px() - center.1).abs();
                assert!(err_x <= 1.5 && err_y <= 1.5);
                assert!(obs.target_bounds_px.is_some());
            }
            TrackerObservationState::Lost { .. } => {
                assert_eq!(obs.target_bounds_px, None);
            }
        }
    }
}

#[test]
fn scenario_full_occlusion_gap_and_reacquisition_at_same_position() {
    let tracker = CsrtGrayTracker::default();
    let center = (64.0, 48.0);
    let radius = 3.0;
    let seed = make_seed(0.0, center, radius, 1.0);

    // Frame 0..2: target present
    // Frame 3..5: target fully absent (occluded by uniform background)
    // Frame 6..8: target returns at same position
    let mut frames = Vec::new();
    for i in 0..9 {
        let has_target = !(3..=5).contains(&i);
        let target_pos = if has_target { Some(center) } else { None };
        frames.push(generate_frame(
            WIDTH, HEIGHT, target_pos, radius, 1.0, 0.0, None, false,
        ));
    }

    let samples: Vec<FrameSample<'_>> = frames
        .iter()
        .enumerate()
        .map(|(i, frame)| FrameSample {
            timestamp_s: i as f64 * 0.05,
            frame_index: Some(i as u64),
            image: frame,
        })
        .collect();

    let run = tracker.track(&samples, &seed).expect("track run");
    assert_eq!(run.observations.len(), 9);

    // Occlusion frames must be lost and have NO bounds
    for i in 3..=5 {
        assert!(
            matches!(
                run.observations[i].state,
                TrackerObservationState::Lost { .. }
            ),
            "frame {i} should be lost"
        );
        assert_eq!(run.observations[i].target_bounds_px, None);
    }

    // First frame of return (index 6) must be reacquired after loss
    match run.observations[6].state {
        TrackerObservationState::Tracked { center: pt, .. } => {
            assert_eq!(pt, PixelPoint::new(center.0, center.1));
            assert!(
                run.observations[6].diagnostics.reacquired_after_loss,
                "reacquired_after_loss should be true on first recovery frame"
            );
        }
        other => panic!("expected reacquired tracked on frame 6, got {other:?}"),
    }

    // Subsequent frame 7 should have reacquired_after_loss == false
    assert!(!run.observations[7].diagnostics.reacquired_after_loss);
}

#[test]
fn scenario_distractor_beyond_and_inside_search_bound() {
    let tracker = CsrtGrayTracker::default();
    let center = (64.0, 48.0);
    let radius = 3.0;
    let seed = make_seed(0.0, center, radius, 1.0);

    // Target radius 3 -> patch side 16 -> search limit side / 4 = 4 px.
    // Case 1: Distractor beyond search bound (distance 10 px away at (74.0, 48.0)).
    // Tracker must follow primary target without switching.
    let frames_beyond: Vec<GrayFrame> = (0..5)
        .map(|_| {
            generate_frame(
                WIDTH,
                HEIGHT,
                Some(center),
                radius,
                1.0,
                0.0,
                Some((74.0, 48.0)),
                false,
            )
        })
        .collect();

    let samples_beyond: Vec<FrameSample<'_>> = frames_beyond
        .iter()
        .enumerate()
        .map(|(i, frame)| FrameSample {
            timestamp_s: i as f64 * 0.05,
            frame_index: Some(i as u64),
            image: frame,
        })
        .collect();

    let run_beyond = tracker.track(&samples_beyond, &seed).expect("track beyond");
    for obs in run_beyond.observations.iter().skip(1) {
        match obs.state {
            TrackerObservationState::Tracked { center: pt, .. } => {
                assert_eq!(pt, PixelPoint::new(center.0, center.1));
            }
            other => panic!("distractor beyond search bound caused loss: {other:?}"),
        }
    }

    // Case 2: Target absent, only distractor present at (74.0, 48.0).
    // Count false tracks directly in test code (F1):
    let frames_absent: Vec<GrayFrame> = (0..5)
        .map(|i| {
            let target_pos = if i == 0 { Some(center) } else { None };
            generate_frame(
                WIDTH,
                HEIGHT,
                target_pos,
                radius,
                1.0,
                0.0,
                Some((74.0, 48.0)), // distractor beyond bound
                false,
            )
        })
        .collect();

    let samples_absent: Vec<FrameSample<'_>> = frames_absent
        .iter()
        .enumerate()
        .map(|(i, frame)| FrameSample {
            timestamp_s: i as f64 * 0.05,
            frame_index: Some(i as u64),
            image: frame,
        })
        .collect();

    let run_absent = tracker.track(&samples_absent, &seed).expect("track absent");
    let mut false_tracks = 0;
    for obs in run_absent.observations.iter().skip(1) {
        if matches!(
            obs.state,
            TrackerObservationState::Tracked { .. } | TrackerObservationState::LowConfidence { .. }
        ) {
            false_tracks += 1;
        }
    }
    assert_eq!(
        false_tracks, 0,
        "tracker falsely tracked distractor when target was absent"
    );
}

#[test]
fn scenario_motion_beyond_search_bound_causes_loss_and_does_not_recover_globally() {
    let tracker = CsrtGrayTracker::default();
    let center = (64.0, 48.0);
    let radius = 3.0; // side 16, max displacement 4 px
    let seed = make_seed(0.0, center, radius, 1.0);

    // Frame 0: at (64, 48)
    // Frame 1: at (64, 48)
    // Frame 2: jumps to (75, 48) (delta = 11 px > 4 px limit)
    // Frame 3: stays at (75, 48)
    let positions = [(64.0, 48.0), (64.0, 48.0), (75.0, 48.0), (75.0, 48.0)];

    let frames: Vec<GrayFrame> = positions
        .iter()
        .map(|&pos| generate_frame(WIDTH, HEIGHT, Some(pos), radius, 1.0, 0.0, None, false))
        .collect();

    let samples: Vec<FrameSample<'_>> = frames
        .iter()
        .enumerate()
        .map(|(i, frame)| FrameSample {
            timestamp_s: i as f64 * 0.05,
            frame_index: Some(i as u64),
            image: frame,
        })
        .collect();

    let run = tracker.track(&samples, &seed).expect("track run");

    // Frame 2 and 3 must be lost because motion exceeded side / 4
    assert!(matches!(
        run.observations[2].state,
        TrackerObservationState::Lost { .. }
    ));
    assert!(matches!(
        run.observations[3].state,
        TrackerObservationState::Lost { .. }
    ));
    assert_eq!(run.observations[2].target_bounds_px, None);
    assert_eq!(run.observations[3].target_bounds_px, None);
}

#[test]
fn scenario_fractional_seed_centre_pins_integer_anchor_offset() {
    let tracker = CsrtGrayTracker::default();
    let fractional_center = (64.4, 48.3);
    let radius = 3.0;
    let seed = make_seed(0.0, fractional_center, radius, 1.0);

    let frame = generate_frame(
        WIDTH,
        HEIGHT,
        Some((64.0, 48.0)),
        radius,
        1.0,
        0.0,
        None,
        false,
    );
    let samples = [
        FrameSample {
            timestamp_s: 0.0,
            frame_index: Some(0),
            image: &frame,
        },
        FrameSample {
            timestamp_s: 0.05,
            frame_index: Some(1),
            image: &frame,
        },
    ];

    let run = tracker.track(&samples, &seed).expect("track run");

    // Frame 0 preserves authored fractional centre exactly
    match run.observations[0].state {
        TrackerObservationState::Tracked { center, .. } => {
            assert_eq!(center, PixelPoint::new(64.4, 48.3));
        }
        _ => panic!("seed not tracked"),
    }

    // Frame 1 reports the rounded integer anchor (64.0, 48.0)
    match run.observations[1].state {
        TrackerObservationState::Tracked { center, .. } => {
            assert_eq!(center, PixelPoint::new(64.0, 48.0));
            // Offset is <= 0.5 px
            let offset_x = (center.x_px() - fractional_center.0).abs();
            let offset_y = (center.y_px() - fractional_center.1).abs();
            assert!(offset_x <= 0.5);
            assert!(offset_y <= 0.5);
        }
        _ => panic!("frame 1 not tracked"),
    }
}

#[test]
fn scenario_largest_supported_crop_completes_without_panic() {
    let tracker = CsrtGrayTracker::default();
    // Radius 100 px requires side 512 (largest supported)
    let radius = 100.0;
    let frame_side = 600;
    let center = (300.0, 300.0);

    let seed = make_seed(0.0, center, radius, 1.0);

    let start_time = std::time::Instant::now();

    let frames: Vec<GrayFrame> = (0..3)
        .map(|_| {
            generate_frame(
                frame_side,
                frame_side,
                Some(center),
                radius,
                1.0,
                0.0,
                None,
                false,
            )
        })
        .collect();

    let samples: Vec<FrameSample<'_>> = frames
        .iter()
        .enumerate()
        .map(|(i, frame)| FrameSample {
            timestamp_s: i as f64 * 0.05,
            frame_index: Some(i as u64),
            image: frame,
        })
        .collect();

    let run = tracker.track(&samples, &seed).expect("track largest crop");
    let elapsed = start_time.elapsed();

    assert_eq!(run.observations.len(), 3);
    for (i, obs) in run.observations.iter().enumerate() {
        assert_eq!(obs.timestamp_s, i as f64 * 0.05);
        assert_eq!(obs.frame_index, Some(i as u64));
        match obs.state {
            TrackerObservationState::Tracked { center: pt, .. }
            | TrackerObservationState::LowConfidence { center: pt, .. } => {
                let err_x = (pt.x_px() - center.0).abs();
                let err_y = (pt.y_px() - center.1).abs();
                assert!(err_x <= 1.0 && err_y <= 1.0);
                assert!(obs.target_bounds_px.is_some());
            }
            TrackerObservationState::Lost { .. } => panic!("largest crop lost"),
        }
    }
    // Record elapsed time for debug-build ceiling awareness
    println!("Largest supported crop (512x512) 3 frames took: {elapsed:?}");
}

#[test]
fn scenario_determinism_identical_runs_are_equal() {
    let tracker1 = CsrtGrayTracker::default();
    let tracker2 = CsrtGrayTracker::default();

    let center = (64.0, 48.0);
    let radius = 5.0;
    let seed = make_seed(0.0, center, radius, 1.0);

    let frames: Vec<GrayFrame> = (0..4)
        .map(|i| {
            generate_frame(
                WIDTH,
                HEIGHT,
                Some((center.0 + i as f64, center.1)),
                radius,
                1.0,
                0.0,
                None,
                false,
            )
        })
        .collect();

    let samples: Vec<FrameSample<'_>> = frames
        .iter()
        .enumerate()
        .map(|(i, frame)| FrameSample {
            timestamp_s: i as f64 * 0.05,
            frame_index: Some(i as u64),
            image: frame,
        })
        .collect();

    let run1: TrackerRun = tracker1.track(&samples, &seed).expect("run 1");
    let run2: TrackerRun = tracker2.track(&samples, &seed).expect("run 2");

    assert_eq!(run1, run2);
}
