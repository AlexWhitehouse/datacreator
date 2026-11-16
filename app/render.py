"""Draw field values onto template pages: typed text, handwriting, comb boxes, ticks and signatures.

Coordinates are PDF points with a top-left origin (PyMuPDF's convention); templates are
normalised to rotation 0 on upload so these match what the editor shows.
"""

from __future__ import annotations

import math
import random
import threading
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .config import FONTS_DIR

HW_DIR = FONTS_DIR / "handwriting"
SIG_DIR = FONTS_DIR / "signature"

TYPED_FONTS = {"helv": "Helvetica", "cour": "Courier", "tiro": "Times"}

INKS = {
    "blue": [(0.05, 0.15, 0.55), (0.10, 0.20, 0.65), (0.0, 0.10, 0.40), (0.15, 0.25, 0.70)],
    "black": [(0.08, 0.08, 0.10), (0.15, 0.15, 0.18), (0.20, 0.20, 0.22)],
}
INKS["mixed"] = INKS["blue"] + INKS["black"]


def _font_name(path: Path) -> str:
    return path.stem.replace("-Regular", "").replace("-Variable", "")


def handwriting_fonts() -> dict[str, Path]:
    return {_font_name(p): p for p in sorted(HW_DIR.glob("*.ttf"))}


def signature_fonts() -> dict[str, Path]:
    return {_font_name(p): p for p in sorted(SIG_DIR.glob("*.ttf"))}


_local = threading.local()


def _font(key: str) -> pymupdf.Font:
    cache = getattr(_local, "fonts", None)
    if cache is None:
        cache = _local.fonts = {}
    if key not in cache:
        cache[key] = pymupdf.Font(key) if key in TYPED_FONTS else pymupdf.Font(fontfile=key)
    return cache[key]


@dataclass
class DocStyle:
    """The 'writer' of one document: the same hand and pen are used for every handwritten field."""
    hw_font: str
    hw_font_name: str
    sig_font: str
    ink: tuple
    slant: float
    jitter: float
    spacing: float
    size_factor: float
    mark: str
    typed_font: str

    def public(self) -> dict:
        return {"handwriting_font": self.hw_font_name, "signature_font": Path(self.sig_font).stem,
                "ink": [round(c, 3) for c in self.ink], "slant_deg": round(self.slant, 2),
                "typed_font": TYPED_FONTS.get(self.typed_font, self.typed_font), "mark": self.mark}


def make_doc_style(rng: random.Random, settings: dict) -> DocStyle:
    hw = handwriting_fonts()
    allowed = [n for n in (settings.get("fonts") or []) if n in hw] or list(hw)
    name = rng.choice(allowed)
    sig = signature_fonts()
    return DocStyle(
        hw_font=str(hw[name]), hw_font_name=name, sig_font=str(rng.choice(list(sig.values()))),
        ink=rng.choice(INKS.get(settings.get("ink") or "mixed", INKS["mixed"])),
        slant=rng.uniform(-3, 12), jitter=rng.uniform(0.5, 1.2), spacing=rng.uniform(0.96, 1.08),
        size_factor=rng.uniform(0.9, 1.1), mark=rng.choice(["tick", "tick", "cross"]),
        typed_font=settings.get("typed_font") if settings.get("typed_font") in TYPED_FONTS else "helv",
    )


# --------------------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------------------

def _wrap(text: str, font: pymupdf.Font, size: float, max_w: float) -> list[str]:
    lines = []
    for para in text.split("\n"):
        words, cur = para.split(" "), ""
        for w in words:
            cand = f"{cur} {w}" if cur else w
            if font.text_length(cand, fontsize=size) <= max_w or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def _write_hw(page, text, x, baseline, size, font, style: DocStyle, rng, angle=0.0, extra_slant=0.0):
    if not text:
        return
    tw = pymupdf.TextWriter(page.rect)
    cx = x
    for i, ch in enumerate(text):
        s = size * (1 + rng.uniform(-0.06, 0.06) * style.jitter)
        dy = (rng.uniform(-0.04, 0.04) + 0.03 * math.sin(i / 2.3)) * size * style.jitter
        tw.append((cx, baseline + dy), ch, font=font, fontsize=s)
        cx += font.text_length(ch, fontsize=s) * style.spacing
    shear = pymupdf.Matrix(1, 0, -math.tan(math.radians(style.slant + extra_slant)), 1, 0, 0)
    tw.write_text(page, color=style.ink, morph=(pymupdf.Point(x, baseline), shear * pymupdf.Matrix(angle)))


def _hw_width(text, font, size, style: DocStyle) -> float:
    return font.text_length(text, fontsize=size) * style.spacing


def _write_typed(page, text, x, baseline, size, font, color=(0, 0, 0)):
    if not text:
        return
    tw = pymupdf.TextWriter(page.rect)
    tw.append((x, baseline), text, font=font, fontsize=size)
    tw.write_text(page, color=color)


