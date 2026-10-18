# translations.json (spec) format, v1

One file describes the whole batch. It is the single source of truth: the agent writes the
translation table once, you can review it, and `apply` / `verify` both read it.

## Coordinates

Every box is `[x, y, w, h]` in pixels, origin top-left. When an image declares `cells`, all boxes in
its regions and erase entries are **cell-relative** and are translated to each targeted cell.

## Top level

```json
{
  "version": 1,
  "seed": 1,
  "defaults": { "font": "C:/Windows/Fonts/segoeuib.ttf", "size": 14, "fill": "#ffffff" },
  "presets": { "tab": { ... } },
  "images": [ ... ]
}
```

## Image

| key | meaning |
|---|---|
| `source` | path relative to `--src`; the output mirrors it under `--out` |
| `cells` | `{ "width": 51, "count": 3 }` for strips of equal-width states (normal/hover/pressed). `width*count` must equal the image width |
| `erase` | list of erase entries applied **once, before any text is drawn** (see below) |
| `style` | optional style overrides for every region of this image |
| `regions` | list of regions to draw (below) |
| `preset`, `text` | pull `cells`/`style`/`erase`/`regions` from `presets[name]`; `text` fills `{text}` in the preset regions. Any key you also write on the image wins |

**Rule:** every image must say how its old text goes away: either an image-level `erase` list, or an
`erase` on every region. `{ "mode": "none" }` is the explicit way to draw without erasing.

Prefer the image-level list when several lines share one background (a settings panel): one block is
erased first, then all the lines are drawn. A per-region erase would wipe the lines drawn before it.

## Region

| key | meaning |
|---|---|
| `text` | the translation. `\n` makes a line break |
| `box` | where the new text must fit (also the default area of a region-level `erase`) |
| `cell` | `"all"` (default), an index, or a list of indexes. Only for images with `cells` |
| `erase` | optional erase entry for this region only |
| style keys | any style key below, overriding `defaults`, the preset and the image `style` |
| `state_overrides` | `{ "2": { "fill": "#9f9fbf" } }`: style per cell index (pressed = lighter/shifted) |
| `align_key` | name that pairs this region with regions of other images in the alignment check (default: region index and cell). `null` opts the region out |

## Erase entries

`{ "mode": ..., ... }`. An image-level entry also needs `box` and may have `cell`.

| mode | fields | what it does |
|---|---|---|
| `none` | | nothing |
| `fill` | `color` | solid colour (`#rrggbb` or `#rrggbbaa`) |
| `copy` | `offset: [dx, dy]`, `box?` | copies the same-size block from the original at `box + offset`. Row-aligned, so noise and any vertical structure survive. The source must be inside the image and must not overlap the erased block. **Check that it is clean** (`analyze --box` on it) |
| `sample` | `ref_box`, `box?`, `max_std?` | fills with pixels drawn at random (seeded, reproducible) from a clean patch. Use when no clean block of the right size exists. Warns if the patch is not homogeneous (`max_std`, default 12) |
| `column-repeat` | `ref_col`, `box?` | repeats one clean column (cell-relative `ref_col`) across the x-span of `box`, **full height of the image/cell** |
| `consensus` | `variants[]`, `min_support?` (2), `ref_box?`, `box?` | per-pixel majority across same-size variants. Never on text shared by most variants. With `ref_box`, pixels that do not look like the background fall back to sampling |

`source` (optional, for `copy`, `column-repeat` and `sample`): take the pixels from another image of the
same dimensions instead of the image itself. Use it when the image has no clean pixels of its own.

`sources` (optional, same modes, for images with `cells`): one single-cell donor image per cell, in cell order,
each exactly one cell in size. Use it when the donors are separate files (a strip of 3 states whose siblings
are three 51x24 images). Boxes stay cell-relative, so `box` and `ref_col` index into the donor.

## Style keys

| key | default | notes |
|---|---|---|
| `font` | required | path to a .ttf/.otf/.ttc (find one with `imgtr fonts`) |
| `font_index` | 0 | face inside a .ttc |
| `size` | required | requested size in px |
| `min_size` | `max(6, size-4)` | the smallest size `fit` may shrink to |
| `fit` | `"shrink"` | `"none"` never shrinks. If text still overflows at `min_size`, the image is **not written** |
| `fill` | `#ffffff` | solid colour, optional alpha |
| `outline` | none | `{ "color": "#000000", "width": 1 }` |
| `shadow` | none | one `{ "color": "#1e2d55dc", "offset": [1, 1] }` or a **list** of them (drawn in order). Must stay inside `box` |
| `align` | `center` | `left\|center\|right`: the ink (outline included) is aligned horizontally |
| `valign` | `middle` | `top\|middle\|bottom` align the ink box. **`baseline`** puts the baseline on row `baseline` (counted from the box top) whatever the font or size: use it to align texts across images. **`metrics`** centres the font's line box (ascent+descent) and **`origin`** puts its top at the box top: the baseline is then stable for one font size. `top`/`middle`/`bottom` follow each label's glyphs, so the baseline drifts with descenders |
| `baseline` | | row of the baseline from the box top; required by `valign: baseline` |
| `spacing` | 4 | line spacing for multi-line text |
| `dx`, `dy` | 0 | nudge after alignment (e.g. pressed state) |
| `opacity` | 1.0 | multiplies the colour alpha |
| `antialias` | true | `false` renders hard-edged text for pixel-font UIs |

