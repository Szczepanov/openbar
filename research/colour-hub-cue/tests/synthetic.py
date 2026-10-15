"""Deterministic synthetic frames shared by the colour and hub cue tests."""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SIZE = (320, 480)  # height, width
BACKGROUND = (200, 200, 205)
PLATE = (70, 60, 55)
HUB = (190, 190, 190)
RADIUS = 60.0


def render(cx: float, cy: float, *, radius: float = RADIUS, ring: bool = False, hub: bool = True,
           noise: float = 1.5, seed: int = 0) -> np.ndarray:
    """Dark plate with a bright hub on a light background; optionally a black outer ring.

    The ring (black, from 1.12 r to 1.30 r) puts a stronger edge than the plate rim inside the radial
    band, with a background-coloured area just inside it, so only the colour gate can reject it.
    """
    image = np.full((*SIZE, 3), BACKGROUND, np.uint8)
    centre = (int(round(cx * 16)), int(round(cy * 16)))

    def disk(r, colour):
        cv2.circle(image, centre, int(round(r * 16)), colour, -1, cv2.LINE_AA, 4)

    if ring:
        disk(1.30 * radius, (0, 0, 0))
        disk(1.12 * radius, BACKGROUND)
    disk(radius, PLATE)
    if hub:
        disk(0.10 * radius, HUB)
    rng = np.random.default_rng(seed)
    noisy = image.astype(np.float64) + rng.normal(0.0, noise, image.shape)
    return np.clip(np.rint(noisy), 0, 255).astype(np.uint8)
