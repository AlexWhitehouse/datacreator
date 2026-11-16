"""Automatic template mark-up.

Two stages, so box positions stay exact:

1. Geometry (no AI): find candidate input areas from the page itself. Ruled boxes and table cells,
   character-box rows, tick boxes, underlines ("Name: ______"), dotted leaders, and the positions of
   any fillable form fields the original PDF had. Lines are found on a rendered image, so the same
   code handles vector PDFs, table grids and scanned forms.
2. Labelling (AI): the page image with numbered candidates, the text near each one and the data-type
   catalogue go to the model, which says what each box is (or to skip it). It may draft new types,
   which are only suggestions until the user creates them.

Nothing is saved here: the editor shows the result as proposals for the user to accept.
"""

from __future__ import annotations

import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pymupdf

from . import ai, db, templates
from .config import AI_CONCURRENCY
from .datatypes import CUSTOM_KINDS, KEY_RE, all_types

DPI = 150
PX = 72 / DPI  # points per pixel
RENDERS = ("text", "multiline", "comb", "checkbox", "signature")
SOURCE_RANK = {"widget": 0, "comb": 1, "tickbox": 2, "box": 3, "placeholder": 4, "lines": 5, "line": 6, "dots": 7, "gap": 8}
BATCH = 20  # candidates labelled per AI call; bigger pages are split into parallel calls


# --------------------------------------------------------------------------------------
# Stage 1: geometry
# --------------------------------------------------------------------------------------

def _runs(dark: np.ndarray, min_len: int):
    """Horizontal runs of dark pixels at least min_len long: arrays (y, x0, x1)."""
    h, w = dark.shape
    padded = np.zeros((h, w + 2), dtype=np.int8)
    padded[:, 1:-1] = dark
    d = np.diff(padded, axis=1)
    sy, sx = np.nonzero(d == 1)
    _, ex = np.nonzero(d == -1)  # row-major order, so starts and ends pair up
    keep = (ex - sx) >= min_len
    return sy[keep], sx[keep], ex[keep]


def _segments(dark: np.ndarray, min_len: int, max_thick: int) -> list[tuple[float, float, float]]:
    """Merge runs on consecutive rows into line segments; returns (pos, start, end) in pixels.

    Runs stacked more than max_thick rows deep are filled areas (shading, logos), not lines.
    """
    ys, x0s, x1s = _runs(dark, min_len)
    segs: list[list[int]] = []  # [y0, y1, x0, x1]
    open_: list[list[int]] = []
    i, n = 0, len(ys)
    while i < n:
        y = ys[i]
        row = []
        while i < n and ys[i] == y:
            row.append((int(x0s[i]), int(x1s[i])))
            i += 1
        still_open = [s for s in open_ if s[1] == y - 1]
        new_open = []
        for x0, x1 in row:
            match = next((s for s in still_open if abs(s[2] - x0) <= 3 and abs(s[3] - x1) <= 3), None)
            if match:
                still_open.remove(match)
                match[1] = y
                match[2], match[3] = min(match[2], x0), max(match[3], x1)
                new_open.append(match)
            else:
                s = [y, y, x0, x1]
                segs.append(s)
                new_open.append(s)
        open_ = new_open
    return [((s[0] + s[1]) / 2, s[2], s[3]) for s in segs if s[1] - s[0] + 1 <= max_thick]


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter == 0:
        return 0.0
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    return inter / (area(a) + area(b) - inter)


def _contains(outer, inner, tol=1.5) -> bool:
    return outer[0] - tol <= inner[0] and outer[1] - tol <= inner[1] and outer[2] + tol >= inner[2] and outer[3] + tol >= inner[3]


def _cells(h_segs, v_segs, tol: float) -> list[tuple[float, float, float, float]]:
    """Rectangles bounded by consecutive horizontal lines and the vertical lines between them."""
    hs = sorted(h_segs)
    if not v_segs:
        return []
    V = np.array(v_segs)  # columns: x, y0, y1
    cells = set()
    min_h, max_h, min_w = 6 / PX, 260 / PX, 5 / PX
    for i, (y1, a0, a1) in enumerate(hs):
        for y2, b0, b1 in hs[i + 1:]:
            gap = y2 - y1
            if gap < min_h:
                continue
            if gap > max_h:
                break
            ox0, ox1 = max(a0, b0), min(a1, b1)
            if ox1 - ox0 < min_w:
                continue
            m = (V[:, 0] >= ox0 - tol) & (V[:, 0] <= ox1 + tol) & (V[:, 1] <= y1 + tol) & (V[:, 2] >= y2 - tol)
            xs = sorted(set(np.round(V[m, 0]).tolist()))
            # collapse double lines
            merged = []
            for x in xs:
                if merged and x - merged[-1] < min_w:
                    continue
                merged.append(x)
            for xa, xb in zip(merged, merged[1:]):
                # the two horizontals must be consecutive across this cell
                blocked = any(y1 + tol < y3 < y2 - tol and min(c1, xb) - max(c0, xa) > 0.5 * (xb - xa)
                              for y3, c0, c1 in hs)
                if not blocked:
                    cells.add((xa, y1, xb, y2))
    return list(cells)


