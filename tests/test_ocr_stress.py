"""OCR stress effects: crossings-out, writing that spills out of its box, and typos (app/stress.py)."""

import csv
import json
import random
import runpy
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parent.parent
HEAVY = {"crossouts": True, "crossouts_pct": 40, "overflow": True, "overflow_pct": 40, "typos": True, "typos_pct": 35}
OFF = {"crossouts": False, "overflow": False, "typos": False}


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    """A referral form with single-line, multi-line, character-box, date, tick-box and signature fields."""
    from fastapi.testclient import TestClient

    from app import runs, templates
    from app.datatypes import all_types
    from app.main import app
    samples = tmp_path_factory.mktemp("stress-samples")
    sys.argv = ["x", str(samples)]
    runpy.run_path(str(ROOT / "scripts" / "make_sample_forms.py"), run_name="__main__")
    c = TestClient(app)
    tid = c.post("/api/templates", files={"file": ("referral_form.pdf", (samples / "referral_form.pdf").read_bytes())},
                 data={"name": "stress referral"}).json()["id"]
    rows = [("title", "title", [100, 108, 160, 128]), ("surname", "last_name", [240, 108, 555, 128]),
            ("forename", "first_name", [100, 138, 555, 158]), ("dob", "date_of_birth", [110, 168, 230, 188]),
            ("postcode", "postcode", [110, 298, 230, 318]), ("phone", "phone_mobile", [310, 298, 555, 318]),
            ("email", "email", [110, 328, 555, 348]), ("nok", "full_name", [130, 388, 360, 408]),
            ("nok_phone", "phone_mobile", [150, 418, 360, 438]), ("practice", "town", [110, 508, 555, 528])]
    fields = [{"name": n, "type": t, "page": 0, "rect": r} for n, t, r in rows]
    fields += [{"name": "address", "type": "full_address", "page": 0, "rect": [110, 230, 555, 290], "render": "multiline"},
               {"name": "nhs", "type": "nhs_number", "page": 0, "rect": [110, 198, 270, 216], "render": "comb",
                "options": {"cells": 10}},
               {"name": "gp_sig", "type": "full_name", "page": 0, "rect": [110, 478, 330, 498], "render": "signature"}]
    fields += [{"name": "sex", "type": "sex", "page": 0, "rect": r, "render": "checkbox", "options": {"check_value": v}}
               for r, v in (([312, 171, 325, 184], "Male"), ([375, 171, 388, 184], "Female"))]
    assert c.put(f"/api/templates/{tid}", json={"fields": fields}).status_code == 200
    t = templates.get(tid)
    types = all_types()
    specs, _ = runs.build_specs([t], types)

    def values(seed):
        ctx, _ = runs.generate_values(seed, specs, types, runs.normalise_settings({"template_ids": [tid]}))
        return ctx.values

    return {"client": c, "tid": tid, "t": t, "values": values}


def _settings(tid, **kw):
    from app.runs import normalise_settings
    return normalise_settings({"template_ids": [tid], "seed": 1, "handwritten_pct": 100, **kw})


def _render(setup, seed, accuracy=None, **kw):
    from app.runs import render_document
    return render_document(setup["t"], setup["values"](seed), _settings(setup["tid"], **kw), seed, set(), accuracy)


def _pages(doc):
    return [(p.read_contents(), p.get_pixmap(dpi=60).samples) for p in doc]


def _many(setup, n=30, **kw):
    out = []
    for seed in range(1, n + 1):
        _, gt = _render(setup, seed, **{**HEAVY, **kw})
        out.append(gt)
    return out


def test_settings_default_off_and_normalise():
    from app.runs import DEFAULTS, normalise_settings
    old = normalise_settings({"template_ids": [1], "scan": "light"})  # settings saved before these effects existed
    assert (old["crossouts"], old["overflow"], old["typos"]) == (False, False, False)
    assert all(DEFAULTS[k] is False for k in ("crossouts", "overflow", "typos"))
    s = normalise_settings({"template_ids": [1], "crossouts": 1, "crossouts_pct": 250, "typos": True, "typos_pct": "-3",
                            "overflow_pct": "junk"})
    assert s["crossouts"] is True and s["crossouts_pct"] == 100 and s["typos_pct"] == 0
    assert s["overflow"] is False and s["overflow_pct"] == DEFAULTS["overflow_pct"]


