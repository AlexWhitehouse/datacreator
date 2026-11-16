"""OCR stress effects: crossings-out, handwriting that spills out of its box, and typos.

Every decision for a field comes from its own random stream, seeded from (doc_seed, field index, effect) and
never from the document's main stream. So:
- with every effect off, documents are drawn exactly as they were before these effects existed;
- accuracy duplicates of a document get the same effects on the fields they both fill;
- turning one effect on, or changing its rate, never moves another effect.

Rules:
- Tick boxes and signatures are never touched.
- Crossings-out and overflow apply to handwritten single-line and multi-line fields only. Typed forms don't get
  hand-struck corrections, and character boxes (comb) are skipped: a struck value and a correction don't fit
  one character per box.
- Typos apply to typed and handwritten text, multi-line and character-box fields. Handwriting gets the slips
  people make with a pen (swapped, dropped or doubled letters); typed text can also hit a neighbouring key.
  Mostly-numeric values get a digit slip. Character boxes only get slips that keep the length.
- Dates are only ever changed to another real date in the same format (a day or month out by one, the last
  two year digits swapped, day and month swapped in numeric formats), so they always parse. A date-looking
  string that can't be parsed gets no typo at all.
- A field with a crossing-out doesn't also get a typo: the writer has just corrected it.
"""

from __future__ import annotations

import random
import re
from datetime import date, datetime

import pymupdf

from .datatypes import format_value

DEFAULTS = {
    "crossouts": False, "crossouts_pct": 10.0,   # average % of filled handwritten fields; each document varies
    "overflow": False, "overflow_pct": 15.0,     # % of handwritten text fields allowed to spill out of the box
    "typos": False, "typos_pct": 5.0,            # % of filled text fields with a typo
}
EFFECTS = ("crossouts", "overflow", "typos")
WRITTEN = ("text", "multiline")                  # crossings-out and overflow
TYPO_RENDERS = ("text", "multiline", "comb")

_SALT = {"rate": 0x51ED27, "crossouts": 0xC2B2AE35, "typos": 0x27D4EB2F, "overflow": 0x165667B1}


def normalise(out: dict) -> None:
    """Clean the effect settings in place. Older settings without these keys get them switched off."""
    for k in EFFECTS:
        out[k] = bool(out.get(k, False))
        try:
            out[f"{k}_pct"] = max(0.0, min(100.0, float(out.get(f"{k}_pct", DEFAULTS[f"{k}_pct"]))))
        except (TypeError, ValueError):
            out[f"{k}_pct"] = DEFAULTS[f"{k}_pct"]


def enabled(settings: dict) -> bool:
    return any(settings.get(k) and settings.get(f"{k}_pct", 0) > 0 for k in EFFECTS)


def _rng(doc_seed: int, idx: int, effect: str) -> random.Random:
    return random.Random((((doc_seed & 0x7FFFFFFF) * 1_000_003 + idx * 7919) ^ _SALT[effect]) & 0xFFFFFFFFFFFF)


def crossout_rate(settings: dict, doc_seed: int) -> float:
    """This document's crossing-out rate: drawn evenly around the setting, so some writers correct themselves
    more than others but the batch averages the setting."""
    if not settings.get("crossouts"):
        return 0.0
    p = settings["crossouts_pct"]
    return round(random.Random(doc_seed ^ _SALT["rate"]).uniform(max(0.0, 2 * p - 100), min(100.0, 2 * p)), 1)


# --------------------------------------------------------------------------------------
# Typos
# --------------------------------------------------------------------------------------

_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
_NEIGHBOURS: dict[str, str] = {}
for _r, _row in enumerate(_ROWS):
    for _c, _ch in enumerate(_row):
        near = []
        for rr, cc in ((_r, _c - 1), (_r, _c + 1), (_r - 1, _c), (_r - 1, _c + 1), (_r + 1, _c - 1), (_r + 1, _c)):
            if 0 <= rr < 3 and 0 <= cc < len(_ROWS[rr]):
                near.append(_ROWS[rr][cc])
        _NEIGHBOURS[_ch] = "".join(near)

_DATE_RE = re.compile(r"\b\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}\b")
_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%y", "%d-%m-%y", "%d.%m.%y")


def _match_case(new: str, like: str) -> str:
    return new.upper() if like.isupper() else new.lower()


def _wrong_date(d, numeric_month: bool, rng: random.Random):
    """A nearby real date someone might write by mistake, or None."""
    cands = []
    for delta in (-1, 1):
        try:
            cands.append(d.replace(day=d.day + delta))
        except ValueError:
            pass
        try:
            cands.append(d.replace(month=d.month + delta))
        except ValueError:
            pass
    y = f"{d.year:04d}"
    if y[2] != y[3]:
        try:
            swapped = d.replace(year=int(y[:2] + y[3] + y[2]))
            if 1900 <= swapped.year <= date.today().year + 1:
                cands += [swapped, swapped]  # the classic slip, so weight it
        except ValueError:
            pass
    if numeric_month and d.day <= 12 and d.day != d.month:
        try:
            cands.append(d.replace(day=d.month, month=d.day))
        except ValueError:
            pass
    cands = [c for c in cands if c != d]
    return rng.choice(cands) if cands else None