def _tilt(rng, max_deg: float, width: float, size: float) -> float:
    """A random baseline tilt, limited so a long line drifts at most ~30% of a letter height."""
    limit = math.degrees(math.atan(0.3 * size / max(width, 1.0)))
    m = min(max_deg, limit)
    return rng.uniform(-m, m)


def _erase_printed_text(page: pymupdf.Page, r: pymupdf.Rect) -> None:
    """Cover printed text inside a box (e.g. a Word "Choose an item." placeholder) in the box's own
    background colour, as if the value had been typed into the original document."""
    words = [w for w in page.get_text("words") if r.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))]
    if not words:
        return
    pix = page.get_pixmap(clip=r, dpi=50, colorspace=pymupdf.csRGB, alpha=False)
    data = pix.samples
    counts: dict[tuple, int] = {}
    for i in range(0, len(data) - 2, 3):
        c = (data[i], data[i + 1], data[i + 2])
        counts[c] = counts.get(c, 0) + 1
    bg = max(counts, key=counts.get) if counts else (255, 255, 255)
    fill = tuple(v / 255 for v in bg)
    for w in words:
        page.draw_rect(pymupdf.Rect(w[:4]) + (-0.8, -0.8, 0.8, 0.8), color=None, fill=fill)


# --------------------------------------------------------------------------------------
# Field renderers. Each returns what was actually drawn (for the ground truth).
# --------------------------------------------------------------------------------------

