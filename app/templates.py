"""Template storage, page rendering and label-based field suggestions."""

from __future__ import annotations

import difflib
import json
import re
import uuid
from functools import lru_cache

import pymupdf

from . import db, uk_data
from .config import TEMPLATES_DIR
from .datatypes import all_types

RENDERS = ("text", "multiline", "comb", "checkbox", "signature")


WIDGET_TYPES = {pymupdf.PDF_WIDGET_TYPE_TEXT: "Text", pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "CheckBox",
                pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "RadioButton", pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "ComboBox",
                pymupdf.PDF_WIDGET_TYPE_LISTBOX: "ListBox", pymupdf.PDF_WIDGET_TYPE_SIGNATURE: "Signature"}


def normalise_pdf(data: bytes) -> tuple[bytes, list[list[float]], list[dict]]:
    """Strip rotation (keeping appearance) and form widgets so editor and renderer agree on coordinates.

    The positions and names of any fillable form fields are returned first: they are the most
    accurate hint for auto-detection.
    """
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Not a readable PDF: {e}") from e
    if doc.needs_pass:
        raise ValueError("Password-protected PDFs are not supported")
    if doc.page_count == 0:
        raise ValueError("The PDF has no pages")
    widgets = []
    for page in doc:
        for w in list(page.widgets() or []):
            if w.field_type in WIDGET_TYPES:
                r = w.rect * page.rotation_matrix  # visual (rotated) coordinates
                widgets.append({"page": page.number, "rect": [round(v, 2) for v in r], "name": w.field_name or "",
                                "type": WIDGET_TYPES.get(w.field_type, ""), "label": w.field_label or ""})
            page.delete_widget(w)
        if page.rotation:
            page.remove_rotation()
    # Re-home every page as a self-contained form XObject on a fresh page. This isolates any graphics
    # state the original content leaves behind (e.g. the matrix remove_rotation adds, or unbalanced
    # q/Q in real-world PDFs) so text drawn on top later lands exactly where the editor shows it.
    out = pymupdf.open()
    sizes = []
    for i, page in enumerate(doc):
        w, h = page.rect.width, page.rect.height
        new = out.new_page(width=w, height=h)
        new.show_pdf_page(new.rect, doc, i)
        sizes.append([round(w, 2), round(h, 2)])
    return out.tobytes(garbage=3, deflate=True), sizes, widgets


