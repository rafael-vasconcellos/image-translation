# translations.json (spec) format, v1

One file describes the whole batch. It is the single source of truth: the agent writes the
translation table once, you can review it, and `apply` / `verify` both read it.

## Coordinates

Every box is `[x, y, w, h]` in pixels, origin top-left. When an image declares `cells`, all boxes in
its regions are **cell-relative** and are translated to each targeted cell automatically.

## Top level

```json
{
  "version": 1,
  "seed": 1,
  "defaults": { "font": "C:/Windows/Fonts/arial.ttf", "size": 14, "fill": "#ffffff" },
  "images": [ ... ]
}
```

## Image

| key | meaning |
|---|---|
| `source` | path relative to `--src`; the output mirrors it under `--out` |
| `cells` | `{ "width": 51, "count": 3 }` for strips of equal-width states (normal/hover/pressed). `width*count` must equal the image width |
| `style` | optional style overrides for every region of this image |
| `regions` | list of regions (below) |

## Region

| key | meaning |
|---|---|
| `text` | the translation. `\n` makes a line break |
| `box` | where the new text must fit (also the default erase area) |
| `cell` | `"all"` (default), an index, or a list of indexes. Only for images with `cells` |
| `erase` | **required**: `{ "mode": ..., ... }`, see below. Use `"none"` to draw on an already blank area |
| style keys | any of the style keys below, overriding `defaults` and the image `style` |
| `state_overrides` | `{ "2": { "dy": 1, "fill": "#dddddd" } }`: style per cell index (pressed = shifted/dimmer) |

## Erase modes

| mode | fields | what it does |
|---|---|---|
| `none` | | nothing |
| `fill` | `color` | solid colour (`#rrggbb` or `#rrggbbaa`) |
| `sample` | `ref_box`, `box?`, `max_std?` | fills the area with pixels drawn at random (seeded, reproducible) from a clean patch. Warns if the patch is not homogeneous (`max_std`, default 12) |
| `column-repeat` | `ref_col`, `box?` | repeats one clean column (cell-relative `ref_col`) across the x-span of `box`, **full height of the image/cell** |
| `consensus` | `variants[]`, `min_support?` (2), `ref_box?`, `box?` | per-pixel majority across same-size variants; with `ref_box`, pixels that do not look like the background fall back to sampling |

`box` inside `erase` overrides the erase area (default: the region `box`). Use it when the old text was
larger than the room the new text needs.

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
| `shadow` | none | `{ "color": "#000000a0", "offset": [1, 1] }`, must stay inside `box` |
| `align` / `valign` | `center` / `middle` | `left\|center\|right`, `top\|middle\|bottom`; the ink (outline included) is aligned |
| `spacing` | 4 | line spacing for multi-line text |
| `dx`, `dy` | 0 | nudge after alignment (e.g. pressed state) |
| `opacity` | 1.0 | multiplies the colour alpha |
| `antialias` | true | `false` renders hard-edged text for pixel-font UIs |

Text is composited with straight-alpha "over", so semi-transparent areas stay semi-transparent
and only the pixels the text covers change. Output is deterministic (same spec + inputs = same bytes).

## Examples

Noisy semi-transparent panel, header shared by all variants (so: `sample`, not `consensus`):

```json
{ "source": "back_sound.png",
  "regions": [
    { "box": [90, 18, 220, 34], "text": "Environment Settings", "size": 20, "align": "left",
      "erase": { "mode": "sample", "ref_box": [400, 300, 120, 120] } },
    { "box": [88, 96, 220, 30], "text": "Master Volume", "align": "left",
      "erase": { "mode": "consensus",
                 "variants": ["back_sound.png", "back_system.png", "back_text.png"],
                 "ref_box": [400, 300, 120, 120] } } ] }
```

Three-state button strip:

```json
{ "source": "btn_end_bt3.png", "cells": { "width": 51, "count": 3 },
  "regions": [
    { "cell": "all", "box": [8, 3, 35, 18], "text": "Exit", "size": 12,
      "erase": { "mode": "column-repeat", "ref_col": 8 },
      "outline": { "color": "#000000", "width": 1 },
      "state_overrides": { "2": { "dy": 1 } } } ] }
```

## Not supported in v0 (the CLI refuses instead of degrading)

16-bit PNG, animated images, GIF output, `tRNS` transparency on non-RGBA images. Palette (P) and LA
sources are written as RGBA with a `mode_changed` warning, and `verify` will flag the mode. JPEG is
re-encoded (lossy), so pixels outside the boxes can differ slightly (`lossy_format` warning).
No gradient text fill, no letter-spacing, no vertical text, no automatic OCR.

