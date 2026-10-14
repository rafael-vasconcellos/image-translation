---
name: image-translation
description: Translate text embedded in image files (game UI, buttons, tab strips, menu panels, sprites, banners, signs) into another language while keeping dimensions, format, file names and artwork intact. Use this whenever the user asks to translate, localize or replace the text inside PNG/JPEG/BMP/WebP images, especially visual-novel or game UI assets with Japanese text, even if they do not mention a skill or image editing. Ships a CLI (scripts/imgtr.py) that does all the pixel work, so the agent only reads, translates, and visually verifies.
compatibility: Python 3.9+ with Pillow and numpy (pip install -r scripts/requirements.txt)
---

# Translate Text Embedded in Images

## Goal

Translate all meaningful, viewer-facing text embedded in the image files of the current working
directory, while preserving the original artwork, composition, visual style, and image properties
as closely as practical. Your job is to look, read, translate and judge. The `imgtr` CLI does the
measuring, erasing, drawing and checking, so you never write pixel code.

## The CLI

Run it as `python <skill_dir>/scripts/imgtr.py <command>`, where `<skill_dir>` is the folder that
contains this SKILL.md. Every command prints one JSON document and exits 0 (ok), 1 (usage/I-O error)
or 2 (a check failed). `--help` on any command explains its options.

| Command | Use it to |
|---|---|
| `inventory [dir]` | list images: size, mode, alpha values, bit depth, **cell-grid candidates** for button strips, duplicates |
| `crop IMG --box x,y,w,h --scale 4 [--cells W:N]` | get an enlarged view (over a checkerboard) of small text |
| `analyze FILES... [--box ..] [--ref-box ..] [--lines] [--palette N] [--ascii] [--cells W:N]` | measure the original: text line positions, ink colours, outline/shadow as a character map, background stats, clean columns in buttons, variant comparison |
| `measure --font F --size N [--stroke W] [--max-width W] TEXT...` | width/height of candidate translations before writing the spec |
| `fonts --filter NAME [--sample "text" --size N]` | find installed fonts and view a specimen sheet |
| `apply translations.json --src . --out translated` | erase the source text and draw the translation |
| `verify . translated --spec translations.json --compare` | prove nothing changed outside the edit boxes; write side-by-side sheets |
| `clean` | delete the `.imgtr-work/` scratch folder |

Do not write ad hoc scripts (`python -c`, per-pixel loops, PIL snippets) to analyze or edit pixels.
Each one costs a slow round trip and has to be debugged; the commands above are already tested. If
something truly cannot be expressed, leave that region unchanged and report it under Skipped.
If `imgtr` fails to import Pillow or numpy, run `pip install -r <skill_dir>/scripts/requirements.txt`
and tell the user; that is the only installation this skill allows.

## Defaults

- **Target language:** the one the user asks for. If none, English, and say so in the final report.
- **Scope:** the current folder only, not subfolders. Recurse (`inventory --recursive`) only if the user
  asks. A request such as "this folder and only this folder" must never leak into other directories.
  Do not skip an image because of its name (`test`, `dummy`, `unused`...).
- **Output:** `translated/` in the working directory, mirroring relative paths and file names. Only
  translated images are written there; originals are never touched. Images without translatable text
  are listed in the report, and copied unchanged only if the user asks (the folder is usually copied
  into a game as a patch, and extra identical files add risk and no value).
- **Scratch files:** only in `./.imgtr-work/` (the CLI uses it for crops and comparison sheets). Never
  leave temporary images in the source folder. Run `clean` at the end.
- **Report:** `translated_report.md` in the working directory, beside `translated/`, never inside it.
- **Consistency:** keep a small glossary for repeated terms, names, buttons and labels. Prefer a
  user-supplied glossary.
- **Autonomy:** process the whole batch without routine confirmation. Ask only for a genuinely
  blocking ambiguity or a required authorization. If one region is unsafe to edit, preserve it and go on.

## Workflow

1. **Inventory.** Run `inventory .` and read the JSON. Note groups of same-size images, `alpha`
   values (a constant alpha such as 153 means a deliberate translucent panel), `cell_candidates`
   (a strip of N states side by side), and anything unsupported (16-bit, animated): those go to Skipped.
2. **Look at every image** at native size, and enlarge small ones with
   `crop IMG --scale 4 [--cells W:N]`. Do not rely on OCR. Identify each visible text region, classify
   it (heading, button label, tab, caption, logo/wordmark, text to keep) and tell text apart from
   texture or artwork. For strips, confirm the cell grid visually and note how the states differ
   (colour, brightness, 1px offset when pressed).
