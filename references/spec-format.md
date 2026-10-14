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
| `valign` | `middle` | `top\|middle\|bottom` align the ink box. **`metrics`** centres the font's line box (ascent+descent) and **`origin`** puts its top at the box top: the baseline then lands on the same row for any text of that font size. Use them for lists of lines, otherwise "Voice" and "Display" drift by the descender |
| `spacing` | 4 | line spacing for multi-line text |
| `dx`, `dy` | 0 | nudge after alignment (e.g. pressed state) |
| `opacity` | 1.0 | multiplies the colour alpha |
| `antialias` | true | `false` renders hard-edged text for pixel-font UIs |

Text is composited with straight-alpha "over", so semi-transparent areas stay semi-transparent
and only the pixels the text covers change. Output is deterministic (same spec + inputs = same bytes).

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
    { "box": [88, 94, 250, 24], "text": "Master Volume", "size": 15, "align": "left", "valign": "metrics",
      "fill": "#dae4ff",
      "shadow": [ { "color": "#1e2d55dc", "offset": [1, 1] }, { "color": "#1e2d558c", "offset": [1, 2] } ] } ] }
```

Three-state button family as a preset (dark label, light drop shadow, lighter label when pressed):

```json
{ "presets": { "tab": {
    "cells": { "width": 80, "count": 3 },
    "erase": [ { "cell": "all", "mode": "column-repeat", "ref_col": 10, "box": [7, 0, 66, 24] } ],
    "style": { "size": 13, "fill": "#3f3f7f", "shadow": { "color": "#ffffffc8", "offset": [1, 1] } },
    "regions": [ { "cell": "all", "box": [7, 2, 66, 20], "text": "{text}",
                   "state_overrides": { "2": { "fill": "#9f9fbf",
                                               "shadow": { "color": "#ffffffb4", "offset": [1, 1] } } } } ] } },
  "images": [ { "source": "btn_tab_sound_bt3.png",  "preset": "tab", "text": "Sound" },
              { "source": "btn_tab_system_bt3.png", "preset": "tab", "text": "System 1" } ] }
```

## Not supported in v0 (the CLI refuses instead of degrading)

16-bit PNG, animated images, GIF output, `tRNS` transparency on non-RGBA images. Palette (P) and LA
sources are written as RGBA with a `mode_changed` warning, and `verify` will flag the mode. JPEG is
re-encoded (lossy), so pixels outside the boxes can differ slightly (`lossy_format` warning).
No gradient text fill, no letter-spacing, no vertical text, no automatic OCR.

