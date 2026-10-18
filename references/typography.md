# Typography, layout and image properties

How this maps to `imgtr`: font, size, `min_size`, fill, `outline`, `shadow`, `align`, `valign`,
`dx`/`dy`, `opacity`, `antialias` and per-state `state_overrides` are all in `spec-format.md`.
`apply` measures the rendered text (outline and shadow included) against the region `box`, shrinks
one pixel at a time down to `min_size`, reports every shrink, and refuses to write the image if the
text still does not fit. Use `fonts --sample` to compare candidate fonts visually.
Limits of v0: solid fills only (no gradient text), no letter-spacing, one font per region.

---

## Alignment across images (rule)

- **Same height means same vertical alignment.** In a group of images with the same height, the
  single-line texts at corresponding positions (same region index and cell, or the same `align_key`) sit
  on the **same baseline row**.
- **Same width means same horizontal alignment.** In a group of images with the same width,
  corresponding texts use the same `align` and the same anchor: the same left edge, centre or right edge.
- **How.** Use `valign: baseline` with one `baseline` value per group, not `middle`/`top`/`bottom`: those
  follow each label's own glyphs and drift with descenders ("System" sat 2 px higher than "Sound").
  Use the same box and `align`, and **one font size per group**: the smallest at which every label of the
  group fits (`measure --max-width`). A label that shrinks alone shifts the baseline, so shrink the group
  or reword the label.
- **Where the baseline comes from.** CJK text has no baseline. Centre the Latin capitals on the ink
  centre of the original text: baseline row = original centre + (`cap_height` + 1) / 2, with `cap_height`
  from `measure`. Take original centres from `analyze --lines` (panels) or from the ink rows (buttons).
- **Lists of lines.** Take the pitch from the originals: fit one grid across all the images of the group
  rather than using each line's own ink top, which jitters by about 2 px with the glyph shapes.
- **Check.** `apply` (also with `--dry-run`) reports `alignment.findings`. Fix every finding, or opt a
  region out with `"align_key": null` and say why in the report.
- **Exception.** If the originals of a group are not aligned beyond glyph noise (about 3 px), do not
  invent alignment: keep each original position, opt those regions out, and report it. The same
  dimensions do not by themselves prove the same layout.

---

## Original guidance

## Recreate the typography and layout

1. Match the source text's visual role and hierarchy: font character, weight, size, color or gradient, outline, shadow, glow, blur, opacity, alignment, spacing, perspective, and baseline direction.
2. Use a font already available in the project or environment when it is a reasonable match. Do not silently substitute a materially different type style; record the fallback if it affects fidelity.
3. Measure the rendered target text with real font metrics. Evaluate the complete result after outlines, shadows, rotation, filtering, and downsampling—not just the unstyled glyphs.
4. Keep the translated text legible and within the original component's actual safe shape. For sloped, rounded, rotated, or irregular regions, check the final effected pixels against the component shape, not only against a rectangular bounding box.
5. When the translation is longer, prefer a natural shorter equivalent that preserves the meaning, an allowed line break, or a modest expansion within existing safe space. Do not distort the component or shrink text below a practical readable size merely to match the source-language width.
6. Preserve empty space that plausibly belongs to a dynamic value, UI control, or neighboring element. Do not add values that were not present in the source image.
7. Never allow the translated text, outline, shadow, or glow to clip, collide with nearby content, or obscure meaningful artwork.

## Preserve image properties and protected content

- Keep the same relative file path and filename within `translated/`.
- Preserve dimensions, file format, color mode, bit depth, animation/frame behavior, and alpha channel where the format and available tools permit. Do not silently flatten transparency or animation.
- For transparent or partially transparent images, treat RGB and alpha as artwork. Preserve pixels outside approved edit regions. Inspect the alpha channel separately and check for halos, matte colors, rectangular patches, and edge seams.
- Where practical, require zero pixel difference in protected regions. Do not claim pixel-perfect preservation unless it was actually checked.
- Preserve portraits, subjects, distinctive illustrations, logos, meaningful background details, and source-specific variant differences unless the user explicitly authorizes changing them.
- Do not use one variant's background in another just because the files look similar. Confirm the composition and semantic state match.
- If an image contains no translatable text, copy it unchanged to the output. If only part of an image can be translated safely, translate the safe regions and document the preserved region.

