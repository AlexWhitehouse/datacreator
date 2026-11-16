"""Create blank sample forms for trying DataCreator: a 2-page referral, a prescription, a visit
assessment (table grid, underlines, dotted leaders, tick boxes) and a fillable PDF form with no drawn boxes.

Usage: .venv/bin/python scripts/make_sample_forms.py [output_dir]
"""

import sys
from pathlib import Path

import pymupdf

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
GREY = (0.35, 0.35, 0.35)


def label(page, x, y, text, size=9, bold=False):
    page.insert_text((x, y), text, fontsize=size, fontname="hebo" if bold else "helv", color=(0, 0, 0))


def box(page, rect, width=0.6):
    page.draw_rect(pymupdf.Rect(rect), color=GREY, width=width)


def comb(page, x, y, cells, cw=16, h=18):
    for i in range(cells):
        box(page, (x + i * cw, y, x + (i + 1) * cw, y + h))


def heading(page, y, text):
    page.draw_rect(pymupdf.Rect(40, y, 555, y + 16), color=None, fill=(0.85, 0.9, 0.95))
    label(page, 46, y + 12, text, 10, True)


def referral():
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    label(p, 40, 50, "Homecare Patient Referral Form", 16, True)
    label(p, 40, 66, "Please complete in BLOCK CAPITALS", 8)
    heading(p, 85, "Patient details")
    label(p, 40, 122, "Title"); box(p, (100, 108, 160, 128))
    label(p, 180, 122, "Surname"); box(p, (240, 108, 555, 128))
    label(p, 40, 152, "Forename(s)"); box(p, (100, 138, 555, 158))
    label(p, 40, 182, "Date of birth"); box(p, (110, 168, 230, 188))
    label(p, 250, 182, "Sex:"); label(p, 285, 182, "Male"); box(p, (312, 171, 325, 184))
    label(p, 340, 182, "Female"); box(p, (375, 171, 388, 184))
    label(p, 40, 212, "NHS number"); comb(p, 110, 198, 10)
    label(p, 40, 242, "Address"); box(p, (110, 230, 555, 290))
    label(p, 40, 312, "Postcode"); box(p, (110, 298, 230, 318))
    label(p, 250, 312, "Telephone"); box(p, (310, 298, 555, 318))
    label(p, 40, 342, "Email"); box(p, (110, 328, 555, 348))
    heading(p, 365, "Next of kin")
    label(p, 40, 402, "Next of kin name"); box(p, (130, 388, 360, 408))
    label(p, 370, 402, "Relationship"); box(p, (435, 388, 555, 408))
    label(p, 40, 432, "Next of kin telephone"); box(p, (150, 418, 360, 438))
    heading(p, 455, "GP details")
    label(p, 40, 492, "GP name"); box(p, (110, 478, 330, 498))
    label(p, 340, 492, "Practice code"); box(p, (410, 478, 555, 498))
    label(p, 40, 522, "GP practice"); box(p, (110, 508, 555, 528))
    heading(p, 545, "Clinical information")
    label(p, 40, 582, "Diagnosis"); box(p, (110, 568, 555, 588))
    label(p, 40, 612, "Allergies"); box(p, (110, 598, 555, 618))
    label(p, 40, 640, "Reason for referral"); box(p, (40, 646, 555, 760))

    p2 = doc.new_page(width=595, height=842)
    heading(p2, 40, "Current medication")
    for i in range(4):
        y = 75 + i * 30
        label(p2, 40, y + 14, f"Medication {i + 1}"); box(p2, (120, y, 330, y + 20))
        label(p2, 340, y + 14, "Dose"); box(p2, (370, y, 555, y + 20))
    heading(p2, 210, "Declaration")
    label(p2, 40, 250, "Consent obtained from patient:"); label(p2, 200, 250, "Yes"); box(p2, (222, 239, 235, 252))
    label(p2, 250, 250, "No"); box(p2, (268, 239, 281, 252))
    label(p2, 40, 290, "Referrer"); box(p2, (110, 276, 330, 296))
    label(p2, 340, 290, "GMC number"); box(p2, (410, 276, 555, 296))
    label(p2, 40, 330, "Signature"); box(p2, (110, 306, 330, 340))
    label(p2, 340, 330, "Date"); box(p2, (370, 316, 480, 336))
    return doc


