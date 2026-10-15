# image-translation

An Agent Skill (`SKILL.md` format) and a small CLI (`imgtr`) for translating the text that is baked
into images, such as game UI buttons, tab strips and menu panels, without touching anything else in the
image.

The agent does what only it can do: look at the image, read the text, translate it and judge the result.
`imgtr` does the pixel work deterministically: measure, erase the old text, draw the translation, and
prove that nothing outside the edited areas changed. The agent never writes ad hoc pixel scripts, so it
spends its steps on translation and review instead of debugging code.

> **Status: v0.2.** Developed and tested on synthetic fixtures and on 13 real UI images from a Japanese
> visual novel. See [Status and limits](#status-and-limits).

## Summary

- [Why a CLI instead of a better prompt](#why-a-cli-instead-of-a-better-prompt)
- [Repository layout](#repository-layout)
- [Requirements](#requirements)
- [Using it with an agent](#using-it-with-an-agent)
- [Using the CLI directly](#using-the-cli-directly)
- [Guarantees and refusals](#guarantees-and-refusals)
- [Status and limits](#status-and-limits)
- [Development](#development)

## Why a CLI instead of a better prompt

Early tests with agents showed the same pattern: one run spent dozens of steps analysing background statistics and delivered no file; another worked, but embedded a 6 KB `python -c` script with every coordinate, colour and translation inside a command line, and verified almost nothing. Both problems come from the agent improvising pixel code. A tested tool with a declarative spec fixes that:

- **Fewer round trips.** One `apply` replaces dozens of analyse-and-patch commands across many files.
- **Reviewable.** The whole translation is one JSON file (`translations.json`) you can read, diff and re-run.
- **Provable.** `verify` reports zero pixel difference outside the declared boxes, or it fails.
- **Safe.** Originals are never written; unsupported inputs are refused instead of silently degraded.

## Repository layout

```
.
└── image-translation/          # the skill: the folder that'll be copied into your agent's skills directory
    ├── SKILL.md                # the workflow the agent follows
    ├── README.md
    ├── references/
    │   ├── spec-format.md      # translations.json reference
    │   ├── reconstruction.md   # how to erase text, by background type
    │   ├── typography.md       # fonts, fitting, alpha and protected content
    │   └── validation.md       # what `verify` proves and what you still have to look at
    └── scripts/
        ├── imgtr
            ├── imgtr.py            # the CLI (single file)
            ├── __init__.py
            └── __main__.py
        ├── tests/test_imgtr.py
        ├── pyproject.toml
        └── requirements.txt
```

## Requirements

- Python 3.9+
- [Pillow](https://pypi.org/project/pillow/) 10+ (developed on 12.x) and [numpy](https://numpy.org/)

```
pip install -r image-translation/scripts/requirements.txt
```

You also need a TrueType/OpenType font that suits the target language. `imgtr fonts` lists what is
installed (on Windows, `C:\Windows\Fonts`).

## Using it with an agent

1. Install Pillow and numpy (above).
2. Copy the `image-translation/` folder to your agent's skills directory.
```bash
npx skills add rafael-vasconcellos/image-translation
```
3. Open the agent in the folder that contains the images and ask, for example:
   *"Translate the images in this folder to English."*

The skill makes the agent work in this order:

1. `inventory`: list images, alpha, button cell grids.
2. Look at every image (`crop` enlarges small text), transcribe and translate.
3. Draft `translations.json` spec with the transcriptions and translations (the agent writes this file itself).
4. `analyze` the originals: text lines, ink colours, outline and shadow, clean background.
5. `fonts` and `measure`: pick a font and check the translations fit.
  - The agent then completes the spec by hand (boxes, erase modes, font and sizes) from these read-only outputs.
6. `apply` one representative image, `verify --compare`, look at the sheet, then do the rest.
7. Check for leftover ink, write the report, `clean`.

Defaults you can rely on:

| | |
|---|---|
| Scope | the current folder only; subfolders only if you ask |
| Output | `translated/`, same relative paths and file names; only translated images are written |
| Report | `translated_report.md`, beside `translated/` (so `translated/` can be copied into a game as is) |
| Scratch files | `./.imgtr-work/` only, removed by `imgtr clean` |
| Originals | never modified; `apply` refuses `--out` equal to `--src` |

## Using the CLI directly

```
python image-translation/scripts/imgtr <command> [options]
```

Every command prints one JSON document on stdout (`--pretty` to indent). Exit codes: `0` ok, `1` usage or
I/O error, `2` a check failed (overflow, verify mismatch, apply error).

| Command | What it does |
|---|---|
| `inventory [dir]` | size, mode, format, alpha values, bit depth, duplicates and **cell-grid candidates** for button strips. Not recursive unless `--recursive` |
| `analyze FILES...` | compare same-size variants (per-pixel majority, where each file differs); with `--box`: pixel stats, plus `--ref-box` and `--lines` (text line positions), `--palette N` (ink colours), `--ascii` (character map of a glyph, to see outline and shadow); with `--cells W:N`: clean and inked columns of each button state |
| `crop IMG` | enlarged crop over a checkerboard, optionally with cell boundaries (`--cells`), for looking at small text (writes support images at `.imgtr-work/`) |
| `fonts` | list installed fonts; `--sample "text"` renders a specimen sheet (writes support images at `.imgtr-work/`) |
| `measure TEXT...` | width and height of candidate translations for a font and size (`--stroke`, `--max-width`) |
| `apply SPEC` | erase the source text and draw the translation as described by the spec (`--src`, `--out`, `--only`, `--dry-run`) |
| `verify SRC OUT` | size, mode and format equal; pixel difference **outside** the edit boxes; original-vs-translated sheets with `--compare` (writes support images at `.imgtr-work/`) |
| `clean` | delete `./.imgtr-work` |

### A minimal example

Settings panel with a translucent, noisy background (copy a clean block over the old text once, then draw
every line), plus a family of 3-state buttons as a preset:

```json
{
  "version": 1,
  "defaults": { "font": "C:/Windows/Fonts/segoeuib.ttf", "size": 15 },
  "presets": {
    "tab": {
      "cells": { "width": 80, "count": 3 },
      "erase": [ { "cell": "all", "mode": "column-repeat", "ref_col": 10, "box": [7, 0, 66, 24] } ],
      "style": { "size": 13, "fill": "#3f3f7f", "shadow": { "color": "#ffffffc8", "offset": [1, 1] } },
      "regions": [ { "cell": "all", "box": [7, 2, 66, 20], "text": "{text}",
                     "state_overrides": { "2": { "fill": "#9f9fbf" } } } ]
    }
  },
  "images": [
    { "source": "back_sound.png",
      "erase": [ { "mode": "copy", "box": [75, 88, 255, 277], "offset": [325, 0] } ],
      "regions": [
        { "box": [88, 94, 250, 24], "text": "Master Volume", "align": "left", "valign": "origin",
          "fill": "#dae4ff" } ] },
    { "source": "btn_tab_sound_bt3.png", "preset": "tab", "text": "Sound" }
  ]
}
```

```
imgtr apply  translations.json --src . --out translated
imgtr verify . translated --spec translations.json --compare
```

`verify` output (abridged):

```json
{"ok":true,"checked":2,"results":[
  {"source":"back_sound.png","same_size":true,"same_mode":true,"same_format":true,
   "outside_diff_pixels":0,"alpha_changed_outside":0,"inside_changed_pixels":78783,
   "compare":{"path":".imgtr-work/compare_back_sound.png"},"ok":true}, ...]}
```

The full spec reference, with every erase mode and style key, is in
[`image-translation/references/spec-format.md`](image-translation/references/spec-format.md).

### Erase modes in one line each

| Mode | Use it for |
|---|---|
| `copy` | a clean block of the same image (or of a sibling via `source`) goes over the old text; keeps real texture and alpha |
| `sample` | no clean block of the right size: fill with pixels drawn at random (seeded) from a clean patch |
| `column-repeat` | button strips with a vertical gradient: repeat one clean column across the text span |
| `consensus` | several variants of one layout whose text differs: per-pixel majority |
| `fill` / `none` | solid colour / draw on an already blank area |

Every image must say how its old text goes away (an image-level `erase` list or an `erase` on each
region); there is no silent default, because a forgotten erase leaves ghost text that no automatic check
can see. That is also why `verify` is paired with the leftover-ink check described in
[`validation.md`](image-translation/references/validation.md).

## Guarantees and refusals

- Output is **deterministic**: the same spec and inputs give the same bytes.
- Pixels outside the declared boxes are left untouched, and `apply` checks this itself before writing.
- Text is composited with straight-alpha "over": translucent areas stay translucent, and only covered
  pixels change.
- If a translation does not fit its box at `min_size`, the image is **not written** and the overflow is
  reported. Every shrink is reported too.
- The CLI refuses: 16-bit PNG, animated images, GIF output, and `tRNS` transparency on non-RGBA images.
  Palette and LA sources are written as RGBA with a `mode_changed` warning that `verify` also flags.
  JPEG is re-encoded (lossy), with a warning.

## Status and limits

Verified:

- 28 automated tests on synthetic fixtures (translucent noisy panel, 3-state pill buttons): exactness
  outside the boxes, alpha preservation, overflow handling, determinism, presets, donor images,
  baseline alignment, text-line detection.
- 13 real UI images (5 panels of 800x600 and 8 button strips): every output passed `verify` with zero
  pixels changed outside the boxes, and the leftover-ink check matched the number of lines drawn.
  Text positions and colours measured by `analyze` agreed with a previous manual, accepted run.

Not verified, or by design out of scope:

- Fit and appearance with the exact font you choose: widths vary by font. `apply` reports any overflow.
- It has not been tested on macOS, or with every agent that supports Agent Skills.
- No OCR, no translation, no generative inpainting. Transcription and translation are the agent's job;
  text over portraits or unique artwork is preserved and reported, not repainted.
- No gradient text fill, no letter-spacing, no vertical text, no animated formats.

## Development

```
python -m unittest discover -s image-translation/scripts/tests -v
```

The tests build their own fixtures and need one TrueType font on the machine (they skip otherwise).
The CLI is a single file with no dependencies beyond Pillow and numpy, to keep it easy to audit and to
copy next to a skill.



