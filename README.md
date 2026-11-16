<img src="assets/icon.svg" width="64" height="64" alt="">

# DataCreator

A local web app that turns blank PDF forms into realistic, **synthetic** filled-in documents. Use them to test OCR and document extraction, train staff, and populate test environments.

All data is generated, so no real patient data goes in or comes out. People are fictional. Organisations, places and medicines may be real.

Created in [T3 Code](https://t3.codes).

## Install (dev)

Needs Linux or macOS with `git`, `curl` and Python 3.11+. This one command clones the repo into `./datacreator`, installs the app and test tools into `.venv`, creates `.env`, writes sample forms and runs the tests:

```bash
curl -fsSL https://raw.githubusercontent.com/AlexWhitehouse/datacreator/main/install.sh | bash
```

Set `DATACREATOR_DIR=path` to clone somewhere else, or `SKIP_TESTS=1` to skip the tests, for example `curl -fsSL … | DATACREATOR_DIR=~/src/datacreator bash`. Running it again updates an existing clone.

## Quick start

```bash
./run.sh            # first run sets up .venv and installs requirements
# open http://127.0.0.1:8765
```

AI data types are optional. To enable them, copy `.env.example` to `.env` and set `REQUESTY_API_KEY`. Calls go through the Requesty EU gateway to `vertex/claude-haiku-5-5@eu`.

Want something to try straight away? This writes four blank sample forms to `samples/`: a 2-page referral, a prescription, a visit assessment with a table grid, underlines and tick boxes, and a fillable PDF form:

```bash
.venv/bin/python scripts/make_sample_forms.py samples
```

### Running as a service on a tailnet

To reach it from other machines, run it as a systemd user service and publish it to your tailnet only (not Funnel) with `tailscale serve`:

- Service: create `~/.config/systemd/user/datacreator.service` with `ExecStart=/path/to/datacreator/run.sh` and `Restart=on-failure`, then `systemctl --user enable --now datacreator` (and `loginctl enable-linger $USER` so it starts at boot). Logs: `journalctl --user -u datacreator -f`
- Proxy: `sudo tailscale serve --bg --https=8765 http://127.0.0.1:8765`, then open `https://<machine>.<tailnet>.ts.net:8765`. To remove it: `sudo tailscale serve --https=8765 off`

The app itself only listens on 127.0.0.1. There's no login, so anyone on your tailnet can use it.

## How it works

1. **Templates.** Upload a blank PDF; multi-page and rotated pages are fine. Drag boxes where data should go. The printed label next to each box is read to suggest a field name, a data type and a drawing style. For example, "Surname" becomes `last_name`, "Next of kin" becomes a full name for person 2, a small box next to "Female" becomes a tick box, and "Dose" on the row for "Medication 2" is named `dose_2`. Each field has:
   - **Field name.** Fields with the same name share one value, within a template and across templates in a linked run. Give the Yes and No boxes of one question the same name so only one gets ticked.
   - **Data type.** Choose from the built-in library or your own types.
   - **Whose data.** Each record has a patient (person 1), a relative (person 2: a spouse who shares the surname and address, a son, a friend…) and unrelated adults (persons 3–8), for clinicians, contacts and so on.
   - **Draw as.** Single line, multi-line, character boxes, tick box or signature.
   - **Format.** Date format, case and other transforms, prefix/suffix, font size, alignment, the value that triggers a tick, required, and a per-field missing % override.
   **Auto-detect fields** (editor toolbar) marks up a whole template for you, in two stages:
   1. *Finding the boxes (no AI).* Input areas come from the page itself:
      - table rules, read from the PDF's vector drawings, including faint grey and white-on-shading borders from Word/LibreOffice
      - the same rules found on the rendered image, using local contrast so lines on shaded cells count; this is what scans rely on
      - rows of character boxes, merged into one field
      - drawn tick boxes and typed ☐ / □ characters
      - underlines, dotted leaders, and stacked note lines
      - wide blanks inside a sentence ("deliver every ______ weeks")
      - Word placeholders such as "Choose an item.", "Please select." and "Click or tap to enter a date."
      - the positions and names of any fillable form fields the original PDF had (captured at upload)

      Cells that hold a printed label keep only their empty part: right of the label, the gap in "Weight: ___ kg", or below it. Label-only cells, section heading bars and logos are dropped.
   2. *Labelling (AI, one call per page).* The model gets the page image with numbered boxes, the text near each box, your full list of data types, and the field names already used in other templates, so it can reuse them for linking. It decides, for each box:
      - whether to skip it
      - the field name and data type
      - whose data it is
      - how to draw it
      - the value that ticks a box, the number of character boxes, the date format, and upper case when the form asks for BLOCK CAPITALS

      Each distinct person on a form gets their own person number, so a clinician's name, signature, registration number, phone and work email agree. Busy pages are split into parallel calls.

      Where nothing in the type list fits, it drafts a new type (a pick-list, pattern or AI text). Drafts are **suggestions only**: you review each one and create or dismiss it. It also lists questions printed on the page that have no box.

   Suggestions appear as dashed boxes with Accept/Reject, one at a time or all at once, and nothing is saved until you accept and save. Running it again leaves accepted fields alone. Without AI it falls back to matching printed labels. A simple form takes about 5 seconds; a dense one-page Word form with 60+ boxes takes about 20.

   Fields placed over a Word placeholder get **Cover printed text in this box**, so a written value replaces "Choose an item." in the cell's own background colour. If the field is left blank, the placeholder stays, as on a real form.
2. **Data types.** Around 50 built-in types draw from a consistent synthetic person per record: title, sex, names, DOB and age agree with each other; the postcode, town and phone area code agree; NHS numbers are valid and in the 999 test range; medication names pair with their doses. You can also create your own:
   - pick-list (paste values or have AI write them)
   - pattern (`MRN-####-??`)
   - number range
   - date range (absolute, relative to today, or relative to another field)
   - combined (`{title} {first_name} {last_name}`)
   - yes/no %
   - **AI-generated text**, in one of two modes:
     - *Live:* written per record and consistent with it. One call per set covers all of that set's AI fields.
     - *Pool:* generated once and then sampled, which is free and repeatable.
3. **Generate.**
   - **Templates:** pick one, or several for *linked sets*. For example, 50 sets × 2 templates gives 100 documents in pairs about the same person.
   - **Data:** set the number of sets, the missing-field %, the age range, and an optional seed.
   - **Handwriting:** set the % handwritten, applied per document or per field. Each document gets one "writer", meaning one handwriting font, pen colour and slant.
   - **Output:** choose a scan effect (none, light, medium, heavy, fax), colour/greyscale/black & white, PDF/PNG/TIFF, DPI, and layout (folder per set, flat, or merged PDF per set).
4. **Runs.** Progress, cancel, and a zip download. Every run writes:
   - `ground_truth.json`: per document, every field with its page, box (PDF points, top-left origin), the drawn value, whether it was missing or handwritten, and the writer style.
   - `ground_truth.csv`: the same, one row per field per document.
   - `records.csv`: one row per set with every field's underlying value, handy for loading test environments.

Same seed + same settings = the same output, apart from live AI text.

## Project layout

```
app/
  main.py        FastAPI routes; serves static/
  datatypes.py   built-in library, custom kinds, per-set SetContext (person records, linking)
  templates.py   PDF upload normalisation (+ fillable-field capture), page images, label → field suggestions
  autodetect.py  automatic mark-up: geometric box detection + AI labelling
  render.py      typed text, handwriting, character boxes, ticks, signatures
  scan.py        rasterising + scan/fax simulation
  runs.py        run engine (AI fill-in, parallel rendering, outputs, ground truth)
  ai.py          Requesty / OpenAI-compatible client
  uk_data.py     towns/postcodes/dialling codes, medications, conditions…
  fonts/         handwriting + signature fonts from Google Fonts (SIL OFL / Apache 2.0; see fonts/licenses/)
static/          single-page front end (no build step)
scripts/make_sample_forms.py
tests/
data/            (git-ignored) SQLite DB, uploaded templates, run outputs
```

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q tests
```

The tests never call the AI.

## Notes and limits (v1)

- Label suggestions need a text layer. On scanned (image-only) templates, name the fields yourself.
- Rendering runs in parallel worker processes, roughly 0.1s per document plus about 0.5s per page for the scan effect at 200 dpi.
- Not in v1: crossings-out, text spilling outside boxes, typos, sharing templates between users.