def create(name: str, data: bytes, widgets: list[dict] | None = None) -> dict:
    pdf, sizes, found = normalise_pdf(data)
    filename = f"{uuid.uuid4().hex}.pdf"
    (TEMPLATES_DIR / filename).write_bytes(pdf)
    widgets = found if widgets is None else widgets
    if widgets:
        _widgets_path(filename).write_text(json.dumps(widgets))
    ts = db.now()
    tid = db.execute("INSERT INTO templates (name, filename, page_sizes, fields, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                     [name, filename, sizes, [], ts, ts])
    return get(tid)


def get(tid: int) -> dict | None:
    return db.query_one("SELECT * FROM templates WHERE id = ?", (tid,))


def path(t: dict):
    return TEMPLATES_DIR / t["filename"]


def _widgets_path(filename: str):
    return TEMPLATES_DIR / (filename.rsplit(".", 1)[0] + ".widgets.json")


def load_widgets(t: dict) -> list[dict]:
    p = _widgets_path(t["filename"])
    return json.loads(p.read_text()) if p.exists() else []


def delete_files(t: dict) -> None:
    path(t).unlink(missing_ok=True)
    _widgets_path(t["filename"]).unlink(missing_ok=True)


@lru_cache(maxsize=64)
def page_png(filename: str, page_no: int, dpi: int) -> bytes:
    doc = pymupdf.open(TEMPLATES_DIR / filename)
    return doc[page_no].get_pixmap(dpi=dpi, alpha=False).tobytes("png")


def clean_fields(fields: list[dict], page_count: int) -> list[dict]:
    out = []
    for f in fields:
        name = str(f.get("name") or "").strip()
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", name):
            raise ValueError(f"Invalid field name '{name}': use letters, digits and underscores, starting with a letter")
        rect = [float(v) for v in f.get("rect", [])]
        if len(rect) != 4 or rect[2] <= rect[0] or rect[3] <= rect[1]:
            raise ValueError(f"Field '{name}' has an invalid box")
        page = int(f.get("page", 0))
        if not 0 <= page < page_count:
            raise ValueError(f"Field '{name}' is on a page that doesn't exist")
        render = f.get("render") if f.get("render") in RENDERS else "text"
        out.append({
            "id": f.get("id") or uuid.uuid4().hex[:10], "name": name, "type": str(f.get("type") or ""),
            "page": page, "rect": [round(v, 2) for v in rect], "render": render,
            "person": max(1, min(9, int(f.get("person") or 1))), "options": f.get("options") or {},
        })
    return out


# --------------------------------------------------------------------------------------
# Suggestions
# --------------------------------------------------------------------------------------

# label phrase -> (type key, person, render)
ALIASES: dict[str, tuple[str, int, str | None]] = {}


def _alias(phrases: str, key: str, person: int = 1, render: str | None = None):
    for p in phrases.split("|"):
        ALIASES[p] = (key, person, render)


_alias("title", "title")
_alias("first name|forename|forenames|first names|given name|given names|christian name", "first_name")
_alias("middle name|middle names|other names", "middle_name")
_alias("surname|last name|family name", "last_name")
_alias("name|full name|patient name|patient|name of patient|client name|service user", "full_name")
_alias("initials", "initials")
_alias("sex|gender", "sex")
_alias("date of birth|dob|d o b|birth date", "date_of_birth")
_alias("age", "age")
_alias("nhs number|nhs no|nhs", "nhs_number")
_alias("hospital number|hospital no|hosp no|mrn|unit number|patient id", "hospital_number")
_alias("marital status", "marital_status")
_alias("ethnicity|ethnic origin|ethnic group", "ethnicity")
_alias("occupation|job|employment", "occupation")
_alias("telephone|tel|phone|phone number|telephone number|home phone|home telephone|tel no|landline", "phone_home")
_alias("mobile|mobile number|mobile no|mob", "phone_mobile")
_alias("email|email address|e mail", "email")
_alias("address|home address|patient address|delivery address", "full_address", 1, "multiline")
_alias("address line 1|address 1|house|street", "address_line_1")
_alias("address line 2|address 2", "address_line_2")
_alias("town|city|town city|post town", "town")
_alias("county", "county")
_alias("postcode|post code|zip", "postcode")
_alias("gp|gp name|general practitioner|doctor", "gp_name")
_alias("gp practice|practice|surgery|practice name|gp surgery", "gp_practice")
_alias("gp address|practice address|surgery address", "gp_address", 1, "multiline")
_alias("gp telephone|gp phone|practice telephone|surgery telephone", "gp_phone")
_alias("practice code|ods code", "gp_practice_code")
_alias("prescriber|prescriber name|clinician|consultant|referrer|referring clinician", "clinician_name")
_alias("gmc|gmc number|gmc no", "gmc_number")
_alias("diagnosis|condition|indication|reason for referral", "condition")
_alias("medication|drug|drug name|medicine|medication name", "medication_name")
_alias("dose|dosage|directions", "medication_dose")
_alias("allergies|allergy|allergy status|known allergies", "allergies")
_alias("height", "height_cm")
_alias("weight", "weight_kg")
_alias("date|todays date|date signed|signature date", "today")
_alias("time", "time")
_alias("signature|signed|sign|patient signature", "signature", 1, "signature")
_alias("next of kin|nok|next of kin name|emergency contact|contact name", "full_name", 2)
_alias("relationship|relationship to patient|relation", "relationship", 2)
_alias("next of kin telephone|nok telephone|nok phone|next of kin phone|emergency contact number", "phone_mobile", 2)
_alias("next of kin address|nok address", "full_address", 2, "multiline")
_alias("prescriber signature|clinician signature|doctor signature", "signature", 3, "signature")


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    words = [w for w in s.split() if w not in uk_data.LABEL_STOPWORDS]
    return " ".join(words)


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    if not s or not s[0].isalpha():
        s = f"field_{s}" if s else "field"
    return s[:48]


def find_label(page: pymupdf.Page, rect: pymupdf.Rect) -> str:
    """The printed text that most plausibly labels a box: inside it, to its left, or just above it."""
    # dotted / underscore leaders ("Name ......") are where the answer goes, not part of the label
    words = [w for w in page.get_text("words") if not re.fullmatch(r"[._…:]{3,}", w[4])]
    if not words:
        return ""
    lines: dict[tuple, list] = {}
    for w in words:
        lines.setdefault((w[5], w[6]), []).append(w)

    def line_text(ws):
        return " ".join(w[4] for w in sorted(ws, key=lambda w: w[0]))

    def bbox(ws):
        return pymupdf.Rect(min(w[0] for w in ws), min(w[1] for w in ws), max(w[2] for w in ws), max(w[3] for w in ws))

    candidates = []
    for ws in lines.values():
        b = bbox(ws)
        inside = [w for w in ws if rect.contains(pymupdf.Rect(w[:4]))]
        if inside:
            candidates.append((0, line_text(inside)))
            continue
        v_overlap = min(b.y1, rect.y1) - max(b.y0, rect.y0)
        if v_overlap > 0.4 * min(b.height, rect.height) and b.x1 <= rect.x0 + 3:
            gap = rect.x0 - b.x1
            if gap < 220:
                # only the words closest to the box on that line
                near = [w for w in ws if w[2] <= rect.x0 + 3]
                near.sort(key=lambda w: w[0])
                # drop words far left of the closest run (e.g. another label on the same line)
                run = [near[-1]]
                for w in reversed(near[:-1]):
                    if run[0][0] - w[2] < 12:
                        run.insert(0, w)
                    else:
                        break
                candidates.append((1 + gap / 100, line_text(run)))
            continue
        h_overlap = min(b.x1, rect.x1) - max(b.x0, rect.x0)
        if h_overlap > 0 and 0 <= rect.y0 - b.y1 < 30:
            candidates.append((2 + (rect.y0 - b.y1) / 30, line_text(ws)))
    if not candidates:
        return ""
    label = min(candidates)[1]
    return re.sub(r"[\s:*_.]+$", "", label).strip()


def match_type(label: str) -> dict:
    n = _norm(label)
    if not n:
        return {}
    types = all_types()
    if n in ALIASES:
        key, person, render = ALIASES[n]
        return {"type": key, "person": person, "render": render or types[key].render}
    # custom types by key/label
    custom = {_norm(t.label): k for k, t in types.items() if not t.builtin}
    custom.update({_norm(k.replace("_", " ")): k for k, t in types.items() if not t.builtin})
    pool = {**{p: ("alias", p) for p in ALIASES}, **{p: ("custom", k) for p, k in custom.items()}}
    best = difflib.get_close_matches(n, list(pool), n=1, cutoff=0.78)
    if not best:
        # try the trailing words, e.g. "patient's surname" -> "surname"
        parts = n.split()
        for i in range(1, len(parts)):
            tail = " ".join(parts[i:])
            if tail in ALIASES:
                best = [tail]
                break
    if not best:
        return {}
    kind, ref = pool[best[0]]
    if kind == "custom":
        return {"type": ref, "person": 1, "render": types[ref].render}
    key, person, render = ALIASES[ref]
    return {"type": key, "person": person, "render": render or types[key].render}


def _left_on_line(page: pymupdf.Page, r: pymupdf.Rect) -> list:
    """Words on the same text row as the box and to its left, ordered left to right."""
    out = []
    for w in page.get_text("words"):
        wr = pymupdf.Rect(w[:4])
        if wr.x1 <= r.x0 + 3 and min(wr.y1, r.y1) - max(wr.y0, r.y0) > 0.4 * min(wr.height, r.height):
            out.append(w)
    return sorted(out, key=lambda w: w[0])


def suggest(t: dict, page_no: int, rect: list[float]) -> dict:
    return suggest_on_page(pymupdf.open(path(t))[page_no], rect)


def suggest_on_page(page: pymupdf.Page, rect: list[float]) -> dict:
    """Field name / type / drawing style for a box, from the printed label next to it (no AI)."""
    r = pymupdf.Rect(rect)
    label = find_label(page, r)
    out = {"label": label, "name": slugify(label) if label else "", **match_type(label)}
    # Rows like "Medication 2 [____] Dose [____]": carry the row number so pairs share a group.
    if label and not re.search(r"\d", label):
        nums = [w[4] for w in _left_on_line(page, r) if re.fullmatch(r"\d{1,2}", w[4])]
        if nums:
            out["name"] = f"{out['name']}_{nums[-1]}"
    # Small square boxes are almost always tick boxes.
    if r.width < 22 and r.height < 22 and 0.6 < r.width / max(r.height, 1) < 1.6:
        out["render"] = "checkbox"
        n = _norm(label)
        if n in ("male", "female", "m", "f", "mr", "mrs", "miss", "ms", "dr", "yes", "no", "y", "n"):
            out["person"] = 1
        if n in ("male", "female", "m", "f"):
            out.update(type="sex", options={"check_value": "Male" if n.startswith("m") else "Female"},
                       name=f"sex_{'male' if n.startswith('m') else 'female'}")
        elif n in ("mr", "mrs", "miss", "ms", "dr"):
            out.update(type="title", options={"check_value": n.title()}, name=f"title_{n}")
        elif n in ("yes", "no", "y", "n"):
            # Name the box after the question on its row so the Yes and No boxes share one value.
            question = [w[4] for w in _left_on_line(page, r) if _norm(w[4]) not in ("yes", "no", "y", "n", "")]
            out.update(type="yes_no", options={"check_value": "Yes" if n.startswith("y") else "No"},
                       name=slugify(" ".join(question[-6:])) if question else "")
        elif not out.get("type"):
            out["type"] = "yes_no"
    if out.get("render") == "text" and r.height > 40:
        out["render"] = "multiline"
    elif out.get("render") == "multiline" and r.height < 30:
        # A one-line box labelled "Address": keep it on one line rather than cutting lines off.
        out["render"] = "text"
        out["options"] = {**out.get("options", {}), "transform": "single_line"}
    return out