def _text_field(page, r: pymupdf.Rect, text: str, opts: dict, hw: bool, style: DocStyle, rng, multiline: bool):
    pad = min(3.0 if not hw else 2.0, r.width * 0.06)
    max_w = (r.width - 2 * pad) * (0.94 if hw else 1.0)
    fixed = float(opts.get("font_size") or 0)
    if hw:
        font = _font(style.hw_font)
        asc = max(font.ascender, 0.8)
        size = fixed or max(min(r.height * 0.85 / asc, 18), min(9.0, r.height * 1.1)) * style.size_factor
        if multiline:
            size = fixed or min(13, r.height * 0.6 / asc) * style.size_factor
    else:
        font = _font(style.typed_font)
        size = fixed or max(min(r.height * 0.72, 11), min(8.0, r.height * 0.95))
        if multiline:
            size = fixed or min(10, r.height * 0.5)
    line_gap = 1.35 if hw else 1.2

    if multiline:
        while True:
            lines = _wrap(text, font, size, max_w)
            if len(lines) * size * line_gap <= r.height - pad or size <= 5:
                break
            size -= 0.5
        max_lines = max(1, int((r.height - pad) // (size * line_gap)))
        lines = lines[:max_lines]
        y = r.y0 + pad + size * (0.95 if hw else 0.9)
        for line in lines:
            x = r.x0 + pad + (rng.uniform(0, 3) * style.jitter if hw else 0)
            if hw:
                _write_hw(page, line, x, y, size, font, style, rng,
                          angle=_tilt(rng, 1.2, _hw_width(line, font, size, style), size))
            else:
                _write_typed(page, line, x, y, size, font)
            y += size * line_gap
        return "\n".join(lines)

    if "\n" in text:  # a multi-line value (e.g. an address) in a one-line box
        text = ", ".join(x.strip() for x in text.splitlines() if x.strip())
    def width(s, t=None):
        t = text if t is None else t
        return _hw_width(t, font, s, style) if hw else font.text_length(t, fontsize=s)
    limit = max_w - (4 if hw else 0)
    min_size = 5.0 if hw else 5.5
    while width(size) > limit and size > min_size:
        size -= 0.25
    # still too long at the smallest legible size: drop words rather than spill out of the box
    while width(size) > limit and " " in text.strip():
        text = text.rsplit(" ", 1)[0].rstrip(",;")
    while width(size) > limit and len(text) > 1:
        text = text[:-1]
    w = width(size)
    align = opts.get("align") or "left"
    if align == "center":
        x = r.x0 + (r.width - w) / 2
    elif align == "right":
        x = r.x1 - pad - w
    else:
        x = r.x0 + pad + (rng.uniform(0, 4) * style.jitter if hw else 0)
    if hw:
        baseline = r.y1 - r.height * rng.uniform(0.18, 0.28)
        _write_hw(page, text, x, baseline, size, font, style, rng, angle=_tilt(rng, 1.5, w, r.height))
    else:
        baseline = r.y0 + (r.height + size * 0.7) / 2
        _write_typed(page, text, x, baseline, size, font)
    return text


def _comb_field(page, r, text, opts, hw, style, rng):
    if opts.get("comb_strip", True):
        text = text.replace(" ", "")
    cells = int(opts.get("cells") or 0) or max(1, len(text))
    text = text[:cells]
    cw = r.width / cells
    font = _font(style.hw_font if hw else style.typed_font)
    asc = max(font.ascender, 0.8) if hw else 1
    size = float(opts.get("font_size") or 0) or min(r.height * (0.72 / asc if hw else 0.62), cw * 1.1, 18)
    if hw:
        size *= style.size_factor
    for i, ch in enumerate(text):
        adv = font.text_length(ch, fontsize=size)
        x = r.x0 + i * cw + (cw - adv) / 2
        if hw:
            x += rng.uniform(-0.12, 0.12) * cw * style.jitter
            baseline = r.y1 - r.height * rng.uniform(0.15, 0.26)
            _write_hw(page, ch, x, baseline, size, font, style, rng, angle=rng.uniform(-3, 3))
        else:
            _write_typed(page, ch, x, r.y0 + (r.height + size * 0.7) / 2, size, font)
    return text


def _checkbox(page, r, checked: bool, hw: bool, style: DocStyle, rng):
    if not checked:
        return False
    j = (lambda: rng.uniform(-0.08, 0.08) * style.jitter) if hw else (lambda: 0.0)
    pt = lambda fx, fy: pymupdf.Point(r.x0 + (fx + j()) * r.width, r.y0 + (fy + j()) * r.height)
    color = style.ink if hw else (0, 0, 0)
    width = max(0.8, min(r.width, r.height) * (0.1 if hw else 0.08))
    mark = style.mark if hw else "cross"
    if mark == "tick":
        page.draw_polyline([pt(0.12, 0.55), pt(0.38, 0.88), pt(0.95, 0.02)], color=color, width=width,
                           lineCap=1, lineJoin=1)
    else:
        inset = 0.15 if hw else 0.2
        page.draw_line(pt(inset, inset), pt(1 - inset, 1 - inset), color=color, width=width, lineCap=1)
        page.draw_line(pt(1 - inset, inset), pt(inset, 1 - inset), color=color, width=width, lineCap=1)
    return True


def _signature(page, r, text, style: DocStyle, rng):
    font = _font(style.sig_font)
    asc = max(font.ascender, 0.8)
    size = min(r.height * 0.85 / asc, 28)
    while font.text_length(text, fontsize=size) > r.width * 0.92 and size > 6:
        size -= 0.5
    x = r.x0 + rng.uniform(0.02, 0.15) * max(0.0, r.width - font.text_length(text, fontsize=size))
    baseline = r.y1 - r.height * rng.uniform(0.2, 0.32)
    _write_hw(page, text, x, baseline, size, font, style, rng, angle=rng.uniform(-4, 2), extra_slant=rng.uniform(0, 8))
    if rng.random() < 0.4:  # underline flourish
        w = font.text_length(text, fontsize=size)
        y = baseline + size * 0.18
        page.draw_bezier(pymupdf.Point(x, y + rng.uniform(-1, 1)), pymupdf.Point(x + w * 0.3, y + size * 0.25),
                         pymupdf.Point(x + w * 0.7, y - size * 0.1), pymupdf.Point(x + w * rng.uniform(0.9, 1.1), y),
                         color=style.ink, width=0.7)
    return text


def is_checked(value, text: str, opts: dict) -> bool:
    want = (opts.get("check_value") or "").strip().lower()
    if want:
        return text.strip().lower() == want
    if isinstance(value, bool):
        return value
    return bool(text.strip()) and text.strip().lower() not in ("no", "n", "false", "0")


def render_field(page: pymupdf.Page, f: dict, text: str, value, hw: bool, style: DocStyle, rng: random.Random):
    r = pymupdf.Rect(f["rect"])
    opts = f.get("options") or {}
    kind = f.get("render") or "text"
    if opts.get("erase") and kind != "checkbox":
        _erase_printed_text(page, r)
    if kind == "checkbox":
        return _checkbox(page, r, is_checked(value, text, opts), hw, style, rng)
    if kind == "signature":
        return _signature(page, r, text, style, rng)
    if kind == "comb":
        return _comb_field(page, r, text, opts, hw, style, rng)
    return _text_field(page, r, text, opts, hw, style, rng, multiline=(kind == "multiline"))


def font_sample_png(name: str, signature: bool = False) -> bytes:
    fonts = signature_fonts() if signature else handwriting_fonts()
    path = fonts[name]
    doc = pymupdf.open()
    page = doc.new_page(width=260, height=34)
    font = pymupdf.Font(fontfile=str(path))
    tw = pymupdf.TextWriter(page.rect)
    text = "Jane Smith 12/03/1984" if not signature else "J Smith"
    size = 18
    while font.text_length(text, fontsize=size) > 250 and size > 8:
        size -= 1
    tw.append((5, 24), text, font=font, fontsize=size)
    tw.write_text(page, color=INKS["blue"][0])
    return page.get_pixmap(dpi=144).tobytes("png")
