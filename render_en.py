#!/usr/bin/env python3
"""render_en.py — render translated-English versions of the original sprites.

For each target image this script:
  1. auto-detects the plate geometry (top/left inset + corner radius) from the
     alpha channel,
  2. auto-detects the plate background colour and the label text colour from
     the pixel histogram,
  3. re-draws a fresh rounded plate and centres the English translation (from
     workdir/catalog.csv) on it.

Text layout:
  * font: Comic Sans (falls back to a bundled default if the TTF is missing),
  * a single shared baseline for every glyph (anchor="ls"),
  * font auto-sized to fit within --fit fraction of the plate width,
  * baseline placed at (height/2 + offset). The offset is auto-derived as
    half the x-height of the fitted font (the ink height of lowercase glyphs
    excluding ascenders and descenders, measured from the 'x' advance box),
    which centres the x-height band on the plate's vertical centre. At the
    size used for short labels this yields ~8px on a 256px plate. Pass
    --offset to force a fixed value instead.
  * multi-line labels: baselines advanced by each line's measured ink extent
    (descender + gap + next ascender) so the white space between lines is
    uniform, with gap = --line-gap x font size (default 0.4, matching the
    10-15px inter-line spacing of the original Japanese art). The "core
    band" — top of the x-height of the first line down to the baseline of
    the last, i.e. excluding all ascenders and descenders — is centred on
    the plate, which reduces exactly to the single-line rule.

Nothing here is hard-coded to one sprite — colours and geometry are read from
each input image, so the same script works across the whole 76-image set.

Usage:
    python render_en.py <filename | group | all> [options]

Positional target:
    filename   exact or space-insensitive catalog filename, e.g. 01_afk.png
               or the bare label, e.g. 01_afk
    group      a group number, e.g. 02  -> every image in that group
    all        every image in the catalog

Options:
    --offset N    force the baseline offset in px below vertical centre
                  (default: auto = half the x-height of the fitted font)
    --fit F       max text width as a fraction of plate width (default 0.84)
    --gap G       extra letter-spacing in px (default 1.0)
    --font PATH   TTF to use (default: Comic Sans if present)
    --bg R,G,B    force the plate background colour (default: auto-detect)
    --fg R,G,B    force the text colour (default: auto-detect)
    --radius R    force the corner radius in px (default: auto-detect)
    --out DIR     output directory (default: <workdir>/render_en)
    --catalog P   catalog.csv path (default: <workdir>/catalog.csv)
    --src DIR     original/ directory (default: <root>/original)

Examples:
    python render_en.py 01_afk
    python render_en.py 02
    python render_en.py all
    python render_en.py all --offset 8 --fit 0.84
"""
import argparse
import csv
import math
import os
import sys
from collections import Counter

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\comic.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
]


def pick_font(path):
    if path and os.path.exists(path):
        return path
    for cand in FONT_CANDIDATES:
        if os.path.exists(cand):
            return cand
    return None  # -> ImageFont.load_default()


def parse_rgb(s):
    if not s:
        return None
    parts = [int(x) for x in s.split(",")]
    if len(parts) != 3:
        raise SystemExit(f"--bg/--fg expects R,G,B got {s!r}")
    return tuple(parts)


def lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def detect_geometry(rgba):
    """Return (top, left, radius). Reads the alpha channel only."""
    w, h = rgba.size
    px = rgba.load()

    def alpha(x, y):
        return px[x, y][3]

    # plate top edge: first row where the centre column is opaque
    top = next((y for y in range(h) if alpha(w // 2, y) >= 250), 0)
    # plate left edge: first col where the centre row is opaque
    left = next((x for x in range(w) if alpha(x, h // 2) >= 250), 0)

    # If the plate is full-bleed there are no rounded corners.
    if top == 0 and left == 0:
        return 0, 0, 0

    # Arc points: first opaque x for the rows just below the top edge.
    pts = []
    for y in range(top + 1, top + 28):
        if y >= h:
            break
        fx = next((x for x in range(w) if alpha(x, y) >= 250), None)
        if fx is not None:
            pts.append((fx, y))

    if len(pts) < 3:
        return top, left, 0

    # Model: x(y) = (left+R) - sqrt(R^2 - (y-(top+R))^2). Least-squares over R.
    maxr = min(w, h) / 2.0
    best = None
    R10 = int(40)
    while R10 / 10.0 < maxr:
        R = R10 / 10.0
        err = 0.0
        for x, y in pts:
            dy = y - (top + R)
            if abs(dy) >= R:
                err += 10.0
                continue
            err += ((left + R) - math.sqrt(R * R - dy * dy) - x) ** 2
        if best is None or err < best[1]:
            best = (R, err)
        R10 += 1
    return top, left, (best[0] if best else 0.0)


def detect_colours(rgba, bg_override=None, fg_override=None):
    """Return (bg, fg) RGB. bg = most common opaque colour; fg = most common
    opaque colour far enough from bg in luminance (the label text)."""
    w, h = rgba.size
    p = rgba.load()
    op = Counter()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b, a = p[x, y]
            if a >= 250:
                op[(r, g, b)] += 1
    if not op:
        raise SystemExit("no opaque pixels found — image may be empty")
    bg = bg_override or op.most_common(1)[0][0]
    bl = lum(bg)

    # Prefer a brighter label; if none is bright enough, take a darker one.
    bright = Counter()
    dark = Counter()
    for (r, g, b), n in op.items():
        if (r, g, b) == bg:
            continue
        l = lum((r, g, b))
        if l > bl + 40:
            bright[(r, g, b)] += n
        elif l < bl - 40:
            dark[(r, g, b)] += n
    fg = (bright or dark).most_common(1)[0][0]
    if bg_override and fg_override:
        return bg, fg
    if bg_override:
        return bg_override, fg
    return bg, fg_override or fg


def text_width(text, font, gap):
    if not text:
        return 0.0
    return sum(font.getlength(c) for c in text) + gap * (len(text) - 1)


def fit_size(text, max_width, max_size, min_size, font_path):
    """Largest font size (>= min_size) whose width fits max_width."""
    for s in range(max_size, min_size - 1, -1):
        try:
            f = ImageFont.truetype(font_path, s)
        except Exception:
            f = ImageFont.load_default(size=s)
        if text_width(text, f, 1.0) <= max_width:
            return s, f
    s = min_size
    try:
        return s, ImageFont.truetype(font_path, s)
    except Exception:
        return s, ImageFont.load_default(size=s)


def x_height(font):
    """Ink height of a lowercase glyph without ascender/descender ('x'),
    i.e. the x-height of the font at its current size."""
    _, t, _, b = font.getbbox("x")
    return b - t


def line_ink(font, line, gap):
    """Ink extent of a full line relative to its baseline: (top, bottom) with
    top <= 0 <= bottom. Drawn on a scratch with the same per-glyph spacing as
    the final render, so extents are exact (per-line descenders included)."""
    tw = text_width(line, font, gap)
    scratch = Image.new("L", (int(tw) + 16, 256), 0)
    d = ImageDraw.Draw(scratch)
    y, x = 128.0, 8.0
    for c in line:
        if c != " ":
            d.text((x, y), c, font=font, fill=255, anchor="ls")
        x += font.getlength(c) + gap
    bb = scratch.getbbox()
    if bb is None:
        return 0.0, 0.0
    return float(bb[1] - 128), float(bb[3] - 128)


def draw_text(rend, d, text, bg, fg, w, h, font_path, offset, fit, gap, line_gap=0.4):
    """Draw `text` on the already-plate-filled `rend` (ImageDraw d).
    offset=None -> auto = half the x-height of the fitted font."""
    lines = [ln for ln in text.split("\n") if ln.strip() != "" or ln == ""]
    max_w = fit * w

    # Size to the widest line.
    size = None
    for s in range(60, 11, -1):
        try:
            f = ImageFont.truetype(font_path, s)
        except Exception:
            f = ImageFont.load_default(size=s)
        if all(text_width(ln, f, gap) <= max_w for ln in lines):
            size = s
            break
    if size is None:
        size = 12
    f = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default(size=size)

    if offset is None:
        offset = x_height(f) / 2.0

    if len(lines) == 1:
        # Exact tuned layout: shared baseline at h/2 + offset.
        baseline = h / 2.0 + offset
        tw = text_width(lines[0], f, gap)
        x = (w - tw) / 2.0
        for c in lines[0]:
            if c != " ":
                d.text((x, baseline), c, font=f, fill=fg, anchor="ls")
            x += f.getlength(c) + gap
        return size

    # Multi-line: per-line ink extents drive evenly spaced baselines
    # (descender of line i + uniform visual gap + ascender of line i+1), and
    # the "core band" — from the top of the x-height of the first line to the
    # baseline of the last (i.e. excluding all ascenders and descenders) — is
    # centred on the plate. For a single line this reduces exactly to the
    # tuned rule baseline = h/2 + ½·x-height.
    inks = [line_ink(f, ln, gap) for ln in lines]
    g = line_gap * size
    advances = []
    for i in range(len(lines) - 1):
        advances.append(inks[i][1] + g + max(-inks[i + 1][0], 0.0))
    advance = sum(advances)
    off = x_height(f) / 2.0 if offset is None else offset
    b0 = h / 2.0 + off + x_height(f) / 2.0 - advance / 2.0
    baselines = [b0]
    for a in advances:
        baselines.append(baselines[-1] + a)
    for baseline, ln in zip(baselines, lines):
        tw = text_width(ln, f, gap)
        x = (w - tw) / 2.0
        for c in ln:
            if c != " ":
                d.text((x, baseline), c, font=f, fill=fg, anchor="ls")
            x += f.getlength(c) + gap
    return size


def load_catalog(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def norm(name):
    return name.replace(" ", "").lower()


def select(rows, target):
    if target == "all":
        return list(rows)
    t = norm(target)
    by_group = [r for r in rows if r.get("group", "") == target]
    if by_group:
        return by_group
    # filename or bare label, space-insensitive
    out = [r for r in rows if norm(r["filename"]) == t or norm(r["filename"]).rsplit(".", 1)[0] == t]
    return out


def render_one(row, src_dir, out_dir, args, font_path):
    src = os.path.join(src_dir, row["filename"])
    if not os.path.exists(src):
        return None, f"missing source: {src}"
    text = (row.get("translation") or "").strip()
    if not text:
        return None, "empty translation"

    rgba = Image.open(src).convert("RGBA")
    w, h = rgba.size
    top, left, radius = detect_geometry(rgba)
    bg, fg = detect_colours(rgba, args.bg, args.fg)

    rend = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(rend)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(round(args.radius or radius)), fill=bg)
    size = draw_text(rend, d, text, bg, fg, w, h, font_path, args.offset, args.fit, args.gap, args.line_gap)

    out_path = os.path.join(out_dir, row["filename"])
    rend.save(out_path)
    return out_path, (f"{row['filename']}: {w}x{h} R={radius:.1f} "
                      f"bg={bg} fg={fg} font={size}px text={text!r}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="filename, group number, or 'all'")
    ap.add_argument("--offset", type=float, default=None,
                    help="force baseline offset below centre (default: auto = half x-height)")
    ap.add_argument("--fit", type=float, default=0.84)
    ap.add_argument("--gap", type=float, default=1.0)
    ap.add_argument("--line-gap", type=float, default=0.4,
                    help="multi-line: visual gap between line inks, as a fraction of font size (default 0.4)")
    ap.add_argument("--font", default=None)
    ap.add_argument("--bg", default=None)
    ap.add_argument("--fg", default=None)
    ap.add_argument("--radius", type=float, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--catalog", default=os.path.join(HERE, "catalog.csv"))
    ap.add_argument("--src", default=os.path.join(ROOT, "original"))
    args = ap.parse_args(argv)

    args.bg = parse_rgb(args.bg)
    args.fg = parse_rgb(args.fg)
    font_path = pick_font(args.font)
    out_dir = args.out or os.path.join(HERE, "render_en")
    os.makedirs(out_dir, exist_ok=True)

    rows = load_catalog(args.catalog)
    sel = select(rows, args.target)
    if not sel:
        print(f"No catalog rows match {args.target!r}.", file=sys.stderr)
        return 2

    ok = 0
    for row in sel:
        path, msg = render_one(row, args.src, out_dir, args, font_path)
        if path:
            print(f"  ok  {msg}")
            ok += 1
        else:
            print(f"  --  {row.get('filename')}: {msg}", file=sys.stderr)
    print(f"{ok}/{len(sel)} rendered -> {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
