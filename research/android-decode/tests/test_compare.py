"""Unit tests for the #258 parity/latency harness (stdlib unittest)."""

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import compare  # noqa: E402


def probe(rotation=None, start_pts=0, pts=(0, 2985, 5970)):
    stream = {
        "index": 0,
        "width": 4,
        "height": 2,
        "time_base": "1/90000",
        "start_pts": start_pts,
    }
    if rotation is not None:
        stream["side_data_list"] = [{"rotation": rotation}]
    return {"streams": [stream], "frames": [{"pts": p} for p in pts]}


def report(total_s, frame_count=3):
    timings = {stage: 0.0 for stage in compare.STAGES}
    timings["total_s"] = total_s
    return {
        "frame_count": frame_count,
        "decoder": {"name": "c2.test.avc.decoder"},
        "timings": timings,
    }


class ParseProbeTest(unittest.TestCase):
    def test_timestamps_follow_start_pts_and_time_base(self):
        ref = compare.parse_probe(probe(start_pts=90, pts=(90, 3075)))
        self.assertEqual(ref, compare.ReferenceProbe(4, 2, (0.0, 2985 / 90000)))

    def test_quarter_rotation_swaps_display_dimensions(self):
        for rotation in (90, -90, 270):
            ref = compare.parse_probe(probe(rotation=rotation))
            self.assertEqual((ref.width_px, ref.height_px), (2, 4), rotation)
        ref = compare.parse_probe(probe(rotation=180))
        self.assertEqual((ref.width_px, ref.height_px), (4, 2))

    def test_missing_stream_is_rejected(self):
        with self.assertRaises(ValueError):
            compare.parse_probe({"streams": []})

    def test_frame_without_pts_is_rejected(self):
        broken = probe()
        broken["frames"].insert(1, {})
        with self.assertRaises(ValueError):
            compare.parse_probe(broken)


class WindowTest(unittest.TestCase):
    def test_window_is_inclusive_and_open_ended(self):
        ts = [0.0, 0.5, 1.0, 1.5]
        self.assertEqual(compare.window_indices(ts, 0.5, 1.0), [1, 2])
        self.assertEqual(compare.window_indices(ts, None, None), [0, 1, 2, 3])
        self.assertEqual(compare.window_indices(ts, 1.0, None), [2, 3])


class TimestampTest(unittest.TestCase):
    def test_microsecond_truncation_is_within_tolerance(self):
        result = compare.compare_timestamps([0.0, 0.033166], [0.0, 2985 / 90000])
        self.assertTrue(result["within_tolerance"], result)

    def test_count_mismatch_and_large_offset_fail(self):
        self.assertFalse(compare.compare_timestamps([0.0], [0.0, 0.1])["within_tolerance"])
        self.assertFalse(compare.compare_timestamps([0.0, 0.2], [0.0, 0.1])["within_tolerance"])


class FrameDiffTest(unittest.TestCase):
    def test_identical_and_different_frames(self):
        self.assertEqual(compare.frame_diff(b"\x00\x10", b"\x00\x10"), (0, 0.0))
        self.assertEqual(compare.frame_diff(b"\x00\x10", b"\x03\x10"), (3, 1.5))
        with self.assertRaises(ValueError):
            compare.frame_diff(b"\x00", b"\x00\x00")

    def test_compare_frames_skips_reference_frames_outside_window(self):
        reference = io.BytesIO(b"\x00\x00" + b"\x10\x10" + b"\x20\x20" + b"\x30\x30")
        prototype = io.BytesIO(b"\x11\x10" + b"\x20\x22")
        result = compare.compare_frames(prototype, reference, 2, [1, 2])
        self.assertEqual(
            result,
            {
                "frames_compared": 2,
                "max_abs_diff": 2,
                "mean_abs_diff": 0.75,
                "frames_bit_exact": 0,
                "within_gate": True,
            },
        )

    def test_compare_frames_reads_luma_of_larger_reference_frames_with_transform(self):
        # Two 2-byte luma frames, each followed by 1 byte of "chroma" (3-byte reference stride).
        reference = io.BytesIO(bytes([0x10, 0xEB, 0x99, 0x7E, 0x10, 0x99]))
        prototype = io.BytesIO(bytes([0x00, 0xFF, 0x80, 0x00]))
        result = compare.compare_frames(
            prototype, reference, 2, [0, 1],
            reference_frame_bytes=3, transform=compare.limited_to_full_table(),
        )
        self.assertEqual(result["frames_bit_exact"], 2)

    def test_limited_to_full_table_anchors(self):
        table = compare.limited_to_full_table()
        self.assertEqual([table[c] for c in (0, 16, 17, 126, 235, 255)], [0, 0, 1, 128, 255, 255])

    def test_compare_frames_fails_closed_on_length_mismatch(self):
        with self.assertRaises(ValueError):
            compare.compare_frames(io.BytesIO(b"\x00\x00"), io.BytesIO(b"\x00\x00"), 2, [0, 1])
        with self.assertRaises(ValueError):
            compare.compare_frames(io.BytesIO(b"\x00\x00\x00\x00"), io.BytesIO(b"\x00\x00"), 2, [0])
        with self.assertRaises(ValueError):
            compare.compare_frames(io.BytesIO(b"\x00"), io.BytesIO(b"\x00\x00"), 2, [0])


class SummarizeTest(unittest.TestCase):
    def test_median_min_max_per_stage(self):
        summary = compare.summarize_reports([report(t) for t in (0.3, 0.1, 0.2, 0.5, 0.4)])
        self.assertEqual(summary["repeats"], 5)
        self.assertEqual(summary["stages_s"]["total_s"], {"median": 0.3, "min": 0.1, "max": 0.5})

    def test_inconsistent_frame_counts_are_rejected(self):
        with self.assertRaises(ValueError):
            compare.summarize_reports([report(0.1, 3), report(0.1, 4)])
        with self.assertRaises(ValueError):
            compare.summarize_reports([])


if __name__ == "__main__":
    unittest.main()