def _merge_combs(cands: list[dict]) -> list[dict]:
    """Rows of 4+ small adjacent equal cells become one character-box field."""
    small = [c for c in cands if c["kind"] in ("box", "tickbox") and (c["rect"][2] - c["rect"][0]) <= 32]
    rows: dict[tuple, list] = {}
    for c in small:
        r = c["rect"]
        rows.setdefault((round(r[1] / 2), round(r[3] / 2)), []).append(c)
    used, out = set(), []
    for row in rows.values():
        row.sort(key=lambda c: c["rect"][0])
        run = [row[0]]
        for c in row[1:] + [None]:
            prev = run[-1]
            ok = c is not None and abs(c["rect"][0] - prev["rect"][2]) <= 2.5 and \
                abs((c["rect"][2] - c["rect"][0]) - (prev["rect"][2] - prev["rect"][0])) <= 0.35 * (prev["rect"][2] - prev["rect"][0])
            if ok:
                run.append(c)
                continue
            if len(run) >= 4:
                out.append({"kind": "comb", "cells": len(run),
                            "rect": [run[0]["rect"][0], min(x["rect"][1] for x in run), run[-1]["rect"][2], max(x["rect"][3] for x in run)]})
                used.update(id(x) for x in run)
            run = [c] if c is not None else []
    return [c for c in cands if id(c) not in used] + out


def _stack_lines(lines: list[dict]) -> list[dict]:
    """Two or more evenly stacked underlines with the same extent become one multi-line area."""
    lines = sorted(lines, key=lambda c: (round(c["rect"][0] / 4), c["rect"][3]))
    out, i = [], 0
    while i < len(lines):
        group = [lines[i]]
        j = i + 1
        while j < len(lines):
            a, b = group[-1]["rect"], lines[j]["rect"]
            if abs(a[0] - b[0]) < 6 and abs(a[2] - b[2]) < 6 and 10 <= b[3] - a[3] <= 32:
                group.append(lines[j])
                j += 1
            else:
                break
        if len(group) >= 2:
            r0, r1 = group[0]["rect"], group[-1]["rect"]
            out.append({"kind": "lines", "rect": [r0[0], r0[1], max(r0[2], r1[2]), r1[3]], "line_count": len(group)})
        else:
            out.append(group[0])
        i = j
    return out


BOX_GLYPHS = "☐☑☒□▢❑❒⬜◻"
PLACEHOLDER_RE = re.compile(
    r"click or tap (here )?to enter (a )?(date|text)\.?|choose an? [\w ]{1,20}?\.|choose an item\.?|"
    r"please select\.?|select an? \w+\.?|enter (a )?(date|text)( here)?\.?", re.I)


def _dark_mask(gray: np.ndarray, delta: float = 40, half: int = 12) -> np.ndarray:
    """Pixels clearly darker than their neighbourhood. Unlike a fixed threshold this finds rules drawn on
    shaded cells and ignores the shading itself."""
    g = gray.astype(np.float32)
    h, w = g.shape
    ii = np.zeros((h + 1, w + 1), dtype=np.float64)
    ii[1:, 1:] = g.cumsum(0).cumsum(1)
    ys, xs = np.arange(h), np.arange(w)
    y0, y1 = np.clip(ys - half, 0, h), np.clip(ys + half + 1, 0, h)
    x0, x1 = np.clip(xs - half, 0, w), np.clip(xs + half + 1, 0, w)
    total = ii[y1][:, x1] - ii[y0][:, x1] - ii[y1][:, x0] + ii[y0][:, x0]
    mean = total / ((y1 - y0)[:, None] * (x1 - x0)[None, :])
    return g < mean - delta


def _glyph_tickboxes(page: pymupdf.Page, dark: np.ndarray) -> list[dict]:
    """Tick boxes typed as characters (☐ □ ...), measured from the drawn square, not the font box."""
    out = []
    raw = page.get_text("rawdict")
    for b in raw.get("blocks", []):
        for line in b.get("lines", []):
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    if ch["c"] not in BOX_GLYPHS:
                        continue
                    bx = pymupdf.Rect(ch["bbox"])
                    px = [int(bx.x0 / PX), int(bx.y0 / PX), int(np.ceil(bx.x1 / PX)), int(np.ceil(bx.y1 / PX))]
                    sub = dark[max(0, px[1]):px[3], max(0, px[0]):px[2]]
                    yy, xx = np.nonzero(sub)
                    if len(xx):
                        r = [(px[0] + xx.min()) * PX, (px[1] + yy.min()) * PX, (px[0] + xx.max() + 1) * PX, (px[1] + yy.max() + 1) * PX]
                    else:
                        r = [bx.x0, bx.y0, bx.x1, bx.y1]
                    out.append({"kind": "tickbox", "rect": r, "glyph": True})
    return out


