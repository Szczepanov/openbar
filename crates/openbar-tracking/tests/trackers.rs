use openbar_core::benchmark::{evaluate_tracker_case, BenchmarkParameters, GroundTruthSample};
use openbar_core::manual_seed::{
    ManualTargetSeed, ManualTargetSeedDocument, PixelPoint, PlateTarget, SeedValidationContext,
};
use openbar_tracking::{
    FrameSample, GrayFrame, LocalContrastConfig, LocalContrastTracker, ManualSeedTracker,
    TemplateMatchConfig, TemplateMatchTracker, TrackerError, TrackerObservation,
    TrackerObservationState,
};
use serde::Deserialize;

const WIDTH: u32 = 64;
const HEIGHT: u32 = 64;
const RADIUS: i32 = 6;

fn context(width_px: u32, height_px: u32, end_s: f64) -> SeedValidationContext {
    SeedValidationContext {
        frame_width_px: width_px,
        frame_height_px: height_px,
        selected_range_start_s: 0.0,
        selected_range_end_s: end_s,
        source_rotation_deg: 0,
    }
}

fn seed(center: (i32, i32)) -> ManualTargetSeed {
    ManualTargetSeed::try_new(
        0.0,
        Some(0),
        PlateTarget::new(
            PixelPoint::new(f64::from(center.0), f64::from(center.1)),
            f64::from(RADIUS),
        ),
        0,
        Some(1.0),
        None,
        context(WIDTH, HEIGHT, 1.0),
    )
    .unwrap()
}

fn disk_frame(center: Option<(i32, i32)>) -> GrayFrame {
    disk_frame_with_geometry(WIDTH, HEIGHT, center, RADIUS, 40)
}

fn disk_frame_with_geometry(
    width: u32,
    height: u32,
    center: Option<(i32, i32)>,
    radius: i32,
    target_intensity: u8,
) -> GrayFrame {
    let mut pixels = vec![220u8; (width * height) as usize];
    if let Some((cx, cy)) = center {
        for y in 0..height as i32 {
            for x in 0..width as i32 {
                let dx = x - cx;
                let dy = y - cy;
                if dx * dx + dy * dy <= radius * radius {
                    pixels[y as usize * width as usize + x as usize] = target_intensity;
                }
            }
        }
    }
    GrayFrame::try_new(width, height, pixels).unwrap()
}

fn samples<'a>(frames: &'a [GrayFrame], timestamps: &[f64]) -> Vec<FrameSample<'a>> {
    frames
        .iter()
        .zip(timestamps)
        .enumerate()
        .map(|(index, (image, timestamp_s))| FrameSample {
            timestamp_s: *timestamp_s,
            frame_index: Some(index as u64),
            image,
        })
        .collect()
}

fn tracked_center(observation: &TrackerObservation) -> PixelPoint {
    match observation.state {
        TrackerObservationState::Tracked { center, .. }
        | TrackerObservationState::LowConfidence { center, .. } => center,
        TrackerObservationState::Lost { reason } => {
            panic!("expected tracked observation, got {reason:?}")
        }
    }
}

fn assert_near(point: PixelPoint, expected: (f64, f64)) {
    assert!((point.x_px() - expected.0).abs() < 0.25);
    assert!((point.y_px() - expected.1).abs() < 0.25);
}

#[test]
fn stationary_and_translated_targets_work_for_both_families_and_common_benchmark() {
    let centers = [(24, 40), (26, 39), (28, 38), (30, 37), (32, 36)];
    let timestamps = [0.0, 0.1, 0.2, 0.3, 0.4];
    let frames = centers
        .iter()
        .map(|center| disk_frame(Some(*center)))
        .collect::<Vec<_>>();
    let frame_samples = samples(&frames, &timestamps);
    let manual_seed = seed(centers[0]);
    let truth = centers
        .iter()
        .zip(timestamps)
        .map(|(center, timestamp_s)| GroundTruthSample {
            timestamp_s,
            center: PixelPoint::new(f64::from(center.0), f64::from(center.1)),
        })
        .collect::<Vec<_>>();
    let template = TemplateMatchTracker::default();
    let contrast = LocalContrastTracker::default();

    for run in [
        template.track(&frame_samples, &manual_seed).unwrap(),
        contrast.track(&frame_samples, &manual_seed).unwrap(),
    ] {
        for (observation, expected) in run.observations.iter().zip(centers) {
            assert_near(
                tracked_center(observation),
                (f64::from(expected.0), f64::from(expected.1)),
            );
        }
        let metrics = evaluate_tracker_case(
            &truth,
            &run.benchmark_predictions(),
            BenchmarkParameters {
                timestamp_tolerance_s: 0.0,
                min_confidence: 0.0,
            },
        )
        .unwrap();
        assert_eq!(metrics.tracking_availability, Some(1.0));
        assert!(metrics.plate_center_mae_px.unwrap() < 0.25);
    }
}

