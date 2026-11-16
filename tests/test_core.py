"""Core tests: generators, label suggestions, PDF normalisation and an end-to-end linked run (no AI)."""

import json
import os
import random
import sys
import time
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    os.environ["DATACREATOR_DATA"] = str(data)
    os.environ["REQUESTY_API_KEY"] = ""  # never call the AI from tests
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient

    from app.main import app
    samples = tmp_path_factory.mktemp("samples")
    sys.argv = ["x", str(samples)]
    import runpy
    runpy.run_path(str(ROOT / "scripts" / "make_sample_forms.py"), run_name="__main__")
    c = TestClient(app)
    c.samples = samples
    return c


def _nhs_valid(n: str) -> bool:
    d = [int(c) for c in n.replace(" ", "")]
    c = 11 - sum(d[i] * (10 - i) for i in range(9)) % 11
    return (0 if c == 11 else c) == d[9]


def test_nhs_numbers_valid_and_in_test_range(client):
    from app.datatypes import nhs_number
    rng = random.Random(1)
    for _ in range(2000):
        n = nhs_number(rng)
        assert n.startswith("999") and _nhs_valid(n)


def test_generation_is_deterministic_and_consistent(client):
    from app.datatypes import FieldSpec, SetContext, all_types
    types = all_types()
    specs = {k: FieldSpec(k, t, p) for k, t, p in [
        ("a", "full_name", 1), ("b", "title", 1), ("c", "sex", 1), ("d", "full_name", 2),
        ("e", "relationship", 2), ("m1", "medication_name", 1), ("d1", "medication_dose", 1)]}
    c1 = SetContext(7, types, specs)
    v1 = [c1.field_value(k) for k in specs]
    ctx = SetContext(7, types, specs)
    v2 = [ctx.field_value(k) for k in specs]
    assert v1 == v2
    # asking for person 2 first still derives them from the patient
    c3 = SetContext(8, types, {"d": specs["d"], "a": specs["a"], "e": specs["e"]})
    nok, patient, rel = c3.field_value("d"), c3.field_value("a"), c3.field_value("e")
    assert rel != "Self" and c3.person(2).relationship == rel
    from app.uk_data import MEDICATIONS
    assert (ctx.values["m1"], ctx.values["d1"]) in MEDICATIONS  # name and dose pair up
    p = ctx.person(1)
    assert (p.title in ("Mr", "Dr", "Master")) == (p.sex == "Male") or p.title == "Dr"


def test_pattern_and_composite(client):
    from app.datatypes import TypeDef, gen_pattern, sample_values
    assert len(gen_pattern(random.Random(1), "MRN-####-??")) == 11
    assert gen_pattern(random.Random(1), r"A\#[XY]")[:2] == "A#"
    t = TypeDef("x", "x", "composite", "Custom", "text", config={"template": "{first_name} {last_name} {date_of_birth:%Y}"})
    for v in sample_values(t, 3):
        assert len(v.split()) == 3 and v.split()[-1].isdigit()


def test_label_suggestions(client):
    r = client.post("/api/templates", files={"file": ("ref.pdf", (client.samples / "referral_form.pdf").read_bytes())},
                    data={"name": "Referral"})
    tid = r.json()["id"]
    cases = {
        (240, 108, 555, 128): ("last_name", 1),
        (110, 168, 230, 188): ("date_of_birth", 1),
        (130, 388, 360, 408): ("full_name", 2),       # next of kin -> person 2
        (312, 171, 325, 184): ("sex", 1),             # 'Male' tick box
    }
    for rect, (typ, person) in cases.items():
        s = client.post(f"/api/templates/{tid}/suggest", json={"page": 0, "rect": list(rect)}).json()
        assert (s["type"], s["person"]) == (typ, person), s
    s = client.post(f"/api/templates/{tid}/suggest", json={"page": 1, "rect": [370, 105, 555, 125]}).json()
    assert s["name"] == "dose_2"


