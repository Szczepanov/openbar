"""Bounded injected-displacement diagnostics; never human annotations or physical truth."""
from __future__ import annotations

import math

import study_io as io

COUNT = 33
OCCLUDED = range(12, 16)


def program():
    return [(4*i, -2*i) if i <= 16 else
            (64 - 8*(i-16), -32 + 4*(i-16)) if i <= 24 else (0, 0)
            for i in range(COUNT)]


def transform(source, shift, target, case, index):
    import numpy as np

    if case not in ('translation', 'blur', 'occlusion'):
        raise ValueError('unknown case')
    dx, dy = shift
    if any(isinstance(v, bool) or not isinstance(v, int) for v in shift):
        raise ValueError('integer pixel translation required')
    height, width = source.shape[:2]
    cx, cy, radius = (io.number(v, 'target', 0) for v in target)
    if radius <= 0 or not (radius+8 <= cx+dx < width-radius-8
                          and radius+8 <= cy+dy < height-radius-8):
        raise ValueError('translated target margin leaves raster')
    output = np.zeros_like(source)
    x0, x1 = max(0, dx), min(width, width+dx)
    y0, y1 = max(0, dy), min(height, height+dy)
    output[y0:y1, x0:x1] = source[y0-dy:y1-dy, x0-dx:x1-dx]
    if case == 'occlusion' and index in OCCLUDED:
        output[math.floor(cy+dy-radius-8):math.ceil(cy+dy+radius+8)+1,
               math.floor(cx+dx-radius-8):math.ceil(cx+dx+radius+8)+1] = 0
    return output


def require_timestamps(timestamps):
    if len(timestamps) != COUNT:
        raise ValueError('expected 33 PTS')
    for i, value in enumerate(timestamps):
        if abs(io.number(value, 'PTS', 0) - i/30) > .000001:
            raise ValueError('PTS differs from frozen motion program')
    rounded = [round(value, 6) for value in timestamps]
    if any(b <= a for a, b in zip(rounded, rounded[1:])):
        raise ValueError('rounded PTS collision')
    return rounded


def distribution(values):
    if not values:
        return {'count': 0, 'mean_px': None, 'p90_px': None, 'max_px': None}
    return {'count': len(values), 'mean_px': sum(values)/len(values),
            'p90_px': sorted(values)[math.ceil(.9*len(values))-1], 'max_px': max(values)}


def score(rows, timestamps, shifts, case):
    io.samples(rows)
    times = require_timestamps(timestamps)
    if [r['timestamp_s'] for r in rows] != times or len(shifts) != COUNT:
        raise ValueError('predictions/program must cover exact decoded PTS')
    if rows[0]['state'] != 'tracked' or shifts[0] != (0, 0):
        raise ValueError('tracked unshifted initialization required')
    shifts = [(io.number(x, 'dx'), io.number(y, 'dy')) for x, y in shifts]
    anchor = rows[0]['center_px']
    details = []
    adjacent = []
    for i, row in enumerate(rows):
        item = {'frame_index': i, 'timestamp_s': times[i], 'state': row['state']}
        if row['state'] == 'tracked':
            p = row['center_px']
            ex = p['x_px'] - anchor['x_px'] - shifts[i][0]
            ey = p['y_px'] - anchor['y_px'] - shifts[i][1]
            item.update(error_x_px=ex, error_y_px=ey, error_px=math.hypot(ex, ey),
                        confidence=row['confidence'])
            if i > 0 and rows[i-1]['state'] == 'tracked' and times[i]-times[i-1] <= .2:
                prev = rows[i-1]['center_px']
                adjacent.append(math.hypot(p['x_px']-prev['x_px']-(shifts[i][0]-shifts[i-1][0]),
                                           p['y_px']-prev['y_px']-(shifts[i][1]-shifts[i-1][1])))
        details.append(item)
    supported = [d for d in details[1:] if d['state'] == 'tracked']
    wrong = [d for d in supported if d['error_px'] > 3]
    confidences = [d['confidence'] for d in wrong]
    recovery = None
    if case == 'occlusion':
        for i in range(16, COUNT-2):
            if all(d.get('error_px', math.inf) <= 3 for d in details[i:i+3]):
                recovery = {'frame_index': i, 'latency_s': times[i]-times[16]}
                break
    return {'eligible': COUNT-1, 'tracked': len(supported), 'lost': COUNT-1-len(supported),
            'missing': 0, 'displacement_error': distribution([d['error_px'] for d in supported]),
            'adjacent_error': distribution(adjacent),
            'return_hold_error': distribution([d['error_px'] for d in details[25:] if d['state'] == 'tracked']),
            'final': details[-1], 'wrong_over_3_px': len(wrong),
            'high_confidence_wrong': sum(c >= .8 for c in confidences),
            'wrong_confidence_range': [min(confidences), max(confidences)] if confidences else None,
            'occluded': details[12:16] if case == 'occlusion' else [],
            'post_occlusion_error': distribution([d['error_px'] for d in details[16:] if d['state'] == 'tracked'])
                                    if case == 'occlusion' else None,
            'recovery_three_frames': recovery, 'frames': details}


def common_support(first, second):
    pairs = [(a, b) for a, b in zip(first['frames'][1:], second['frames'][1:])
             if a['state'] == b['state'] == 'tracked']
    return {'count': len(pairs), 'first_error': distribution([a['error_px'] for a, _ in pairs]),
            'second_error': distribution([b['error_px'] for _, b in pairs])}
