from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research" / "phone-ui"))
import phone_app  # noqa: E402


def ffmpeg_available() -> bool:
    return all(shutil.which(tool) is not None for tool in ("ffmpeg", "ffprobe"))


class PhoneAppTests(unittest.TestCase):
    def test_build_manual_seed_document_validates_against_schema(self) -> None:
        doc = phone_app.build_manual_seed_document(
            fixture_id="phone-0123456789abcdef",
            source_video_sha256="a" * 64,
            encoded_width_px=320,
            encoded_height_px=240,
            rotation_deg=0,
            display_width_px=320,
            display_height_px=240,
            timestamp_s=0.0,
            frame_index=0,
            center_x_px=100.0,
            center_y_px=120.0,
            radius_px=12.0,
        )
        self.assertEqual(doc["schema_version"], 1)
        self.assertEqual(doc["seed"]["target"]["radius_px"], 12.0)
        self.assertEqual(doc["seed"]["coordinate_space"], "display_top_left")
        self.assertEqual(doc["seed"]["source_rotation_deg"], 0)

    def test_build_manual_seed_rejects_out_of_bounds_circle(self) -> None:
        with self.assertRaises(phone_app.PhoneAppError):
            phone_app.build_manual_seed_document(
                fixture_id="phone-0123456789abcdef",
                source_video_sha256="a" * 64,
                encoded_width_px=320,
                encoded_height_px=240,
                rotation_deg=0,
                display_width_px=320,
                display_height_px=240,
                timestamp_s=0.0,
                frame_index=0,
                center_x_px=10.0,
                center_y_px=10.0,
                radius_px=20.0,
            )

    def test_upward_rep_preview_follows_metric_interval_semantics(self) -> None:
        analysis = {
            "derived": {
                "kinematics": {
                    "samples": [
                        {"timestamp_s": 0.0, "y_m": 0.00, "vy_mps": None},
                        {"timestamp_s": 0.5, "y_m": 0.25, "vy_mps": 0.50},
                        {"timestamp_s": 1.0, "y_m": 0.65, "vy_mps": 0.80},
                        {"timestamp_s": 1.5, "y_m": 0.60, "vy_mps": -0.10},
                    ]
                }
            }
        }
        reps = phone_app.upward_rep_preview(analysis)
        self.assertEqual(len(reps), 1)
        rep = reps[0]
        self.assertEqual(rep["rep_number"], 1)
        self.assertEqual(rep["start_s"], 0.0)
        self.assertEqual(rep["end_s"], 1.0)
        self.assertAlmostEqual(rep["upward_rom_m"], 0.65)
        self.assertAlmostEqual(rep["mean_upward_velocity_mps"], 0.65)
        self.assertAlmostEqual(rep["peak_upward_velocity_mps"], 0.80)

    def test_http_endpoints_and_end_to_end_csrt_gray_flow(self) -> None:
        if not ffmpeg_available():
            self.skipTest("ffmpeg/ffprobe not on PATH")

        synthetic_mp4 = ROOT / "validation" / "fixtures" / "public" / "synthetic-clean-side-12.mp4"
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            cli_prefix = phone_app.resolve_openbar_cli()
            handler = phone_app.make_handler(work_dir, cli_prefix)
            server = phone_app.http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
                conn.request("GET", "/")
                resp = conn.getresponse()
                html_body = resp.read().decode("utf-8")
                self.assertEqual(resp.status, 200)
                self.assertIn("OpenBar Mobile Test Harness", html_body)

                conn.request(
                    "POST",
                    "/api/probe-frame?name=synthetic-clean-side-12.mp4&at_s=0.0",
                    body=synthetic_mp4.read_bytes(),
                    headers={"Content-Type": "application/octet-stream"},
                )
                probe_resp = conn.getresponse()
                probe_data = json.loads(probe_resp.read().decode("utf-8"))
                self.assertEqual(probe_resp.status, 200, probe_data)
                self.assertEqual(probe_data["display_width_px"], 320)
                self.assertEqual(probe_data["display_height_px"], 240)
                self.assertEqual(probe_data["frame_index"], 0)

                analyze_payload = {
                    "video_path": probe_data["video_path"],
                    "fixture_id": probe_data["fixture_id"],
                    "source_video_sha256": probe_data["source_video_sha256"],
                    "encoded_width_px": probe_data["encoded_width_px"],
                    "encoded_height_px": probe_data["encoded_height_px"],
                    "rotation_deg": probe_data["rotation_deg"],
                    "display_width_px": probe_data["display_width_px"],
                    "display_height_px": probe_data["display_height_px"],
                    "timestamp_s": probe_data["timestamp_s"],
                    "frame_index": probe_data["frame_index"],
                    "center_x_px": 100.0,
                    "center_y_px": 180.0,
                    "radius_px": 12.0,
                    "plate_diameter_m": 0.45,
                    "tracker": "csrt-gray",
                    "filter_preset": "savitzky-golay-7",
                    "exercise": "back_squat",
                }
                conn.request(
                    "POST",
                    "/api/analyze",
                    body=json.dumps(analyze_payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                analyze_resp = conn.getresponse()
                analyze_data = json.loads(analyze_resp.read().decode("utf-8"))
                self.assertEqual(analyze_resp.status, 200, analyze_data)
                self.assertEqual(analyze_data["tracker"], "csrt-gray-v1")
                self.assertEqual(analyze_data["observation_counts"]["total"], 12)
                self.assertIn("<svg", analyze_data["diagnostic_svg"] or "")
                conn.close()
            finally:
                server.shutdown()
                server.server_close()

    def test_opencv_csrt_observations_flow_when_opencv_available(self) -> None:
        if not ffmpeg_available():
            self.skipTest("ffmpeg/ffprobe not on PATH")
        try:
            phone_app.resolve_opencv_python()
        except phone_app.PhoneAppError:
            self.skipTest("opencv-contrib-python not installed")

        synthetic_mp4 = ROOT / "validation" / "fixtures" / "public" / "synthetic-clean-side-12.mp4"
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            probe = phone_app.probe_and_extract_seed_frame(synthetic_mp4, work_dir, at_s=0.0)
            seed_doc = phone_app.build_manual_seed_document(
                fixture_id=probe["fixture_id"],
                source_video_sha256=probe["source_video_sha256"],
                encoded_width_px=probe["encoded_width_px"],
                encoded_height_px=probe["encoded_height_px"],
                rotation_deg=probe["rotation_deg"],
                display_width_px=probe["display_width_px"],
                display_height_px=probe["display_height_px"],
                timestamp_s=probe["timestamp_s"],
                frame_index=probe["frame_index"],
                center_x_px=100.0,
                center_y_px=190.0,
                radius_px=24.0,
            )
            result = phone_app.run_phone_analysis(
                video_path=synthetic_mp4,
                seed_doc=seed_doc,
                plate_diameter_m=0.45,
                tracker="opencv-csrt",
                filter_preset="savitzky-golay-7",
                exercise="back_squat",
                work_dir=work_dir,
            )
            self.assertEqual(result["tracker"], "opencv-csrt")
            self.assertEqual(result["observation_counts"]["total"], 12)
            self.assertIn("<svg", result["diagnostic_svg"] or "")


if __name__ == "__main__":
    unittest.main()
