#!/usr/bin/env python3
"""imgtr - deterministic toolkit for replacing text inside images (built for AI agents).

The agent does the seeing, reading and translating. imgtr does the pixel work:
measure, erase text, draw translated text, verify nothing else changed.

Every command prints ONE JSON document on stdout. Exit codes:
  0 ok | 1 usage or I/O error | 2 a check failed (overflow, verify mismatch, apply error)

Needs: Python 3.9+, Pillow, numpy.   Run `imgtr.py <command> --help` for details.
"""
from __future__ import annotations

import argparse
import copy
import functools
import hashlib
import json
import os
import shutil
import sys
import zlib
from collections import Counter
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageColor, ImageDraw, ImageFont
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(f"imgtr needs Pillow and numpy: pip install pillow numpy ({exc})\n")
    sys.exit(1)

__version__ = "0.2.0"

IMG_EXT = {".png", ".bmp", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".gif"}
WRITE_FORMATS = {"PNG", "BMP", "JPEG", "WEBP", "TIFF"}
DEFAULT_EXCLUDES = {"translated", ".imgtr-work", ".git", "node_modules", "__pycache__", ".venv"}
WORKDIR = ".imgtr-work"
STYLE_KEYS = ("font", "font_index", "size", "min_size", "fit", "fill", "outline", "shadow",
              "align", "valign", "baseline", "spacing", "antialias", "opacity", "dx", "dy")
ERASE_MODES = ("none", "fill", "copy", "sample", "column-repeat", "consensus")


class CliError(Exception):
    def __init__(self, msg: str, code: int = 1):
        super().__init__(msg)
        self.code = code


# --------------------------------------------------------------------------- helpers
def emit(obj, pretty=False):
    text = json.dumps(obj, ensure_ascii=False, indent=2 if pretty else None,
                      separators=None if pretty else (",", ":"))
    sys.stdout.write(text + "\n")


def box_arg(s: str):
    try:
        x, y, w, h = (int(float(v)) for v in s.replace(" ", "").split(","))
    except Exception:
        raise argparse.ArgumentTypeError("expected x,y,w,h (integers)")
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("w and h must be > 0")
    return [x, y, w, h]


def cells_arg(s: str):
    try:
        w, n = (int(v) for v in s.split(":"))
    except Exception:
        raise argparse.ArgumentTypeError("expected WIDTH:COUNT, e.g. 51:3")
    if w <= 0 or n <= 0:
        raise argparse.ArgumentTypeError("WIDTH and COUNT must be > 0")
    return (w, n)


def open_image(path) -> Image.Image:
    try:
        im = Image.open(path)
        im.load()
    except FileNotFoundError:
        raise CliError(f"file not found: {path}")
    except Exception as exc:
        raise CliError(f"cannot decode {path}: {exc}")
    return im


def to_rgba(im: Image.Image) -> np.ndarray:
    return np.array(im.convert("RGBA"), dtype=np.uint8)


def png_header(path):
    """(bit_depth, color_type, interlace) for PNG files, else None."""
    try:
        with open(path, "rb") as f:
            head = f.read(33)
        if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
            return None
        return head[24], head[25], head[28]
    except OSError:
        return None


def bbox_of(mask: np.ndarray):
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]


def runs(flags, value):
    out, start = [], None
    for i, f in enumerate(list(flags) + [not value]):
        if f == value and start is None:
            start = i
        elif f != value and start is not None:
            out.append([start, i - 1])
            start = None
    return out


def alpha_summary(arr):
    u = np.unique(arr[..., 3])
    d = {"min": int(u[0]), "max": int(u[-1]), "unique": int(len(u))}
    if len(u) <= 8:
        d["values"] = [int(v) for v in u]
    return d


def list_images(root, recursive, excludes):
    root = Path(root)
    if not root.is_dir():
        raise CliError(f"not a directory: {root}")
    found = []
    if recursive:
        for dp, dns, fns in os.walk(root):
            dns[:] = sorted(d for d in dns if d not in excludes)
            for fn in sorted(fns):
                if Path(fn).suffix.lower() in IMG_EXT:
                    found.append(Path(dp) / fn)
    else:
        for p in sorted(root.iterdir()):
            if p.is_file() and p.suffix.lower() in IMG_EXT:
                found.append(p)
    return found


def workdir() -> Path:
    p = Path(WORKDIR)
    p.mkdir(exist_ok=True)
    return p


