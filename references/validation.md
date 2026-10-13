# Validation

`imgtr verify SRC OUT --spec translations.json --compare` automates the mechanical checks:
dimensions, mode and format equal; **zero pixel difference outside the declared boxes**
(`outside_diff_pixels`, `alpha_changed_outside`); and it writes an original-vs-translated sheet over a
checkerboard to `.imgtr-work/compare_<name>.png`. Exit code 2 means a check failed.

It cannot judge the visual items (ghosted glyphs, spelling, legibility, style, seams in the
erased area). View every compare sheet yourself. A passing `verify` is necessary, not sufficient.
Do not claim pixel-perfect preservation beyond what `verify` reported.

---

## Original guidance

## Validate every output

Before accepting an image:

- Reopen and decode it successfully.
- Confirm expected dimensions, format, color mode, and transparency/frame properties.
- Compare the source and output side by side at native resolution and in enlarged crops around every edited region.
- Verify that all intended source text is removed and that the target text is spelled correctly, natural, legible, unclipped, and stylistically consistent.
- Look for ghosted source glyphs, leftover outlines, accidental edits to neighboring content, visible seams, edge halos, unexpected background changes, and layout collisions.
- For transparency, compare alpha against the original and composite the result over light, dark, midtone, and strongly colored backgrounds when practical.
- Check that protected regions remain unchanged when promised, using pixel comparisons for the specific regions involved.
- Recheck complete images after local edits; text may also appear elsewhere in the same image.
- Do not accept an image solely because OCR, hashes, metadata, or a small thumbnail passed. Perform a visual review of the actual candidate.

If a candidate fails, correct it and validate again. If it cannot be repaired safely, keep the original for that image in the output and record the reason instead of presenting a damaged image as complete.