def _free_area(cell: list[float], words: list) -> tuple[list[float] | None, str, str]:
    """For a cell containing printed text, the part left for the answer.

    Returns (rect or None if the cell is label-only, label text, placeholder text).
    """
    inside = [w for w in words if cell[0] <= (w[0] + w[2]) / 2 <= cell[2] and cell[1] <= (w[1] + w[3]) / 2 <= cell[3]]
    if not inside:
        return cell, "", ""
    inside.sort(key=lambda w: (w[5], w[6], w[7]))
    # find Word-style placeholder text and separate it from the label
    text, spans = "", []
    for w in inside:
        start = len(text) + (1 if text else 0)
        text = f"{text} {w[4]}" if text else w[4]
        spans.append((start, len(text)))
    placeholder, ph_idx = "", set()
    m = next((m for m in PLACEHOLDER_RE.finditer(text) if not text[m.end():].strip(" .")), None)  # must end the text
    if m:
        placeholder = m.group(0)
        ph_idx = {i for i, (a, b) in enumerate(spans) if a < m.end() and b > m.start()}
    label_words = [w for i, w in enumerate(inside) if i not in ph_idx]
    label = " ".join(w[4] for w in label_words)
    if not label_words:
        return cell, "", placeholder  # the whole cell is the answer area
    if placeholder:
        # answer goes where the placeholder is: from the placeholder to the cell's right edge
        ph = [w for i, w in enumerate(inside) if i in ph_idx]
        return [min(w[0] for w in ph) - 1, cell[1] + 0.5, cell[2] - 0.5, cell[3] - 0.5], label, placeholder
    cw, ch = cell[2] - cell[0], cell[3] - cell[1]
    # widest horizontal gap not covered by printed text (handles "Weight: ____ kg")
    covered = sorted((w[0], w[2]) for w in label_words)
    gaps, x = [], cell[0] + 1
    for a, b in covered:
        if a - 3 > x:
            gaps.append((x, a - 3))
        x = max(x, b + 3)
    if cell[2] - 1 > x:
        gaps.append((x, cell[2] - 1))
    gap = max(gaps, key=lambda g: g[1] - g[0], default=None)
    right = None
    if gap and gap[1] - gap[0] >= max(30, 0.22 * cw) and gap[0] > cell[0] + 2:
        right = [gap[0], cell[1] + 0.5, gap[1], cell[3] - 0.5]
    ly1 = max(w[3] for w in label_words)
    below = [cell[0] + 0.5, ly1 + 1.5, cell[2] - 0.5, cell[3] - 0.5]
    below_ok = below[3] - below[1] >= 12 and cw >= 30
    if below_ok and (below[3] - below[1] >= 0.5 * ch or right is None):
        return below, label, ""
    return right, label, ""


def _vector_segments(page: pymupdf.Page) -> tuple[list, list]:
    """Rules drawn as vectors (lines, thin filled rectangles, stroked rectangle edges), in pixels.

    Word/LibreOffice table borders come out exactly this way, including faint grey and white-on-shading
    borders that image analysis can't see reliably.
    """
    hs, vs = [], []
    try:
        drawings = page.get_drawings()
    except Exception:
        return hs, vs

    def add(x0, y0, x1, y1):
        if abs(y1 - y0) < 0.8 and abs(x1 - x0) >= 8:
            hs.append(((y0 + y1) / 2 / PX, min(x0, x1) / PX, max(x0, x1) / PX))
        elif abs(x1 - x0) < 0.8 and abs(y1 - y0) >= 8:
            vs.append(((x0 + x1) / 2 / PX, min(y0, y1) / PX, max(y0, y1) / PX))

    for d in drawings:
        for it in d["items"]:
            if it[0] == "l":
                add(it[1].x, it[1].y, it[2].x, it[2].y)
            elif it[0] in ("re", "qu"):
                r = it[1] if it[0] == "re" else it[1].rect
                if r.height <= 2.5 and r.width >= 8:
                    add(r.x0, (r.y0 + r.y1) / 2, r.x1, (r.y0 + r.y1) / 2)
                elif r.width <= 2.5 and r.height >= 8:
                    add((r.x0 + r.x1) / 2, r.y0, (r.x0 + r.x1) / 2, r.y1)
                elif d.get("color") is not None and d["type"] in ("s", "fs"):
                    add(r.x0, r.y0, r.x1, r.y0); add(r.x0, r.y1, r.x1, r.y1)
                    add(r.x0, r.y0, r.x0, r.y1); add(r.x1, r.y0, r.x1, r.y1)
    return hs, vs


