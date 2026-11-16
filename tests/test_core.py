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
    # conftest.py has already pointed DATACREATOR_DATA at a temp dir and blanked the AI key
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app
    assert config.DATA_DIR == Path(os.environ["DATACREATOR_DATA"]) and config.DATA_DIR != ROOT / "data"
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


def test_combined_run_puts_each_set_in_one_document(client):
    ids = [_upload(client, "referral_form"), _upload(client, "prescription")]  # 2 pages + 1 page
    assert client.put(f"/api/templates/{ids[0]}", json={"fields": [
        {"name": "patient", "type": "full_name", "page": 1, "rect": [100, 138, 555, 158], "options": {"required": True}}]}).status_code == 200
    assert client.put(f"/api/templates/{ids[1]}", json={"fields": [
        {"name": "patient", "type": "full_name", "page": 0, "rect": [120, 76, 420, 96], "options": {"required": True}}]}).status_code == 200
    # prescription first: the order of template_ids is the page order
    order = [ids[1], ids[0]]
    pdf = client.post("/api/preview", json={"template_ids": order, "combine": True, "seed": 3}).content
    assert pymupdf.open(stream=pdf).page_count == 3
    run = client.post("/api/runs", json={"template_ids": order, "count": 3, "seed": 9, "combine": True,
                                         "layout": "folders", "formats": ["pdf", "png"]}).json()
    for _ in range(240):
        r = client.get(f"/api/runs/{run['id']}").json()
        if r["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert r["status"] == "done", r["message"]
    assert r["total"] == 3 and r["summary"]["documents"] == 3 and r["summary"]["forms"] == 6
    out = Path(r["summary"]["output_dir"])
    doc = pymupdf.open(out / "set_0001" / "set_0001.pdf")
    assert doc.page_count == 3 and len(list((out / "set_0001").glob("*.png"))) == 3
    gt = json.loads((out / "ground_truth.json").read_text())
    s1 = [d for d in gt["documents"] if d["set"] == 1]
    assert [d["first_page"] for d in s1] == [1, 2] and s1[0]["files"] == s1[1]["files"]
    rx, ref = ({f["name"]: f for f in d["fields"]} for d in s1)
    assert rx["patient"]["value"] == ref["patient"]["value"]          # same patient on both forms
    assert (rx["patient"]["merged_page"], ref["patient"]["merged_page"]) == (1, 3)
    words = " ".join(w[4] for w in doc[2].get_text("words"))
    assert ref["patient"]["value"].split()[-1] in words               # drawn on the referral's 2nd page


def _wait(client, run_id):
    for _ in range(240):
        r = client.get(f"/api/runs/{run_id}").json()
        if r["status"] in ("done", "failed"):
            return r
        time.sleep(0.5)
    return r


def _referral_with_fields(client):
    tid = _upload(client, "referral_form")
    rows = [("surname", "last_name", [240, 108, 555, 128]), ("forename", "first_name", [100, 138, 555, 158]),
            ("dob", "date_of_birth", [110, 168, 230, 188]), ("address", "address_line_1", [100, 228, 555, 248]),
            ("postcode", "postcode", [100, 258, 230, 278]), ("phone", "phone_mobile", [300, 258, 555, 278]),
            ("nok", "full_name", [130, 388, 360, 408])]
    fields = [{"name": n, "type": t, "page": 0, "rect": r} for n, t, r in rows]
    fields.append({"name": "nhs", "type": "nhs_number", "page": 0, "rect": [110, 198, 270, 216], "options": {"required": True}})
    fields += [{"name": "consent", "type": "yes_no", "page": 1, "rect": r, "render": "checkbox", "options": {"check_value": v}}
               for r, v in (([222, 239, 235, 252], "Yes"), ([268, 239, 281, 252], "No"))]
    assert client.put(f"/api/templates/{tid}", json={"fields": fields}).status_code == 200
    return tid


def test_settings_missing_range_and_duplicate_counts(client):
    from app.runs import document_count, normalise_settings
    s = normalise_settings({"template_ids": [1], "missing_pct": 25})
    assert (s["missing_min"], s["missing_max"]) == (25, 25)                      # older single rate
    s = normalise_settings({"template_ids": [1, 2], "missing_min": 60, "missing_max": 10, "count": 10,
                            "duplicate": True, "duplicate_pct": 25, "duplicate_levels": [50, "100", 50, 0, 120]})
    assert (s["missing_min"], s["missing_max"]) == (10, 60)
    assert s["duplicate_levels"] == [100, 50, 0]
    assert document_count(s) == (10 + 3 * 2) * 2                                  # 3 of 10 sets × 3 versions, 2 templates
    assert document_count({**s, "combine": True}) == 10 + 3 * 2


def test_missing_range_varies_per_document(client):
    tid = _referral_with_fields(client)
    r = _wait(client, client.post("/api/runs", json={"template_ids": [tid], "count": 24, "seed": 5,
                                                     "missing_min": 0, "missing_max": 100}).json()["id"])
    assert r["status"] == "done", r["message"]
    gt = json.loads((Path(r["summary"]["output_dir"]) / "ground_truth.json").read_text())
    blanks = [d["fields_missing"] for d in gt["documents"]]
    assert min(blanks) <= 2 and max(blanks) >= 6                                  # some nearly full, some sparse
    for d in gt["documents"]:
        f = {x["name"]: x for x in d["fields"] if x["name"] != "consent"}
        assert not f["nhs"]["missing"]                                             # required
        ticks = [x["missing"] for x in d["fields"] if x["name"] == "consent"]
        assert len(set(ticks)) == 1                                                # a question's boxes go blank together


def test_duplicates_at_accuracy_levels(client):
    tid = _referral_with_fields(client)
    r = _wait(client, client.post("/api/runs", json={"template_ids": [tid], "count": 4, "seed": 11, "layout": "folders",
                                                     "duplicate": True, "duplicate_pct": 50,
                                                     "duplicate_levels": [100, 70, 30, 0]}).json()["id"])
    assert r["status"] == "done", r["message"]
    assert r["total"] == 4 + 2 * 3 and r["summary"]["duplicated_sets"] == 2
    out = Path(r["summary"]["output_dir"])
    gt = json.loads((out / "ground_truth.json").read_text())
    assert len(gt["documents"]) == 10
    dup_sets = {d["set"] for d in gt["documents"] if d["accuracy"] is not None}
    assert len(dup_sets) == 2
    for st in dup_sets:
        docs = sorted((d for d in gt["documents"] if d["set"] == st), key=lambda d: -d["accuracy"])
        assert [d["accuracy"] for d in docs] == [100, 70, 30, 0]
        assert all((out / f).exists() for d in docs for f in d["files"]) and docs[1]["files"][0].endswith("_acc70.pdf")
        blank = [{x["name"] for x in d["fields"] if x["missing"]} for d in docs]
        assert blank[0] == set() and blank[3] == {n for n in (x["name"] for x in docs[0]["fields"]) if n != "nhs"}
        assert blank[0] <= blank[1] <= blank[2] <= blank[3]                        # nested: each level blanks more
        assert len({d["style"]["handwriting_font"] for d in docs}) == 1             # same writer
        for d in docs[1:]:                                                          # filled fields drawn identically
            assert all(docs[0]["fields"][i]["value"] == x["value"] for i, x in enumerate(d["fields"]) if not x["missing"])
    plain = [d for d in gt["documents"] if d["accuracy"] is None]
    assert len(plain) == 2 and not any(f.endswith("_acc100.pdf") for d in plain for f in d["files"])
