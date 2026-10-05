"""Algebraic box-error decomposition; does not infer tracker-internal causes."""
from __future__ import annotations

import math

import study_io as io


def decompose(boxes, rows, shifts, seed_box):
    io.samples(rows)
    if len(boxes) != len(rows) or len(shifts) != len(rows) or rows[0]['state'] != 'tracked':
        raise ValueError('one box/program record per observation and tracked seed required')
    x0, y0, w0, h0 = (io.number(v, 'seed box') for v in seed_box)
    if w0 <= 0 or h0 <= 0:
        raise ValueError('positive seed box size required')
    anchor = rows[0]['center_px']
    rounding = (x0+(w0-1)/2-anchor['x_px'], y0+(h0-1)/2-anchor['y_px'])
    result = []
    for i, (box, row, shift) in enumerate(zip(boxes, rows, shifts)):
        entry = {'frame_index': i, 'timestamp_s': row['timestamp_s'], 'state': row['state']}
        dx, dy = (io.number(v, 'shift') for v in shift)
        if row['state'] == 'lost':
            if box is not None:
                raise ValueError('lost diagnostics cannot carry bounds')
        else:
            x, y, w, h = (io.number(v, 'box') for v in box)
            if w <= 0 or h <= 0:
                raise ValueError('positive box size required')
            location = (x-x0-dx, y-y0-dy)
            size = ((w-w0)/2, (h-h0)/2)
            residual = tuple(a+b for a, b in zip(location, size))
            total = tuple(a+b for a, b in zip(residual, rounding))
            # Initialization emits the confirmed seed, not the rounded integer-box centre.
            if i > 0:
                p = row['center_px']
                emitted = (p['x_px']-anchor['x_px']-dx, p['y_px']-anchor['y_px']-dy)
                if any(abs(a-b) > .000501 for a, b in zip(total, emitted)):
                    raise ValueError('box decomposition disagrees with emitted centre')
                entry.update(top_left_residual_px=location, half_size_change_px=size,
                             fixed_rounding_offset_px=rounding, seed_box_relative_residual_px=residual,
                             error_xy_px=total, error_px=math.hypot(*total), confidence=row['confidence'])
            entry['box_xywh_px'] = (x, y, w, h)
        result.append(entry)
    return result
