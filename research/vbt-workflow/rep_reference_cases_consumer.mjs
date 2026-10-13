// #180 parity bridge: run the pinned consumer's own parser and segmentation over the rep-metrics
// reference cases (validation/reference-cases/rep-metrics-v1.json).
//
// Usage:
//   node --experimental-strip-types rep_reference_cases_consumer.mjs emit  <consumer-app-dir> <cases.json>
//   node --experimental-strip-types rep_reference_cases_consumer.mjs check <consumer-app-dir> <cases.json>
//
// For every case this builds the minimal analysis-v1 subset the consumer's parseOpenBarAnalysis reads
// (kinematics derived with backward-difference@1 from the case samples and the case's recorded
// max_gap_s / min_confidence), calls parseOpenBarAnalysis(text, CONCENTRIC_SEGMENTATION_V2) and takes the
// reps exactly as returned. No segmentation or metric logic lives here.
//
//   emit  writes one JSON document {consumer_commit, cases: {<case id>: {breaks, reps}}} to stdout.
//   check compares that output with each case's expected.consumer_reps / expected.consumer_breaks and
//         exits 1 on any difference. It is a parity check against the pin, not a pass/fail accuracy claim.
//
// The consumer checkout must be at the commit recorded in the case file (pinned_consumer.commit).
import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { isDeepStrictEqual } from 'node:util';

async function consumerModule(appDir, name) {
    return import(pathToFileURL(resolve(appDir, 'src', 'observations', name)).href);
}

// Mirrors core derive_velocity (backward-difference@1) so the consumer sees what OpenBar would export.
function kinematicSamples(samples, config) {
    return samples.map((sample, index) => {
        const previous = samples[index - 1];
        let vy = null;
        if (previous) {
            const dt = sample.timestamp_s - previous.timestamp_s;
            const supported = dt <= config.max_gap_s
                && previous.confidence >= config.min_confidence
                && sample.confidence >= config.min_confidence;
            vy = supported ? (sample.y_m - previous.y_m) / dt : null;
        }
        return {
            timestamp_s: sample.timestamp_s, x_m: 0, y_m: sample.y_m,
            vx_mps: previous && vy !== null ? 0 : null, vy_mps: vy,
            confidence: previous ? Math.min(previous.confidence, sample.confidence) : sample.confidence,
        };
    });
}

function analysisText(testCase, config) {
    const samples = kinematicSamples(testCase.samples, config);
    const positions = samples.map(({ timestamp_s, x_m, y_m, confidence }) => ({ timestamp_s, x_m, y_m, confidence }));
    return JSON.stringify({
        schema_version: 1,
        identity: { source_id: testCase.id, source_sha256: 'a'.repeat(64) },
        calibration: {
            method: 'plate_diameter', method_version: 1,
            scale: { diameter_m: 0.45, diameter_px: 200, metres_per_pixel: 0.00225 },
            coordinate_convention: 'reference_centre_x_right_y_up',
            quality: { status: 'unassessed' },
        },
        derived: {
            calibrated: { samples: positions },
            kinematics: {
                input: 'calibrated',
                method: { implementation: 'backward-difference', version: '1', parameters: config },
                samples,
            },
        },
        provenance: {
            pipeline: { openbar_version: '0.1.0' },
            tracker: { id: 'synthetic', implementation: { implementation: 'synthetic', version: '1', parameters: {} } },
        },
    });
}

function consumerOutput(parser, segmentation, testCase, config) {
    const parsed = parser.parseOpenBarAnalysis(analysisText(testCase, config), segmentation.CONCENTRIC_SEGMENTATION_V2);
    // The adapter drops the run indices; the segmenter returns them, in the same order as parsed.reps.
    const segments = segmentation.segmentConcentricReps(parsed.frames, segmentation.CONCENTRIC_SEGMENTATION_V2);
    return {
        breaks: parsed.breaks.map(({ beforeIndex, afterIndex, reason }) => ({ before_index: beforeIndex, after_index: afterIndex, reason })),
        reps: parsed.reps.map((rep, position) => ({
            index: rep.index, start_frame: rep.startFrame, end_frame: rep.endFrame, frame_count: rep.frameCount,
            run_start_index: segments[position].runStartIndex, run_end_index: segments[position].runEndIndex,
            start_time_s: rep.startTimeS, end_time_s: rep.endTimeS, duration_s: rep.durationS,
            mean_velocity_mps: rep.meanVelocityMps, peak_velocity_mps: rep.peakVelocityMps,
            rom_cm: rep.romCm, complete: rep.complete, exclusion: rep.exclusion,
        })),
    };
}

const [mode, appDir, casesPath] = process.argv.slice(2);
if (!['emit', 'check'].includes(mode) || !appDir || !casesPath) {
    console.error('usage: rep_reference_cases_consumer.mjs <emit|check> <consumer-app-dir> <cases.json>');
    process.exit(2);
}
const document = JSON.parse(await readFile(resolve(casesPath), 'utf-8'));
const config = document.kinematics;
const head = execFileSync('git', ['-C', resolve(appDir), 'rev-parse', 'HEAD'], { encoding: 'utf-8' }).trim();
if (head !== document.pinned_consumer.commit) {
    console.error(`consumer checkout is at ${head}, expected pinned commit ${document.pinned_consumer.commit}`);
    process.exit(1);
}
const parser = await consumerModule(appDir, 'openBarAnalysis.ts');
const segmentation = await consumerModule(appDir, 'concentricSegmentation.ts');
const actual = {};
for (const testCase of document.cases) {
    actual[testCase.id] = consumerOutput(parser, segmentation, testCase, {
        max_gap_s: config.max_gap_s, min_confidence: config.min_confidence,
    });
}
if (mode === 'emit') {
    process.stdout.write(`${JSON.stringify({ consumer_commit: head, cases: actual }, null, 2)}\n`);
} else {
    let failed = 0;
    for (const testCase of document.cases) {
        const expected = { breaks: testCase.expected.consumer_breaks, reps: testCase.expected.consumer_reps };
        const same = isDeepStrictEqual(actual[testCase.id], expected);
        console.log(`${same ? 'MATCH   ' : 'MISMATCH'} ${testCase.id}`);
        if (!same) failed += 1;
    }
    process.exit(failed === 0 ? 0 : 1);
}