def page_candidates(page: pymupdf.Page, widgets: list[dict]) -> list[dict]:
    pw, ph = page.rect.width, page.rect.height
    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY, alpha=False)
    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    dark = _dark_mask(gray)
    max_thick = int(3.5 / PX)
    vh, vv = _vector_segments(page)
    h_segs = _segments(dark, int(8 / PX), max_thick) + vh
    v_segs = _segments(dark.T, int(8 / PX), max_thick) + vv
    tol = 3 / PX
    all_words = page.get_text("words")
    # ignore leaders, typed tick boxes and invisible runs (Word legacy form fields are runs of en-spaces)
    words = [w for w in all_words if w[4].strip() and not re.fullmatch(r"[._…:]{3,}", w[4])
             and not set(w[4]) <= set(BOX_GLYPHS)]

    cells = []
    for x0, y0, x1, y1 in _cells(h_segs, v_segs, tol):
        r = [x0 * PX, y0 * PX, x1 * PX, y1 * PX]
        w, h = r[2] - r[0], r[3] - r[1]
        if w > 0.97 * pw and h > 0.6 * ph:
            continue  # page border
        tick = w <= 24 and h <= 24 and 0.6 <= w / max(h, 0.1) <= 1.6
        if any(_iou(r, c["rect"]) > 0.8 for c in cells):
            continue  # the same cell found from both the image and the vector rules
        cells.append({"kind": "tickbox" if tick else "box", "rect": r})
    all_cell_rects = [c["rect"] for c in cells]

    # strokes of large/bold letters (D, O, 0...) can close into small "boxes": drop anything inside a word
    word_rects = [pymupdf.Rect(w[:4]) + (-1, -1, 1, 1) for w in words]
    cells = [c for c in cells if not any(wr.contains(pymupdf.Rect(c["rect"])) for wr in word_rects)]
    cands = cells + _glyph_tickboxes(page, dark)

    # frames that merely enclose other candidates (sections, rows of tick boxes with labels) are not inputs
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    cands = [c for c in cands if c.get("glyph") or not any(
        o is not c and _contains(c["rect"], o["rect"]) and area(o["rect"]) < 0.9 * area(c["rect"]) for o in cands)]
    cands = _merge_combs(cands)

    # cells with printed text: keep just the answer area, drop label-only cells
    for c in cands:
        if c["kind"] == "box":
            c["cell"] = list(c["rect"])
            c["free"], c["label_inside"], c["placeholder"] = _free_area(c["rect"], words)
    empties = [c["cell"] for c in cands if c["kind"] == "box" and not c["label_inside"] and c["free"] is not None]

    def has_empty_neighbour(cell):
        """A label cell whose answer goes in the empty cell beside or below it."""
        for e in empties:
            if e is cell:
                continue
            v_overlap = min(cell[3], e[3]) - max(cell[1], e[1])
            h_overlap = min(cell[2], e[2]) - max(cell[0], e[0])
            ch, cw, eh, ew = cell[3] - cell[1], cell[2] - cell[0], e[3] - e[1], e[2] - e[0]
            if abs(e[0] - cell[2]) < 4 and v_overlap > 0.6 * min(ch, eh) and eh <= 1.6 * ch:
                return True  # same-row answer cell to the right
            if abs(e[1] - cell[3]) < 4 and h_overlap > 0.6 * cw and 0.6 * cw <= ew <= 1.6 * cw:
                return True  # answer cell directly below a header cell
        return False

    kept = []
    for c in cands:
        if c["kind"] != "box":
            kept.append(c)
            continue
        free, label, placeholder = c.pop("free"), c["label_inside"], c["placeholder"]
        if free is None or (label and not placeholder and has_empty_neighbour(c["cell"])):
            continue
        letters = [ch for ch in label if ch.isalpha()]
        if len(letters) >= 6 and sum(ch.isupper() for ch in letters) >= 0.8 * len(letters):
            continue  # SECTION HEADING bar
        # keep clear of the cell's ruling (borders can be several points thick)
        free = [free[0] + 1.2, free[1] + 1, free[2] - 1.2, free[3] - 1]
        if free[2] - free[0] < 12 or free[3] - free[1] < (9 if label else 7):
            continue  # too small to write in (e.g. the sliver left under a table header's label)
        c["rect"] = free
        for k in ("label_inside", "placeholder"):
            if not c[k]:
                del c[k]
        kept.append(c)
    cands = kept

    # underlines: real ones sit next to a label, not on a cell edge or alongside another rule
    lines = []
    for y, x0, x1 in h_segs:
        yy, a, b = y * PX, x0 * PX, x1 * PX
        if b - a < 40 or b - a > 0.92 * pw:
            continue
        # the top/bottom edge of a frame or box: vertical rules meet both of its ends
        def corner(x):
            return any(abs(vx * PX - x) < 3 and (abs(vy0 * PX - yy) < 3 or abs(vy1 * PX - yy) < 3) for vx, vy0, vy1 in v_segs)
        if corner(a) and corner(b):
            continue
        # on a cell edge? (a full-width row border spans several cells, so add up their edges)
        edge_cover = sum(max(0.0, min(b, r[2]) - max(a, r[0])) for r in all_cell_rects
                         if abs(r[1] - yy) < 4 or abs(r[3] - yy) < 4)
        if edge_cover > 0.3 * (b - a):
            continue
        if any(1 < abs(o[0] * PX - yy) < 5 and min(b, o[2] * PX) - max(a, o[1] * PX) > 0.5 * (b - a)
               for o in h_segs if o != (y, x0, x1)):
            continue  # double rule / box edge
        labelled = any(abs(w[3] - yy) <= 6 and 0 <= a - w[2] <= 80 for w in words) or \
            any(0 <= yy - w[3] <= 26 and min(b, w[2]) - max(a, w[0]) > 0 for w in words)
        # lines for continued writing (e.g. several "notes" lines) only have a label by the first one
        stacked = any(10 <= abs(o[0] * PX - yy) <= 32 and abs(o[1] * PX - a) < 6 and abs(o[2] * PX - b) < 6 for o in h_segs)
        if (labelled or stacked) and not any(abs(l["rect"][3] - yy) < 1.5 and abs(l["rect"][0] - a) < 4 for l in lines):
            lines.append({"kind": "line", "rect": [a, max(0, yy - 16), b, yy]})

    # dotted / underscore leaders in the text layer ("Name ........")
    for wd in all_words:
        if re.fullmatch(r"[._…]{5,}", wd[4]):
            lines.append({"kind": "dots", "rect": [wd[0], max(0, wd[3] - 16), wd[2], wd[3]]})
    cands += _stack_lines(lines)

    # a line's area shouldn't swallow printed text above it: trim to the text's bottom edge
    for c in cands:
        if c["kind"] in ("line", "dots"):
            r = c["rect"]
            above = [w[3] for w in words if w[3] <= r[3] - 3 and w[3] > r[1] and min(w[2], r[2]) - max(w[0], r[0]) > 2]
            if above:
                r[1] = min(max(above) + 1, r[3] - 8)

    # Word placeholders outside any box ("Therapy Area: Please select.")
    by_line: dict[tuple, list] = {}
    for w in words:
        by_line.setdefault((w[5], w[6]), []).append(w)
    for ws in by_line.values():
        ws.sort(key=lambda w: w[0])
        text = " ".join(w[4] for w in ws)
        for m in PLACEHOLDER_RE.finditer(text):
            if text[m.end():].strip(" ."):
                continue  # part of a longer label ("please select all required:"), not a placeholder
            pos, hit = 0, []
            for w in ws:
                a, b = pos, pos + len(w[4])
                if a < m.end() and b > m.start():
                    hit.append(w)
                pos = b + 1
            if not hit:
                continue
            r = [min(w[0] for w in hit), min(w[1] for w in hit), max(w[2] for w in hit), max(w[3] for w in hit)]
            cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
            if any(c["rect"][0] <= cx <= c["rect"][2] and c["rect"][1] <= cy <= c["rect"][3] for c in cands):
                continue
            nxt = [w[0] for w in ws if w[0] > r[2] + 1]
            r[2] = min(min(nxt) - 3 if nxt else pw - 30, r[2] + 90)
            r[0] -= 1
            cands.append({"kind": "placeholder", "rect": r, "placeholder": m.group(0)})

    # blanks inside a printed sentence ("Please deliver every          weeks")
    vrules = [(x * PX, y0 * PX, y1 * PX) for x, y0, y1 in v_segs]
    for ws in by_line.values():
        for wa, wb in zip(ws, ws[1:]):
            gap = wb[0] - wa[2]
            if not 28 <= gap <= 140:
                continue
            r = [wa[2] + 2, min(wa[1], wb[1]) - 1, wb[0] - 2, max(wa[3], wb[3]) + 1]
            cy = (r[1] + r[3]) / 2
            if any(r[0] < x < r[2] and y0 <= cy <= y1 for x, y0, y1 in vrules):
                continue  # a table border runs through it: these are separate cells
            cx = (r[0] + r[2]) / 2
            if any(c["rect"][0] <= cx <= c["rect"][2] and c["rect"][1] <= cy <= c["rect"][3] for c in cands):
                continue
            cands.append({"kind": "gap", "rect": r})

    # logos and other images are never inputs
    imgs = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    cands = [c for c in cands if not any(im.contains(pymupdf.Point((c["rect"][0] + c["rect"][2]) / 2, (c["rect"][1] + c["rect"][3]) / 2)) for im in imgs)]

    for w in widgets:
        cands.append({"kind": "widget", "rect": list(w["rect"]), "widget_name": w.get("name") or "",
                      "widget_type": w.get("type") or ""})

    # de-duplicate: keep the best source for overlapping candidates
    cands.sort(key=lambda c: SOURCE_RANK[c["kind"]])
    out: list[dict] = []
    for c in cands:
        if any(_iou(c["rect"], k["rect"]) > 0.45 for k in out):
            continue
        out.append(c)
    for c in out:
        c["rect"] = [round(v, 2) for v in c["rect"]]
    out.sort(key=lambda c: (round(c["rect"][1] / 6), c["rect"][0]))
    return out