def test_all_off_reproduces_previous_output(setup, monkeypatch):
    """With every effect off (or on at 0%) documents are drawn exactly as they were before the effects existed:
    the same as bypassing the stress hooks altogether."""
    from app import render, stress
    for seed in (3, 8, 21):
        no_keys = _pages(_render(setup, seed)[0])
        off = _render(setup, seed, **OFF, crossouts_pct=50, typos_pct=50, overflow_pct=50)
        zero = _render(setup, seed, crossouts=True, crossouts_pct=0, overflow=True, overflow_pct=0, typos=True, typos_pct=0)
        assert _pages(off[0]) == no_keys and _pages(zero[0]) == no_keys
        for gt in (off[1], zero[1]):
            assert not any(k in f for f in gt["fields"] for k in ("crossed_out", "overflow", "ink_rect", "typo",
                                                                  "intended_value"))
        with monkeypatch.context() as m:  # no stress code at all: plan() is a no-op and render_field gets no fx
            m.setattr(stress, "plan", lambda f, i, text, *a: (text, None, None))
            m.setattr(stress, "finish", lambda *a: None)
            orig = render.render_field
            m.setattr(render, "render_field", lambda *a: orig(*a[:7]))
            bare = _render(setup, seed)
        assert _pages(bare[0]) == no_keys
        assert [f["value"] for f in bare[1]["fields"]] == [f["value"] for f in off[1]["fields"]]


def test_deterministic_with_seed(setup):
    a_doc, a = _render(setup, 17, **HEAVY)
    b_doc, b = _render(setup, 17, **HEAVY)
    assert a == b and _pages(a_doc) == _pages(b_doc)
    _, c = _render(setup, 18, **HEAVY)
    assert c != a


def test_effects_stay_independent(setup):
    """Turning one effect on or off doesn't move which fields get the others."""
    def marks(gt, key):
        return {f["name"] for f in gt["fields"] if f.get(key)}
    for seed in range(1, 8):
        _, both = _render(setup, seed, **HEAVY)
        _, cross_only = _render(setup, seed, **{**HEAVY, "typos": False, "overflow": False})
        assert marks(both, "crossed_out") == marks(cross_only, "crossed_out")


def test_accuracy_duplicates_share_effects(setup):
    _, full = _render(setup, 9, accuracy=100, **HEAVY)
    _, half = _render(setup, 9, accuracy=40, **HEAVY)
    keys = ("value", "crossed_out", "overflow", "ink_rect", "typo", "intended_value")
    for a, b in zip(full["fields"], half["fields"]):
        if not b["missing"]:
            assert {k: a.get(k) for k in keys} == {k: b.get(k) for k in keys}, a["name"]


def test_ground_truth_for_each_effect(setup):
    gts = _many(setup)
    fields = [f for gt in gts for f in gt["fields"]]
    crossed = [f for f in fields if "crossed_out" in f]
    typos = [f for f in fields if f.get("typo")]
    over = [f for f in fields if f.get("overflow")]
    assert len(crossed) >= 10 and len(typos) >= 10 and len(over) >= 10
    for f in crossed:
        assert f["handwritten"] and f["render"] in ("text", "multiline")
        assert isinstance(f["crossed_out"], str) and f["crossed_out"].strip() and " " not in f["crossed_out"]
        assert f["value"] and f["crossed_out"] != f["value"]       # value stays the final intended value
        assert not f.get("typo")                                     # a corrected field isn't also misspelt
    for f in typos:
        assert f["value"] != f["intended_value"]                     # value is what's on the page
        assert len(f["value"]) and len(f["intended_value"])
    for f in over:
        assert f["handwritten"] and f["render"] in ("text", "multiline")
        x0, y0, x1, y1 = f["rect"]
        ix0, iy0, ix1, iy1 = f["ink_rect"]
        assert ix1 > x1 + 0.5 or iy1 > y1 + 0.5                       # ink runs past the right or bottom edge
        assert 0 <= ix0 and 0 <= iy0 and ix1 <= 595 and iy1 <= 842   # but stays on the page
    for gt in gts:
        assert 0 <= gt["crossout_rate"] <= 80                       # each document draws its own rate around 40%
    assert len({gt["crossout_rate"] for gt in gts}) > 5


def test_typo_values_match_the_page(setup):
    """The typo'd value is what's actually drawn: OCR should read it."""
    checked = 0
    for seed in range(1, 15):
        doc, gt = _render(setup, seed, handwritten_pct=0, **HEAVY)  # typed, so the text layer can be read back
        page = doc[0]
        for f in gt["fields"]:
            if f.get("typo") and f["render"] == "text":
                words = " ".join(w[4] for w in page.get_text("words", clip=pymupdf.Rect(f["rect"]) + (-2, -2, 2, 2)))
                assert words == f["value"], (words, f)
                checked += 1
    assert checked >= 5


def test_dates_stay_valid(setup):
    gts = _many(setup, n=60, typos_pct=100, crossouts=False)
    dobs = [f for gt in gts for f in gt["fields"] if f["name"] == "dob" and f.get("typo")]
    assert len(dobs) >= 20
    for f in dobs:
        wrong = datetime.strptime(f["value"], "%d/%m/%Y").date()   # still a real date in the same format
        right = datetime.strptime(f["intended_value"], "%d/%m/%Y").date()
        assert wrong != right and date.fromisoformat(f["source_value"]) == right


