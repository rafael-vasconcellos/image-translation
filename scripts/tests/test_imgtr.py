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


def run_raw(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = imgtr.main(list(argv))
    return code, buf.getvalue()


def run(*argv):
    code, output = run_raw(*argv)
    return code, json.loads(output)


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

    def test_apply_and_fonts_outputs_are_pretty_by_default(self):
        self.write_spec([self.panel_entry()])
        code, output = run_raw("apply", str(self.spec_path), "--src", str(self.src),
                               "--out", str(self.out), "--dry-run")
        self.assertEqual(code, 0, output)
        self.assertIn('\n  "ok": true,', output)

        missing_spec = Path(self._tmp.name) / "missing.json"
        code, output = run_raw("apply", str(missing_spec), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 1)
        self.assertIn('\n  "ok": false,', output)

        empty_fonts = Path(self._tmp.name) / "fonts"
        empty_fonts.mkdir()
        code, output = run_raw("fonts", "--dir", str(empty_fonts))
        self.assertEqual(code, 0, output)
        self.assertIn('\n  "ok": true,', output)

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

    def test_cells_are_detected_even_when_the_pressed_state_differs_slightly(self):
        make_strip(self.src / "btn_real.png", "END", 51)
        im = np.array(Image.open(self.src / "btn_real.png"))
        edge = im[:, 102, 3].astype(int)
        im[:, 102, 3] = np.where((edge > 0) & (edge < 255), np.clip(edge + 30, 0, 255), edge)  # 3rd state
        Image.fromarray(im).save(self.src / "btn_real.png")
        _, r = run("inventory", str(self.src))
        by = {i["path"]: i for i in r["images"]}
        self.assertEqual(by["btn_real.png"]["cell_candidates"][0], {"count": 3, "width": 51})

    def test_erase_source_borrows_a_clean_sibling_image(self):
        # btn_tab.png has no clean column in its middle once we pretend text spans it all:
        # borrow the middle of a sibling that has the same shape, then draw
        make_strip(self.src / "btn_other.png", "OTHER", 80)
        self.write_spec([{"source": "btn_tab.png", "cells": {"width": 80, "count": 3},
                          "erase": [{"cell": "all", "mode": "copy", "offset": [0, 0], "box": [3, 0, 12, 24],
                                     "source": "btn_other.png"},
                                    {"cell": "all", "mode": "column-repeat", "ref_col": 8, "box": [15, 0, 50, 24],
                                     "source": "btn_other.png"}],
                          "regions": [{"cell": "all", "box": [10, 2, 60, 20], "text": "Tab", "size": 12}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 0, r)
        code, v = run("verify", str(self.src), str(self.out), "--spec", str(self.spec_path))
        self.assertEqual(code, 0, v)

    def test_erase_source_must_match_dimensions(self):
        self.write_spec([{"source": "btn_end.png", "cells": {"width": 51, "count": 3},
                          "erase": [{"cell": "all", "mode": "column-repeat", "ref_col": 8, "box": [7, 0, 37, 24],
                                     "source": "btn_tab.png"}],
                          "regions": [{"cell": "all", "box": [8, 3, 35, 18], "text": "x", "size": 12}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("same dimensions", r["images"][0]["error"])

    def test_sources_gives_each_cell_its_own_single_cell_donor(self):
        strip = Image.open(self.src / "btn_tab.png").convert("RGBA")  # 240x24 = 3 cells of 80
        donors = []
        for k in range(3):
            name = f"donor_{k}.png"
            strip.crop((k * 80, 0, (k + 1) * 80, 24)).save(self.src / name)
            donors.append(name)
        make_strip(self.src / "btn_dest.png", "ZZZ", 80)
        self.write_spec([{"source": "btn_dest.png", "cells": {"width": 80, "count": 3},
                          "erase": [{"cell": "all", "mode": "column-repeat", "ref_col": 10, "box": [7, 0, 66, 24],
                                     "sources": donors}],
                          "regions": [{"cell": "all", "box": [7, 2, 66, 20], "text": "ok", "size": 12}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 0, r)
        out = np.array(Image.open(self.out / "btn_dest.png"))
        for k in range(3):  # a column far from the drawn label equals the donor's reference column
            d = np.array(Image.open(self.src / donors[k]))
            self.assertTrue(np.array_equal(out[:, k * 80 + 70], d[:, 10]))

    def test_sources_must_be_cell_sized(self):
        make_strip(self.src / "btn_dest.png", "ZZZ", 80)
        self.write_spec([{"source": "btn_dest.png", "cells": {"width": 80, "count": 3},
                          "erase": [{"cell": "all", "mode": "column-repeat", "ref_col": 10, "box": [7, 0, 66, 24],
                                     "sources": ["btn_tab.png"] * 3}],
                          "regions": [{"cell": "all", "box": [7, 2, 66, 20], "text": "ok", "size": 12}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("exactly one cell", r["images"][0]["error"])

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
        self.assertIn("declare 'erase'", r["images"][0]["error"])

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

    # ---- v0.2 additions (learned from a real agent session)
    def test_analyze_lines_palette_and_ascii(self):
        code, r = run("analyze", str(self.src / "back_a.png"), "--box", "80,90,300,100",
                      "--ref-box", "300,250,100,100", "--lines", "--palette", "3")
        self.assertEqual(code, 0, r)
        f = r["files"][0]
        self.assertEqual(len(f["lines"]), 2)
        self.assertTrue(95 <= f["lines"][0]["y"][0] <= 105 and 135 <= f["lines"][1]["y"][0] <= 145)
        self.assertIn([255, 255, 255, 255], [c["rgba"] for c in f["palette"]])
        code, r = run("analyze", str(self.src / "back_a.png"), "--box", "85,98,100,30",
                      "--ref-box", "300,250,100,100", "--ascii")
        rows = r["files"][0]["ascii"]
        self.assertEqual((len(rows), len(rows[0])), (30, 100))
        self.assertTrue(any("#" in row for row in rows))

    def test_copy_mode_erases_from_a_clean_area_of_the_same_image(self):
        self.write_spec([{"source": "back_a.png", "regions": [
            {"box": [20, 18, 240, 34], "text": " ", "erase": {"mode": "copy", "offset": [380, 0]}}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 0, r)
        patch = np.array(Image.open(self.out / "back_a.png"))[18:52, 20:260]
        self.assertLess(int(patch[..., 0].max()), 80)
        code, v = run("verify", str(self.src), str(self.out), "--spec", str(self.spec_path))
        self.assertEqual(code, 0, v)

    def test_copy_mode_rejects_overlapping_source(self):
        self.write_spec([{"source": "back_a.png", "regions": [
            {"box": [20, 18, 240, 34], "text": "x", "erase": {"mode": "copy", "offset": [100, 0]}}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("overlaps", r["images"][0]["error"])

    def test_one_erase_block_for_many_lines_keeps_every_line(self):
        self.write_spec([{"source": "back_a.png",
                          "erase": [{"mode": "copy", "box": [85, 95, 300, 80], "offset": [400, 0]}],
                          "regions": [{"box": [88, 98, 200, 30], "text": "Master", "align": "left"},
                                      {"box": [88, 138, 200, 30], "text": "Volume", "align": "left"}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 0, r)
        out = np.array(Image.open(self.out / "back_a.png"))
        for y0 in (98, 138):  # both lines survive: erasing happens once, before any drawing
            self.assertGreater(int((out[y0:y0 + 30, 88:288, 0] > 200).sum()), 20)
        code, v = run("verify", str(self.src), str(self.out), "--spec", str(self.spec_path))
        self.assertEqual(code, 0, v)

    def test_region_erase_is_optional_only_if_the_image_erases(self):
        self.write_spec([{"source": "back_a.png", "regions": [{"box": [88, 98, 200, 30], "text": "x"}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("declare 'erase'", r["images"][0]["error"])

    def test_presets_turn_a_button_family_into_one_line_each(self):
        Image.open(self.src / "btn_end.png").save(self.src / "btn_end2.png")
        preset = {"cells": {"width": 51, "count": 3},
                  "erase": [{"cell": "all", "mode": "column-repeat", "ref_col": 8, "box": [7, 0, 37, 24]}],
                  "style": {"size": 12},
                  "regions": [{"cell": "all", "box": [8, 3, 35, 18], "text": "{text}"}]}
        self.write_spec([{"source": "btn_end.png", "preset": "pill", "text": "Exit"},
                         {"source": "btn_end2.png", "preset": "pill", "text": "Quit"}], presets={"pill": preset})
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 0, r)
        a, b = (np.array(Image.open(self.out / n)) for n in ("btn_end.png", "btn_end2.png"))
        self.assertFalse(np.array_equal(a, b))
        code, v = run("verify", str(self.src), str(self.out), "--spec", str(self.spec_path))
        self.assertEqual(code, 0, v)

    def test_preset_text_placeholder_is_checked(self):
        self.write_spec([{"source": "btn_end.png", "preset": "p"}],
                        presets={"p": {"regions": [{"box": [8, 3, 35, 18], "text": "{text}"}]}})
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 1)
        self.assertIn("text", r["error"])

    def _blank(self, name):
        Image.new("RGBA", (100, 40), (50, 50, 50, 255)).save(self.src / name)

    def _top_ink_row(self, path):
        a = np.array(Image.open(path))
        return int(np.where((a[..., 0] > 200).any(axis=1))[0].min())

    def test_valign_metrics_gives_the_same_baseline_for_any_text(self):
        for n in ("blank_a.png", "blank_b.png"):
            self._blank(n)
        base = {"box": [0, 0, 100, 40], "size": 20, "align": "left", "erase": {"mode": "none"}}
        for mode, outdir in (("metrics", "m"), ("middle", "c")):
            self.write_spec([{"source": "blank_a.png", "regions": [dict(base, text="HHH", valign=mode)]},
                             {"source": "blank_b.png", "regions": [dict(base, text="HHg", valign=mode)]}])
            code, r = run("apply", str(self.spec_path), "--src", str(self.src),
                          "--out", str(Path(self._tmp.name) / outdir))
            self.assertEqual(code, 0, r)
        top = lambda d, n: self._top_ink_row(Path(self._tmp.name) / d / n)  # noqa: E731
        self.assertEqual(top("m", "blank_a.png"), top("m", "blank_b.png"))
        self.assertNotEqual(top("c", "blank_a.png"), top("c", "blank_b.png"))  # ink-centring drifts

    def test_shadow_can_be_a_list(self):
        self._blank("blank_a.png")
        self.write_spec([{"source": "blank_a.png", "regions": [
            {"box": [0, 0, 100, 40], "text": "Hi", "size": 16, "erase": {"mode": "none"},
             "shadow": [{"color": "#1e2d55dc", "offset": [1, 1]}, {"color": "#1e2d558c", "offset": [1, 2]}]}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 0, r)

    def test_jpeg_source_is_saved_as_jpeg(self):
        Image.new("RGB", (120, 60), (20, 20, 20)).save(self.src / "photo.jpg", quality=90)
        self.write_spec([{"source": "photo.jpg", "regions": [
            {"box": [10, 10, 100, 40], "text": "Hi", "size": 16,
             "erase": {"mode": "fill", "color": "#141414"}}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 0, r)
        with Image.open(self.out / "photo.jpg") as im:
            self.assertEqual((im.format, im.size), ("JPEG", (120, 60)))
        warns = [w for res in r["images"] for w in res.get("warnings", [])]
        self.assertTrue(any("lossy_format" in w for w in warns), warns)

    def test_measure_reports_widths_and_fit(self):
        code, r = run("measure", "Hi", "A much longer label", "--font", FONT, "--size", "14", "--max-width", "40")
        self.assertEqual(code, 0)
        res = {x["text"]: x for x in r["results"]}
        self.assertTrue(res["Hi"]["fits"])
        self.assertFalse(res["A much longer label"]["fits"])

    # ---- alignment rule: same height -> same baseline, same width -> same horizontal anchor
    def _two_blanks(self):
        for n in ("blank_a.png", "blank_b.png"):
            self._blank(n)

    def _align_spec(self, a, b):
        base = {"box": [0, 0, 100, 40], "size": 20, "erase": {"mode": "none"}}
        self.write_spec([{"source": "blank_a.png", "regions": [dict(base, **a)]},
                         {"source": "blank_b.png", "regions": [dict(base, **b)]}])
        return run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out), "--dry-run")

    def test_alignment_flags_baseline_drift_between_same_height_images(self):
        self._two_blanks()
        code, r = self._align_spec({"text": "HHH", "align": "left"}, {"text": "HHg", "align": "left"})
        fs = r["alignment"]["findings"]
        self.assertEqual(code, 0)  # warnings, not failures, unless --strict-alignment
        self.assertTrue(any(f["rule"] == "same-height" and f["issue"] == "baseline differs" for f in fs), fs)
        self.assertIn("40", r["alignment"]["groups"]["height"])

    def test_fixed_baseline_aligns_texts_whatever_the_glyphs(self):
        self._two_blanks()
        code, r = self._align_spec({"text": "HHH", "align": "left", "valign": "baseline", "baseline": 28},
                                   {"text": "HHg", "align": "left", "valign": "baseline", "baseline": 28})
        self.assertEqual(r["alignment"]["findings"], [])
        self.assertEqual({e["baseline"] for i in r["images"] for e in i["regions"]}, {28})

    def test_baseline_mode_really_puts_the_baseline_on_that_row(self):
        self._two_blanks()
        self.write_spec([{"source": "blank_a.png", "regions": [
            {"box": [0, 0, 100, 40], "text": "HHH", "size": 20, "align": "left", "valign": "baseline",
             "baseline": 28, "erase": {"mode": "none"}}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 0, r)
        a = np.array(Image.open(self.out / "blank_a.png"))
        rows = np.where((a[..., 0] > 200).any(axis=1))[0]
        self.assertEqual(int(rows.max()), 27)  # the last ink row of flat-bottomed capitals sits on row baseline-1

    def test_baseline_valign_requires_a_baseline_value(self):
        self._blank("blank_a.png")
        self.write_spec([{"source": "blank_a.png", "regions": [
            {"box": [0, 0, 100, 40], "text": "H", "size": 20, "valign": "baseline", "erase": {"mode": "none"}}]}])
        code, r = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("baseline", r["images"][0]["error"])

    def test_alignment_flags_different_horizontal_anchor_for_same_width_images(self):
        self._two_blanks()
        code, r = self._align_spec({"text": "Hi", "align": "left"}, {"text": "Hi", "align": "center"})
        self.assertTrue(any(f["rule"] == "same-width" and f["issue"] == "align modes differ"
                            for f in r["alignment"]["findings"]))
        code, r = self._align_spec({"text": "Hi", "align": "left"}, {"text": "Hi", "align": "left", "dx": 5})
        self.assertTrue(any(f["issue"] == "anchor differs" for f in r["alignment"]["findings"]))

    def test_align_key_null_opts_a_region_out(self):
        self._two_blanks()
        code, r = self._align_spec({"text": "Hi", "align": "left"},
                                   {"text": "Hi", "align": "left", "dx": 9, "align_key": None})
        self.assertEqual(r["alignment"]["findings"], [])

    def test_align_key_rejects_non_string_and_blank_values(self):
        self._blank("blank_a.png")
        for value in ([], {}, "", "   "):
            with self.subTest(align_key=value):
                self.write_spec([{"source": "blank_a.png", "regions": [
                    {"box": [0, 0, 100, 40], "text": "Hi", "size": 20,
                     "align_key": value, "erase": {"mode": "none"}}]}])
                code, r = run("apply", str(self.spec_path), "--src", str(self.src),
                              "--out", str(self.out), "--dry-run")
                self.assertEqual(code, 2)
                self.assertIn("align_key must be a non-empty string or null", r["images"][0]["error"])

    def test_align_key_keeps_cell_specific_alignment_checks_separate(self):
        for name in ("blank_a.png", "blank_b.png"):
            Image.new("RGBA", (200, 40), (50, 50, 50, 255)).save(self.src / name)
        base = {"box": [0, 0, 100, 40], "cell": "all", "text": "Hi", "size": 20,
                "align": "left", "align_key": "label", "erase": {"mode": "none"}}
        images = [
            {"source": "blank_a.png", "cells": {"width": 100, "count": 2},
             "regions": [dict(base, valign="baseline", baseline=28)]},
            {"source": "blank_b.png", "cells": {"width": 100, "count": 2},
             "regions": [dict(base, valign="baseline", baseline=28,
                              state_overrides={"0": {"baseline": 27, "dx": 5}})]},
        ]
        self.write_spec(images)
        code, r = run("apply", str(self.spec_path), "--src", str(self.src),
                      "--out", str(self.out), "--dry-run")
        self.assertEqual(code, 0, r)
        findings = r["alignment"]["findings"]
        cell_zero = [f for f in findings if f["key"] == "label:0"]
        self.assertTrue(any(f["issue"] == "baseline differs" for f in cell_zero), findings)
        self.assertTrue(any(f["issue"] == "anchor differs" for f in cell_zero), findings)
        self.assertFalse(any(f["key"] == "label:1" for f in findings), findings)

    def test_alignment_flags_a_label_that_shrank_alone(self):
        self._two_blanks()
        code, r = self._align_spec({"text": "Hi", "align": "left", "valign": "baseline", "baseline": 28},
                                   {"text": "A fairly long label here", "align": "left", "valign": "baseline",
                                    "baseline": 28, "min_size": 10})
        self.assertTrue(any(f["issue"] == "font size differs" for f in r["alignment"]["findings"]), r["alignment"])

    def test_strict_alignment_changes_the_exit_code(self):
        self._two_blanks()
        self._align_spec({"text": "HHH", "align": "left"}, {"text": "HHg", "align": "left"})
        code, _ = run("apply", str(self.spec_path), "--src", str(self.src), "--out", str(self.out),
                      "--dry-run", "--strict-alignment")
        self.assertEqual(code, 2)

    def test_measure_reports_cap_height(self):
        code, r = run("measure", "Hi", "--font", FONT, "--size", "20")
        self.assertGreater(r["cap_height"], 10)
        self.assertGreater(r["cap_height"], r["x_height"])


if __name__ == "__main__":
    unittest.main()
