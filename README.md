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

AI data types are optional. To enable them, open **Settings** (the gear icon, top right), add your Requesty API key and click **Test connection**. Calls go through the Requesty EU gateway to `vertex/claude-haiku-5-5@eu`. The same page sets the base URL, model, parallel AI calls and reasoning effort (keep it at "none": otherwise hidden reasoning can use up the token budget and replies come back empty). Changes apply straight away, without a restart.

Settings are saved to `.env` in the app folder (mode 0600). The key is never sent back to the browser: the page only shows its last 4 characters. You can still edit `.env` by hand (see `.env.example`); restart the app afterwards. Set `DATACREATOR_ENV_FILE` to keep it somewhere else.

Light, dark or system theme: use the sun/moon button in the header, or **Settings › Appearance**. The choice is saved in your browser.

Want something to try straight away? This writes four blank sample forms to `samples/`: a 2-page referral, a prescription, a visit assessment with a table grid, underlines and tick boxes, and a fillable PDF form:

```bash
.venv/bin/python scripts/make_sample_forms.py samples
```

### Updating

When a newer version is on GitHub, an **Update** button appears in the header (it checks on page load and every 30 minutes; click the small refresh icon to check now). It lists the new commits, then fast-forwards the clone, reinstalls `requirements.txt` if it changed (rolling back if that fails), restarts the server in place and reloads the page. Your templates, data types, runs and `.env` are untouched.

It won't update while a run is in progress, if this clone has uncommitted changes to tracked files, or if it has local commits that aren't on GitHub. In those cases, update from a terminal with `git pull` and restart. Re-running the install command also updates an existing clone.

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
   **Auto-detect** (editor toolbar) marks up a whole template for you, in two stages:
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

   Suggestions appear as dashed boxes. Review them one by one (<kbd>A</kbd> accepts, <kbd>X</kbd> rejects, and the editor moves on to the next suggestion, across pages) or accept them all. Nothing is saved until you accept and save. Running it again leaves accepted fields alone. Without AI it falls back to matching printed labels. A simple form takes about 5 seconds; a dense one-page Word form with 60+ boxes takes about 20.

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
     With several templates, choose what **each set becomes**: separate documents (one file per template), or **one combined document** holding every form in the order you set. For example, a registration form plus a prescription for the same patient in one PDF, so 50 sets give 50 documents. The ground truth records each form's `first_page` and each field's `merged_page` in the combined file.
   - **Data:** set the number of sets, the age range, an optional seed, and a **missing-fields range**. Each document picks its own rate in that range (e.g. 0–60%), so a batch mixes nearly complete forms with sparse ones. Blanks are chosen at random per document. Tick boxes for one question go blank together, and required fields are always filled.
   - **Duplicates:** copy some or all sets at several **accuracy levels** (% of fields filled in), e.g. `100, 90, 70, 50, 30, 10` gives a clean copy plus five progressively emptier ones. Choose the % of sets to duplicate (picked at random) and the levels. The copies share the record, writer, handwriting and scan, and each lower level blanks the fields of the level above plus more, so they differ only in missing data. Files end in `_acc90` etc. Sets that aren't duplicated use the missing-fields range.
   - **Realism & OCR stress:**
     - *Handwriting:* set the % handwritten, applied per document or per field. Each document gets one "writer", meaning one handwriting font, pen colour and slant.
     - *Scan:* a scan effect (none, light, medium, heavy, fax) and colour, greyscale or black & white.
     - *OCR stress:* three effects, each with its own switch and %. All are off by default, and **Off / Light / Heavy** fills in all three at once.
       - **Crossings-out:** a wrong or half-written value struck through with one line, two lines or a scribble, then the right value, in the same hand and ink. Only handwritten single-line and multi-line fields get them. Typed fields don't, because a hand-struck correction on a typed form is a different kind of document, and neither do character boxes. The % is an average: each document draws its own rate around it, so some writers correct themselves more than others. If the correction can't fit in the box, it isn't made.
       - **Writing outside the box:** in this % of handwritten text fields, the writing isn't shrunk to fit. It runs past the right edge or sits low across the bottom edge (multi-line fields can run a line or two below), and it always stays on the page.
       - **Typos:** a swapped, dropped or doubled letter, a neighbouring key (typed text only), or a digit slip in numbers. Character boxes only get slips that keep the length. A date only ever becomes another real date in the same format (a day or month out by one, the last two year digits swapped, or day and month swapped), so it still parses. A date-like string that can't be read as a date gets no typo.
       - These never touch tick boxes or signatures. A field that's crossed out doesn't also get a typo. **Preview** lists the affected fields in the bottom margin of each page.
   - **Output:** PDF/PNG/TIFF, DPI, and layout (folder per set, or all in one folder).
4. **Runs.** Live progress (the Runs tab shows a badge while anything is running, and you get a notice with a download link when a run finishes), cancel, a zip download, and **Reuse settings** to start a new run with the same settings, either with a new seed or the same seed to reproduce it exactly. Every run writes:
   - `ground_truth.json`: per document, every field with its page, box (PDF points, top-left origin), the drawn value, whether it was missing or handwritten, and the writer style. Each document also records its `missing_rate`, `fields_missing` and, for duplicates, its `accuracy`.
     OCR stress adds these to a field when the effect is on the page:
     - `crossed_out`: the struck text. `value` stays the final, intended value.
     - `overflow: true` and `ink_rect`: the bounding box of the ink actually drawn, in the same PDF-point, top-left-origin convention as the field's `rect`, which stays the field's box.
     - `typo: true` and `intended_value`: `value` is what's actually written on the page, typo included, because that's what OCR should read. `intended_value` is the correct text.

     With crossings-out on, each document also records its `crossout_rate`.
   - `ground_truth.csv`: the same, one row per field per document. The OCR stress columns (`crossed_out`, `overflow`, `ink_x0`…`ink_y1`, `typo`, `intended_value`) come after the original columns, which keep their order.
   - `records.csv`: one row per set with every field's underlying value, handy for loading test environments. It always holds the intended values, never typos.

Same seed + same settings = the same output, apart from live AI text.

## Project layout

```
app/
  main.py        FastAPI routes; serves static/
  datatypes.py   built-in library, custom kinds, per-set SetContext (person records, linking)
  templates.py   PDF upload normalisation (+ fillable-field capture), page images, label → field suggestions
  autodetect.py  automatic mark-up: geometric box detection + AI labelling
  render.py      typed text, handwriting, character boxes, ticks, signatures
  stress.py      OCR stress: crossings-out, writing outside boxes, typos (decisions + ground truth)
  scan.py        rasterising + scan/fax simulation
  runs.py        run engine (AI fill-in, parallel rendering, outputs, ground truth)
  updater.py     self-update from GitHub (fast-forward, reinstall requirements, restart in place)
  ai.py          Requesty / OpenAI-compatible client
  uk_data.py     towns/postcodes/dialling codes, medications, conditions…
  fonts/         handwriting + signature fonts from Google Fonts (SIL OFL / Apache 2.0; see fonts/licenses/)
static/          single-page front end: app.js (router) + js/ui.js, js/state.js, js/views/*.js (plain ES modules, no build step)
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
- Not in v1: sharing templates between users.