def test_never_on_tick_boxes_or_signatures(setup):
    for gt in _many(setup, crossouts_pct=100, overflow_pct=100, typos_pct=100):
        for f in gt["fields"]:
            if f["render"] in ("checkbox", "signature"):
                assert not any(k in f for k in ("crossed_out", "overflow", "ink_rect", "typo", "intended_value")), f
            if f["render"] == "comb":
                assert "crossed_out" not in f and "overflow" not in f


def test_typed_fields_get_typos_but_no_crossings_out_or_overflow(setup):
    fields = [f for gt in _many(setup, handwritten_pct=0, crossouts_pct=100, overflow_pct=100) for f in gt["fields"]]
    assert any(f.get("typo") for f in fields)
    assert not any("crossed_out" in f or "overflow" in f for f in fields)


def test_make_typo_kinds():
    from app.stress import make_typo
    rng = random.Random(4)
    seen = set()
    for _ in range(300):
        t = make_typo("Hutchinson", None, {}, rng, typed=True)
        assert t and t != "Hutchinson" and t[0] == "H" and abs(len(t) - 10) <= 1
        seen.add("drop" if len(t) == 9 else "double" if len(t) == 11 else
                 "transpose" if sorted(t) == sorted("Hutchinson") else "neighbour")
    assert seen == {"drop", "double", "transpose", "neighbour"}
    for _ in range(100):
        n = make_typo("9434765919", None, {}, rng, typed=False, same_length=True)  # character boxes keep length
        assert n and n.isdigit() and len(n) == 10
        assert make_typo("12/03/1984", None, {}, rng, typed=True) != "12/03/1984"
        datetime.strptime(make_typo("12/03/1984", None, {}, rng, typed=True), "%d/%m/%Y")
    assert make_typo("31/02/1984", None, {}, rng, typed=True) is None  # can't keep it a real date: leave it
    assert make_typo("M", None, {}, rng, typed=True) is None


def test_run_outputs_and_csv(setup):
    c, tid = setup["client"], setup["tid"]
    run = c.post("/api/runs", json={"template_ids": [tid], "count": 8, "seed": 5, "handwritten_pct": 100, **HEAVY}).json()
    for _ in range(240):
        r = c.get(f"/api/runs/{run['id']}").json()
        if r["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert r["status"] == "done", r["message"]
    out = Path(r["summary"]["output_dir"])
    gt = json.loads((out / "ground_truth.json").read_text())
    assert gt["settings"]["typos"] is True
    rows = list(csv.reader((out / "ground_truth.csv").open()))
    head = rows[0]
    assert head[:16] == ["set", "template", "file", "accuracy", "field", "type", "render", "page", "x0", "y0", "x1",
                         "y1", "value", "missing", "handwritten", "handwriting_font"]  # existing columns unchanged
    assert head[16:] == ["crossed_out", "overflow", "ink_x0", "ink_y0", "ink_x1", "ink_y1", "typo", "intended_value"]
    flat = [f for d in gt["documents"] for f in d["fields"]]
    assert len(rows) - 1 == len(flat)
    for row, f in zip(rows[1:], flat):
        rec = dict(zip(head, row))
        assert rec["field"] == f["name"]
        assert rec["crossed_out"] == f.get("crossed_out", "")
        assert rec["typo"] == ("TRUE" if f.get("typo") else "FALSE")
        assert rec["intended_value"] == f.get("intended_value", "")
        assert rec["overflow"] == ("TRUE" if f.get("overflow") else "FALSE")
        assert [rec[k] for k in ("ink_x0", "ink_y0", "ink_x1", "ink_y1")] == \
            ([str(v) for v in f["ink_rect"]] if f.get("overflow") else [""] * 4)
    # records.csv keeps the intended values, never the typo'd ones
    records = list(csv.DictReader((out / "records.csv").open()))
    typod = [(d["set"], f) for d in gt["documents"] for f in d["fields"] if f.get("typo") and f["render"] == "text"]
    assert typod
    for s, f in typod:
        assert records[s - 1][f["name"]] == f["intended_value"]


def test_preview_notes_effects(setup):
    c, tid = setup["client"], setup["tid"]
    pdf = c.post("/api/preview", json={"template_ids": [tid], "seed": 3, "handwritten_pct": 100, **HEAVY}).content
    text = pymupdf.open(stream=pdf, filetype="pdf")[0].get_text()
    assert "OCR stress" in text
    pdf = c.post("/api/preview", json={"template_ids": [tid], "seed": 3, "handwritten_pct": 100}).content
    assert "OCR stress" not in pymupdf.open(stream=pdf, filetype="pdf")[0].get_text()