# --------------------------------------------------------------------------------------
# Stage 2: labelling
# --------------------------------------------------------------------------------------

SYSTEM = (
    "You analyse blank forms (UK healthcare / home care) to configure a synthetic test-data generator. "
    "You map each input box on a form to a data type from a catalogue. Respond with JSON only."
)

INSTRUCTIONS = """The image shows page {page} of a blank form. Red rectangles with red numbers mark candidate input areas found automatically.

Candidates:
{candidates}

Data type catalogue (key: label [category, value kind]):
{catalogue}

Field names already used by other templates (reuse a name exactly when the field means the same thing, so linked runs share the value):
{linked}

{scope}For each candidate that is an area the person completing the form writes in, return a "fields" entry:
{{"id": N, "name": "snake_case_name", "type": "<catalogue key or null>", "person": 1-8, "render": "text|multiline|comb|checkbox|signature", "options": {{...}}, "new_type": "<key, only if type is null>"}}.
List every other candidate number (label cells, headings, pre-printed values, decoration, duplicates, office-use-only areas) in "skip".

Rules:
- person: every distinct person on the form gets their own number, used for ALL of that person's fields (name, phone, email, signature, registration number): 1 = the patient / service user; 2 = next of kin, parent or carer (a relative of the patient); 3 = the main prescriber / referring clinician; 4-8 = each other distinct person (e.g. clinical specialist, pharmacist, validating clinician, invoice contact, homecare lead). For staff, prefer clinician_name for doctors, full_name otherwise, and work_email for NHS email addresses.
- If tick boxes already capture a value (e.g. Male / Female boxes), do not also map a nearby empty box to the same value: skip it.
- A box showing placeholder text (e.g. "Please select.", "Choose an item.") IS the answer area for its label; prefer it over a narrow empty spacer cell next to the label.
- Strips inside table header cells (e.g. under "Route", "Directions", "Drug and dose") and section heading bars are not answers: skip them.
- render: "checkbox" for tick boxes (set options.check_value to the value that ticks it, e.g. "Female" for type sex, "Yes"/"No" for yes_no, "Mrs" for title); "comb" for rows of single-character boxes (set options.cells); "multiline" for tall areas meant for several lines; "signature" for signature boxes; otherwise "text".
- Tick boxes answering one question must share one field name (e.g. the Yes and No boxes for "consent_given").
- Rows such as "Medication 2 ... Dose ..." should get matching numbers (medication_2 / dose_2) so name and dose pair up.
- Dates: if the form shows a format hint (e.g. DD/MM/YYYY) set options.date_format using Python strftime codes.
- If the form asks for BLOCK CAPITALS, set options.transform = "upper" on its free-text fields.
- Prefer catalogue types. Only when nothing fits, set "type": null and "new_type" to a key defined in new_types.

Also return:
- "new_types": draft types for fields nothing in the catalogue fits. Each: {{"key": "snake_case", "label": "...", "description": "...", "kind": "picklist|pattern|number_range|date_range|boolean|ai", "config": {{...}}}}. Config by kind: picklist {{"values": [15-30 realistic values]}}; pattern {{"pattern": "# digit, ? letter, e.g. MRN-######"}}; number_range {{"min", "max", "decimals", "suffix"}}; date_range {{"mode": "relative_today", "min_days", "max_days"}}; boolean {{"true_pct"}}; ai (free-text sentences/paragraphs) {{"prompt": "...", "mode": "live", "max_words": N}}.
- "missed": questions or labels PRINTED on this page that expect an answer but have no candidate box near them: [{{"label": "<the printed text>", "note": "..."}}]. Do not guess at fields the form doesn't show; return [] if none.

Return {{"fields": [...], "skip": [numbers], "new_types": [...], "missed": [...]}}."""


