"""Observe one frozen CSRT scale-search ablation through the unchanged research producer."""
from __future__ import annotations


def run_case(producer, manifest_path, fixture_id, seed_path, *, single_scale=False):
    """Return raw prediction, separate runtime and coordinate-free-loss box observations.

    This temporarily replaces the producer's constructor and restores it even on failure.
    Run sequentially in an isolated research process; it is not a production tracker API.
    """
    params = producer.cv2.TrackerCSRT_Params()
    defaults = {name: getattr(params, name) for name in dir(params) if not name.startswith('_')}
    if single_scale:
        params.number_of_scales = 1
    effective = {name: getattr(params, name) for name in defaults}
    original = producer.TRACKERS['csrt']
    boxes, success = [], []

    class ObservedCSRT:
        def __init__(self):
            self.inner = producer.cv2.TrackerCSRT.create(params) if single_scale else original()

        def init(self, frame, box):
            result = self.inner.init(frame, box)
            boxes.append(tuple(box))
            success.append(True)
            return result

        def update(self, frame):
            ok, box = self.inner.update(frame)
            success.append(bool(ok))
            boxes.append(tuple(box) if ok and producer.valid_box(box, frame.shape[1], frame.shape[0]) else None)
            return ok, box

    producer.TRACKERS['csrt'] = ObservedCSRT
    try:
        prediction, sidecar = producer.track(manifest_path, fixture_id, seed_path, 'csrt', None, False)
    finally:
        producer.TRACKERS['csrt'] = original
    if sidecar is not None or len(boxes) != len(prediction['samples']):
        raise ValueError('one plain CSRT box record per observation required')
    for box, row in zip(boxes, prediction['samples']):
        if (box is None) != (row['state'] == 'lost'):
            raise ValueError('box state disagrees with producer observation')
    runtime = prediction.pop('runtime')
    if single_scale:
        implementation = prediction['implementation']
        implementation['name'] = 'opencv-csrt-single-scale-research'
        implementation['version'] = 'scale-ablation-1'
        implementation['config']['csrt_parameters'] = effective
        implementation['config']['ablation'] = {'parameter': 'number_of_scales', 'baseline': defaults['number_of_scales'], 'value': 1}
    return prediction, runtime, {'boxes_xywh_px': boxes, 'update_success': success, 'parameters': effective}