def _date_typo(text: str, value, opts: dict, rng: random.Random) -> str | None:
    if isinstance(value, (date, datetime)):
        fmt = opts.get("date_format") or "%d/%m/%Y"
        wrong = _wrong_date(value, "%m" in fmt or "%-m" in fmt, rng)
        return format_value(wrong, opts) if wrong else None
    m = _DATE_RE.search(text)
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(m.group(0), fmt).date()
        except ValueError:
            continue
        wrong = _wrong_date(d, True, rng)
        if not wrong:
            return None
        return text[:m.start()] + wrong.strftime(fmt) + text[m.end():]
    return None  # looks like a date but isn't one we can keep valid: leave it alone


def _digit_slip(text: str, rng: random.Random, same_length: bool) -> str | None:
    digits = [i for i, c in enumerate(text) if c.isdigit()]
    if not digits:
        return None
    ops = ["neighbour", "neighbour"]
    pairs = [i for i in digits if i + 1 < len(text) and text[i + 1].isdigit() and text[i] != text[i + 1]]
    if pairs:
        ops += ["transpose", "transpose", "transpose"]
    if not same_length and len(digits) >= 3:
        ops += ["drop", "double"]
    op = rng.choice(ops)
    if op == "transpose":
        i = rng.choice(pairs)
        return text[:i] + text[i + 1] + text[i] + text[i + 2:]
    i = rng.choice(digits)
    if op == "neighbour":
        d = int(text[i])
        new = str(d + rng.choice((-1, 1))) if 0 < d < 9 else str(1 if d == 0 else 8)
        return text[:i] + new + text[i + 1:]
    if op == "drop":
        return text[:i] + text[i + 1:]
    return text[:i] + text[i] + text[i:]


def _letter_slip(text: str, rng: random.Random, typed: bool, same_length: bool) -> str | None:
    words = [m for m in re.finditer(r"[A-Za-z]{3,}", text)]
    if not words:
        return None
    m = rng.choice(words)
    w, s = m.group(0), m.start()
    # never the first letter of a word: real slips are nearly always inside it
    ops = ["transpose"] * 3
    if typed:
        ops += ["neighbour"] * 3
    if not same_length:
        ops += ["drop", "drop", "double", "double"]
    if same_length and not typed:
        ops += ["neighbour"]
    for _ in range(8):
        op = rng.choice(ops)
        i = rng.randrange(1, len(w))
        if op == "transpose":
            if i + 1 >= len(w) or w[i].lower() == w[i + 1].lower():
                continue
            new = w[:i] + _match_case(w[i + 1], w[i]) + _match_case(w[i], w[i + 1]) + w[i + 2:]
        elif op == "neighbour":
            near = _NEIGHBOURS.get(w[i].lower())
            if not near:
                continue
            new = w[:i] + _match_case(rng.choice(near), w[i]) + w[i + 1:]
        elif op == "drop":
            if len(w) < 4:
                continue
            new = w[:i] + w[i + 1:]
        else:
            if i + 1 < len(w) and w[i + 1].lower() == w[i].lower():
                continue
            new = w[:i + 1] + w[i] + w[i + 1:]
        if new != w:
            return text[:s] + new + text[s + len(w):]
    return None


def make_typo(text: str, value, opts: dict, rng: random.Random, typed: bool, same_length: bool = False) -> str | None:
    """A realistic misspelling of text, or None when there's nothing sensible to change."""
    if not text.strip():
        return None
    if isinstance(value, (date, datetime)) or _DATE_RE.search(text):
        new = _date_typo(text, value, opts, rng)
        if new is not None and same_length and len(new) != len(text):
            return None
        return new if new != text else None
    alnum = [c for c in text if c.isalnum()]
    if not alnum:
        return None
    numeric = sum(c.isdigit() for c in alnum) / len(alnum) >= 0.5
    if numeric:
        new = _digit_slip(text, rng, same_length) if sum(c.isdigit() for c in alnum) >= 2 else None
    else:
        new = _letter_slip(text, rng, typed, same_length) or _digit_slip(text, rng, True)
    return new if new and new != text else None


def _edit(a: str, b: str) -> tuple[int, str, str]:
    """(position, old, new) of the one changed stretch between a and b."""
    p = 0
    while p < min(len(a), len(b)) and a[p] == b[p]:
        p += 1
    q = 0
    while q < min(len(a), len(b)) - p and a[len(a) - 1 - q] == b[len(b) - 1 - q]:
        q += 1
    return p, a[p:len(a) - q], b[p:len(b) - q]


# --------------------------------------------------------------------------------------
# Crossings-out
# --------------------------------------------------------------------------------------