def _annotated_png(page: pymupdf.Page, cands: list[dict]) -> bytes:
    """Page image with each candidate outlined and numbered (drawn on a throwaway copy)."""
    doc = pymupdf.open()
    doc.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
    p = doc[0]
    for i, c in enumerate(cands):
        r = pymupdf.Rect(c["rect"])
        p.draw_rect(r, color=(0.9, 0, 0), width=0.8)
        tag = str(i)
        w = len(tag) * 4.4 + 2
        if r.width < 30 or r.height < 10:
            at = pymupdf.Rect(r.x1 + 1, r.y0, r.x1 + 1 + w, r.y0 + 8)
        else:
            at = pymupdf.Rect(r.x0 + 1, r.y0 + 1, r.x0 + 1 + w, r.y0 + 9)
        p.draw_rect(at, color=None, fill=(1, 1, 1), fill_opacity=0.9)
        p.insert_text((at.x0 + 1, at.y1 - 1.5), tag, fontsize=7.5, fontname="hebo", color=(0.9, 0, 0))
    return p.get_pixmap(dpi=120, alpha=False).tobytes("png")


def _describe(i: int, c: dict, label: str) -> str:
    r = c["rect"]
    w, h = r[2] - r[0], r[3] - r[1]
    kind = {"box": "box", "tickbox": "small square (tick box?)", "comb": f"row of {c.get('cells')} character boxes",
            "line": "underline", "lines": f"{c.get('line_count')} stacked lines", "dots": "dotted line",
            "widget": f"fillable PDF field '{c.get('widget_name')}' ({c.get('widget_type')})",
            "placeholder": "Word placeholder (a drop-down / date picker in the original document)",
            "gap": "blank space between words on a printed line"}[c["kind"]]
    extra = ""
    if c.get("label_inside"):
        extra += f', answer space in a cell labelled "{c["label_inside"]}"'
    if c.get("placeholder"):
        extra += f', shows placeholder text "{c["placeholder"]}" (the value replaces it)'
    return f"#{i}: {kind}, {w:.0f}x{h:.0f}pt" + extra + (f', nearby text: "{label}"' if label and not c.get("label_inside") else "")


