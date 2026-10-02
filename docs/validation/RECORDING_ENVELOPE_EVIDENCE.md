# Recording-envelope evidence study

Study: `m0-recording-envelope-boundaries`  
Issue: #53

This report is evidence bookkeeping, not a marketing accuracy claim. Non-unknown boundaries are
accepted only when the study's preregistered evidence policy and frozen-candidate checks pass.

Ready to promote boundaries: **False**.
- Blocker: tracker/filter candidate is not frozen
- Blocker: minimum held-out fixture count is not preregistered
- Blocker: minimum comparable sample count is not preregistered
- Blocker: no benchmark result artifacts are referenced
- Blocker: no matched-reference measurement evidence covers calibrated_position
- Blocker: no matched-reference measurement evidence covers velocity
- Blocker: fixture manifest contains no eligible held-out non-synthetic fixtures

| Boundary | Dimension / variable | Tested values | Eligible fixtures | Comparable samples | Classification | Rationale |
| --- | --- | --- | ---: | ---: | --- | --- |
| yaw | camera_geometry / `approx_yaw_deg` | not yet tested | 0 | 0 | **unknown** | No controlled held-out real yaw sweep exists. |
| pitch | camera_geometry / `approx_pitch_deg` | not yet tested | 0 | 0 | **unknown** | No controlled held-out real pitch sweep exists. |
| camera-height | camera_geometry / `camera_height_m` | not yet tested | 0 | 0 | **unknown** | Camera-height boundary has not been measured. |
| camera-roll | camera_geometry / `camera_roll_deg` | not yet tested | 0 | 0 | **unknown** | Camera-roll boundary has not been measured on held-out real footage. |
| distance | camera_geometry / `distance_m` | not yet tested | 0 | 0 | **unknown** | No controlled held-out real distance sweep exists. |
| framing | framing / `minimum_plate_frame_margin_px` | not yet tested | 0 | 0 | **unknown** | Framing/cropping tolerance has not been quantified. |
| fps | video_quality / `measured_fps` | not yet tested | 0 | 0 | **unknown** | No held-out real FPS sweep exists. |
| resolution | video_quality / `display_resolution_px` | not yet tested | 0 | 0 | **unknown** | No held-out real resolution sweep exists. |
| plate-size | target_visibility / `plate_diameter_px` | not yet tested | 0 | 0 | **unknown** | No real plate-pixel-size boundary evidence exists. |
| contrast | target_visibility / `plate_background_contrast` | not yet tested | 0 | 0 | **unknown** | Contrast metric and boundary must be preregistered and measured on real held-out clips. |
| motion-blur | video_quality / `exposure_or_blur_metric` | not yet tested | 0 | 0 | **unknown** | Realistic shutter/motion-blur boundary has not been measured. |
| compression | video_quality / `compression_level` | not yet tested | 0 | 0 | **unknown** | Compression boundary has not been measured. |
| occlusion | target_visibility / `max_consecutive_occlusion_duration_s` | not yet tested | 0 | 0 | **unknown** | Short/medium/long real occlusion spans have not been benchmarked. |
| camera-motion | camera_stability / `background_motion_metric` | not yet tested | 0 | 0 | **unknown** | Minor handheld versus meaningful camera motion has not been quantified. |

## Frozen candidate

```json
{
  "filter": null,
  "git_commit": null,
  "note": "Issue #53 depends on selecting and freezing a tracker/filter candidate after representative real evidence exists.",
  "pipeline_version": "m0-benchmark-v1",
  "status": "not_frozen",
  "tracker": null
}
```
