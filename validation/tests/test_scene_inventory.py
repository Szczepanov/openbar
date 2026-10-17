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

HEAD = "package_id,timestamp_s,frame_index,inventory_state,object_index,object_class,x_px,y_px,radius_px,notes"
FRAMES = [
    {"file": "frames/a.png", "frame_index": 0, "timestamp_s": 0.0},
    {"file": "frames/b.png", "frame_index": 12, "timestamp_s": 0.5},
    {"file": "frames/c.png", "frame_index": 24, "timestamp_s": 1.0},
]
CONFIG = label_package.page_config("fixture-x", "pass-a", (320, 240), FRAMES)
PID = CONFIG["package_id"]
REST = [f"{PID},0.500000,12,done,,,,,,", f"{PID},1.000000,24,not_done,,,,,,"]
VALID = "\n".join([
    HEAD,
    f"{PID},0.000000,0,done,0,stored_plate,10.00,200.50,30.25,",
    f"{PID},0.000000,0,done,1,other,300.00,5.00,4.00,round lamp",
    f"{PID},0.000000,0,done,2,unknown,150.00,100.00,9.50,",
    *REST,
]) + "\n"
LABELS = "\n".join([
    "timestamp_s,requested_timestamp_s,frame_index,annotation_state,visibility,quality,x_px,y_px,"
    "radius_px,diameter_px,left_px,top_px,right_px,bottom_px,notes",
    "0.000000,,0,labelled,visible,high,151.00,101.00,10.50,,,,,,",
    "0.500000,,12,labelled,visible,high,100.00,100.00,,,,,,,",
    "1.000000,,24,not_annotated,unknown,not_assessed,,,,,,,,,",
]) + "\n"