def prescription():
    doc = pymupdf.open()
    # Landscape page.
    p = doc.new_page(width=842, height=595)
    label(p, 40, 50, "Homecare Prescription", 16, True)
    label(p, 40, 90, "Patient name"); box(p, (120, 76, 420, 96))
    label(p, 440, 90, "NHS no."); box(p, (490, 76, 640, 96))
    label(p, 40, 120, "D.O.B."); box(p, (120, 106, 240, 126))
    label(p, 260, 120, "Hospital number"); box(p, (350, 106, 500, 126))
    label(p, 40, 150, "Address"); box(p, (120, 136, 640, 156))
    label(p, 40, 180, "Allergies"); box(p, (120, 166, 640, 186))
    for i in range(3):
        y = 210 + i * 30
        label(p, 40, y + 14, f"Drug {i + 1}"); box(p, (120, y, 400, y + 20))
        label(p, 410, y + 14, "Dose"); box(p, (450, y, 800, y + 20))
    label(p, 40, 340, "Prescriber"); box(p, (120, 326, 400, 346))
    label(p, 40, 380, "Signature"); box(p, (120, 356, 400, 390))
    label(p, 420, 380, "Date"); box(p, (450, 366, 570, 386))
    return doc


def assessment():
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    label(p, 40, 50, "Home Visit Assessment", 16, True)
    label(p, 40, 66, "Please complete in BLOCK CAPITALS", 8)
    # table grid drawn with lines: label column + answer column
    rows = ["Surname", "First name", "Date of birth (DD/MM/YYYY)", "NHS number", "Postcode", "Telephone"]
    x0, xm, x1, y0, rh = 40, 210, 555, 85, 22
    for i in range(len(rows) + 1):
        p.draw_line((x0, y0 + i * rh), (x1, y0 + i * rh), color=GREY, width=0.6)
    for x in (x0, xm, x1):
        p.draw_line((x, y0), (x, y0 + len(rows) * rh), color=GREY, width=0.6)
    for i, r in enumerate(rows):
        label(p, x0 + 5, y0 + i * rh + 15, r)
    y = y0 + len(rows) * rh + 30
    label(p, 40, y, "Title:")
    for i, t in enumerate(["Mr", "Mrs", "Miss", "Ms"]):
        label(p, 80 + i * 60, y, t); box(p, (80 + i * 60 + 24, y - 10, 80 + i * 60 + 36, y + 2))
    y += 34
    label(p, 40, y, "Visit date: ____________________")
    label(p, 300, y, "Nurse name"); p.draw_line((360, y + 2), (555, y + 2), color=GREY, width=0.6)
    y += 30
    label(p, 40, y, "Ward / team ................................................................................")
    y += 30
    label(p, 40, y, "Mobility:"); label(p, 100, y, "Independent"); box(p, (160, y - 10, 172, y + 2))
    label(p, 190, y, "With aid"); box(p, (232, y - 10, 244, y + 2))
    label(p, 262, y, "Hoist"); box(p, (292, y - 10, 304, y + 2))
    y += 30
    label(p, 40, y, "Visit notes")
    for i in range(4):
        p.draw_line((40, y + 22 + i * 20), (555, y + 22 + i * 20), color=GREY, width=0.6)
    y += 22 + 4 * 20 + 30
    label(p, 40, y, "Signature"); p.draw_line((100, y + 2), (300, y + 2), color=GREY, width=0.6)
    label(p, 320, y, "Date"); p.draw_line((350, y + 2), (480, y + 2), color=GREY, width=0.6)
    # a frame around the whole page content (should be ignored)
    p.draw_rect(pymupdf.Rect(30, 30, 565, y + 20), color=GREY, width=0.4)
    return doc


def fillable():
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    label(p, 40, 50, "Delivery Confirmation (fillable)", 16, True)
    items = [("Patient name", "patient_name", 70), ("Delivery address", "delivery_address", 100),
             ("Delivery date", "delivery_date", 130), ("Received by", "received_by", 160)]
    for text, name, y in items:
        label(p, 40, y + 13, text)
        w = pymupdf.Widget(); w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT; w.field_name = name
        w.rect = pymupdf.Rect(150, y, 450, y + 18); p.add_widget(w)
    label(p, 40, 203, "Cold chain maintained")
    w = pymupdf.Widget(); w.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX; w.field_name = "cold_chain"
    w.rect = pymupdf.Rect(170, 192, 182, 204); p.add_widget(w)
    return doc


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    referral().save(OUT / "referral_form.pdf")
    rx = prescription()
    rx.save(OUT / "prescription.pdf")
    assessment().save(OUT / "visit_assessment.pdf")
    fillable().save(OUT / "delivery_confirmation_fillable.pdf")
    print(f"Wrote 4 sample forms to {OUT}/")