Text is composited with straight-alpha "over", so semi-transparent areas stay semi-transparent
and only the pixels the text covers change. Output is deterministic (same spec + inputs = same bytes).

## Alignment check

Rule: images of the same **height** share one baseline row; images of the same **width** share one
horizontal anchor. `apply` (also with `--dry-run`) compares single-line regions that sit at the same
position in images of equal height or width and returns:

```json
"alignment": { "ok": false,
  "groups": { "height": { "24": ["btn_a.png", "btn_b.png"] }, "width": { "240": ["btn_a.png", "btn_c.png"] } },
  "findings": [ { "rule": "same-height", "height": 24, "key": "0:1", "issue": "baseline differs",
                  "baseline": { "btn_a.png": 17, "btn_b.png": 15 } } ] }
```

Issues: `baseline differs`, `font size differs` (a label shrank alone), `align modes differ`, `anchor differs`
(centres may differ by 1 px, from rounding). Findings are warnings; `--strict-alignment` makes them exit 2.
Resolve each one or opt the region out with `"align_key": null`. The rationale and exceptions are in
`typography.md`.

## Examples

Settings panel (noisy translucent plate): one `copy` block per text column, then every line drawn.
Values below come from a real working session (Segoe UI Bold, dark outline title, light body with a
double drop shadow):

```json
{ "source": "back_sound.png",
  "erase": [
    { "mode": "copy", "box": [20, 20, 190, 50],  "offset": [380, 0] },
    { "mode": "copy", "box": [75, 88, 255, 277], "offset": [325, 0] } ],
  "regions": [
    { "box": [25, 24, 190, 40], "text": "SETTINGS", "size": 28, "align": "left",
      "outline": { "color": "#000000", "width": 3 } },
    { "box": [88, 90, 312, 24], "text": "Master Volume", "size": 15, "align": "left",
      "valign": "baseline", "baseline": 18,
      "fill": "#dae4ff",
      "shadow": [ { "color": "#1e2d55dc", "offset": [1, 1] }, { "color": "#1e2d558c", "offset": [1, 2] } ] } ] }
```

Three-state button family as a preset (dark label, light drop shadow, lighter label when pressed):

```json
{ "presets": { "tab": {
    "cells": { "width": 80, "count": 3 },
    "erase": [ { "cell": "all", "mode": "column-repeat", "ref_col": 10, "box": [7, 0, 66, 24] } ],
    "style": { "size": 13, "fill": "#3f3f7f", "shadow": { "color": "#ffffffc8", "offset": [1, 1] },
               "valign": "baseline", "baseline": 15 },
    "regions": [ { "cell": "all", "box": [7, 2, 66, 20], "text": "{text}",
                   "state_overrides": { "2": { "fill": "#9f9fbf",
                                               "shadow": { "color": "#ffffffb4", "offset": [1, 1] } } } } ] } },
  "images": [ { "source": "btn_tab_sound_bt3.png",  "preset": "tab", "text": "Sound" },
              { "source": "btn_tab_system_bt3.png", "preset": "tab", "text": "System 1" } ] }
```

A button whose label covers its own rounded caps (here columns 3..46 of a 51 px cell) has no clean column
to repeat. Rebuild those columns from a sibling with the same caps and gradient; each cell takes its own
state from the donor because the boxes are translated per cell:

```json
{ "source": "btn_cfginit_bt3.png", "cells": { "width": 51, "count": 3 },
  "erase": [
    { "cell": "all", "mode": "copy",          "offset": [0, 0], "box": [3, 0, 12, 24],  "source": "btn_end_bt3.png" },
    { "cell": "all", "mode": "column-repeat", "ref_col": 8,     "box": [15, 0, 22, 24], "source": "btn_end_bt3.png" },
    { "cell": "all", "mode": "copy",          "offset": [0, 0], "box": [37, 0, 10, 24], "source": "btn_end_bt3.png" } ],
  "regions": [ { "cell": "all", "box": [7, 2, 37, 20], "text": "Reset", "size": 13 } ] }
```

A strip whose siblings are separate single-cell files uses `sources` instead (match each cell to the donor
whose untouched cap columns are identical):

```json
"erase": [ { "cell": "all", "mode": "column-repeat", "ref_col": 8, "box": [15, 0, 22, 24],
             "sources": ["btn_back_off.png", "btn_back_over.png", "btn_back_on.png"] } ]
```

Check that the donor really shares the pixels outside the text: `analyze a.png b.png` shows in `ink_bbox`
where two same-size files differ; everything outside it is identical.

## Not supported in v0 (the CLI refuses instead of degrading)

16-bit PNG, animated images, GIF output, `tRNS` transparency on non-RGBA images. Palette (P) and LA
sources are written as RGBA with a `mode_changed` warning, and `verify` will flag the mode. JPEG is
re-encoded (lossy), so pixels outside the boxes can differ slightly (`lossy_format` warning).
No gradient text fill, no letter-spacing, no vertical text, no automatic OCR.