def make_struck(text: str, value, opts: dict, rng: random.Random) -> str | None:
    """What the writer wrote first and then crossed out: a false start (the beginning of the value, sometimes
    with a wrong last letter) or a misspelt/wrong first word. Always one word, so it sits on the first line."""
    tokens = text.split()
    if not tokens:
        return None
    first = tokens[0]
    if len(tokens) == 1 and len(first) <= 14 and rng.random() < 0.6:
        wrong = make_typo(first, value, opts, rng, typed=False)
        if wrong and " " not in wrong.strip():
            return wrong.strip()
    elif len(first) <= 12 and rng.random() < 0.55:
        wrong = make_typo(first, None, {}, rng, typed=False)
        if wrong and " " not in wrong:
            return wrong
    if len(first) < 2:
        return None
    k = rng.randint(1, min(4, len(first) - 1))
    start = first[:k]
    if k >= 2 and rng.random() < 0.5:
        near = _NEIGHBOURS.get(start[-1].lower())
        if near:
            start = start[:-1] + _match_case(rng.choice(near), start[-1])
    return start


# --------------------------------------------------------------------------------------
# Per-field plan and ground truth
# --------------------------------------------------------------------------------------

def plan(f: dict, idx: int, text: str, value, hw: bool, settings: dict, doc_seed: int, cross_rate: float):
    """Decide this field's effects. Returns (text to draw, fx for render.render_field or None, typo info)."""
    kind = f.get("render") or "text"
    if kind in ("checkbox", "signature") or not text:
        return text, None, None
    opts = f.get("options") or {}
    fx: dict = {}
    if settings.get("crossouts") and hw and kind in WRITTEN:
        r = _rng(doc_seed, idx, "crossouts")
        if r.random() * 100 < cross_rate:
            struck = make_struck(text, value, opts, r)
            if struck:
                fx["crossout"] = {"struck": struck, "mark": r.choice(("line", "line", "double", "scribble")),
                                  "seed": r.randrange(1 << 30)}
    typo = None
    if settings.get("typos") and kind in TYPO_RENDERS and "crossout" not in fx:
        r = _rng(doc_seed, idx, "typos")
        if r.random() * 100 < settings["typos_pct"]:
            src = text
            if kind == "text" and "\n" in src:  # drawn on one line, as the renderer would
                src = ", ".join(x.strip() for x in src.splitlines() if x.strip())
            if kind == "comb" and opts.get("comb_strip", True):
                src = src.replace(" ", "")
            new = make_typo(src, value, opts, r, typed=not hw, same_length=(kind == "comb"))
            if kind == "comb" and new and opts.get("comb_strip", True):
                new = new.replace(" ", "")
            if new and new != src:
                typo = (src, *_edit(src, new))
                text = new
    if settings.get("overflow") and hw and kind in WRITTEN:
        r = _rng(doc_seed, idx, "overflow")
        if r.random() * 100 < settings["overflow_pct"]:
            fx["overflow"] = {"mode": r.choice(("right", "right", "below")), "grow": r.uniform(1.05, 1.3),
                              "drop": r.uniform(0.1, 0.32), "wide": r.uniform(1.04, 1.15),
                              "below": r.choice((1, 1, 2))}
    return text, (fx or None), typo


def finish(entry: dict, drawn, fx: dict | None, typo) -> None:
    """Add the effects that actually made it onto the page to a field's ground truth."""
    if fx:
        if fx.get("crossed_out"):
            entry["crossed_out"] = fx["crossed_out"]
        if fx.get("overflow") is True and fx.get("ink_rect"):
            entry["overflow"] = True
            entry["ink_rect"] = fx["ink_rect"]
    if typo and isinstance(drawn, str):
        src, pos, old, new = typo
        flat = drawn.replace("\n", " ")
        written = (src[:pos] + new + src[pos + len(old):]).replace("\n", " ")
        # drawn in full, or fitting to the box cut the text off after the slip rather than before it
        if flat == written or (len(flat) > pos + len(new) and flat[pos:pos + len(new)] == new):
            entry["typo"] = True
            entry["intended_value"] = drawn[:pos] + old + drawn[pos + len(new):]


# --------------------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------------------

def annotate_preview(doc: pymupdf.Document, gt: dict, settings: dict) -> None:
    """Note in each page's bottom margin which fields got which effect, so a preview shows where to look."""
    if not enabled(settings):
        return
    notes: dict[int, list[str]] = {}
    for f in gt["fields"]:
        bits = []
        if f.get("crossed_out"):
            bits.append(f'{f["name"]} crossed out ("{f["crossed_out"]}")')
        if f.get("typo"):
            bits.append(f'{f["name"]} typo ("{f["value"]}")')
        if f.get("overflow"):
            bits.append(f"{f['name']} spills out")
        if bits:
            notes.setdefault(f["page"] - 1, []).extend(bits)
    font = pymupdf.Font("helv")
    for pno, page in enumerate(doc):
        if pno in notes:
            msg = "Preview, OCR stress: " + "; ".join(notes[pno])
        elif pno == 0 and not notes:
            msg = "Preview, OCR stress: no field on this sample was picked (rates are per field)"
        else:
            continue
        size, room = 6.5, page.rect.width - 16
        while font.text_length(msg, fontsize=size) > room and len(msg) > 20:
            msg = msg[:-6].rstrip("; ") + "..."  # base-14 Helvetica has no ellipsis glyph
        page.insert_text((8, page.rect.height - 5), msg, fontsize=size, color=(0.8, 0.1, 0.3))
