"""Frozen synthetic target-disk/background separation; not plate segmentation."""
from controlled_motion import transform
import study_io as io


def background_layers(source, target, shifts):
    import numpy as np

    if source.dtype != np.uint8 or source.ndim != 3 or source.shape[2] != 3 or not shifts:
        raise ValueError('BGR uint8 raster and nonempty motion program required')
    cx, cy, radius = (io.number(v, 'target', 0) for v in target)
    yy, xx = np.ogrid[:source.shape[0], :source.shape[1]]
    mask = (xx-cx)**2 + (yy-cy)**2 <= (radius+8)**2
    corridor = np.zeros_like(mask)
    for shift in shifts:
        corridor |= transform(mask, shift, target, 'translation', 0)
    background = source.copy()
    background[corridor] = 128
    layer = np.zeros_like(source)
    layer[mask] = source[mask]
    initial = background.copy()
    initial[mask] = source[mask]
    return initial, background, layer, mask


def target_on_background(background, layer, mask, shift, target):
    if layer.shape != background.shape or mask.shape != background.shape[:2]:
        raise ValueError('target/background raster mismatch')
    shifted_mask = transform(mask, shift, target, 'translation', 0)
    shifted_layer = transform(layer, shift, target, 'translation', 0)
    output = background.copy()
    output[shifted_mask] = shifted_layer[shifted_mask]
    return output