def checker(size, sq=8):
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    g = np.where(((xx // sq + yy // sq) % 2).astype(bool), 255, 204).astype(np.uint8)
    return Image.fromarray(np.dstack([g, g, g, np.full((h, w), 255, np.uint8)]))


def flatten(im_rgba: Image.Image, bg="checker", scale=1):
    """Scale with nearest-neighbour, then composite over a visible background."""
    if scale != 1:
        im_rgba = im_rgba.resize((im_rgba.width * scale, im_rgba.height * scale), Image.NEAREST)
    if bg == "checker":
        base = checker(im_rgba.size)
    else:
        named = {"white": (255, 255, 255, 255), "black": (0, 0, 0, 255), "gray": (128, 128, 128, 255)}
        base = Image.new("RGBA", im_rgba.size, named.get(bg) or ImageColor.getcolor(bg, "RGBA"))
    return Image.alpha_composite(base, im_rgba)


def clamp_box(box, w, h):
    x0, y0 = max(0, box[0]), max(0, box[1])
    x1, y1 = min(w, box[0] + box[2]), min(h, box[1] + box[3])
    if x1 <= x0 or y1 <= y0:
        raise CliError(f"box {box} is outside the {w}x{h} image")
    return [x0, y0, x1 - x0, y1 - y0]


def box_stats(arr, box):
    x, y, w, h = clamp_box(box, arr.shape[1], arr.shape[0])
    sub = arr[y:y + h, x:x + w].reshape(-1, 4).astype(float)
    a_vals = np.unique(sub[:, 3]).astype(int)
    return {"box": [x, y, w, h],
            "min": [int(v) for v in sub.min(axis=0)], "max": [int(v) for v in sub.max(axis=0)],
            "mean": [round(float(v), 2) for v in sub.mean(axis=0)],
            "std": [round(float(v), 2) for v in sub.std(axis=0)],
            "alpha_values": [int(v) for v in a_vals[:16]], "alpha_unique": int(len(a_vals))}


def consensus_of(arrs):
    """Per-pixel most common RGBA across variants. Returns (consensus, support)."""
    stack = np.stack(arrs)  # (N,H,W,4)
    n, h, w, _ = stack.shape
    counts = np.ones((n, h, w), dtype=np.int16)
    for i in range(n):
        for j in range(i + 1, n):
            e = np.all(stack[i] == stack[j], axis=-1)
            counts[i] += e
            counts[j] += e
    best = counts.argmax(axis=0)
    support = counts.max(axis=0)
    cons = np.take_along_axis(stack, best[None, :, :, None], axis=0)[0]
    return cons, support


def detect_cells(arr, max_cells=8, min_width=8, tol=48):
    """Find strips of N equal-width cells (button states) from edge-column alpha."""
    a = arr[..., 3]
    _, w = a.shape
    if a.min() == a.max():
        return []
    found = []
    for n in range(2, max_cells + 1):
        if w % n:
            continue
        cw = w // n
        if cw < min_width:
            continue
        first, last = a[:, 0], a[:, cw - 1]
        if first.min() == first.max() and last.min() == last.max():
            continue  # edges carry no shape information
        # states may differ slightly (a pressed button is a touch smaller), so compare with a tolerance
        def close(x, y):
            return int(np.abs(x.astype(int) - y.astype(int)).max()) <= tol
        if all(close(a[:, k * cw], first) and close(a[:, k * cw + cw - 1], last) for k in range(n)):
            found.append({"count": n, "width": cw})
    return sorted(found, key=lambda c: -c["count"])  # finest consistent grid first


# --------------------------------------------------------------------------- inventory
def pack_rgba(a):
    a = a.astype(np.uint32)
    return (a[..., 0] << 24) | (a[..., 1] << 16) | (a[..., 2] << 8) | a[..., 3]


def unpack_rgba(v):
    v = int(v)
    return [(v >> 24) & 255, (v >> 16) & 255, (v >> 8) & 255, v & 255]


def background_model(arr, sub, ref_box):
    """(mean, std) per RGBA channel from a clean patch, else the most common colour of the box."""
    if ref_box:
        x, y, w, h = clamp_box(ref_box, arr.shape[1], arr.shape[0])
        patch = arr[y:y + h, x:x + w].reshape(-1, 4).astype(float)
        return patch.mean(axis=0), patch.std(axis=0)
    vals, counts = np.unique(pack_rgba(sub), return_counts=True)
    return np.array(unpack_rgba(vals[counts.argmax()]), dtype=float), np.zeros(4)


def ink_mask(sub, mean, std, tol):
    """Pixels that deviate from the background model in any channel, alpha included."""
    return np.any(np.abs(sub.astype(float) - mean) > np.maximum(tol * std, 2.0), axis=-1)


def text_lines(ink, box, merge_gap):
    merged = []
    for r in runs(ink.any(axis=1), True):
        if merged and r[0] - merged[-1][1] - 1 <= merge_gap:
            merged[-1][1] = r[1]
        else:
            merged.append(list(r))
    out = []
    for y0, y1 in merged:
        cols = np.where(ink[y0:y1 + 1].any(axis=0))[0]
        out.append({"y": [box[1] + y0, box[1] + y1], "x": [box[0] + int(cols.min()), box[0] + int(cols.max())],
                    "h": y1 - y0 + 1, "w": int(cols.max() - cols.min() + 1)})
    return out


def ascii_map(sub, ink):
    luma = 0.299 * sub[..., 0] + 0.587 * sub[..., 1] + 0.114 * sub[..., 2]
    cls = np.where(luma >= 170, "#", np.where(luma <= 85, "@", "+"))
    return ["".join(cls[y, x] if ink[y, x] else " " for x in range(sub.shape[1])) for y in range(sub.shape[0])]


def cmd_inventory(args):
    excludes = set(DEFAULT_EXCLUDES) | set(args.exclude or [])
    root = Path(args.dir)
    images, hashes = [], {}
    for p in list_images(root, args.recursive, excludes):
        rel = p.relative_to(root).as_posix()
        try:
            im = open_image(p)
        except CliError as exc:
            images.append({"path": rel, "error": str(exc)})
            continue
        arr = to_rgba(im)
        mode_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        info = {"path": rel, "size": [im.width, im.height], "mode": im.mode, "format": im.format,
                "bytes": p.stat().st_size,
                "animated": bool(getattr(im, "is_animated", False)),
                "has_alpha": bool(mode_alpha),
                "pixels_id": hashlib.sha1(arr.tobytes() + f"{im.size}".encode()).hexdigest()[:12]}
        hdr = png_header(p)
        if hdr:
            info["bit_depth"] = hdr[0]
        if mode_alpha:
            info["alpha"] = alpha_summary(arr)
            cells = detect_cells(arr)
            if cells:
                info["cell_candidates"] = cells
        images.append(info)
        hashes.setdefault(info["pixels_id"], []).append(rel)
    groups = {}
    for i in images:
        if "size" in i:
            groups.setdefault((tuple(i["size"]), i["mode"]), []).append(i["path"])
    emit({"ok": True, "dir": str(root), "recursive": bool(args.recursive), "count": len(images),
          "images": images,
          "groups": [{"size": list(k[0]), "mode": k[1], "files": v} for k, v in groups.items()],
          "duplicates": [v for v in hashes.values() if len(v) > 1]}, args.pretty)
    return 0


# --------------------------------------------------------------------------- analyze
def cmd_analyze(args):
    files = [Path(f) for f in args.files]
    imgs = [open_image(f) for f in files]
    arrs = [to_rgba(im) for im in imgs]
    out = {"ok": True, "files": [{"file": f.name, "size": [im.width, im.height], "mode": im.mode}
                                 for f, im in zip(files, imgs)]}
    if len(arrs) >= 2:
        if len({a.shape for a in arrs}) != 1:
            raise CliError("variant analysis needs files with identical dimensions")
        cons, support = consensus_of(arrs)
        unresolved = support == 1
        out["variants"] = {
            "unresolved_pixels": int(unresolved.sum()),
            "unresolved_bbox": bbox_of(unresolved),
            "note": "ink_bbox = pixels that differ from the per-pixel majority; unresolved = no two "
                    "variants agree (text overlaps text). A text shared by most variants becomes the "
                    "'consensus' and shows up in NO ink_bbox - never use consensus mode on it."}
        for entry, a in zip(out["files"], arrs):
            diff = np.any(a != cons, axis=-1)
            entry["ink_pixels"] = int(diff.sum())
            entry["ink_bbox"] = bbox_of(diff)
        if args.out_consensus:
            Image.fromarray(cons).save(args.out_consensus)
            out["variants"]["consensus_saved"] = args.out_consensus
    if (args.lines or args.palette or args.ascii or args.ref_box) and not args.box:
        raise CliError("--lines, --palette, --ascii and --ref-box need --box")
    if args.box:
        for entry, a in zip(out["files"], arrs):
            entry["stats"] = box_stats(a, args.box)
            if args.lines or args.palette or args.ascii:
                x, y, w, h = entry["stats"]["box"]
                sub = a[y:y + h, x:x + w]
                mean, std = background_model(a, sub, args.ref_box)
                ink = ink_mask(sub, mean, std, args.tol)
                entry["ink"] = {"pixels": int(ink.sum()), "bbox": bbox_of(ink),
                                "background": {"mean": [round(float(v), 1) for v in mean],
                                               "std": [round(float(v), 1) for v in std],
                                               "source": "ref_box" if args.ref_box else "most_common_colour"}}
                if entry["ink"]["bbox"]:
                    bx = entry["ink"]["bbox"]
                    entry["ink"]["bbox"] = [bx[0] + x, bx[1] + y, bx[2], bx[3]]
                if args.lines:
                    entry["lines"] = text_lines(ink, [x, y, w, h], args.merge_gap)
                if args.palette:
                    vals, counts = np.unique(pack_rgba(sub[ink] if ink.any() else sub.reshape(-1, 4)),
                                             return_counts=True)
                    top = np.argsort(-counts)[:args.palette]
                    entry["palette"] = [{"rgba": unpack_rgba(vals[i]), "hex": "#%02x%02x%02x%02x" % tuple(unpack_rgba(vals[i])),
                                         "count": int(counts[i])} for i in top]
                if args.ascii:
                    if w > 160 or h > 60:
                        raise CliError("--ascii needs a box of at most 160x60 pixels")
                    entry["ascii"] = ascii_map(sub, ink)
                    entry["ascii_legend"] = "' ' background, '#' light ink, '@' dark ink, '+' mid"
    if args.cells:
        cw, n = args.cells
        for entry, a in zip(out["files"], arrs):
            if a.shape[1] != cw * n:
                raise CliError(f"{entry['file']}: width {a.shape[1]} != {cw}x{n}")
            cells_out = []
            for k in range(n):
                cell = a[:, k * cw:(k + 1) * cw]
                cols = [cell[:, x].tobytes() for x in range(cw)]
                ref = args.ref_col
                if ref is None:
                    best, cnt = Counter(cols).most_common(1)[0]
                    ref = cols.index(best) if cnt >= 2 else None
                if ref is None:
                    cells_out.append({"cell": k, "ref_col": None, "note": "no repeated column found"})
                    continue
                differs = [c != cols[ref] for c in cols]
                cells_out.append({"cell": k, "ref_col": ref, "differs": runs(differs, True),
                                  "clean": runs(differs, False)})
            entry["cells"] = cells_out
        out["cells_note"] = ("columns are cell-relative. 'clean' runs equal ref_col (safe for "
                             "column-repeat). 'differs' runs touching a cell edge are usually the "
                             "rounded caps, not text.")
    emit(out, args.pretty)
    return 0


# --------------------------------------------------------------------------- crop
def cmd_crop(args):
    path = Path(args.image)
    im = open_image(path).convert("RGBA")
    box = clamp_box(args.box, im.width, im.height) if args.box else [0, 0, im.width, im.height]
    x, y, w, h = box
    scale = max(1, args.scale)
    while scale > 1 and max(w, h) * scale > 3000:
        scale -= 1
    view = flatten(im.crop((x, y, x + w, y + h)), args.bg, scale)
    if args.cells:
        cw, n = args.cells
        d = ImageDraw.Draw(view)
        for k in range(1, n):
            cx = (k * cw - x) * scale
            if 0 < cx < view.width:
                d.line([(cx, 0), (cx, view.height)], fill=(255, 0, 0, 255), width=1)
    out = Path(args.out) if args.out else workdir() / f"crop_{path.stem}_{x}_{y}_{w}x{h}_s{scale}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    view.convert("RGB").save(out)
    emit({"ok": True, "out": str(out), "box": box, "scale": scale, "size": list(view.size)}, args.pretty)
    return 0


# --------------------------------------------------------------------------- fonts
def font_dirs():
    dirs = []
    if sys.platform.startswith("win"):
        dirs += [os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    elif sys.platform == "darwin":
        dirs += ["/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    else:
        dirs += ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
                 os.path.expanduser("~/.local/share/fonts")]
    return [d for d in dirs if d and os.path.isdir(d)]


def cmd_fonts(args):
    exts = {".ttf", ".otf", ".ttc", ".otc"}
    needle = (args.filter or "").lower()
    rows = []
    for d in (args.dir or font_dirs()):
        for dp, _, fns in os.walk(d):
            for fn in sorted(fns):
                if Path(fn).suffix.lower() not in exts:
                    continue
                p = os.path.join(dp, fn)
                try:
                    fam, style = ImageFont.truetype(p, 12).getname()
                except Exception:
                    continue
                if needle and needle not in f"{fn} {fam} {style}".lower():
                    continue
                rows.append({"path": p, "family": fam, "style": style})
    rows.sort(key=lambda r: (r["family"].lower(), r["style"].lower()))
    limit = args.limit or (12 if args.sample else 60)
    shown = rows[:limit]
    out = {"ok": True, "total_matches": len(rows), "shown": len(shown), "fonts": shown}
    if args.sample and shown:
        size = args.size
        sheet = Image.new("RGB", (900, 28 * len(shown) + 8), (255, 255, 255))
        d = ImageDraw.Draw(sheet)
        for i, r in enumerate(shown):
            y = 4 + 28 * i
            d.text((6, y + 8), f"{i}", fill=(160, 0, 0))
            try:
                d.text((28, y), args.sample, font=ImageFont.truetype(r["path"], size), fill=(0, 0, 0))
            except Exception:
                d.text((28, y), "(render failed)", fill=(160, 0, 0))
            d.text((520, y + 8), f"{r['family']} {r['style']}"[:60], fill=(90, 90, 90))
        dest = Path(args.out) if args.out else workdir() / "font_sample.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(dest)
        out["sample_sheet"] = str(dest)
    emit(out, args.pretty)
    return 0


# --------------------------------------------------------------------------- spec
def expand_presets(spec):
    """images[i].preset pulls cells/style/erase/regions from spec.presets[name]; image keys win.
    A preset region text may contain {text}, filled from the image's own "text"."""
    presets = spec.get("presets") or {}
    out = []
    for i, im in enumerate(spec["images"]):
        name = im.get("preset")
        if name is None:
            out.append(im)
            continue
        if name not in presets:
            raise CliError(f"images[{i}].preset {name!r} is not defined in spec.presets")
        base = copy.deepcopy(presets[name])
        merged = dict(base)
        for k, v in im.items():
            if k not in ("preset", "text"):
                merged[k] = v
        if isinstance(base.get("style"), dict) and isinstance(im.get("style"), dict):
            merged["style"] = {**base["style"], **im["style"]}
        if "regions" not in im:
            regs = copy.deepcopy(base.get("regions") or [])
            text = im.get("text")
            has_ph = any("{text}" in str(r.get("text", "")) for r in regs)
            if has_ph and text is None:
                raise CliError(f"images[{i}] uses preset {name!r}: set its 'text'")
            if text is not None and not has_ph:
                raise CliError(f"images[{i}] has 'text' but preset {name!r} has no {{text}} placeholder")
            for r in regs:
                if text is not None:
                    r["text"] = str(r.get("text", "")).replace("{text}", text)
            merged["regions"] = regs
        out.append(merged)
    spec["images"] = out


def load_spec(path):
    try:
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
    except FileNotFoundError:
        raise CliError(f"spec not found: {path}")
    except json.JSONDecodeError as exc:
        raise CliError(f"spec is not valid JSON: {exc}")
    if spec.get("version", 1) != 1:
        raise CliError("unsupported spec version (expected 1)")
    if not isinstance(spec.get("images"), list) or not spec["images"]:
        raise CliError("spec.images must be a non-empty list")
    expand_presets(spec)
    return spec


def check_box(box, where, w=None, h=None):
    if (not isinstance(box, (list, tuple)) or len(box) != 4
            or not all(isinstance(v, int) and not isinstance(v, bool) for v in box)):
        raise CliError(f"{where}: box must be [x,y,w,h] integers")
    if box[2] <= 0 or box[3] <= 0:
        raise CliError(f"{where}: w and h must be > 0")
    if w is not None and (box[0] < 0 or box[1] < 0 or box[0] + box[2] > w or box[1] + box[3] > h):
        raise CliError(f"{where}: box {box} falls outside the {w}x{h} image/cell area")


def cell_targets(img_spec, sel, size, where):
    """Cell indexes a box applies to, the cell width and the usable area width."""
    w, _ = size
    cells = img_spec.get("cells")
    if cells:
        cw, n = int(cells["width"]), int(cells["count"])
        if cw * n != w:
            raise CliError(f"{where}: cells {cw}x{n} do not add up to image width {w}")
        if sel == "all":
            idx = list(range(n))
        elif isinstance(sel, int) and not isinstance(sel, bool):
            idx = [sel]
        elif isinstance(sel, list):
            idx = [int(i) for i in sel]
        else:
            raise CliError(f"{where}.cell must be 'all', an index or a list of indexes")
        if any(i < 0 or i >= n for i in idx):
            raise CliError(f"{where}.cell out of range 0..{n - 1}")
        return idx, cw, cw
    if sel not in ("all", 0):
        raise CliError(f"{where}: 'cell' given but the image has no 'cells'")
    return [0], 0, w


def erase_geometry(img_spec, erase, default_box, size, where, sel="all"):
    """[(cell, erase_box, ox)] for one erase entry; cell-relative boxes are translated per cell."""
    _, h = size
    if not isinstance(erase, dict) or erase.get("mode") not in ERASE_MODES:
        raise CliError(f"{where}.mode is required, one of {list(ERASE_MODES)}")
    idx, cw, area_w = cell_targets(img_spec, sel, size, where)
    ebox = erase.get("box", default_box)
    if ebox is None:
        raise CliError(f"{where}.box is required for an image-level erase")
    check_box(ebox, f"{where}.box", area_w, h)
    out = []
    for c in idx:
        ox = c * cw if img_spec.get("cells") else 0
        eb = [ebox[0] + ox, 0, ebox[2], h] if erase["mode"] == "column-repeat" \
            else [ebox[0] + ox, ebox[1], ebox[2], ebox[3]]
        out.append((c, eb, ox))
    return out


def region_geometry(img_spec, region, size, where):
    """[(cell, draw_box, erase_box or None, ox)] for one region."""
    sel = region.get("cell", "all")
    idx, cw, area_w = cell_targets(img_spec, sel, size, where)
    box = region.get("box")
    check_box(box, f"{where}.box", area_w, size[1])
    erases = {}
    if region.get("erase") is not None:
        erases = {c: eb for c, eb, _ in erase_geometry(img_spec, region["erase"], box, size,
                                                        f"{where}.erase", sel)}
    out = []
    for c in idx:
        ox = c * cw if img_spec.get("cells") else 0
        out.append((c, [box[0] + ox, box[1], box[2], box[3]], erases.get(c), ox))
    return out


def image_erases(img_spec, size, where):
    """[(entry, cell, erase_box, ox)] for the image-level 'erase' list (applied before any region)."""
    entries = img_spec.get("erase") or []
    if not isinstance(entries, list):
        raise CliError(f"{where}.erase must be a list of erase entries")
    out = []
    for ei, entry in enumerate(entries):
        for c, eb, ox in erase_geometry(img_spec, entry, None, size, f"{where}.erase[{ei}]",
                                        (entry or {}).get("cell", "all")):
            out.append((entry, c, eb, ox))
    return out


def style_for(spec, img_spec, region, cell):
    st = dict(spec.get("defaults") or {})
    st.update(img_spec.get("style") or {})
    st.update({k: region[k] for k in STYLE_KEYS if k in region})
    st.update((region.get("state_overrides") or {}).get(str(cell), {}))
    return st


@functools.lru_cache(maxsize=64)
def get_font(path, size, index):
    try:
        return ImageFont.truetype(path, size, index=index)
    except OSError:
        raise CliError(f"font not found or unreadable: {path!r} (use `imgtr fonts --filter NAME`)")


def shadows_of(st):
    sh = st.get("shadow")
    if not sh:
        return []
    return list(sh) if isinstance(sh, list) else [sh]


def layout_text(text, st, bw, bh, where):
    if not st.get("font"):
        raise CliError(f"{where}: no 'font' (set it in defaults or in the region)")
    if not st.get("size"):
        raise CliError(f"{where}: no 'size'")
    outline = st.get("outline") or None
    sw = int(outline.get("width", 1)) if outline else 0
    soffs = [tuple(x.get("offset", [1, 1])) for x in shadows_of(st)]
    spacing = int(st.get("spacing", 4))
    align, valign = st.get("align", "center"), st.get("valign", "middle")
    if align not in ("left", "center", "right") or valign not in ("top", "middle", "bottom", "metrics", "origin", "baseline"):
        raise CliError(f"{where}: align must be left|center|right and "
                       f"valign top|middle|bottom|metrics|origin|baseline")
    if valign == "baseline" and "baseline" not in st:
        raise CliError(f"{where}: valign 'baseline' needs a 'baseline' value (row, counted from the box top)")
    requested = int(st["size"])
    min_size = int(st.get("min_size", max(6, requested - 4)))
    sizes = range(requested, min_size - 1, -1) if st.get("fit", "shrink") == "shrink" else [requested]
    dx, dy = int(st.get("dx", 0)), int(st.get("dy", 0))
    antialias = bool(st.get("antialias", True))
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    probe.fontmode = "L" if antialias else "1"
    last = None
    for s in sizes:
        font = get_font(st["font"], s, int(st.get("font_index", 0)))
        l, t, r, b = probe.multiline_textbbox((0, 0), text, font=font, spacing=spacing,
                                              align=align, stroke_width=sw)
        tw, th = r - l, b - t
        ix = {"left": 0, "center": (bw - tw) // 2, "right": bw - tw}[align] + dx
        asc, desc = font.getmetrics()
        single = "\n" not in text
        if valign == "baseline" and single:
            # the baseline sits on a fixed row whatever the font or size: the exact way to align texts
            iy = int(st["baseline"]) - asc + t + dy
        elif valign in ("metrics", "origin") and single:
            # line-box placement: the baseline lands on the same row for any text of this font size
            oy = (bh - (asc + desc)) // 2 if valign == "metrics" else 0
            iy = oy + t + dy
        else:
            iy = {"top": 0, "middle": (bh - th) // 2, "bottom": bh - th}.get(valign, (bh - th) // 2) + dy
        ink = [ix, iy, ix + tw, iy + th]
        sinks = [[ink[0] + o[0], ink[1] + o[1], ink[2] + o[0], ink[3] + o[1]] for o in soffs]
        fits = all(b_[0] >= 0 and b_[1] >= 0 and b_[2] <= bw and b_[3] <= bh for b_ in [ink] + sinks)
        last = {"font": font, "size": s, "origin": (l, t), "ink": ink, "sw": sw, "spacing": spacing,
                "align": align, "antialias": antialias, "fits": fits, "need": [tw, th],
                "requested": requested, "baseline_rel": (iy - t + asc) if single else None}
        if fits:
            return last
    return last


def color_rgba(value, where):
    try:
        return ImageColor.getcolor(value, "RGBA")
    except (ValueError, TypeError):
        raise CliError(f"{where}: invalid color {value!r}")


def render_layer(bw, bh, text, st, lay, where):
    l, t = lay["origin"]
    xy = (lay["ink"][0] - l, lay["ink"][1] - t)
    opacity = float(st.get("opacity", 1.0))

    def mask(stroke):
        m = Image.new("L", (bw, bh), 0)
        d = ImageDraw.Draw(m)
        d.fontmode = "L" if lay["antialias"] else "1"
        d.multiline_text(xy, text, font=lay["font"], fill=255, spacing=lay["spacing"],
                         align=lay["align"], stroke_width=stroke, stroke_fill=255)
        return m

    def ink(m, color, shift=(0, 0)):
        c = color_rgba(color, where)
        if shift != (0, 0):
            moved = Image.new("L", (bw, bh), 0)
            moved.paste(m, shift)
            m = moved
        k = (c[3] / 255.0) * opacity
        m = m.point([int(round(v * k)) for v in range(256)])
        solid = Image.new("RGBA", (bw, bh), c[:3] + (0,))
        solid.putalpha(m)
        return solid

    fill_mask = mask(0)
    outline_mask = mask(lay["sw"]) if lay["sw"] else None
    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    for shadow in shadows_of(st):
        layer = Image.alpha_composite(layer, ink(outline_mask or fill_mask, shadow.get("color", "#000000"),
                                                 tuple(shadow.get("offset", [1, 1]))))
    if outline_mask is not None:
        layer = Image.alpha_composite(layer, ink(outline_mask, st["outline"].get("color", "#000000")))
    return Image.alpha_composite(layer, ink(fill_mask, st.get("fill", "#ffffff")))


def erase_box_pixels(arr, orig, erase, ebox, ox, seed_parts, where, consensus_cache, src_dir, warns,
                     cell=0, cell_w=None):
    mode = erase["mode"]
    x, y, w, h = ebox
    if mode == "none":
        return
    if mode == "fill":
        arr[y:y + h, x:x + w] = color_rgba(erase.get("color"), f"{where}.erase.color")
        return
    donor = orig  # pixels to copy/sample from: the image itself, or erase.source (a sibling image)
    if erase.get("source"):
        if mode == "consensus":
            raise CliError(f"{where}.erase.source is not used by mode 'consensus' (use 'variants')")
        key = ("source", str(erase["source"]))
        if key not in consensus_cache:
            consensus_cache[key] = to_rgba(open_image(Path(src_dir) / erase["source"]))
        donor = consensus_cache[key]
        if donor.shape != orig.shape:
            raise CliError(f"{where}.erase.source must have the same dimensions as the image")
    dox = 0  # x offset of the donor inside the destination (non-zero for per-cell donors)
    if erase.get("sources"):
        if erase.get("source") or mode == "consensus":
            raise CliError(f"{where}.erase: 'sources' cannot be combined with 'source' or mode 'consensus'")
        srcs = erase["sources"]
        if not isinstance(srcs, list) or cell >= len(srcs):
            raise CliError(f"{where}.erase.sources needs one donor image per cell (missing cell {cell})")
        key = ("source", str(srcs[cell]))
        if key not in consensus_cache:
            consensus_cache[key] = to_rgba(open_image(Path(src_dir) / srcs[cell]))
        donor = consensus_cache[key]
        if donor.shape[0] != orig.shape[0] or donor.shape[1] != (cell_w or orig.shape[1]):
            raise CliError(f"{where}.erase.sources[{cell}] must be exactly one cell "
                           f"({cell_w or orig.shape[1]}x{orig.shape[0]})")
        dox = ox
    if mode == "copy":
        off = erase.get("offset")
        if (not isinstance(off, (list, tuple)) or len(off) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in off)):
            raise CliError(f"{where}.erase.offset must be [dx, dy] integers for mode 'copy'")
        sx, sy = x + off[0], y + off[1]
        if sx - dox < 0 or sy < 0 or sx - dox + w > donor.shape[1] or sy + h > donor.shape[0]:
            raise CliError(f"{where}.erase: the copy source [{sx},{sy},{w},{h}] falls outside the image")
        if donor is orig and sx < x + w and x < sx + w and sy < y + h and y < sy + h:
            raise CliError(f"{where}.erase: the copy source overlaps the area being erased")
        arr[y:y + h, x:x + w] = donor[sy:sy + h, sx - dox:sx - dox + w]
        return
    if mode == "column-repeat":
        if "ref_col" not in erase:
            raise CliError(f"{where}.erase.ref_col is required for column-repeat")
        ref = int(erase["ref_col"]) + ox
        if not 0 <= ref < orig.shape[1] or not 0 <= ref - dox < donor.shape[1]:
            raise CliError(f"{where}.erase.ref_col out of range")
        arr[:, x:x + w] = donor[:, ref - dox:ref - dox + 1]
        return

    def sample_patch():
        rb = erase.get("ref_box")
        if rb is None:
            raise CliError(f"{where}.erase.ref_box is required for mode '{mode}'")
        check_box(rb, f"{where}.erase.ref_box")
        rx, ry, rw, rh = [rb[0] + ox, rb[1], rb[2], rb[3]]
        if rx - dox < 0 or ry < 0 or rx - dox + rw > donor.shape[1] or ry + rh > donor.shape[0]:
            raise CliError(f"{where}.erase.ref_box falls outside the image")
        if donor is orig and rx < x + w and x < rx + rw and ry < y + h and y < ry + rh:
            raise CliError(f"{where}.erase.ref_box overlaps the area being erased")
        patch = donor[ry:ry + rh, rx - dox:rx - dox + rw].reshape(-1, 4)
        spread = float(patch[:, :3].astype(float).std(axis=0).max())
        limit = float(erase.get("max_std", 12))
        if spread > limit:
            warns.append(f"ref_box is not homogeneous (std={spread:.1f} > {limit}); the fill may show")
        return patch

    if mode == "sample":
        patch = sample_patch()
        rng = np.random.default_rng([zlib.crc32(p.encode()) for p in seed_parts])
        arr[y:y + h, x:x + w] = patch[rng.integers(0, len(patch), size=h * w)].reshape(h, w, 4)
        return

    # consensus
    names = tuple(erase.get("variants") or ())
    if len(names) < 2:
        raise CliError(f"{where}.erase.variants needs at least 2 files")
    if names not in consensus_cache:
        arrs = [to_rgba(open_image(Path(src_dir) / n)) for n in names]
        if any(a.shape != orig.shape for a in arrs):
            raise CliError(f"{where}.erase.variants must share the image dimensions")
        consensus_cache[names] = consensus_of(arrs)
    cons, support = consensus_cache[names]
    sub = cons[y:y + h, x:x + w].copy()
    ok = support[y:y + h, x:x + w] >= int(erase.get("min_support", 2))
    if erase.get("ref_box") is not None:
        patch = sample_patch()
        mean, std = patch.astype(float).mean(axis=0), patch.astype(float).std(axis=0)
        tol = np.maximum(4 * std, 2)
        ok &= np.all(np.abs(sub.astype(float) - mean) <= tol, axis=-1)
    bad = ~ok
    if bad.any():
        if erase.get("ref_box") is None:
            raise CliError(f"{where}: {int(bad.sum())} pixels have no consensus; add erase.ref_box "
                           f"to fall back to sampling")
        rng = np.random.default_rng([zlib.crc32(p.encode()) for p in seed_parts])
        fb = patch[rng.integers(0, len(patch), size=h * w)].reshape(h, w, 4)
        sub[bad] = fb[bad]
        warns.append(f"consensus: {int(bad.sum())} of {h * w} pixels used the sampled fallback")
    arr[y:y + h, x:x + w] = sub


def save_like(src_im, rgba, dest, src_path):
    mode, fmt = src_im.mode, src_im.format
    warns = []
    if mode == "RGBA":
        im = rgba
    elif mode == "RGB":
        im = rgba.convert("RGB")
    elif mode == "L":
        im = rgba.convert("L")
    else:
        im = rgba
        warns.append(f"mode_changed: source is {mode}, output saved as RGBA")
    kw = {}
    if src_im.info.get("icc_profile"):
        kw["icc_profile"] = src_im.info["icc_profile"]
    if src_im.info.get("dpi"):
        kw["dpi"] = src_im.info["dpi"]
    if fmt == "JPEG":
        kw.update(quality=95, subsampling="keep")
        warns.append("lossy_format: JPEG is re-encoded, pixels outside the edit areas will differ slightly")
    elif fmt == "WEBP":
        kw["lossless"] = True
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, format=fmt, **kw)
    return warns


def process_image(spec, img_spec, idx, src_dir, out_dir, allow_overflow, dry_run, consensus_cache):
    name = img_spec.get("source")
    where = f"images[{idx}]"
    if not name:
        raise CliError(f"{where}.source is required")
    src_path = Path(src_dir) / name
    dest = Path(out_dir) / name
    if dest.resolve() == src_path.resolve():
        raise CliError(f"{where}: output would overwrite the source file")
    im = open_image(src_path)
    if getattr(im, "is_animated", False):
        raise CliError(f"{name}: animated images are not supported in v0")
    if im.format not in WRITE_FORMATS:
        raise CliError(f"{name}: format {im.format} is not supported for writing")
    hdr = png_header(src_path)
    if hdr and hdr[0] == 16:
        raise CliError(f"{name}: 16-bit PNG would be silently reduced to 8-bit; not supported in v0")
    if "transparency" in im.info and im.mode != "RGBA":
        raise CliError(f"{name}: tRNS transparency on {im.mode} images is not supported in v0")
    orig = to_rgba(im)
    arr = orig.copy()
    h, w = orig.shape[:2]
    report, warnings, overflow = [], [], False
    seed = int(spec.get("seed", 1))
    regions = img_spec.get("regions")
    if not isinstance(regions, list) or not regions:
        raise CliError(f"{where}.regions must be a non-empty list")
    if not img_spec.get("erase") and not all(r.get("erase") is not None for r in regions):
        raise CliError(f"{where}: declare 'erase' on the image or on every region "
                       f"(use mode 'none' to draw without erasing)")
    erase_report = []
    for ei, (entry, cell, ebox, ox) in enumerate(image_erases(img_spec, (w, h), where)):
        ew = []
        erase_box_pixels(arr, orig, entry, ebox, ox, [str(seed), name, "image", str(ei), str(cell)],
                         f"{where}.erase[{ei}]", consensus_cache, src_dir, ew, cell=cell,
                         cell_w=(img_spec.get("cells") or {}).get("width"))
        erase_report.append({"mode": entry["mode"], "cell": cell if img_spec.get("cells") else None,
                             "box": ebox, **({"warnings": ew} if ew else {})})
    for ri, region in enumerate(regions):
        rwhere = f"{where}.regions[{ri}]"
        text = region.get("text")
        if not isinstance(text, str) or not text:
            raise CliError(f"{rwhere}.text is required")
        align_key = region.get("align_key")
        if align_key is not None and (not isinstance(align_key, str) or not align_key.strip()):
            raise CliError(f"{rwhere}.align_key must be a non-empty string or null")
        for cell, dbox, ebox, ox in region_geometry(img_spec, region, (w, h), rwhere):
            st = style_for(spec, img_spec, region, cell)
            warns = []
            if ebox is not None:
                erase_box_pixels(arr, orig, region["erase"], ebox, ox,
                                 [str(seed), name, str(ri), str(cell)], rwhere, consensus_cache,
                                 src_dir, warns, cell=cell, cell_w=(img_spec.get("cells") or {}).get("width"))
            lay = layout_text(text, st, dbox[2], dbox[3], rwhere)
            entry = {"region": ri, "cell": cell if img_spec.get("cells") else None, "text": text,
                     "requested_size": lay["requested"], "size_used": lay["size"]}
            if region.get("erase") is not None:
                entry["erase_mode"] = region["erase"]["mode"]
            if lay["baseline_rel"] is not None:
                ix0, ix1 = dbox[0] + lay["ink"][0], dbox[0] + lay["ink"][2]
                entry["align"] = lay["align"]
                entry["baseline"] = dbox[1] + lay["baseline_rel"]
                entry["anchor"] = {"left": ix0, "right": ix1}.get(lay["align"], (ix0 + ix1) / 2)
            if "align_key" in region:
                entry["align_key"] = region["align_key"]
            if lay["size"] != lay["requested"]:
                warns.append(f"shrunk from {lay['requested']} to {lay['size']}")
            if not lay["fits"]:
                overflow = True
                entry["overflow"] = {"need_wh": lay["need"], "box_wh": [dbox[2], dbox[3]],
                                     "size_tried": lay["size"]}
            else:
                layer = render_layer(dbox[2], dbox[3], text, st, lay, rwhere)
                x, y, bw, bh = dbox
                tile = Image.fromarray(arr[y:y + bh, x:x + bw].copy())
                arr[y:y + bh, x:x + bw] = np.asarray(Image.alpha_composite(tile, layer))
                entry["ink_box"] = [x + lay["ink"][0], y + lay["ink"][1],
                                    lay["ink"][2] - lay["ink"][0], lay["ink"][3] - lay["ink"][1]]
            if warns:
                entry["warnings"] = warns
            report.append(entry)
    # internal guard: nothing may change outside the declared boxes
    allowed = allowed_mask(img_spec, (w, h))
    stray = int((np.any(arr != orig, axis=-1) & ~allowed).sum())
    if stray:
        raise CliError(f"{name}: internal error, {stray} pixels changed outside the declared boxes")
    result = {"source": name, "size": [w, h], "regions": report}
    if erase_report:
        result["erase"] = erase_report
    if overflow and not allow_overflow:
        result["status"] = "overflow"
        result["ok"] = False
        return result
    if dry_run:
        result["status"] = "dry-run"
    else:
        warnings += save_like(im, Image.fromarray(arr), dest, src_path)
        result["status"] = "written"
        result["output"] = str(dest)
    result["ok"] = not overflow
    changed = np.any(arr != orig, axis=-1)
    result["changed_pixels"] = int(changed.sum())
    result["changed_bbox"] = bbox_of(changed)
    if not changed.any():
        warnings.append("nothing_changed: the output is identical to the source")
    if warnings:
        result["warnings"] = warnings
    return result


def allowed_mask(img_spec, size):
    w, h = size
    mask = np.zeros((h, w), dtype=bool)
    for _, _, eb, _ in image_erases(img_spec, size, "image"):
        mask[eb[1]:eb[1] + eb[3], eb[0]:eb[0] + eb[2]] = True
    for ri, region in enumerate(img_spec.get("regions") or []):
        for _, dbox, ebox, _ in region_geometry(img_spec, region, size, f"regions[{ri}]"):
            for box in (dbox, ebox):
                if box is not None:
                    x, y, bw, bh = box
                    mask[y:y + bh, x:x + bw] = True
    return mask


def alignment_findings(results):
    """Rule: same image height -> same baseline row; same image width -> same horizontal anchor.
    Compares single-line regions that sit at the same position (same region index and cell, or the
    same `align_key`); a region opts out with "align_key": null."""
    imgs = [r for r in results if r.get("size") and r.get("regions") and r.get("status") != "error"]
    findings, groups = [], {"height": {}, "width": {}}
    for rule, dim, idx in (("same-height", "height", 1), ("same-width", "width", 0)):
        by_size = {}
        for r in imgs:
            by_size.setdefault(r["size"][idx], []).append(r)
        for size, members in sorted(by_size.items()):
            if len(members) < 2:
                continue
            groups[dim][str(size)] = [m["source"] for m in members]
            keyed = {}
            for r in members:
                for e in r["regions"]:
                    if "baseline" not in e or ("align_key" in e and e["align_key"] is None):
                        continue
                    cell = e.get("cell")
                    align_key = e.get("align_key")
                    if align_key:
                        key = ("align_key", align_key, cell)
                        display_key = f"{align_key}:{cell}" if cell is not None else str(align_key)
                    else:
                        key = ("region", e["region"], cell)
                        display_key = f"{e['region']}:{cell}" if cell is not None else str(e["region"])
                    keyed.setdefault(key, (display_key, []))[1].append((r["source"], e))
            for key, (display_key, items) in keyed.items():
                if len(items) < 2:
                    continue
                if dim == "height":
                    vals = {n: e["baseline"] for n, e in items}
                    if max(vals.values()) != min(vals.values()):
                        findings.append({"rule": rule, dim: size, "key": display_key, "issue": "baseline differs",
                                         "baseline": vals})
                    sizes = {n: e["size_used"] for n, e in items}
                    if len(set(sizes.values())) > 1:
                        findings.append({"rule": rule, dim: size, "key": display_key, "issue": "font size differs",
                                         "size": sizes})
                else:
                    modes = {n: e["align"] for n, e in items}
                    vals = {n: e["anchor"] for n, e in items}
                    tol = 1 if set(modes.values()) == {"center"} else 0
                    if len(set(modes.values())) > 1:
                        findings.append({"rule": rule, dim: size, "key": display_key, "issue": "align modes differ",
                                         "align": modes})
                    elif max(vals.values()) - min(vals.values()) > tol:
                        findings.append({"rule": rule, dim: size, "key": display_key, "issue": "anchor differs",
                                         "align": next(iter(modes.values())), "anchor": vals})
    return findings, {k: v for k, v in groups.items() if v}


def cmd_apply(args):
    spec = load_spec(args.spec)
    src_dir, out_dir = Path(args.src), Path(args.out)
    if not src_dir.is_dir():
        raise CliError(f"--src is not a directory: {src_dir}")
    if src_dir.resolve() == out_dir.resolve():
        raise CliError("--out must differ from --src (originals are never overwritten)")
    only = set(args.only or [])
    cache, results, ok = {}, [], True
    for i, img_spec in enumerate(spec["images"]):
        name = str(img_spec.get("source", ""))
        if only and name not in only and Path(name).name not in only:
            continue
        try:
            res = process_image(spec, img_spec, i, src_dir, out_dir, args.allow_overflow, args.dry_run, cache)
        except CliError as exc:
            res = {"source": name, "ok": False, "status": "error", "error": str(exc)}
        ok &= bool(res.get("ok"))
        results.append(res)
    if only and not results:
        raise CliError("--only matched no image in the spec")
    findings, groups = alignment_findings(results)
    emit({"ok": ok, "out": str(out_dir), "images": results,
          "alignment": {"ok": not findings, "groups": groups, "findings": findings}}, args.pretty)
    if not ok or (args.strict_alignment and findings):
        return 2
    return 0


# --------------------------------------------------------------------------- verify
def make_compare(src_im, out_im, focus, scale, bg, dest):
    a, b = src_im.convert("RGBA"), out_im.convert("RGBA")
    x, y, w, h = focus
    scale = max(1, scale)
    while scale > 1 and max(w, h) * scale > 1600:
        scale -= 1
    rows = [flatten(im.crop((x, y, x + w, y + h)), bg, scale) for im in (a, b)]
    label_h, gap = 14, 6
    sheet = Image.new("RGB", (rows[0].width, 2 * (rows[0].height + label_h) + gap), (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    for i, (row, label) in enumerate(zip(rows, ("original", "translated"))):
        oy = i * (rows[0].height + label_h + gap)
        d.text((2, oy + 1), label, fill=(255, 255, 255))
        sheet.paste(row.convert("RGB"), (0, oy + label_h))
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)
    return scale


def verify_pair(src_path, out_path, img_spec, extra_allow, args):
    res = {"source": str(src_path.name), "output": str(out_path)}
    if not out_path.exists():
        res.update(ok=False, error="output file missing")
        return res
    a_im, b_im = open_image(src_path), open_image(out_path)
    checks = {"same_size": a_im.size == b_im.size, "same_mode": a_im.mode == b_im.mode,
              "same_format": a_im.format == b_im.format}
    res.update(checks)
    failed = [k for k, v in checks.items() if not v]
    if checks["same_size"]:
        a, b = to_rgba(a_im), to_rgba(b_im)
        w, h = a_im.size
        mask = np.zeros((h, w), dtype=bool)
        if img_spec is not None:
            mask |= allowed_mask(img_spec, (w, h))
        for bx in extra_allow:
            x, y, bw, bh = bx
            mask[max(0, y):y + bh, max(0, x):x + bw] = True
        diff = np.any(a != b, axis=-1)
        res["outside_diff_pixels"] = int((diff & ~mask).sum())
        res["outside_diff_bbox"] = bbox_of(diff & ~mask)
        res["alpha_changed_outside"] = int(((a[..., 3] != b[..., 3]) & ~mask).sum())
        res["inside_changed_pixels"] = int((diff & mask).sum())
        res["changed_bbox"] = bbox_of(diff)
        if res["outside_diff_pixels"]:
            failed.append("outside_diff")
        if not diff.any():
            res["warning"] = "no pixel changed"
        if args.compare:
            union = mask | diff
            fb = bbox_of(union) or [0, 0, w, h]
            pad = 6
            fx, fy = max(0, fb[0] - pad), max(0, fb[1] - pad)
            focus = [fx, fy, min(w, fb[0] + fb[2] + pad) - fx, min(h, fb[1] + fb[3] + pad) - fy]
            dest = Path(args.compare_dir) if args.compare_dir else workdir()
            dest = dest / f"compare_{Path(res['source']).stem}.png"
            used = make_compare(a_im, b_im, focus, args.scale, args.bg, dest)
            res["compare"] = {"path": str(dest), "focus": focus, "scale": used}
    res["ok"] = not failed
    if failed:
        res["failed_checks"] = failed
    return res


def cmd_verify(args):
    src, outp = Path(args.src), Path(args.out)
    extra = args.allow or []
    pairs = []
    if args.spec:
        spec = load_spec(args.spec)
        only = set(args.only or [])
        for img_spec in spec["images"]:
            n = str(img_spec.get("source", ""))
            if only and n not in only and Path(n).name not in only:
                continue
            pairs.append((src / n if src.is_dir() else src, outp / n if outp.is_dir() else outp, img_spec))
    elif src.is_file() and outp.is_file():
        pairs.append((src, outp, None))
    else:
        for p in list_images(outp, True, DEFAULT_EXCLUDES - {"translated"}):
            rel = p.relative_to(outp)
            if (src / rel).exists():
                pairs.append((src / rel, p, None))
    if not pairs:
        raise CliError("nothing to verify (no matching pairs)")
    results = [verify_pair(s, o, sp, extra, args) for s, o, sp in pairs]
    ok = all(r.get("ok") for r in results)
    emit({"ok": ok, "checked": len(results), "results": results}, args.pretty)
    return 0 if ok else 2


def cmd_measure(args):
    font = get_font(args.font, args.size, args.font_index)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    asc, desc = font.getmetrics()
    cap = probe.textbbox((0, 0), "H", font=font)
    xh = probe.textbbox((0, 0), "x", font=font)
    rows = []
    for text in args.text:
        l, t, r, b = probe.multiline_textbbox((0, 0), text, font=font, spacing=args.spacing,
                                              stroke_width=args.stroke)
        row = {"text": text, "width": r - l, "height": b - t}
        if args.max_width:
            row["fits"] = (r - l) <= args.max_width
        rows.append(row)
    emit({"ok": True, "font": args.font, "size": args.size, "stroke": args.stroke,
          "ascent": asc, "descent": desc,
          "cap_height": cap[3] - cap[1], "x_height": xh[3] - xh[1], "results": rows}, args.pretty)
    return 0


def cmd_clean(args):
    p = Path(WORKDIR)
    existed = p.is_dir()
    if existed:
        shutil.rmtree(p)
    emit({"ok": True, "removed": existed, "path": str(p)}, args.pretty)
    return 0


# --------------------------------------------------------------------------- main
def build_parser():
    p = argparse.ArgumentParser(prog="imgtr", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"imgtr {__version__}")
    p.add_argument("--pretty", action="store_true", help="indent the JSON output")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("inventory", help="list images with size, mode, alpha, cell-grid candidates")
    s.add_argument("dir", nargs="?", default=".")
    s.add_argument("--recursive", action="store_true", help="default is this folder only")
    s.add_argument("--exclude", action="append", help="directory name to skip (repeatable)")
    s.set_defaults(func=cmd_inventory)

    s = sub.add_parser("analyze", help="compare variants, measure a box, scan button cells",
                       description="With 2+ same-size files: per-pixel majority across variants and the "
                                   "bbox of what each file adds. --box: pixel stats. --cells: per-cell "
                                   "clean/ink column runs.")
    s.add_argument("files", nargs="+")
    s.add_argument("--box", type=box_arg, help="x,y,w,h: min/max/mean/std and alpha values")
    s.add_argument("--cells", type=cells_arg, help="WIDTH:COUNT, e.g. 51:3")
    s.add_argument("--ref-col", type=int, help="cell-relative reference column (default: most repeated)")
    s.add_argument("--out-consensus", help="save the consensus image here")
    s.add_argument("--ref-box", type=box_arg, help="x,y,w,h of a clean background patch (for --lines/--palette/--ascii)")
    s.add_argument("--tol", type=float, default=4.0, help="ink = deviates from the patch by > tol*std (min 2)")
    s.add_argument("--lines", action="store_true", help="with --box: split the ink into text lines (y/x ranges)")
    s.add_argument("--merge-gap", type=int, default=1, help="merge lines closer than N empty rows (default 1)")
    s.add_argument("--palette", type=int, metavar="N", help="with --box: the N most common ink colours")
    s.add_argument("--ascii", action="store_true", help="with --box (max 160x60): character map of the ink")
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("crop", help="enlarged crop over a checkerboard, for looking at small text")
    s.add_argument("image")
    s.add_argument("--box", type=box_arg)
    s.add_argument("--scale", type=int, default=4)
    s.add_argument("--bg", default="checker", help="checker|white|black|gray|#hex")
    s.add_argument("--cells", type=cells_arg, help="draw cell boundaries (WIDTH:COUNT)")
    s.add_argument("--out", help=f"default: {WORKDIR}/crop_*.png")
    s.set_defaults(func=cmd_crop)

    s = sub.add_parser("fonts", help="list installed fonts, optionally render a sample sheet")
    s.add_argument("--filter", help="substring of file name, family or style")
    s.add_argument("--dir", action="append", help="extra/alternate font directory (repeatable)")
    s.add_argument("--limit", type=int)
    s.add_argument("--sample", help="text to render for each listed font")
    s.add_argument("--size", type=int, default=18)
    s.add_argument("--out", help=f"default: {WORKDIR}/font_sample.png")
    s.set_defaults(func=cmd_fonts)

    s = sub.add_parser("measure", help="width/height of candidate translations in a font, before building a spec")
    s.add_argument("text", nargs="+")
    s.add_argument("--font", required=True)
    s.add_argument("--font-index", type=int, default=0)
    s.add_argument("--size", type=int, required=True)
    s.add_argument("--stroke", type=int, default=0, help="outline width, included in the measure")
    s.add_argument("--spacing", type=int, default=4)
    s.add_argument("--max-width", type=int, help="report fits=true/false against this width")
    s.set_defaults(func=cmd_measure)

    s = sub.add_parser("apply", help="erase source text and draw the translation, as described by a spec",
                       description="See references/spec-format.md. Never writes into --src.")
    s.add_argument("spec")
    s.add_argument("--src", default=".", help="folder with the original images (default: .)")
    s.add_argument("--out", required=True, help="output folder, mirrors the source paths")
    s.add_argument("--only", action="append", help="process only this source (repeatable)")
    s.add_argument("--allow-overflow", action="store_true", help="write images even if a text overflows")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--strict-alignment", action="store_true",
                   help="exit 2 if the alignment check (same height / same width) has findings")
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("verify", help="check output against source: size/mode/format, pixels outside edits")
    s.add_argument("src", help="source file or folder")
    s.add_argument("out", help="output file or folder")
    s.add_argument("--spec", help="restrict to the spec's images and allow only its boxes")
    s.add_argument("--allow", type=box_arg, action="append", help="extra editable box x,y,w,h (repeatable)")
    s.add_argument("--only", action="append")
    s.add_argument("--compare", action="store_true", help="write original-vs-translated sheets")
    s.add_argument("--compare-dir")
    s.add_argument("--scale", type=int, default=4)
    s.add_argument("--bg", default="checker")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("clean", help=f"delete ./{WORKDIR}")
    s.set_defaults(func=cmd_clean)
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        emit({"ok": False, "error": str(exc)})
        sys.stderr.write(f"imgtr: {exc}\n")
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