def _catalogue(types) -> str:
    return "\n".join(f"{k}: {t.label} [{t.category}, {t.value_kind}]" + (f" - {t.description}" if t.description else "")
                     for k, t in types.items())


def _linked_names(exclude_tid: int) -> str:
    names = {}
    for t in db.query("SELECT id, name, fields FROM templates"):
        if t["id"] == exclude_tid:
            continue
        for f in t["fields"]:
            names.setdefault(f["name"], (f["type"], f.get("person", 1)))
    if not names:
        return "(none yet)"
    return "\n".join(f"{n} ({ty}, person {p})" for n, (ty, p) in sorted(names.items())[:300])


def _clean_new_type(d: dict, types) -> dict | None:
    key = templates.slugify(str(d.get("key") or d.get("label") or ""))
    kind = d.get("kind")
    if not KEY_RE.match(key) or kind not in CUSTOM_KINDS or kind == "composite":
        return None
    cfg = d.get("config") if isinstance(d.get("config"), dict) else {}
    if kind == "picklist":
        cfg = {"values": [str(v).strip() for v in cfg.get("values", []) if str(v).strip()][:200]}
    if kind == "ai":
        cfg = {"prompt": str(cfg.get("prompt") or d.get("description") or ""), "mode": "live",
               **({"max_words": int(cfg["max_words"])} if str(cfg.get("max_words", "")).isdigit() else {})}
    return {"key": key, "label": str(d.get("label") or key.replace("_", " ").capitalize()),
            "description": str(d.get("description") or ""), "kind": kind, "category": "Custom",
            "config": cfg, "exists": key in types}


def _fallback_field(page: pymupdf.Page, c: dict) -> dict | None:
    """Without AI: the label-matching heuristic used when drawing boxes by hand."""
    sug = templates.suggest_on_page(page, c["rect"])
    if not sug.get("label") and c["kind"] != "widget":
        return None
    if not sug.get("label") and c.get("widget_name"):
        sug.update(templates.match_type(c["widget_name"].replace("_", " ")), name=templates.slugify(c["widget_name"]))
    return {"name": sug.get("name") or "field", "type": sug.get("type", ""), "person": sug.get("person", 1),
            "render": sug.get("render") or "text", "options": sug.get("options") or {}, "label": sug.get("label", "")}


def _finalise(c: dict, f: dict, types, drafts: dict) -> dict:
    render = f.get("render") if f.get("render") in RENDERS else None
    if c["kind"] == "tickbox" or c.get("widget_type") in ("CheckBox", "RadioButton"):
        render = "checkbox"
    elif c["kind"] == "comb":
        render = "comb"
    elif c["kind"] == "lines" and render in (None, "text"):
        render = "multiline"
    typ = f.get("type") if f.get("type") in types else None
    draft = None
    if not typ and f.get("new_type"):
        nk = templates.slugify(str(f["new_type"]))
        if nk in types:
            typ = nk
        elif nk in drafts:
            typ, draft = nk, nk
    opts = f.get("options") if isinstance(f.get("options"), dict) else {}
    opts = {k: v for k, v in opts.items() if k in ("check_value", "cells", "date_format", "transform", "align") and v not in (None, "")}
    if c.get("placeholder"):
        opts["erase"] = True  # cover the printed placeholder when a value is written
    if render == "comb":
        opts["cells"] = int(opts.get("cells") or c.get("cells") or 0) or None
        if not opts["cells"]:
            opts.pop("cells")
    if opts.get("transform") not in (None, "upper", "lower", "title", "no_spaces", "single_line"):
        opts.pop("transform")
    t = types.get(typ)
    render = render or (t.render if t else "text")
    if render == "multiline" and (c["rect"][3] - c["rect"][1]) < 26:
        render = "text"
    try:
        person = max(1, min(8, int(f.get("person") or 1)))
    except (TypeError, ValueError):
        person = 1
    if draft or (t is not None and not t.per_person and t.kind != "composite"):
        person = 1  # the type doesn't read from a person, so the choice is meaningless
    return {"name": templates.slugify(str(f.get("name") or "field")), "type": typ or "", "person": person,
            "render": render, "options": opts, "draft_type": draft, "label": f.get("label", "")}