#[test]
fn occlusion_is_explicit_and_reentry_is_reacquired_without_carried_coordinates() {
    let frames = vec![
        disk_frame(Some((24, 40))),
        disk_frame(None),
        disk_frame(Some((27, 38))),
    ];
    let frame_samples = samples(&frames, &[0.0, 0.1, 0.2]);
    let manual_seed = seed((24, 40));
    let template = TemplateMatchTracker::default();
    let contrast = LocalContrastTracker::default();

    for run in [
        template.track(&frame_samples, &manual_seed).unwrap(),
        contrast.track(&frame_samples, &manual_seed).unwrap(),
    ] {
        assert!(matches!(
            run.observations[1].state,
            TrackerObservationState::Lost { .. }
        ));
        assert!(run.observations[1].target_bounds_px.is_none());
        assert_near(tracked_center(&run.observations[2]), (27.0, 38.0));
        assert!(run.observations[2].diagnostics.reacquired_after_loss);
    }
}

#[test]
fn target_leaving_and_reentering_frame_is_reported_as_loss_then_recovery() {
    let frames = vec![
        disk_frame(Some((24, 40))),
        disk_frame(Some((3, 40))),
        disk_frame(Some((25, 39))),
    ];
    let frame_samples = samples(&frames, &[0.0, 0.1, 0.2]);
    let manual_seed = seed((24, 40));
    let template = TemplateMatchTracker::default();
    let contrast = LocalContrastTracker::default();

    for run in [
        template.track(&frame_samples, &manual_seed).unwrap(),
        contrast.track(&frame_samples, &manual_seed).unwrap(),
    ] {
        assert!(matches!(
            run.observations[1].state,
            TrackerObservationState::Lost { .. }
        ));
        assert_near(tracked_center(&run.observations[2]), (25.0, 39.0));
        assert!(run.observations[2].diagnostics.reacquired_after_loss);
    }
}

#[test]
fn invalid_seed_sequence_empty_sequence_irregular_timestamps_and_determinism_are_covered() {
    let template = TemplateMatchTracker::default();
    let manual_seed = seed((24, 40));
    assert!(matches!(
        template.track(&[], &manual_seed),
        Err(TrackerError::EmptySequence)
    ));

    let small = GrayFrame::try_new(32, 32, vec![220; 32 * 32]).unwrap();
    let small_samples = [FrameSample {
        timestamp_s: 0.0,
        frame_index: Some(0),
        image: &small,
    }];
    assert!(matches!(
        template.track(&small_samples, &manual_seed),
        Err(TrackerError::SeedTargetOutsideFrame)
    ));

    let first = disk_frame(Some((24, 40)));
    let changed = GrayFrame::try_new(63, 64, vec![220; 63 * 64]).unwrap();
    let changed_samples = [
        FrameSample {
            timestamp_s: 0.0,
            frame_index: Some(0),
            image: &first,
        },
        FrameSample {
            timestamp_s: 0.1,
            frame_index: Some(1),
            image: &changed,
        },
    ];
    assert!(matches!(
        template.track(&changed_samples, &manual_seed),
        Err(TrackerError::FrameDimensionsChanged { index: 1, .. })
    ));

    let centers = [(24, 40), (25, 39), (26, 38), (27, 37)];
    let frames = centers
        .iter()
        .map(|center| disk_frame(Some(*center)))
        .collect::<Vec<_>>();
    let timestamps = [0.0, 0.013, 0.091, 0.44];
    let frame_samples = samples(&frames, &timestamps);
    let contrast = LocalContrastTracker::default();
    let first = contrast.track(&frame_samples, &manual_seed).unwrap();
    let second = contrast.track(&frame_samples, &manual_seed).unwrap();
    assert_eq!(first, second);
    assert_eq!(
        first
            .observations
            .iter()
            .map(|observation| observation.timestamp_s)
            .collect::<Vec<_>>(),
        timestamps.to_vec()
    );
}

#[test]
fn one_frame_sequence_is_supported_and_low_contrast_seed_fails_explicitly() {
    let frame = disk_frame(Some((24, 40)));
    let frames = [frame];
    let frame_samples = samples(&frames, &[0.0]);
    assert_eq!(
        TemplateMatchTracker::default()
            .track(&frame_samples, &seed((24, 40)))
            .unwrap()
            .observations
            .len(),
        1
    );

    let flat = GrayFrame::try_new(WIDTH, HEIGHT, vec![120; (WIDTH * HEIGHT) as usize]).unwrap();
    let flat_samples = [FrameSample {
        timestamp_s: 0.0,
        frame_index: Some(0),
        image: &flat,
    }];
    assert!(matches!(
        LocalContrastTracker::default().track(&flat_samples, &seed((24, 40))),
        Err(TrackerError::InsufficientSeedContrast { .. })
    ));
}