def test_rotated_pdf_overlay_lands_in_box(client):
    src = pymupdf.open(client.samples / "prescription.pdf")
    out = pymupdf.open()
    p = out.new_page(width=595, height=842)
    p.show_pdf_page(p.rect, src, 0, rotate=90)
    p.set_rotation(90)
    r = client.post("/api/templates", files={"file": ("rot.pdf", out.tobytes())}, data={"name": "Rot"})
    t = r.json()
    assert t["page_sizes"] == [[842.0, 595.0]]
    rect = [120, 76, 420, 96]
    s = client.post(f"/api/templates/{t['id']}/suggest", json={"page": 0, "rect": rect}).json()
    assert s["type"] == "full_name"
    client.put(f"/api/templates/{t['id']}", json={"name": "Rot", "fields": [
        {"name": "n", "type": "full_name", "page": 0, "rect": rect, "render": "text"}]})
    pdf = client.post(f"/api/templates/{t['id']}/preview", json={"handwritten_pct": 0, "seed": 1}).content
    page = pymupdf.open(stream=pdf)[0]
    words = [w for w in page.get_text("words") if pymupdf.Rect(rect).contains(pymupdf.Rect(w[:4]))]
    assert len(words) >= 2


def test_linked_run_end_to_end(client):
    ids = []
    for name in ("referral_form", "prescription"):
        r = client.post("/api/templates", files={"file": (f"{name}.pdf", (client.samples / f"{name}.pdf").read_bytes())},
                        data={"name": name})
        ids.append(r.json()["id"])
    f1 = [{"name": "patient", "type": "full_name", "page": 0, "rect": [100, 138, 555, 158]},
          {"name": "nhs", "type": "nhs_number", "page": 0, "rect": [110, 198, 270, 216], "render": "comb", "options": {"cells": 10}},
          {"name": "consent", "type": "yes_no", "page": 1, "rect": [222, 239, 235, 252], "render": "checkbox", "options": {"check_value": "Yes"}},
          {"name": "consent", "type": "yes_no", "page": 1, "rect": [268, 239, 281, 252], "render": "checkbox", "options": {"check_value": "No"}}]
    f2 = [{"name": "patient", "type": "full_name", "page": 0, "rect": [120, 76, 420, 96], "options": {"required": True}},
          {"name": "nhs", "type": "nhs_number", "page": 0, "rect": [490, 76, 640, 96]}]
    assert client.put(f"/api/templates/{ids[0]}", json={"fields": f1}).status_code == 200
    assert client.put(f"/api/templates/{ids[1]}", json={"fields": f2}).status_code == 200
    run = client.post("/api/runs", json={"template_ids": ids, "count": 5, "seed": 42, "missing_pct": 20,
                                         "scan": "light", "formats": ["pdf", "png", "tiff"], "layout": "folders"}).json()
    for _ in range(240):
        r = client.get(f"/api/runs/{run['id']}").json()
        if r["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert r["status"] == "done", r["message"]
    out = Path(r["summary"]["output_dir"])
    gt = json.loads((out / "ground_truth.json").read_text())
    assert len(gt["documents"]) == 10
    for s in range(1, 6):
        docs = [d for d in gt["documents"] if d["set"] == s]
        vals = [{f["name"]: f["source_value"] for f in d["fields"]} for d in docs]
        assert vals[0]["patient"] == vals[1]["patient"] and vals[0]["nhs"] == vals[1]["nhs"]
        ticks = [f["value"] for f in docs[0]["fields"] if f["name"] == "consent" and not f["missing"]]
        assert ticks.count(True) <= 1  # Yes/No boxes sharing a name are never both ticked
        req = [f for f in docs[1]["fields"] if f["name"] == "patient"][0]
        assert not req["missing"]
    assert (out / "set_0001" / "referral_form.tiff").exists()
    assert (out / "records.csv").read_text().count("\n") == 6
    assert client.get(f"/api/runs/{run['id']}/download").status_code == 200


def _upload(client, name):
    r = client.post("/api/templates", files={"file": (f"{name}.pdf", (client.samples / f"{name}.pdf").read_bytes())},
                    data={"name": name})
    return r.json()["id"]


def test_autodetect_geometry(client):
    from app import autodetect, templates
    expected = {"referral_form": [{"box": 17, "tickbox": 2, "comb": 1}, {"box": 12, "tickbox": 2}],
                # the six label-only cells of its table are dropped; their answer cells remain
                "visit_assessment": [{"box": 6, "tickbox": 7, "line": 4, "dots": 1, "lines": 1}],
                "delivery_confirmation_fillable": [{"widget": 5}]}
    for name, pages in expected.items():
        t = templates.get(_upload(client, name))
        doc = pymupdf.open(templates.path(t))
        widgets = templates.load_widgets(t)
        for pno, want in enumerate(pages):
            got = {}
            for c in autodetect.page_candidates(doc[pno], [w for w in widgets if w["page"] == pno]):
                got[c["kind"]] = got.get(c["kind"], 0) + 1
            assert got == want, (name, pno, got)


def test_autodetect_without_ai_uses_labels_and_skips_existing(client):
    tid = _upload(client, "referral_form")
    existing = [{"name": "surname", "type": "last_name", "page": 0, "rect": [240, 108, 555, 128]}]
    r = client.post(f"/api/templates/{tid}/autodetect", json={"existing": existing, "use_ai": False}).json()
    assert r["ai_used"] is False
    def at(rect):  # detected edges sit ~2pt inside the drawn lines (clearance from borders)
        return next((p for p in r["proposals"] if all(abs(a - b) <= 2.5 for a, b in zip(p["rect"], rect))), None)
    assert at([240, 108, 555, 128]) is None                         # already marked up: not proposed again
    assert at([110, 168, 230, 188])["type"] == "date_of_birth"
    comb = [p for p in r["proposals"] if p["render"] == "comb"][0]
    assert comb["type"] == "nhs_number" and comb["options"]["cells"] == 10
    sexes = sorted(p["options"].get("check_value") for p in r["proposals"] if p["type"] == "sex")
    assert sexes == ["Female", "Male"]                                # tick boxes keep their meaning without AI
    names = [p["name"] for p in r["proposals"]]
    assert "surname" not in names or names.count("surname") == 1


def test_free_area_rules_for_word_style_cells(client):
    from app.autodetect import _free_area

    def w(x0, x1, text, y0=100, y1=110, line=0, no=0):
        return (x0, y0, x1, y1, text, 0, line, no)

    cell = [40, 95, 300, 115]
    # "Weight: ________ kg": the answer goes in the gap, not after "kg"
    free, label, ph = _free_area(cell, [w(45, 80, "Weight:"), w(270, 285, "kg", no=1)])
    assert label == "Weight: kg" and ph == "" and 80 < free[0] < 90 and 260 < free[2] < 270
    # a Word placeholder ending the cell is the answer area
    free, label, ph = _free_area(cell, [w(45, 90, "Diagnosis:"), w(150, 180, "Please", no=1), w(182, 210, "select.", no=2)])
    assert ph.lower().startswith("please select") and free[0] < 150
    # ...but the same words inside a longer label are just a label
    free, label, ph = _free_area(cell, [w(45, 80, "Options"), w(82, 110, "please", no=1), w(112, 140, "select", no=2),
                                         w(142, 160, "all", no=3), w(162, 200, "required:", no=4)])
    assert ph == ""
    # a label at the top of a tall cell leaves the space below
    tall = [40, 95, 300, 180]
    free, label, ph = _free_area(tall, [w(45, 120, "Prescriber"), w(122, 160, "Notes:", no=1)])
    assert free[1] > 110 and free[3] == 179.5