3. **Transcribe and translate.** Transcribe exactly, keeping punctuation, numbers, symbols and
   placeholders. If a reading is uncertain, flag it instead of inventing. Translate by this
   precedence: user glossary, established translations elsewhere in the same set, the meaning
   implied by the image and surrounding files, then a conservative reading. Prefer natural wording,
   keep related controls consistent, keep names/brands/identifiers/URLs/numbers/universal symbols.
   Record everything in `translations.json` (format in `references/spec-format.md`): this table is
   your per-image inventory.
4. **Measure the original, then choose how to erase.** Do not eyeball positions or colours. Use
   `analyze IMG --box <text column> --ref-box <clean patch> --lines --palette 4` for the position of each
   line and the ink colours, `--ascii` on one glyph to see outline/shadow structure, and `--cells W:N`
   for button columns. Pick the `erase.mode` from `references/reconstruction.md`. Typical case:
   translucent noisy panel gives one image-level `copy` block per text column; 3-state pill buttons give
   `column-repeat`, and a family of identical buttons becomes a `preset`. If a button has no clean column
   (`ref_col: null`), borrow the span from a sibling with `erase.source`. Text over portraits, distinctive objects
   or unique illustration details is preserved and reported, not repainted.
5. **Pick a font and check fit.** `fonts --filter ... --sample "Your text"` and view the sheet. Choose the
   closest match in character and weight; record a fallback in the report if it affects fidelity. Then
   `measure` your candidate translations against the available width, and reword now rather than after
   `apply` reports an overflow. For lists of lines use `valign: metrics` so baselines do not drift.
6. **One representative first.** Run `apply --only <one image> ...`, then `verify --spec --compare --only
   <it>`, and view the sheet. Complete one representative per distinct background/layout, including the
   hard cases (longest translation, smallest text, 3-state strips, translucent panels). Fix the method
   before applying it to the rest of the family. After `apply`, run the leftover-ink check from
   `references/validation.md` (`analyze` on the output: line count must equal regions drawn). See that
   file for the rest of what to look for.
7. **Batch.** `apply` everything, `verify` everything, repair what fails.
8. **Report and clean.** Write the report, run `clean`.

## Budget and stop rules

These exist because earlier attempts burned an hour analysing backgrounds and produced no files.

- A first translated image must exist within about 12 tool calls after the inventory.
- At most two attempts per erase technique, then move to a simpler one. `sample` is a legitimate
  floor. If the background is noise-like (small `std` in `analyze --box`), do not study periodicity,
  tiling, or inpainting.
- `overflow`: first shorten the wording naturally, then allow a `\n` line break, then a modest size
  reduction within `min_size`. Never shrink below a practical readable size to keep the source width.
  If it still does not fit, report the region; do not force it.
- `verify` reports `outside_diff_pixels > 0`: the declared box is wrong. Fix the box in the spec and
  re-run; never patch pixels by hand.
- Always work from the untouched originals; `apply` does, and refuses to write into `--src`.

## Deliverables and report

`translated/` with the translated images; `translated_report.md` (one concise file) with:

- source and target languages, noting any default assumption;
- counts: discovered, translated, left unchanged, skipped or needing review;
- a table, one row per translated region: `Image | Region | Original text | Translation | Erase mode | Status`;
- a short list of images or regions left unchanged or needing review, with the reason and what was preserved;
- validation done (quote the `verify` numbers) and limitations: font substitution, uncertain readings,
  unsupported formats.

No large logs or encoded image data in the report.

## Final response

State the output folder, how many images and regions were processed, a few `source → translation`
examples, the checks you ran and what you actually looked at, and any limitation. Be honest about
what was and was not visually verified. If anything is untranslated or uncertain, end with a clearly
labeled **Skipped / needs review** section listing each image and region, why it was left unchanged,
and what would allow safe completion. Do not imply the batch is complete when items remain.

## Completion criteria

Done only when: every discovered image has a recorded disposition; every written image was
checked with `verify` (exit 0) and its compare sheet viewed; all strings reviewed; originals untouched;
`translated/` and `translated_report.md` exist; `.imgtr-work/` is gone.

## References (read when needed)

- `references/spec-format.md`: the `translations.json` fields, erase modes, style keys, examples. Read before writing the spec.
- `references/reconstruction.md`: choosing how to erase text, and the full technique ladder. Read at step 4 or when text sits over artwork.
- `references/typography.md`: matching fonts, fitting longer translations, preserving alpha and protected content.
- `references/validation.md`: what `verify` proves, and the visual checklist it cannot do for you.

