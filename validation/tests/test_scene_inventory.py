from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "validation" / "tools"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"openbar_{name}", TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scene_inventory = load("scene_inventory")
label_package = load("label_package")

HEAD = "timestamp_s,frame_index,inventory_state,object_index,object_class,x_px,y_px,radius_px,notes"
FRAMES = [
    {"file": "frames/a.png", "frame_index": 0, "timestamp_s": 0.0},
    {"file": "frames/b.png", "frame_index": 12, "timestamp_s": 0.5},
    {"file": "frames/c.png", "frame_index": 24, "timestamp_s": 1.0},
]
CONFIG = label_package.page_config("fixture-x", "pass-a", (320, 240), FRAMES)
VALID = "\n".join([
    HEAD,
    "0.000000,0,done,0,stored_plate,10.00,200.50,30.25,",
    "0.000000,0,done,1,other,300.00,5.00,,round lamp",
    "0.500000,12,done,,,,,,",
    "1.000000,24,not_done,,,,,,",
]) + "\n"


def csv_with(*rows: str) -> str:
    return "\n".join([HEAD, *rows]) + "\n"


class PackageConfigTests(unittest.TestCase):
    def test_reads_the_config_label_package_embeds(self):
        page = label_package.render_page(label_package.PAGE_TEMPLATE.read_text(encoding="utf-8"), CONFIG)

        self.assertEqual(scene_inventory.package_config(page), CONFIG)

    def test_rejects_a_page_without_exactly_one_config(self):
        for page in ["<html></html>", "const CONFIG = {\"frames\": []};\nconst CONFIG = {\"frames\": []};"]:
            with self.assertRaises(scene_inventory.InventoryError):
                scene_inventory.package_config(page)


class SummaryTests(unittest.TestCase):
    def test_summary_counts_done_frames_and_classes_and_hashes_the_bytes(self):
        data = VALID.encode("utf-8")

        self.assertEqual(scene_inventory.summary(data, CONFIG), {
            "fixture_id": "fixture-x",
            "annotator_id": "pass-a",
            "package_id": CONFIG["package_id"],
            "inventory_sha256": hashlib.sha256(data).hexdigest(),
            "frames": 3,
            "frames_done": 2,
            "objects_by_class": {
                "another_plate_target_bar": 0, "concentric_rim": 0, "other_loaded_bar": 0, "stored_plate": 1,
                "rack_equipment": 0, "background_object": 0, "other": 1,
            },
        })

    def test_done_without_objects_differs_from_not_done(self):
        frames = scene_inventory.parse(VALID, CONFIG)

        self.assertEqual([(f["inventory_state"], len(f["objects"])) for f in frames],
                         [("done", 2), ("done", 0), ("not_done", 0)])
        self.assertEqual(frames[0]["objects"][0], {
            "object_class": "stored_plate", "x_px": 10.0, "y_px": 200.5, "radius_px": 30.25, "notes": "",
        })

    def test_rejects_invalid_inventories(self):
        rest = ["0.500000,12,done,,,,,,", "1.000000,24,not_done,,,,,,"]
        cases = {
            "header": HEAD.replace("notes", "note") + "\n" + "\n".join(["0.000000,0,done,,,,,,", *rest]) + "\n",
            "class": csv_with("0.000000,0,done,0,plate,1.00,1.00,,", *rest),
            "outside": csv_with("0.000000,0,done,0,stored_plate,320.00,1.00,,", *rest),
            "negative": csv_with("0.000000,0,done,0,stored_plate,-0.01,1.00,,", *rest),
            "non-finite": csv_with("0.000000,0,done,0,stored_plate,nan,1.00,,", *rest),
            "infinite": csv_with("0.000000,0,done,0,stored_plate,1.00,1.00,inf,", *rest),
            "underscore": csv_with("0.000000,0,done,0,stored_plate,1_0,1.00,,", *rest),
            "padded": csv_with("0.000000,0,done,0,stored_plate, 1.00,1.00,,", *rest),
            "exponent": csv_with("0.000000,0,done,0,stored_plate,1e2,1.00,,", *rest),
            "radius": csv_with("0.000000,0,done,0,stored_plate,1.00,1.00,0,", *rest),
            "other without description": csv_with("0.000000,0,done,0,other,1.00,1.00,, ", *rest),
            "objects on an unchecked frame": csv_with("0.000000,0,not_done,0,stored_plate,1.00,1.00,,", *rest),
            "index gap": csv_with("0.000000,0,done,1,stored_plate,1.00,1.00,,", *rest),
            "mixed states": csv_with("0.000000,0,done,0,stored_plate,1.00,1.00,,",
                                     "0.000000,0,not_done,1,stored_plate,2.00,2.00,,", *rest),
            "unknown state": csv_with("0.000000,0,checked,,,,,,", *rest),
            "missing frame": csv_with("0.000000,0,done,,,,,,", rest[0]),
            "frame not in package": csv_with("0.000000,1,done,,,,,,", *rest),
            "wrong order": csv_with(rest[0], "0.000000,0,done,,,,,,", rest[1]),
            "split frame": csv_with("0.000000,0,done,0,stored_plate,1.00,1.00,,", *rest[:1],
                                    "0.000000,0,done,1,stored_plate,2.00,2.00,,", rest[1]),
            "columns": csv_with("0.000000,0,done,,,,,", *rest),
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(scene_inventory.InventoryError):
                scene_inventory.parse(text, CONFIG)

    def test_an_inventory_with_no_done_frame_is_rejected(self):
        text = csv_with("0.000000,0,not_done,,,,,,", "0.500000,12,not_done,,,,,,", "1.000000,24,not_done,,,,,,")

        with self.assertRaises(scene_inventory.InventoryError):
            scene_inventory.summary(text.encode("utf-8"), CONFIG)


class CliTests(unittest.TestCase):
    def run_cli(self, csv_text: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp)
            page = label_package.render_page(label_package.PAGE_TEMPLATE.read_text(encoding="utf-8"), CONFIG)
            (package / "index.html").write_text(page, encoding="utf-8")
            (package / "inventory.csv").write_bytes(csv_text.encode("utf-8"))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = scene_inventory.main(["validate", "--package", str(package),
                                             "--csv", str(package / "inventory.csv")])
        return code, out.getvalue() or err.getvalue()

    def test_validate_prints_the_summary(self):
        code, output = self.run_cli(VALID)

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), scene_inventory.summary(VALID.encode("utf-8"), CONFIG))

    def test_validate_reports_errors_without_a_traceback(self):
        code, output = self.run_cli(csv_with("0.000000,0,done,0,plate,1.00,1.00,,"))

        self.assertEqual(code, 1)
        self.assertTrue(output.startswith("scene-inventory: "))


if __name__ == "__main__":
    unittest.main()
