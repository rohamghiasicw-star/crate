#!/usr/bin/env python3
"""Addify app icon: the waveGlyph wave in white on the brand purple.

The wave is the exact path waveGlyph(w) draws in crate.html (viewBox 0 0 30 20,
stroke-width 3.1, round caps):
    M3.4 10c2.2-8.6 5.5-8.6 7.7 0s5.5 8.6 7.7 0 5.5-8.6 7.7 0
It is rasterised here with an analytic distance field (distance from each pixel centre
to the curve, antialiased over one pixel), which gives true round caps and needs only
numpy + Pillow. No rsvg/cairo/Chrome required.

Output is RGB with no alpha channel (App Store rejects icons with alpha), full-bleed
square: iOS applies the rounded mask itself.

Run with the project's python:
    DEVELOPER_DIR=/Library/Developer/CommandLineTools /usr/bin/python3 ios/tools/make_icon.py \
        ios/Addify/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png [size]
"""
import sys

import numpy as np
from PIL import Image

# Palette (Konnor spec 2026-09-23): brand purple #6B5FE0. Subtle top-to-bottom gradient
# from a light tint of it to a touch deeper, so the tile reads as the brand colour.
TOP = (0x81, 0x77, 0xE5)     # #6B5FE0 mixed 15% toward white
BOTTOM = (0x65, 0x59, 0xD5)  # #6B5FE0 about 5% deeper
GLYPH = (255, 255, 255)

# waveGlyph path, as absolute cubic segments (the "s" shorthand expanded by reflection).
SEGMENTS = [
    ((3.4, 10.0), (5.6, 1.4), (8.9, 1.4), (11.1, 10.0)),
    ((11.1, 10.0), (13.3, 18.6), (16.6, 18.6), (18.8, 10.0)),
    ((18.8, 10.0), (21.0, 1.4), (24.3, 1.4), (26.5, 10.0)),
]
STROKE = 3.1
# Inked extent of the stroked path, caps included: x 1.85..28.05, y 2.0..18.0.
INK_W = (26.5 - 3.4) + STROKE          # 26.2
INK_CX = (3.4 + 26.5) / 2              # 14.95
INK_CY = 10.0                          # curve extremes are 10 -/+ 6.45, symmetric

# Share of the icon width the inked glyph spans. Chosen for weight at 60px and 20px.
WIDTH_FRAC = 0.74


def polyline(n_per_seg=600):
    pts = []
    t = np.linspace(0.0, 1.0, n_per_seg)
    for (p0, p1, p2, p3) in SEGMENTS:
        p0, p1, p2, p3 = map(np.array, (p0, p1, p2, p3))
        mt = 1 - t
        c = (mt**3)[:, None] * p0 + (3 * mt * mt * t)[:, None] * p1 \
            + (3 * mt * t * t)[:, None] * p2 + (t**3)[:, None] * p3
        pts.append(c if not pts else c[1:])
    return np.concatenate(pts)


def glyph_alpha(n, width_frac=WIDTH_FRAC):
    """Coverage (0..1) of the white wave on an n x n canvas."""
    s = width_frac * n / INK_W                     # glyph units -> pixels
    r = STROKE * s / 2.0                           # stroke half-width in pixels
    pts = (polyline() - [INK_CX, INK_CY]) * s + n / 2.0
    a, b = pts[:-1], pts[1:]
    dist = np.full((n, n), np.inf)
    pad = r + 2
    for (ax, ay), (bx, by) in zip(a, b):
        x0 = max(int(np.floor(min(ax, bx) - pad)), 0)
        x1 = min(int(np.ceil(max(ax, bx) + pad)) + 1, n)
        y0 = max(int(np.floor(min(ay, by) - pad)), 0)
        y1 = min(int(np.ceil(max(ay, by) + pad)) + 1, n)
        if x0 >= x1 or y0 >= y1:
            continue
        ys, xs = np.mgrid[y0:y1, x0:x1]
        px, py = xs + 0.5, ys + 0.5                # pixel centres
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        tt = np.clip(((px - ax) * dx + (py - ay) * dy) / l2, 0.0, 1.0) if l2 else 0.0
        d = np.hypot(px - (ax + tt * dx), py - (ay + tt * dy))
        np.minimum(dist[y0:y1, x0:x1], d, out=dist[y0:y1, x0:x1])
    return np.clip(r - dist + 0.5, 0.0, 1.0)


def render(n, width_frac=WIDTH_FRAC):
    y = (np.arange(n) + 0.5) / n
    top, bot = np.array(TOP, float), np.array(BOTTOM, float)
    bg = (top[None, :] * (1 - y)[:, None] + bot[None, :] * y[:, None])[:, None, :]
    bg = np.broadcast_to(bg, (n, n, 3))
    a = glyph_alpha(n, width_frac)[:, :, None]
    out = bg * (1 - a) + np.array(GLYPH, float) * a
    return Image.fromarray(np.round(out).astype(np.uint8))  # HxWx3 uint8 -> RGB, no alpha


if __name__ == "__main__":
    dest = sys.argv[1] if len(sys.argv) > 1 else "AppIcon-1024.png"
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 1024
    render(size).save(dest, "PNG", optimize=True)
    print("wrote", dest, size)
