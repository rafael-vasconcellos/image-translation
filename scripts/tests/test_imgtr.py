"""Tests for imgtr. Run from the skill folder:  python -m unittest discover -s scripts/tests -v
Fixtures are synthetic (alpha-153 noisy panels, 3-state pill buttons), generated in a temp dir."""
import io, json, os, subprocess, sys, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import imgtr  # noqa: E402


def find_font():
    cands = []
    if sys.platform.startswith("win"):
        cands += [r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf"]
    cands += ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


FONT = find_font()


def run(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = imgtr.main(list(argv))
    return code, json.loads(buf.getvalue())


def make_panel(path, labels, header="SETTINGS"):
    rng = np.random.default_rng(0)
    n = rng.integers(-6, 7, size=(600, 800, 1))
    rgb = np.clip(np.array([63, 88, 166]) + n, 0, 255).astype(np.uint8)
    arr = np.dstack([rgb, np.full((600, 800), 153, np.uint8)])
    arr[:6, :6, 3] = 0
    arr[:6, -6:, 3] = 0
    im = Image.fromarray(arr)
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(FONT, 20)
    d.text((20, 20), header, font=f, fill=(255, 255, 255, 255))
    for i, lab in enumerate(labels):
        d.text((90, 100 + 40 * i), lab, font=f, fill=(255, 255, 255, 255))
    im.save(path)


def make_strip(path, label, cw, ch=24, n=3):
    arr = np.zeros((ch, cw * n, 4), np.uint8)
    states = [(70, 120, 200), (110, 160, 230), (40, 80, 150)]
    r = ch // 2
    for k in range(n):
        base = np.array(states[k % 3], float)
        for y in range(ch):
            shade = 1.15 - 0.3 * y / ch
            arr[y, k * cw:(k + 1) * cw, :3] = np.clip(base * shade, 0, 255).astype(np.uint8)
        yy, xx = np.mgrid[0:ch, 0:cw]
        cx = np.where(xx < r, r - 0.5, np.where(xx >= cw - r, cw - r - 0.5, xx))
        inside = (xx - cx) ** 2 + (yy - (ch - 1) / 2) ** 2 <= (r - 0.2) ** 2
        arr[:, k * cw:(k + 1) * cw, 3] = np.where(inside, 255, 0)
    im = Image.fromarray(arr)
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(FONT, 11)
    for k in range(n):
        tw = d.textlength(label, font=f)
        d.text((k * cw + (cw - tw) / 2, 5), label, font=f, fill=(255, 255, 255, 255))
    im.save(path)


@unittest.skipIf(FONT is None, "no TrueType font found for the fixtures")
class ImgtrTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.src = Path(self._tmp.name) / "src"
        self.out = Path(self._tmp.name) / "src" / "translated"
        self.src.mkdir()
        for n, lab in (("back_a.png", ["Alpha", "Beta"]), ("back_b.png", ["Gamma", "Delta"]),
                       ("back_c.png", ["Epsilon", "Zeta"])):
            make_panel(self.src / n, lab)
        make_strip(self.src / "btn_end.png", "END", 51)
        make_strip(self.src / "btn_tab.png", "TAB", 80)
        self.spec_path = Path(self._tmp.name) / "spec.json"
        self.cwd = os.getcwd()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self._tmp.cleanup()

    def write_spec(self, images, **top):
        spec = {"version": 1, "defaults": {"font": FONT, "size": 18, "fill": "#ffffff"}, "images": images}
        spec.update(top)
        self.spec_path.write_text(json.dumps(spec), encoding="utf-8")

    def panel_entry(self, name="back_a.png"):
        return {"source": name, "regions": [
            {"box": [20, 20, 160, 30], "text": "SETTINGS", "align": "left", "valign": "middle",
             "erase": {"mode": "sample", "ref_box": [300, 250, 100, 100]}},
            {"box": [90, 98, 200, 30], "text": "Sound", "align": "left",
             "erase": {"mode": "consensus", "variants": ["back_a.png", "back_b.png", "back_c.png"],
                       "ref_box": [300, 250, 100, 100]}}]}

    # ---- inventory
    def test_inventory_finds_cells_and_alpha(self):
        code, r = run("inventory", str(self.src))
        self.assertEqual(code, 0)
        by = {i["path"]: i for i in r["images"]}
        self.assertEqual(by["btn_end.png"]["cell_candidates"][0], {"count": 3, "width": 51})
        self.assertEqual(by["btn_tab.png"]["cell_candidates"][0], {"count": 3, "width": 80})
        self.assertNotIn("cell_candidates", by["back_a.png"])
        self.assertEqual((by["back_a.png"]["alpha"]["min"], by["back_a.png"]["alpha"]["max"]), (0, 255))
        self.assertNotIn("translated", json.dumps(r["images"]))

    def test_inventory_is_not_recursive_by_default(self):
        (self.src / "sub").mkdir()
        make_strip(self.src / "sub" / "x.png", "X", 51)
        _, flat = run("inventory", str(self.src))
        _, deep = run("inventory", str(self.src), "--recursive")
        self.assertEqual(flat["count"], 5)
        self.assertEqual(deep["count"], 6)

    # ---- analyze
    def test_analyze_variants_separates_unique_text_from_shared_header(self):
        code, r = run("analyze", *(str(self.src / n) for n in ("back_a.png", "back_b.png", "back_c.png")))
        self.assertEqual(code, 0)
        a = r["files"][0]
        x, y, w, h = a["ink_bbox"]
        self.assertGreaterEqual(y, 90)  # header (y=20) is shared, so it is the consensus, not ink
        self.assertGreater(r["variants"]["unresolved_pixels"], 0)

    def test_analyze_cells_reports_clean_runs(self):
        code, r = run("analyze", str(self.src / "btn_end.png"), "--cells", "51:3")
        cell0 = r["files"][0]["cells"][0]
        self.assertIsNotNone(cell0["ref_col"])
        self.assertTrue(cell0["clean"])

    # ---- apply + verify
    def test_apply_panel_is_pixel_exact_outside_the_boxes(self):
        self.write_spec([self.panel_entry()])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 0, r)
        code, v = run("verify", str(self.src), str(self.out), "--spec", str(self.spec_path))
        self.assertEqual(code, 0, v)
        res = v["results"][0]
        self.assertEqual(res["outside_diff_pixels"], 0)
        self.assertEqual(res["alpha_changed_outside"], 0)
        self.assertTrue(res["same_mode"] and res["same_format"] and res["same_size"])
        self.assertGreater(res["inside_changed_pixels"], 0)

    def test_erased_area_has_no_ink_left_and_keeps_alpha_153(self):
        self.write_spec([{"source": "back_a.png", "regions": [
            {"box": [20, 20, 160, 30], "text": " ", "erase": {"mode": "sample", "ref_box": [300, 250, 100, 100]}}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 0, r)
        out = np.array(Image.open(self.out / "back_a.png"))
        patch = out[20:50, 20:180]
        self.assertEqual(set(np.unique(patch[..., 3])), {153})
        self.assertLess(int(patch[..., 0].max()), 80)  # background R is 63+-6; white text (255) is gone

    def test_apply_cells_with_state_overrides(self):
        self.write_spec([{"source": "btn_end.png", "cells": {"width": 51, "count": 3},
                          "regions": [{"cell": "all", "box": [8, 3, 35, 18], "text": "Exit", "size": 12,
                                       "erase": {"mode": "column-repeat", "ref_col": 8},
                                       "outline": {"color": "#000000", "width": 1},
                                       "state_overrides": {"2": {"dy": 1, "fill": "#dddddd"}}}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 0, r)
        code, v = run("verify", str(self.src), str(self.out), "--spec", str(self.spec_path), "--compare")
        self.assertEqual(code, 0, v)
        self.assertTrue(Path(v["results"][0]["compare"]["path"]).exists())
        out = np.array(Image.open(self.out / "btn_end.png"))
        self.assertEqual(out[0, 0, 3], 0)  # transparent corner preserved
        self.assertEqual(out.shape, (24, 153, 4))

    def test_overflow_is_reported_and_nothing_is_written(self):
        self.write_spec([{"source": "btn_end.png", "cells": {"width": 51, "count": 3},
                          "regions": [{"box": [8, 3, 35, 18], "text": "A VERY LONG LABEL", "size": 12,
                                       "min_size": 12, "erase": {"mode": "column-repeat", "ref_col": 8}}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertEqual(r["images"][0]["status"], "overflow")
        self.assertFalse((self.out / "btn_end.png").exists())

    def test_shrink_is_reported_not_silent(self):
        self.write_spec([{"source": "btn_end.png", "cells": {"width": 51, "count": 3},
                          "regions": [{"box": [8, 3, 35, 18], "text": "Cancel", "size": 14, "min_size": 7,
                                       "erase": {"mode": "column-repeat", "ref_col": 8}}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 0, r)
        reg = r["images"][0]["regions"][0]
        self.assertLess(reg["size_used"], reg["requested_size"])
        self.assertIn("warnings", reg)

    def test_refuses_to_overwrite_the_source_folder(self):
        self.write_spec([self.panel_entry()])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.src))
        self.assertEqual(code, 1)
        self.assertFalse(r["ok"])

    def test_erase_mode_is_mandatory(self):
        self.write_spec([{"source": "btn_end.png", "regions": [{"box": [8, 3, 35, 18], "text": "x"}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("erase.mode", r["images"][0]["error"])

    def test_output_is_deterministic(self):
        self.write_spec([self.panel_entry()])
        run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        first = (self.out / "back_a.png").read_bytes()
        run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(Path(self._tmp.name) / "o2"))
        self.assertEqual(first, (Path(self._tmp.name) / "o2" / "back_a.png").read_bytes())

    def test_verify_catches_edits_outside_allowed_boxes(self):
        self.write_spec([self.panel_entry()])
        run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        im = Image.open(self.out / "back_a.png").convert("RGBA")
        im.putpixel((700, 500), (255, 0, 0, 153))
        im.save(self.out / "back_a.png")
        code, v = run("verify", str(self.src), str(self.out), "--spec", str(self.spec_path))
        self.assertEqual(code, 2)
        self.assertEqual(v["results"][0]["outside_diff_pixels"], 1)

    def test_crop_and_clean_use_the_workdir(self):
        code, r = run("crop", str(self.src / "btn_end.png"), "--scale", "6", "--cells", "51:3")
        self.assertEqual(code, 0)
        self.assertTrue(r["out"].replace("\\", "/").startswith(".imgtr-work/"))
        self.assertEqual(run("clean")[1]["removed"], True)
        self.assertFalse(Path(".imgtr-work").exists())

    def test_unsupported_inputs_are_refused_not_degraded(self):
        arr = np.zeros((10, 40, 3), np.uint16)
        pal = Image.new("P", (40, 10))
        pal.save(self.src / "pal.png")
        self.write_spec([{"source": "pal.png", "regions": [
            {"box": [1, 1, 30, 8], "text": "x", "size": 8, "erase": {"mode": "none"}}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        res = r["images"][0]
        self.assertTrue(res.get("warnings") is None or any("mode_changed" in w for w in res["warnings"]))


if __name__ == "__main__":
    unittest.main()