def frame0(*objects: str) -> str:
    return "\n".join([HEAD, *[f"{PID},0.000000,0,{row}" for row in objects], *REST]) + "\n"


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
            "package_id": PID,
            "inventory_sha256": hashlib.sha256(data).hexdigest(),
            "frames": 3,
            "frames_done": 2,
            "done_timestamps_s": [0.0, 0.5],
            "objects_by_class": {
                "another_plate_target_bar": 0, "concentric_rim": 0, "other_loaded_bar": 0, "stored_plate": 1,
                "rack_equipment": 0, "background_object": 0, "other": 1, "unknown": 1,
            },
            "checked_against_labels": False,
        })

    def test_done_without_objects_differs_from_not_done(self):
        frames = scene_inventory.parse(VALID, CONFIG)

        self.assertEqual([(f["inventory_state"], len(f["objects"])) for f in frames],
                         [("done", 3), ("done", 0), ("not_done", 0)])
        self.assertEqual(frames[0]["objects"][0], {
            "object_class": "stored_plate", "x_px": 10.0, "y_px": 200.5, "radius_px": 30.25, "notes": "",
        })

    def test_crlf_line_endings_parse_but_hash_differently(self):
        crlf = VALID.replace("\n", "\r\n").encode("utf-8")

        self.assertEqual(scene_inventory.parse(crlf.decode("utf-8"), CONFIG), scene_inventory.parse(VALID, CONFIG))
        self.assertNotEqual(scene_inventory.summary(crlf, CONFIG)["inventory_sha256"],
                            scene_inventory.summary(VALID.encode("utf-8"), CONFIG)["inventory_sha256"])

    def test_rejects_invalid_inventories(self):
        cases = {
            "header": VALID.replace(HEAD, HEAD.replace("notes", "note")),
            "other package": VALID.replace(PID, "0123456789abcdef"),
            "class": frame0("done,0,plate,1.00,1.00,1.00,"),
            "missing radius": frame0("done,0,stored_plate,1.00,1.00,,"),
            "outside": frame0("done,0,stored_plate,320.00,1.00,1.00,"),
            "negative": frame0("done,0,stored_plate,-0.01,1.00,1.00,"),
            "non-finite": frame0("done,0,stored_plate,nan,1.00,1.00,"),
            "infinite": frame0("done,0,stored_plate,1.00,1.00,inf,"),
            "underscore": frame0("done,0,stored_plate,1_0,1.00,1.00,"),
            "padded": frame0("done,0,stored_plate, 1.00,1.00,1.00,"),
            "exponent": frame0("done,0,stored_plate,1e2,1.00,1.00,"),
            "zero radius": frame0("done,0,stored_plate,1.00,1.00,0.00,"),
            "other without description": frame0("done,0,other,1.00,1.00,1.00, "),
            "objects on an unchecked frame": frame0("not_done,0,stored_plate,1.00,1.00,1.00,"),
            "index gap": frame0("done,1,stored_plate,1.00,1.00,1.00,"),
            "mixed states": frame0("done,0,stored_plate,1.00,1.00,1.00,", "not_done,1,stored_plate,2.00,2.00,1.00,"),
            "unknown state": frame0("checked,,,,,,"),
            "missing frame": "\n".join([HEAD, f"{PID},0.000000,0,done,,,,,,", REST[0]]) + "\n",
            "frame not in package": "\n".join([HEAD, f"{PID},0.000000,1,done,,,,,,", *REST]) + "\n",
            "spreadsheet timestamp": "\n".join([HEAD, f"{PID},0,0,done,,,,,,", *REST]) + "\n",
            "wrong order": "\n".join([HEAD, REST[0], f"{PID},0.000000,0,done,,,,,,", REST[1]]) + "\n",
            "split frame": "\n".join([HEAD, f"{PID},0.000000,0,done,0,stored_plate,1.00,1.00,1.00,", REST[0],
                                      f"{PID},0.000000,0,done,1,stored_plate,2.00,2.00,1.00,", REST[1]]) + "\n",
            "columns": frame0("done,,,,,,,"),
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(scene_inventory.InventoryError):
                scene_inventory.parse(text, CONFIG)

    def test_errors_name_the_physical_line(self):
        with self.assertRaisesRegex(scene_inventory.InventoryError, "^line 3: "):
            scene_inventory.parse(frame0("done,0,stored_plate,1.00,1.00,1.00,", "done,1,plate,1.00,1.00,1.00,"), CONFIG)

    def test_rejects_a_bom_or_blank_line_the_page_never_writes(self):
        for name, data, message in [
            ("bom", b"\xef\xbb\xbf" + VALID.encode("utf-8"), "BOM"),
            ("blank", VALID.replace("\n", "\n\n", 1).encode("utf-8"), "line 2: blank line"),
        ]:
            with self.subTest(name), self.assertRaisesRegex(scene_inventory.InventoryError, message):
                scene_inventory.summary(data, CONFIG)

    def test_an_inventory_with_no_done_frame_is_rejected(self):
        text = "\n".join([HEAD, *[f"{PID},{f['timestamp_s']:.6f},{f['frame_index']},not_done,,,,,," for f in FRAMES]])

        with self.assertRaisesRegex(scene_inventory.InventoryError, "no frame has a complete inventory"):
            scene_inventory.summary((text + "\n").encode("utf-8"), CONFIG)

    def test_labels_reject_an_object_that_re_marks_the_labelled_plate(self):
        result = scene_inventory.summary(VALID.encode("utf-8"), CONFIG,
                                         LABELS.replace("151.00,101.00,10.50", "40.00,40.00,8.00").encode("utf-8"))
        self.assertTrue(result["checked_against_labels"])

        # Object 2 (150, 100, r 9.5) is within 2 px in centre and radius of the plate (151, 101, r 10.5).
        with self.assertRaisesRegex(scene_inventory.InventoryError, "frame 0, object 2: this is the labelled plate"):
            scene_inventory.summary(VALID.encode("utf-8"), CONFIG, LABELS.encode("utf-8"))

    def test_a_concentric_rim_with_a_different_radius_is_not_the_plate(self):
        text = frame0("done,0,concentric_rim,151.00,101.00,5.00,")

        scene_inventory.summary(text.encode("utf-8"), CONFIG, LABELS.encode("utf-8"))

    def test_labels_must_be_this_packages_well_formed_label_csv(self):
        rows = LABELS.splitlines()
        cases = {
            "the inventory itself": VALID,
            "empty": "",
            "not a number": LABELS.replace("151.00,101.00", "abc,101.00"),
            "nan": LABELS.replace("151.00,101.00,10.50", "nan,nan,nan"),
            "spreadsheet timestamp": LABELS.replace("\n0.000000,", "\n0,"),
            "other package frames": LABELS.replace(",,12,", ",,13,"),
            "missing frame": "\n".join(rows[:-1]) + "\n",
            "truncated row": LABELS.replace(",,,,,,\n0.500000", ",,,,\n0.500000"),
            "unknown state": LABELS.replace("not_annotated,unknown", "skipped,unknown"),
            "bom": "\ufeff" + LABELS,
            "blank line": LABELS.replace("\n", "\n\n", 1),
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(scene_inventory.InventoryError):
                scene_inventory.summary(VALID.encode("utf-8"), CONFIG, text.encode("utf-8"))

    def test_empty_frame_row_with_stray_fields_names_the_problem(self):
        text = "\n".join([HEAD, f"{PID},0.000000,0,done,,,,,, ", *REST]) + "\n"
        with self.assertRaisesRegex(scene_inventory.InventoryError, "without object_index must have empty object fields"):
            scene_inventory.parse(text, CONFIG)

    def test_line_numbers_ignore_unicode_line_separators_in_notes(self):
        text = frame0("done,0,other,1.00,1.00,1.00,lamp\u2028x", "done,1,stored_plate,1.00,1.00,1.00,") + ""
        scene_inventory.summary(text.encode("utf-8"), CONFIG)


class CliTests(unittest.TestCase):
    def run_cli(self, csv_text: str, page: str | None = None, labels: str | None = None) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp)
            if page is None:
                page = label_package.render_page(label_package.PAGE_TEMPLATE.read_text(encoding="utf-8"), CONFIG)
            if page:
                (package / "index.html").write_text(page, encoding="utf-8")
            (package / "inventory.csv").write_bytes(csv_text.encode("utf-8"))
            extra = []
            if labels is not None:
                (package / "labels.csv").write_bytes(labels.encode("utf-8"))
                extra = ["--labels", str(package / "labels.csv")]
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = scene_inventory.main(["validate", "--package", str(package),
                                             "--csv", str(package / "inventory.csv"), *extra])
        return code, out.getvalue() or err.getvalue()

    def test_validate_prints_the_same_summary_every_run(self):
        first, second = self.run_cli(VALID), self.run_cli(VALID)

        self.assertEqual(first, second)
        self.assertEqual(first[0], 0)
        self.assertEqual(json.loads(first[1]), scene_inventory.summary(VALID.encode("utf-8"), CONFIG))

    def test_validate_reports_errors_without_a_traceback(self):
        for name, csv_text, page in [
            ("bad class", frame0("done,0,plate,1.00,1.00,1.00,"), None),
            ("no index.html", VALID, ""),
            ("invalid CONFIG", VALID, "const CONFIG = {not json};"),
        ]:
            with self.subTest(name):
                code, output = self.run_cli(csv_text, page)
                self.assertEqual(code, 1)
                self.assertTrue(output.startswith("scene-inventory: "), output)
        for name, labels in [("re-marked plate", LABELS), ("broken labels", "x\n")]:
            with self.subTest(name):
                code, output = self.run_cli(VALID, None, labels)
                self.assertEqual(code, 1)
                self.assertTrue(output.startswith("scene-inventory: "), output)


if __name__ == "__main__":
    unittest.main()
