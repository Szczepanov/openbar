from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import csrt_scale_ablation as ablation


class ScaleAblationTests(unittest.TestCase):
    def producer(self, *, failure=False):
        created = []

        class Params:
            number_of_scales = 33
            scale_step = 1.02

        class Tracker:
            def init(self, frame, box):
                return None

            def update(self, frame):
                return True, (0, 0, -1, 10)

        original = Tracker

        def create(params):
            created.append(params.number_of_scales)
            return Tracker()

        def track(*args):
            self.assertIs(args[-1], False)
            tracker = producer.TRACKERS['csrt']()
            frame = SimpleNamespace(shape=(30, 30, 3))
            tracker.init(frame, (0, 0, 10, 10))
            tracker.update(frame)
            if failure:
                raise ValueError('decode failed')
            return {'implementation': {'name': 'opencv-csrt', 'version': 'spike-1', 'config': {}},
                    'runtime': {'processing_wall_s': 1},
                    'samples': [{'state': 'tracked'}, {'state': 'lost'}]}, None

        producer = SimpleNamespace(cv2=SimpleNamespace(TrackerCSRT_Params=Params,
                                   TrackerCSRT=SimpleNamespace(create=create)),
                                   TRACKERS={'csrt': original}, track=track,
                                   valid_box=lambda box, width, height: box[2] > 0 and box[3] > 0)
        return producer, original, created

    def test_baseline_single_scale_provenance_and_invalid_box_loss(self):
        for single in (False, True):
            with self.subTest(single_scale=single):
                producer, original, created = self.producer()
                prediction, runtime, trace = ablation.run_case(producer, 'm', 'id', 's', single_scale=single)
                self.assertIs(producer.TRACKERS['csrt'], original)
                self.assertEqual(created, [1] if single else [])
                self.assertEqual(trace['boxes_xywh_px'], [(0, 0, 10, 10), None])
                self.assertEqual(trace['update_success'], [True, True])
                self.assertEqual(trace['parameters'], {'number_of_scales': 1 if single else 33, 'scale_step': 1.02})
                self.assertEqual(runtime, {'processing_wall_s': 1})
                self.assertNotIn('runtime', prediction)
                self.assertEqual(prediction['samples'][1], {'state': 'lost'})
                if single:
                    self.assertEqual(prediction['implementation']['name'], 'opencv-csrt-single-scale-research')
                    self.assertEqual(prediction['implementation']['config']['csrt_parameters'], trace['parameters'])
                else:
                    self.assertEqual(prediction['implementation'], {'name': 'opencv-csrt', 'version': 'spike-1', 'config': {}})

    def test_constructor_restored_on_decoder_failure(self):
        producer, original, _ = self.producer(failure=True)
        with self.assertRaisesRegex(ValueError, 'decode failed'):
            ablation.run_case(producer, 'm', 'id', 's', single_scale=True)
        self.assertIs(producer.TRACKERS['csrt'], original)

    def test_constructor_restored_on_constructor_failure(self):
        producer, original, _ = self.producer()

        def fail(params):
            raise ValueError('constructor failed')

        producer.cv2.TrackerCSRT.create = fail
        with self.assertRaisesRegex(ValueError, 'constructor failed'):
            ablation.run_case(producer, 'm', 'id', 's', single_scale=True)
        self.assertIs(producer.TRACKERS['csrt'], original)


if __name__ == '__main__':
    unittest.main()
