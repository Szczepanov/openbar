# ADR-0007: Integer pixel coordinates are pixel centres

- Status: Accepted
- Date: 2026-10-01

## Context

Seeds, annotations, tracker observations, benchmark comparisons and the canonical `Analysis` all
store continuous display-oriented pixel coordinates (origin top-left, +X right, +Y down, after
rotation metadata; ADR-0006). The contracts in `docs/validation/ANNOTATION.md` and
`MANUAL_TARGET_SEED.md` described the origin as "the top-left corner of the display frame" and
the valid range as `0 <= x < width`. That reads as a pixel-edge convention, where pixel `i` spans
`i..i+1` and its centre is `i + 0.5`.

The code does not work that way:

- the contrast tracker's weighted centroid weights column `x` at `f64::from(x)`;
- the template tracker reports the matched pixel index itself as the centre;
- both trackers map a continuous centre to a pixel with `round()`, so pixel `i` owns
  `[i - 0.5, i + 0.5)`;
- the synthetic test and experiment frames draw a disk as the pixels with
  `dx² + dy² <= r²` around an integer centre, so their ground truth centre is a pixel centre;
- the labelling page merged in PR #52 converts image-space clicks with `u - 0.5` and maps stored coordinates back with `x + 0.5`.

Under the edge reading, every tracker output would be biased by half a pixel up and left. The
benchmark compares annotations against predictions, so a human annotating under one convention
and a tracker measuring under the other would add a systematic 0.71 px error to an M0 budget of
3 px MAE.

Calibration and kinematics only use coordinate differences (`x_px - x0_px`) and the seed
diameter. They are unaffected by the choice. The benchmark is unaffected only if both inputs
share one convention.

## Decision

OpenBar uses the **pixel-centre** convention for every pixel coordinate it stores or exchanges:

- the integer coordinate `(i, j)` is the centre of the pixel in column `i`, row `j`;
- pixel `i` covers the continuous interval `[i - 0.5, i + 0.5)` on each axis;
- the origin `(0, 0)` is the centre of the top-left pixel, and the top-left corner of the frame
  is `(-0.5, -0.5)`;
- point coordinates over the raster occupy `[-0.5, width - 0.5) × [-0.5, height - 0.5)`;
- the outer raster edges themselves are at `-0.5`, `width - 0.5`, `-0.5`, and `height - 0.5`;
- `GrayscaleImage::intensity(x, y)` is the sample at the pixel centred on `(x, y)`.

The serialized origin names (`display_top_left` in seeds, `top_left` in annotations) are
unchanged. They name the top-left pixel, whose centre is the origin.

### v1 validation window

The v1 validators keep their existing acceptance window instead of the exact pixel-centre geometry:

| Check | Accepted in v1 | Exact pixel-centre geometry |
| --- | --- | --- |
| Point (seed centre, annotation centre, analysis measurement) | `0 <= x < width` | `-0.5 <= x < width - 0.5` |
| Target bounds (seed, annotation, analysis, tracker `bounds_fit`) | `0 <= left`, `right <= width` | `-0.5 <= left`, `right <= width - 0.5` |

This window deliberately differs from the pixel-centre geometry. It is half a pixel stricter on the left and
top, and half a pixel looser on the right and bottom. We keep it because:

1. Existing v1 inputs may legally occupy the half-pixel mismatch region. For example, an
   annotation centre at `width - 0.25` is valid v1 even though it is outside the exact point
   domain, and a seed/observation bound may end at `right = width`. The first-party labelling page
   deliberately emits only the intersection `[0, width - 0.5)`, but the persisted v1 contract is
   broader and tracker bounds continue to obey that contract.
2. Moving the window is therefore a real contract and behaviour change, not a documentation-only
   cleanup. `annotation-v1.schema.json` pins `minimum: 0` on centres and bounds, and narrowing the
   right edge would reject documents that are valid today. Under the AGENTS.md versioning rules
   that needs version bumps across seed, annotation and analysis formats; changing tracker edge
   acceptance would also require explicit tracker-version/benchmark review.
3. The disagreement is confined to a half-pixel border. Preserve it explicitly for v1
   compatibility, while first-party producers should prefer the intersection of both windows.

Producers should emit points in `[0, width - 0.5) × [0, height - 0.5)`, the intersection of the
two windows, so their output stays valid if a later schema version adopts the exact extent.

### Consumers that draw pixels

Anything that places a coordinate over a raster image must draw pixel `i` with its edges at `i`
and `i + 1` in image units and the coordinate at `x + 0.5`. The diagnostic SVG renderer
(`diagnostic-svg@2`) does this. Version 1 drew overlays at `x`, half a pixel up and left of the
measured centre.

## Consequences

- Annotation, seed and tracker coordinates can be compared without a half-pixel correction.
- No serialized shape, schema version or golden fixture changes. The decision documents what the
  trackers already did.
- The renderer version changes from `diagnostic-svg@1` to `@2`, because overlay positions in its
  output move by half a source pixel.
- Future decoders, trackers and UI layers must follow this convention. A layer that resamples or
  crops frames must map coordinates through pixel centres, for example
  `x_out = (x_in + 0.5) * scale - 0.5`, rather than scaling `x_in` directly.
- Adopting the exact pixel-centre geometry for validation would need a new schema version for each
  affected format.