def detect(tid: int, existing: list[dict], pages: list[int] | None = None, use_ai: bool = True) -> dict:
    t = templates.get(tid)
    if t is None:
        raise ValueError("Template not found")
    doc = pymupdf.open(templates.path(t))
    widgets = templates.load_widgets(t)
    pages = [p for p in (pages if pages else range(doc.page_count)) if 0 <= p < doc.page_count]
    types = all_types()
    use_ai = use_ai and ai.available()

    # Stage 1 (sequential: PyMuPDF isn't thread-safe)
    jobs = []
    for pno in pages:
        page = doc[pno]
        cands = page_candidates(page, [w for w in widgets if w["page"] == pno])
        taken = [f["rect"] for f in existing if f.get("page") == pno]
        cands = [c for c in cands if not any(_iou(c["rect"], r) > 0.3 or _contains(r, c["rect"], 2) for r in taken)]
        labels = [templates.find_label(page, pymupdf.Rect(c["rect"])) for c in cands]
        png = _annotated_png(page, cands) if (use_ai and cands) else None
        jobs.append((pno, cands, labels, png))

    catalogue, linked = _catalogue(types), _linked_names(tid)

    def label_chunk(args):
        pno, cands, labels, png, lo, hi = args
        listing = "\n".join(_describe(i, c, labels[i]) for i, c in enumerate(cands))
        scope = "" if (lo, hi) == (0, len(cands)) else (
            f"Label ONLY candidates #{lo} to #{hi - 1} in this answer; the others are listed for context and "
            f"are handled separately (keep names consistent with how you would name them). Report missed questions "
            f"only for the part of the page around your candidates.\n")
        content = [
            {"type": "text", "text": INSTRUCTIONS.format(page=pno + 1, candidates=listing, catalogue=catalogue,
                                                         linked=linked, scope=scope)},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
        ]
        res, err = None, None
        for _ in range(2):
            try:
                res = ai._chat_json(content, max_tokens=min(32000, 3000 + 90 * (hi - lo)), system=SYSTEM, temperature=0.2)
            except Exception as e:
                err = str(e)
                continue
            # the model occasionally answers only part of a batch: retry once if many boxes are unaccounted for
            seen = set()
            if isinstance(res, dict):
                for f in res.get("fields") or []:
                    if isinstance(f, dict) and str(f.get("id", "")).lstrip("#").isdigit():
                        seen.add(int(str(f["id"]).lstrip("#")))
                seen.update(int(x) for x in res.get("skip") or [] if str(x).isdigit())
            if len(seen & set(range(lo, hi))) >= 0.7 * (hi - lo):
                return pno, lo, hi, res, None
        return pno, lo, hi, res, (None if res is not None else err)

    calls = []
    for pno, cands, labels, png in jobs:
        if cands and use_ai:
            n_chunks = -(-len(cands) // BATCH)
            size = -(-len(cands) // n_chunks)
            calls += [(pno, cands, labels, png, lo, min(lo + size, len(cands))) for lo in range(0, len(cands), size)]
    results: dict[int, list] = {}
    if calls:
        with ThreadPoolExecutor(max_workers=max(1, min(AI_CONCURRENCY, len(calls)))) as pool:
            for pno, lo, hi, res, err in pool.map(label_chunk, calls):
                results.setdefault(pno, []).append((lo, hi, res, err))

    proposals, drafts, missed, errors = [], {}, [], []
    for pno, cands, labels, _ in jobs:
        by_id, covered = {}, set()  # covered: candidate ids an AI answer was responsible for
        for lo, hi, res, err in results.get(pno, []):
            if err:
                errors.append(f"Page {pno + 1}: {err}")
                continue
            if not isinstance(res, dict):
                continue
            covered.update(range(lo, hi))
            for d in res.get("new_types") or []:
                if isinstance(d, dict):
                    nt = _clean_new_type(d, types)
                    if nt and nt["key"] not in drafts and not nt["exists"]:
                        drafts[nt["key"]] = nt
            for f in res.get("fields") or []:
                if isinstance(f, dict) and str(f.get("id", "")).lstrip("#").isdigit():
                    i = int(str(f["id"]).lstrip("#"))
                    if lo <= i < hi and not f.get("skip"):
                        by_id[i] = f
            for m in res.get("missed") or []:
                if isinstance(m, dict) and m.get("label") and not any(x["label"] == str(m["label"]) for x in missed):
                    missed.append({"page": pno, "label": str(m["label"]), "note": str(m.get("note") or "")})
        for i, c in enumerate(cands):
            if i in covered:
                f = by_id.get(i)
                if f is None:
                    continue
                f = {**f, "label": labels[i]}
            else:
                f = _fallback_field(doc[pno], c)
                if f is None:
                    continue
            p = _finalise(c, f, types, drafts)
            p.update(page=pno, rect=c["rect"], source=c["kind"])
            proposals.append(p)

    _unique_names(proposals, existing)
    used = {p["draft_type"] for p in proposals if p["draft_type"]}
    return {
        "proposals": proposals,
        "new_types": [d for k, d in drafts.items() if k in used],
        "missed": missed,
        "errors": errors,
        "ai_used": use_ai,
        "candidates": sum(len(j[1]) for j in jobs),
    }


def _unique_names(proposals: list[dict], existing: list[dict]) -> None:
    """Make names unique, except tick boxes deliberately sharing one question's name."""
    seen: dict[str, dict] = {f["name"]: f for f in existing}
    for p in proposals:
        base, name = p["name"] or "field", p["name"] or "field"
        prev = seen.get(name)
        shared_tick = prev is not None and p["render"] == "checkbox" and prev.get("render") == "checkbox" \
            and prev.get("type") == p["type"] and p["options"].get("check_value") != (prev.get("options") or {}).get("check_value")
        if prev is not None and not shared_tick:
            k = 2
            while f"{base}_{k}" in seen:
                k += 1
            name = f"{base}_{k}"
        p["name"] = name
        seen.setdefault(name, p)
