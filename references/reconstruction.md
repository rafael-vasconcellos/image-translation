# Reconstruction: erasing the source text

How the techniques below map to `imgtr` (see `spec-format.md` for the fields):

| Situation | `erase.mode` | Notes |
|---|---|---|
| Flat or finely noisy background (panel, plate) | `sample` | Draws pixels at random from a clean `ref_box`, so noise, colour correlation and alpha are reproduced exactly. Check `analyze --box` on the patch first: a small `std` means this is enough. |
| Button/tab strips with a vertical gradient (3 states side by side) | `column-repeat` | Repeats one clean column (`ref_col`, cell-relative) across the text span, full cell height. Get `ref_col` and the clean runs from `analyze --cells W:N`. |
| Several variants of the same layout whose text differs, background has structure | `consensus` | Majority vote per pixel across `variants`. Never use it on text shared by most variants (the majority IS that text). Give a `ref_box` so suspicious pixels fall back to sampling. |
| Solid colour | `fill` | |
| Text over artwork, portraits, unique details | none of the above | Read items 5 and 6 below: preserve the region and report it. |

Stop rule: at most two attempts per technique. `sample` is the floor, not a failure; if the
background is noise-like, do not investigate periodicity, tiling or inpainting.

---

## Original guidance (technique ladder and working in families)

## Choose the least destructive reconstruction method

Treat each image as both artwork and a text-bearing composition. Remove or replace only the text that is meant to be translated.

Choose a technique based on the actual background, preferring the first safe option available:

1. **Editable source or clean layer:** Use a clean source image, layer, vector text object, or verified text-free variant when available. Rebuild from that source rather than repairing a flattened image unnecessarily.
2. **Flat or simple background:** Reconstruct the affected area with a sampled fill, gradient, or matching patch.
3. **Repeating texture or regular pattern:** Restore the pattern from nearby clean pixels or a verified matching variant.
4. **Structured panel or button:** Rebuild the existing component, preserving its real outline, corners, bevels, dividers, texture, and proportions. Do not invent a new panel, pill, badge, or backing shape when the source has none.
5. **Text over meaningful artwork:** Prefer clean-layer recomposition or a verified matching source. If unavailable, consider tightly masked local inpainting only when a suitable tool is already available and authorized. Treat its output as a candidate, not as automatically correct.
6. **Text over a person, portrait, distinctive object, or unique illustration detail:** Do not damage or repaint that content just to remove text. If the text cannot be separated and safely reconstructed, leave that region unchanged and report it.

Do not download models, install tools, send images to external services, or use broader generative image edits without the required user authorization. A tool's ability to generate an image is not, by itself, permission to change the source artwork.

For any reconstruction method:

- Work from the verified original, not from a previously edited candidate.
- Make candidates in a temporary working area, not in the input or final output tree.
- Cover the complete source glyph, including antialiasing, outline, shadow, and glow, without unnecessarily expanding the edit region.
- Inspect the text-free reconstruction before adding translated text.
- Reject results with source-glyph remnants, smeared details, repeated foreign features, seams, abrupt texture changes, or unexplained color or alpha changes.
- Never use blurred source text as a background texture or cover source text with a translucent plate that leaves the old lettering visible underneath.

## Work efficiently across image families

1. Complete a representative image for each distinct background and layout type before processing the entire family.
2. Include difficult cases in the representative set: the longest translation, smallest text, low-contrast text, text near edges, transparency, irregular shapes, and text over detailed artwork.
3. Inspect the representative result at native resolution and, when relevant, at its likely display size over a representative background.
4. Reuse accepted reconstruction and typography settings only for images whose layout and visual treatment genuinely match. Apply explicit per-image differences rather than assuming all variants are identical.
5. If a representative fails, improve the method before applying it to the rest of the family.
