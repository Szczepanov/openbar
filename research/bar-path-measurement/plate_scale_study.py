"""Frozen #88 development diagnostic; never modifies canonical calibration."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import subprocess
import tempfile

import study_io as io
import label_package
import scale_reference as sr

FIXTURES = tuple(sorted('vbt-' + value for value in (
    'e169ecc156007a9e', '45854c8cdf352a6f', 'bb101cea545505fc', 'a8cd0fb856ab83ca',
    '4eb16d0c845e4bea', 'e2d13139fc682295', '017cea944dfbb194', 'ad613d6b13787901')))
VERSION = 1


def require(condition, message):
    if not condition:
        raise ValueError(message)


def positive(value, name):
    value = io.number(value, name)
    require(value > 0, f'{name} must be positive')
    return value


def write_new(path, value):
    require(not path.exists(), 'output already exists')
    with tempfile.TemporaryDirectory(prefix='plate-scale-write-', dir=path.parent) as temporary:
        staged = Path(temporary) / 'output.json'
        io.write(staged, value)
        require(not path.exists(), 'output already exists')
        staged.replace(path)


def validate_geometry(rows, geometry):
    io.samples(rows)
    require(isinstance(geometry, list) and len(rows) == len(geometry), 'geometry length mismatch')
    required = {'timestamp_s', 'fit_attempted', 'accepted', 'reject_reasons', 'base_confidence'}
    optional = {'radius_px', 'inlier_count', 'coverage_bins'}
    for row, fit in zip(rows, geometry):
        require(isinstance(fit, dict) and required <= set(fit) <= required | optional,
                'invalid geometry fields')
        require(io.number(fit['timestamp_s'], 'geometry timestamp', 0) == row['timestamp_s'],
                'geometry timestamp mismatch')
        require(type(fit['fit_attempted']) is bool and type(fit['accepted']) is bool,
                'geometry flags must be boolean')
        reasons = fit['reject_reasons']
        require(isinstance(reasons, list) and all(isinstance(x, str) and x for x in reasons),
                'invalid rejection reasons')
        require(io.number(fit['base_confidence'], 'base confidence', 0) <= 1,
                'base confidence must be <= 1')
        for key in ('inlier_count', 'coverage_bins'):
            if key in fit:
                require((not fit['accepted'] and fit[key] is None)
                        or (type(fit[key]) is int and fit[key] >= 0), f'invalid {key}')
        if fit.get('coverage_bins') is not None:
            require(fit['coverage_bins'] <= 36, 'coverage exceeds 36 bins')
        if 'radius_px' in fit:
            if fit['accepted'] or fit['radius_px'] is not None:
                positive(fit['radius_px'], 'fitted radius')


def estimate(rows, geometry, *, seed_timestamp_s, seed_radius_px, diameter_m):
    validate_geometry(rows, geometry)
    seed_time = io.number(seed_timestamp_s, 'seed timestamp', 0)
    seed_radius = positive(seed_radius_px, 'seed radius')
    diameter = positive(diameter_m, 'physical diameter')
    require(rows[0]['timestamp_s'] == seed_time, 'timeline must start at initialization')
    support = dict(initialization=0, eligible=0, lost=0, fallback=0, insufficient_geometry=0)
    eligible = []
    for row, fit in zip(rows, geometry):
        if row['timestamp_s'] == seed_time:
            support['initialization'] += 1
        elif row['state'] == 'lost':
            support['lost'] += 1
        elif not fit['accepted'] or not fit['fit_attempted'] or fit['reject_reasons']:
            support['fallback'] += 1
        elif (fit.get('coverage_bins', 0) < 18 or fit.get('inlier_count', 0) == 0
              or 'radius_px' not in fit):
            support['insufficient_geometry'] += 1
        else:
            support['eligible'] += 1
            eligible.append((row['timestamp_s'], fit['radius_px']))
    post_seed = len(rows) - support['initialization']
    fraction = len(eligible) / post_seed if post_seed else 0.0
    span = eligible[-1][0] - eligible[0][0] if eligible else 0.0
    reasons = []
    if len(eligible) < 10:
        reasons.append('eligible_count_below_10')
    if fraction < .5:
        reasons.append('eligible_fraction_below_half')
    if span < .5:
        reasons.append('eligible_span_below_0_5_s')
    result = dict(status='unsupported' if reasons else 'supported', reasons=reasons,
                  support=support, eligible_fraction=fraction, eligible_span_s=span,
                  diameter=None, seed_radius_deviation_pct=None,
                  scale_m_per_px=None, empirical_band_m_per_px=None)
    if eligible:
        radii = sorted(radius for _, radius in eligible)
        median = statistics.median(radii)
        p10, p90 = (radii[math.ceil(q * len(radii)) - 1] for q in (.1, .9))
        mean_time = statistics.mean(time for time, _ in eligible)
        mean_radius = statistics.mean(radii)
        denominator = sum((time - mean_time) ** 2 for time, _ in eligible)
        slope = (sum((time - mean_time) * 2 * (radius - mean_radius)
                     for time, radius in eligible) / denominator) if denominator else None
        result['diameter'] = dict(median_px=2 * median, p10_px=2 * p10, p90_px=2 * p90,
                                  min_px=2 * radii[0], max_px=2 * radii[-1],
                                  time_slope_px_per_s=slope)
        result['seed_radius_deviation_pct'] = 100 * (seed_radius / median - 1)
        if not reasons:
            result['scale_m_per_px'] = diameter / (2 * median)
            result['empirical_band_m_per_px'] = [diameter / (2 * p90), diameter / (2 * p10)]
    json.dumps(result, allow_nan=False)
    return result


def compare(estimated, seed_scale, reference):
    seed_scale = positive(seed_scale, 'seed scale')
    point, lower, upper = (positive(reference[f'reference_scale{part}_m_per_px'], 'reference')
                           for part in ('', '_lower', '_upper'))
    require(lower <= point <= upper, 'reference interval is reversed')
    result = {}
    for name, scale in (('seed', seed_scale), ('multi', estimated['scale_m_per_px']
                        if estimated['status'] == 'supported' else None)):
        error = 100 * (positive(scale, name) / point - 1) if scale is not None else None
        result[f'{name}_signed_relative_error_pct'] = error
        result[f'{name}_absolute_relative_error_pct'] = abs(error) if error is not None else None
        result[f'{name}_signed_error_range_pct'] = ([100 * (scale / upper - 1),
                                                     100 * (scale / lower - 1)]
                                                    if scale is not None else None)
    band = estimated['empirical_band_m_per_px'] if estimated['status'] == 'supported' else None
    result.update(band_contains_reference_point=band[0] <= point <= band[1] if band else None,
                  band_overlaps_reference_interval=band[0] <= upper and band[1] >= lower if band else None,
                  band_contains_reference_interval=band[0] <= lower and band[1] >= upper if band else None)
    json.dumps(result, allow_nan=False)
    return result


def decision(rows):
    require(len(rows) == 8 and len({r['fixture_id'] for r in rows}) == 8, 'decision requires eight unique cases')
    supported = [r for r in rows if r['estimate']['status'] == 'supported']
    comparisons = [r['comparison'] for r in supported]
    seed_mean = statistics.mean(r['comparison']['seed_absolute_relative_error_pct'] for r in rows)
    multi_mean = statistics.mean(c['multi_absolute_relative_error_pct'] for c in comparisons) if comparisons else None
    improved = sum(c['multi_absolute_relative_error_pct'] < c['seed_absolute_relative_error_pct'] for c in comparisons)
    worsening = max((c['multi_absolute_relative_error_pct'] - c['seed_absolute_relative_error_pct']
                     for c in comparisons), default=None)
    covered = sum(c['band_contains_reference_interval'] is True for c in comparisons)
    keep = (len(supported) == 8 and multi_mean < seed_mean and improved >= 6
            and worsening <= 1 and covered == 8)
    return dict(outcome='retain_research' if keep else 'reject', supported=len(supported),
                improved=improved, full_reference_intervals_covered=covered,
                seed_mean_absolute_error_pct=seed_mean,
                multi_common_support_mean_absolute_error_pct=multi_mean,
                seed_common_support_mean_absolute_error_pct=statistics.mean(
                    c['seed_absolute_relative_error_pct'] for c in comparisons) if comparisons else None,
                maximum_worsening_percentage_points=worsening)


def bind(item, prediction, geometry, analysis, confirmed_seed, probed, click, prediction_sha):
    """Tie retained artifacts to the confirmed geometry and authoritative source PTS."""
    require(set(geometry) == {'format', 'format_version', 'fixture_id', 'implementation', 'samples'}
            and geometry['format'] == 'openbar-research-geometry-sidecar'
            and type(geometry['format_version']) is int and geometry['format_version'] == 0,
            'unsupported geometry contract')
    require(geometry['fixture_id'] == item['id'] and geometry['implementation'] == prediction['implementation'],
            'geometry identity/provenance mismatch')
    validate_geometry(prediction['samples'], geometry['samples'])
    implementation = prediction['implementation']
    require(implementation['name'] == 'sam2.1-bplus-circle' and implementation['version'] == 'gpu-spike-3',
            'unexpected producer')
    tracker = analysis['provenance']['tracker']['implementation']
    require(tracker['implementation'] == implementation['name'] and tracker['version'] == implementation['version']
            and tracker['parameters']['prediction_sha256'] == prediction_sha, 'canonical producer mismatch')
    label_package.require_fixture_probe_match(item, probed)
    require((click['width_px'], click['height_px']) == io.size(item), 'reference display mismatch')
    times = [round(t, 6) for t in probed['timestamps_s']]
    require(len(set(times)) == len(times), 'ambiguous rounded PTS')
    require(type(click['frame_index']) is int and 0 <= click['frame_index'] < len(times)
            and times[click['frame_index']] == click['timestamp_s'], 'reference PTS mismatch')
    seed = analysis['manual_seed']
    for key in ('timestamp_s', 'frame_index', 'target', 'coordinate_space', 'source_rotation_deg'):
        require(seed[key] == confirmed_seed['seed'][key], 'confirmed seed geometry mismatch')
    require(0 <= seed['frame_index'] < len(times) and times[seed['frame_index']] == seed['timestamp_s'],
            'seed PTS mismatch')
    end = implementation['config']['end_s']
    require(implementation['config']['seed_timestamp_s'] == seed['timestamp_s'], 'producer seed mismatch')
    expected_times = [t for t in times if seed['timestamp_s'] <= t <= end]
    require([r['timestamp_s'] for r in prediction['samples']] == expected_times, 'prediction PTS mismatch')
    require(len(analysis['raw_observations']) == len(expected_times), 'canonical sample count mismatch')
    for predicted, raw in zip(prediction['samples'], analysis['raw_observations']):
        require(raw['timestamp_s'] == predicted['timestamp_s']
                and times[raw['frame_index']] == raw['timestamp_s'], 'canonical PTS mismatch')
        if predicted['state'] == 'lost':
            require(raw['tracking_state'] == 'lost' and 'measurement' not in raw, 'canonical loss mismatch')
        else:
            measured = raw.get('measurement', {})
            require(raw['tracking_state'] in ('tracked', 'low_confidence') and measured == {
                'timestamp_s': predicted['timestamp_s'], **predicted['center_px'],
                'confidence': predicted['confidence']}, 'canonical observation mismatch')


def freeze(root, manifest_path, session_path, output, cli):
    manifest = io.load(manifest_path)
    session = io.load(session_path)
    require(session.get('format') == 'openbar-research-vbt-session-record'
            and session.get('format_version') == 1, 'unsupported confirmed session')
    clips = session['clips']
    require(tuple(sorted(c['fixture_id'] for c in clips)) == FIXTURES, 'frozen fixture set mismatch')
    hashes = {}

    def record(path):
        path = path.resolve()
        hashes[str(path)] = io.digest(path)
        return path

    def source(relative):
        path = (root / relative).resolve()
        require(path.is_relative_to(root.resolve()), 'input path escapes repository')
        return record(path)

    record(manifest_path)
    record(session_path)
    record(cli)
    cases = []
    for clip in sorted(clips, key=lambda c: c['fixture_id']):
        fixture_id = clip['fixture_id']
        item = io.fixture(manifest, fixture_id)
        io.private_output(item, output)
        require(clip['decision'] == 'confirmed', 'unconfirmed fixture refused')
        media = io.media_path(item, root)
        record(media)
        require(clip['video']['sha256'] == item['media']['sha256']
                and source(clip['video']['path']) == media, 'session video mismatch')
        seed_path = source(clip['seed']['path'])
        csv_path = source(clip['scale_click_csv']['path'])
        for key, path in (('seed', seed_path), ('scale_click_csv', csv_path)):
            require(hashes[str(path)] == clip[key]['sha256'], 'confirmed input hash mismatch')
        seed = io.seed(seed_path, item)
        stem = f'validation/private/vbt/sam2/{fixture_id}.sam2-bplus-circle.'
        prediction_path = source(stem + 'prediction-v1.json')
        prediction = io.prediction(prediction_path, item)
        geometry = io.load(source(stem + 'geometry.json'))
        analysis_path = source(stem + 'analysis-v1.json')
        analysis = io.load(analysis_path)
        # The Rust consumer owns canonical cross-field validation; no Python substitute.
        with tempfile.TemporaryDirectory(prefix='plate-scale-validate-', dir=output.parent) as temporary:
            subprocess.run([str(cli), 'render', '--analysis', str(analysis_path),
                            '--output', str(Path(temporary) / 'analysis.svg')], check=True,
                           capture_output=True, text=True)
        seed_scale = sr.plate_scale_from_analysis(analysis, fixture_id, item['media']['sha256'])
        package = f'validation/private/vbt/sessions/2026-10-03/packages/{fixture_id}/'
        metadata = io.load(source(package + 'metadata.json'))
        config = io.load(source(package + 'reference-config.json'))
        click, _ = sr.parse_click_csv(csv_path.read_bytes())
        sr.validate_package_binding(metadata, config, click, fixture_id, item['media']['sha256'])
        probed = label_package.probe(media)
        bind(item, prediction, geometry, analysis, seed, probed, click, io.digest(prediction_path))
        reference = sr.measure_segment(known_length_m=click['known_length_m'],
            point_a={'x_px': click['point_a_x_px'], 'y_px': click['point_a_y_px']},
            point_b={'x_px': click['point_b_x_px'], 'y_px': click['point_b_y_px']},
            width_px=click['width_px'], height_px=click['height_px'])
        cases.append(dict(fixture_id=fixture_id, prediction=prediction, geometry=geometry,
                          seed=analysis['manual_seed'], diameter_m=analysis['calibration']['scale']['diameter_m'],
                          seed_scale_m_per_px=seed_scale, reference=reference,
                          probed=probed, analysis_sha256=io.digest(analysis_path)))
    for path in (Path(__file__), io.ROOT / 'research/bar-path-measurement/study_io.py',
                 io.ROOT / 'validation/tools/scale_reference.py', io.ROOT / 'validation/tools/label_package.py',
                 io.ROOT / 'validation/tools/schema_check.py', io.ROOT / 'validation/tools/annotations.py',
                 io.ROOT / 'research/bar-path-measurement/tests/test_plate_scale_study.py',
                 io.ROOT / 'docs/plans/PLATE_SCALE_STUDY_PLAN.md'):
        record(path)
    for path in sorted((io.ROOT / 'validation/schema').glob('*.json')):
        record(path)
    require(all(io.digest(Path(p)) == sha for p, sha in hashes.items()), 'input changed during freeze')
    require(not output.exists(), 'freeze output already exists')
    write_new(output, dict(format='openbar-research-plate-scale-freeze', format_version=VERSION,
                         commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=io.ROOT, text=True).strip(),
                         inputs_sha256=hashes, cases=cases))


def score(bundle):
    require(set(bundle) == {'format', 'format_version', 'commit', 'inputs_sha256', 'cases'}
            and bundle['format'] == 'openbar-research-plate-scale-freeze'
            and type(bundle['format_version']) is int and bundle['format_version'] == VERSION,
            'unsupported frozen-input contract')
    require(tuple(c['fixture_id'] for c in bundle['cases']) == FIXTURES, 'frozen fixture set mismatch')
    rows = []
    for case in bundle['cases']:
        seed = case['seed']
        result = estimate(case['prediction']['samples'], case['geometry']['samples'],
                          seed_timestamp_s=seed['timestamp_s'], seed_radius_px=seed['target']['radius_px'],
                          diameter_m=case['diameter_m'])
        rows.append(dict(fixture_id=case['fixture_id'], estimate=result, seed_scale_m_per_px=case['seed_scale_m_per_px'],
                         comparison=compare(result, case['seed_scale_m_per_px'], case['reference']),
                         reference=case['reference'], producer=case['prediction']['implementation']))
    return dict(format='openbar-research-plate-scale-report', format_version=VERSION,
                cases=rows, decision=decision(rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('freeze')
    prepare.add_argument('--repository-root', type=Path, required=True)
    prepare.add_argument('--manifest', type=Path, required=True)
    prepare.add_argument('--session', type=Path, required=True)
    prepare.add_argument('--output', type=Path, required=True)
    prepare.add_argument('--cli', type=Path, required=True, help='existing authoritative openbar-cli binary')
    replay = commands.add_parser('score')
    replay.add_argument('--bundle', type=Path, required=True)
    replay.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(args.output.resolve().is_relative_to((io.ROOT / 'validation/private').resolve()),
            'study artifacts must stay private')
    require(not args.output.exists(), 'output already exists')
    if args.command == 'freeze':
        freeze(args.repository_root, args.manifest, args.session, args.output, args.cli)
    else:
        bundle = io.load(args.bundle)
        report = score(bundle)
        report['frozen_input_sha256'] = io.digest(args.bundle)
        write_new(args.output, report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