#[test]
fn low_confidence_is_explicit_for_both_tracker_families() {
    let template_frames = [
        disk_frame_with_geometry(WIDTH, HEIGHT, Some((24, 40)), RADIUS, 40),
        disk_frame_with_geometry(WIDTH, HEIGHT, Some((24, 40)), RADIUS, 110),
    ];
    let template_samples = samples(&template_frames, &[0.0, 0.1]);
    let template_run = TemplateMatchTracker::default()
        .track(&template_samples, &seed((24, 40)))
        .unwrap();
    assert!(matches!(
        template_run.observations[1].state,
        TrackerObservationState::LowConfidence { .. }
    ));

    let contrast_frames = [
        disk_frame_with_geometry(WIDTH, HEIGHT, Some((24, 40)), RADIUS, 40),
        disk_frame_with_geometry(WIDTH, HEIGHT, Some((24, 40)), 4, 40),
    ];
    let contrast_samples = samples(&contrast_frames, &[0.0, 0.1]);
    let contrast_run = LocalContrastTracker::default()
        .track(&contrast_samples, &seed((24, 40)))
        .unwrap();
    assert!(matches!(
        contrast_run.observations[1].state,
        TrackerObservationState::LowConfidence { .. }
    ));
}

#[derive(Debug, Deserialize)]
struct AnnotationDocument {
    samples: Vec<AnnotationSample>,
}

#[derive(Debug, Deserialize)]
struct AnnotationSample {
    timestamp_s: f64,
    annotation_state: String,
    #[serde(default)]
    center_px: Option<JsonPoint>,
}

#[derive(Debug, Clone, Copy, Deserialize)]
struct JsonPoint {
    x_px: f64,
    y_px: f64,
}

#[test]
fn canonical_fixture_seed_and_annotations_feed_both_trackers_through_same_evaluator() {
    let annotations: AnnotationDocument = serde_json::from_str(include_str!(
        "../../../validation/fixtures/public/annotations/synthetic-clean-side-12.annotation-v1.json"
    ))
    .unwrap();
    let seed_document: ManualTargetSeedDocument = serde_json::from_str(include_str!(
        "../../../validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json"
    ))
    .unwrap();
    seed_document.validate(context(320, 240, 0.916667)).unwrap();

    let centers = annotations
        .samples
        .iter()
        .enumerate()
        .map(|(index, sample)| {
            sample
                .center_px
                .map(|point| (point.x_px.round() as i32, point.y_px.round() as i32))
                .or_else(|| (index == 10).then_some((110, 99)))
        })
        .collect::<Vec<_>>();
    let frames = centers
        .iter()
        .map(|center| disk_frame_with_geometry(320, 240, *center, 24, 40))
        .collect::<Vec<_>>();
    let timestamps = annotations
        .samples
        .iter()
        .map(|sample| sample.timestamp_s)
        .collect::<Vec<_>>();
    let frame_samples = samples(&frames, &timestamps);
    let truth = annotations
        .samples
        .iter()
        .filter(|sample| sample.annotation_state == "labelled")
        .filter_map(|sample| {
            sample.center_px.map(|point| GroundTruthSample {
                timestamp_s: sample.timestamp_s,
                center: PixelPoint::new(point.x_px, point.y_px),
            })
        })
        .collect::<Vec<_>>();

    let template = TemplateMatchTracker::try_new(TemplateMatchConfig {
        search_radius_px: 24,
        ..TemplateMatchConfig::default()
    })
    .unwrap();
    let contrast = LocalContrastTracker::try_new(LocalContrastConfig {
        search_radius_px: 24,
        ..LocalContrastConfig::default()
    })
    .unwrap();

    for run in [
        template
            .track(&frame_samples, seed_document.seed())
            .unwrap(),
        contrast
            .track(&frame_samples, seed_document.seed())
            .unwrap(),
    ] {
        let metrics = evaluate_tracker_case(
            &truth,
            &run.benchmark_predictions(),
            BenchmarkParameters {
                timestamp_tolerance_s: 0.001,
                min_confidence: 0.0,
            },
        )
        .unwrap();
        assert_eq!(metrics.comparable_samples, truth.len());
        assert_eq!(metrics.tracking_availability, Some(1.0));
        assert!(metrics.plate_center_mae_px.unwrap() < 0.25);
    }
}
