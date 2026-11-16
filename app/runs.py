"""Run engine: generate N linked sets across one or more templates, render, and write ground truth."""

from __future__ import annotations

import csv
import json
import os
import random
import re
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pymupdf

from . import ai, config, db, render, scan, stress, templates
from .config import RUNS_DIR
from .datatypes import AI_PENDING, FieldSpec, GenerationError, SetContext, all_types, format_value, raw_json

DEFAULTS = {
    "template_ids": [], "count": 10, "seed": None, "missing_min": 0.0, "missing_max": 0.0, "handwritten_pct": 50.0,
    "handwriting_mode": "document", "fonts": [], "ink": "mixed", "typed_font": "helv",
    "scan": "none", "colour": "colour", "dpi": 200, "formats": ["pdf"], "layout": "folders",
    "combine": False, "age_min": 18, "age_max": 95,
    # duplicates: copies of a set at several accuracy levels (% of fields filled in)
    "duplicate": False, "duplicate_pct": 100.0, "duplicate_levels": [100, 90, 70, 50, 30, 10],
    # OCR stress (off unless switched on, so older saved settings keep their output): crossings-out, writing that
    # spills out of its box, typos. Each has an on/off switch and a % (see app/stress.py).
    **stress.DEFAULTS,
}
FORMATS = ("pdf", "png", "tiff")
LAYOUTS = ("folders", "flat")
MAX_COUNT = 10000
MAX_DOCUMENTS = 50000
MAX_LEVELS = 20
RENDER_WORKERS = max(1, min(8, (os.cpu_count() or 2) - 1))


def normalise_settings(s: dict) -> dict:
    if s.get("missing_pct") not in (None, "") and s.get("missing_min") is None and s.get("missing_max") is None:
        s = {**s, "missing_min": s["missing_pct"], "missing_max": s["missing_pct"]}  # older single-rate settings
    out = {**DEFAULTS, **{k: v for k, v in s.items() if k in DEFAULTS and v is not None}}
    out["template_ids"] = [int(t) for t in out["template_ids"]]
    if not out["template_ids"]:
        raise ValueError("Select at least one template")
    out["count"] = int(out["count"])
    if not 1 <= out["count"] <= MAX_COUNT:
        raise ValueError(f"Number of sets must be between 1 and {MAX_COUNT}")
    out["seed"] = int(out["seed"]) if out.get("seed") not in (None, "") else random.randint(1, 2**31 - 1)
    for k in ("missing_min", "missing_max", "handwritten_pct", "duplicate_pct"):
        out[k] = max(0.0, min(100.0, float(out[k])))
    out["missing_min"], out["missing_max"] = sorted((out["missing_min"], out["missing_max"]))
    out["duplicate"] = bool(out["duplicate"])
    levels = []
    for v in out["duplicate_levels"] if isinstance(out["duplicate_levels"], list) else []:
        v = int(round(max(0.0, min(100.0, float(v)))))
        if v not in levels:
            levels.append(v)
    out["duplicate_levels"] = sorted(levels, reverse=True)[:MAX_LEVELS] or [100]
    out["dpi"] = max(72, min(600, int(out["dpi"])))
    out["formats"] = [f for f in out["formats"] if f in FORMATS] or ["pdf"]
    if out["layout"] == "merged":  # older runs/settings: "merged PDF per set" is now the combine option
        out["combine"], out["layout"] = True, "flat"
    out["layout"] = out["layout"] if out["layout"] in LAYOUTS else "folders"
    # combine: each set becomes one document holding every template, in the order given
    out["combine"] = bool(out["combine"]) and len(out["template_ids"]) > 1
    out["scan"] = out["scan"] if out["scan"] in scan.PRESETS else "none"
    out["age_min"], out["age_max"] = int(out["age_min"]), int(out["age_max"])
    stress.normalise(out)
    if document_count(out) > MAX_DOCUMENTS:
        raise ValueError(f"That would make {document_count(out):,} documents; the limit per run is {MAX_DOCUMENTS:,}")
    return out


def duplicated_count(settings: dict) -> int:
    """How many sets get duplicated (rounded half up, as the UI shows it)."""
    return int(settings["count"] * settings["duplicate_pct"] / 100 + 0.5) if settings["duplicate"] else 0


def document_count(settings: dict) -> int:
    per_set = 1 if settings["combine"] else max(1, len(settings["template_ids"]))
    extra = duplicated_count(settings) * (len(settings["duplicate_levels"]) - 1)
    return (settings["count"] + extra) * per_set


def load_templates(ids: list[int]) -> list[dict]:
    out = []
    for tid in ids:
        t = templates.get(tid)
        if t is None:
            raise ValueError(f"Template {tid} not found")
        if not t["fields"]:
            raise ValueError(f"Template '{t['name']}' has no fields")
        out.append(t)
    return out


def build_specs(tpls: list[dict], types: dict) -> tuple[dict[str, FieldSpec], list[str]]:
    """One spec per field name across all templates; the first definition of a name wins."""
    specs: dict[str, FieldSpec] = {}
    owner: dict[str, str] = {}
    warnings: list[str] = []
    for t in tpls:
        for f in t["fields"]:
            if f["type"] not in types:
                raise ValueError(f"Field '{f['name']}' in '{t['name']}' uses unknown data type '{f['type']}'")
            if f["name"] in specs:
                s = specs[f["name"]]
                if (s.type_key, s.person) != (f["type"], f.get("person", 1)):
                    warnings.append(f"'{f['name']}' is {f['type']} (person {f.get('person', 1)}) in '{t['name']}' but "
                                    f"{s.type_key} (person {s.person}) in '{owner[f['name']]}': using the latter")
                continue
            specs[f["name"]] = FieldSpec(f["name"], f["type"], int(f.get("person") or 1))
            owner[f["name"]] = t["name"]
    return specs, warnings


def generate_values(seed: int, specs, types, settings) -> tuple[SetContext, list[str]]:
    ctx = SetContext(seed, types, specs, settings["age_min"], settings["age_max"])
    errors = []
    for name in specs:
        try:
            ctx.field_value(name)
        except GenerationError as e:
            ctx.values[name] = ""
            errors.append(str(e))
    return ctx, errors


def ai_fields_for(specs, types) -> list[dict]:
    out = []
    for name, s in specs.items():
        t = types[s.type_key]
        if t.kind == "ai" and t.config.get("mode") != "pool":
            out.append({"name": name, "prompt": t.config.get("prompt", ""), "max_words": t.config.get("max_words"),
                        "person": s.person})
    return out


def ai_context(ctx: SetContext, ai_fields: list[dict]) -> dict:
    record = {"patient": ctx.person(1).summary()}
    for p in sorted({f["person"] for f in ai_fields} | set(ctx.persons)):
        if p > 1:
            record[f"person_{p}"] = ctx.person(p).summary()
    others = {}
    for k, v in ctx.values.items():
        if v is AI_PENDING or v in ("", None):
            continue
        others[k] = format_value(v, {})
        if len(others) >= 60:
            break
    record["other_form_fields"] = others
    return record


def fill_ai(ctx: SetContext, ai_fields: list[dict]) -> str | None:
    """Fill a set's live-AI fields in one call. Returns an error message on failure."""
    try:
        result = ai.fill_fields(ai_context(ctx, ai_fields), ai_fields)
        for f in ai_fields:
            ctx.values[f["name"]] = result.get(f["name"], "")
        return None
    except Exception as e:
        for f in ai_fields:
            ctx.values[f["name"]] = None
        return str(e)


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower() or "template"


def _missing_fields(t: dict, settings: dict, doc_seed: int, accuracy: int | None) -> tuple[set[int], float]:
    """Indexes of fields left blank, and the document's target missing rate (%).

    Fields are blanked in units: tick boxes sharing a name (one question) go together, other fields one by one.
    Required fields are never blank. Normal documents draw their own rate from the missing range, and a field's
    own missing % overrides it. An accuracy variant keeps exactly accuracy% of the units, chosen from one fixed
    order per document, so each lower level blanks the fields of the level above plus some more."""
    rng = random.Random(doc_seed ^ 0x5BD1E995)
    units: dict = {}
    for i, f in enumerate(t["fields"]):
        key = ("tick", f["name"]) if f["render"] == "checkbox" else ("field", i)
        units.setdefault(key, []).append(i)
    fields = t["fields"]
    eligible = [ix for ix in units.values() if not any((fields[i].get("options") or {}).get("required") for i in ix)]
    missing: set[int] = set()
    if accuracy is not None:
        order = eligible[:]
        rng.shuffle(order)
        keep = int(len(order) * accuracy / 100 + 0.5)
        for ix in order[keep:]:
            missing.update(ix)
        return missing, round(100 - accuracy, 1)
    rate = rng.uniform(settings["missing_min"], settings["missing_max"])
    for ix in eligible:
        own = (fields[ix[0]].get("options") or {}).get("missing_pct")
        if rng.random() * 100 < (rate if own in (None, "") else float(own)):
            missing.update(ix)
    return missing, round(rate, 1)


def render_document(t: dict, values: dict, settings: dict, doc_seed: int, ai_failed: set[str],
                    accuracy: int | None = None):
    """Render one template for one set. Returns (pymupdf doc, ground-truth entry).

    Everything except which fields are blank depends only on doc_seed, so accuracy variants of one document
    share the writer, the handwriting and each field's jitter. OCR stress effects (crossings-out, overflow,
    typos) come from their own per-field streams (app/stress.py), so they're shared too, and leave everything
    else exactly as it was when they're off."""
    rng = random.Random(doc_seed)
    style = render.make_doc_style(rng, settings)
    pct = settings["handwritten_pct"]
    doc_hw = rng.random() * 100 < pct
    per_field = settings["handwriting_mode"] == "field"
    blank, rate = _missing_fields(t, settings, doc_seed, accuracy)
    cross_rate = stress.crossout_rate(settings, doc_seed)
    doc = pymupdf.open(templates.path(t))
    gt_fields = []
    for idx, f in enumerate(t["fields"]):
        opts = f.get("options") or {}
        value = values.get(f["name"])
        text = format_value(value, opts)
        missing = idx in blank
        hw = True if f["render"] == "signature" else (rng.random() * 100 < pct if per_field else doc_hw)
        entry = {"name": f["name"], "type": f["type"], "person": f.get("person", 1), "render": f["render"],
                 "page": f["page"] + 1, "rect": f["rect"], "handwritten": hw, "missing": missing}
        if f["name"] in ai_failed:
            entry.update(value=None, missing=True, error="AI generation failed")
        elif missing or (not text and f["render"] != "checkbox"):
            entry["value"] = None if missing else ""
        else:
            text, fx, typo = stress.plan(f, idx, text, value, hw, settings, doc_seed, cross_rate)
            drawn = render.render_field(doc[f["page"]], f, text, value, hw, style,
                                        random.Random((doc_seed * 1_000_003 + idx) & 0x7FFFFFFF), fx)
            entry["value"] = drawn
            stress.finish(entry, drawn, fx, typo)
        if f["render"] == "checkbox" and entry.get("value") is None and not missing and f["name"] not in ai_failed:
            entry["value"] = False
        entry["source_value"] = raw_json(value)
        gt_fields.append(entry)
    gt = {"handwritten": doc_hw if not per_field else None, "accuracy": accuracy, "missing_rate": rate,
          "fields_missing": sum(1 for e in gt_fields if e["missing"]), "style": style.public(), "fields": gt_fields}
    if settings.get("crossouts"):
        gt["crossout_rate"] = cross_rate
    return doc, gt


def _write_outputs(docs: list[tuple[str, pymupdf.Document]], base: Path, settings: dict, seed: int) -> list[str]:
    """docs: [(stem, doc)] written under base. Merged layout passes a single combined doc."""
    files = []
    fmts, preset = settings["formats"], settings["scan"]
    for stem, doc in docs:
        images = None
        if preset != "none" or "png" in fmts or "tiff" in fmts:
            images = [scan.page_image(p, settings["dpi"], preset, settings["colour"], seed + i * 7919 + len(files))
                      for i, p in enumerate(doc)]
        if "pdf" in fmts:
            out = base / f"{stem}.pdf"
            if preset == "none" and settings["colour"] == "colour":
                doc.save(out, garbage=3, deflate=True)
            else:
                if images is None:
                    images = [scan.page_image(p, settings["dpi"], preset, settings["colour"], seed + i)
                              for i, p in enumerate(doc)]
                img_doc = pymupdf.open()
                for p, img in zip(doc, images):
                    page = img_doc.new_page(width=p.rect.width, height=p.rect.height)
                    page.insert_image(page.rect, stream=scan.encode_for_pdf(img))
                img_doc.save(out, garbage=3, deflate=True)
            files.append(out)
        if "png" in fmts:
            for i, img in enumerate(images):
                out = base / (f"{stem}.png" if len(images) == 1 else f"{stem}_p{i + 1}.png")
                img.save(out, compress_level=1 if preset != "none" else 6)
                files.append(out)
        if "tiff" in fmts:
            out = base / f"{stem}.tiff"
            comp = "group4" if images[0].mode == "1" else "tiff_lzw"
            # Save from fresh copies: an earlier PNG/JPEG save leaves encoder settings on each image, which
            # Pillow's multi-page TIFF writer would pass on to libtiff for the appended pages.
            frames = [im.copy() for im in images]
            frames[0].save(out, format="TIFF", save_all=True, append_images=frames[1:], compression=comp,
                           dpi=(settings["dpi"],) * 2)
            files.append(out)
    return [str(f) for f in files]


def _render_templates(tpls, values, settings, set_seed, ai_failed, accuracy=None) -> list[tuple]:
    """[(template, doc, ground truth, doc seed)] for one set (at one accuracy level, if duplicating)."""
    out = []
    for ti, t in enumerate(tpls):
        doc_seed = (set_seed * 31 + ti * 7 + 1) & 0x7FFFFFFF
        doc, gt = render_document(t, values, settings, doc_seed, ai_failed, accuracy)
        out.append((t, doc, gt, doc_seed))
    return out


def _combine(set_docs) -> pymupdf.Document:
    """One document holding every template of a set, in order. Ground truth gets the page offsets."""
    combined = pymupdf.open()
    for _, doc, gt, _ in set_docs:
        offset = combined.page_count
        combined.insert_pdf(doc)
        gt["first_page"] = offset + 1  # 1-based, like every page number in the ground truth
        for f in gt["fields"]:  # page numbers in the combined file are offset by the forms before it
            f["merged_page"] = f["page"] + offset
    return combined


def render_set(job, tpls, settings, stems, out_dir) -> tuple[int, list[dict]]:
    """Render and write every template for one set, once per accuracy level if the set is duplicated.
    Runs in a worker process."""
    i, set_name, values, set_seed, ai_failed, levels = job
    out_dir = Path(out_dir)
    results = []
    for level in levels:
        suffix = "" if level is None else f"_acc{level}"
        set_docs = _render_templates(tpls, values, settings, set_seed, ai_failed, level)
        if settings["combine"]:
            base = out_dir / set_name if settings["layout"] == "folders" else out_dir
            base.mkdir(exist_ok=True)
            files = _write_outputs([(set_name + suffix, _combine(set_docs))], base, settings, set_docs[0][3])
            outputs = [(files, t, gt) for t, _, gt, _ in set_docs]
        elif settings["layout"] == "folders":
            base = out_dir / set_name
            base.mkdir(exist_ok=True)
            outputs = [(_write_outputs([(stems[t["id"]] + suffix, doc)], base, settings, s), t, gt)
                       for t, doc, gt, s in set_docs]
        else:
            outputs = [(_write_outputs([(f"{set_name}_{stems[t['id']]}{suffix}", doc)], out_dir, settings, s), t, gt)
                       for t, doc, gt, s in set_docs]
        results += [{"set": i + 1, "template": t["name"], "template_id": t["id"],
                     "files": [str(Path(f).relative_to(out_dir)) for f in files], **gt} for files, t, gt in outputs]
    return i, results


class Cancelled(Exception):
    pass


def _set_status(run_id, **kw):
    db.update("runs", "id", run_id, kw)


def _check_cancel(run_id):
    r = db.query_one("SELECT status FROM runs WHERE id = ?", (run_id,))
    if r is None or r["status"] == "cancelling":
        raise Cancelled()


def execute(run_id: str) -> None:
    run = db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
    settings = run["settings"]
    out_dir = RUNS_DIR / run_id
    started = time.time()
    try:
        tpls = load_templates(settings["template_ids"])
        types = all_types()
        specs, warnings = build_specs(tpls, types)
        ai_fields = ai_fields_for(specs, types)
        if ai_fields and not ai.available():
            raise ValueError("These templates use live AI types but no AI key is set (add one in Settings)")
        out_dir.mkdir(parents=True, exist_ok=True)
        n, seed = settings["count"], settings["seed"]
        per_set = 1 if settings["combine"] else len(tpls)  # output documents per set (per version)
        total = document_count(settings)
        # which sets are duplicated at every accuracy level (the rest get one version, missing from the range)
        dup = set(random.Random(seed ^ 0x2545F491).sample(range(n), duplicated_count(settings)))
        # status is already "running": the queue worker claimed it. Don't set it again, or a cancel
        # request that arrived in between would be overwritten.
        _set_status(run_id, total=total, progress=0, message="Generating data")
        _check_cancel(run_id)

        # 1. values for every set
        contexts, gen_errors = [], set()
        for i in range(n):
            ctx, errs = generate_values((seed * 1_000_003 + i) & 0x7FFFFFFF, specs, types, settings)
            contexts.append(ctx)
            gen_errors.update(errs)
        warnings += sorted(gen_errors)

        # 2. live AI fields, one call per set, in parallel
        ai_failed: dict[int, set[str]] = {}
        ai_errors: list[str] = []
        if ai_fields:
            _set_status(run_id, message=f"Writing AI fields (0/{n} sets)")
            done = 0
            pool = ThreadPoolExecutor(max_workers=config.AI_CONCURRENCY)  # read per run: Settings can change it
            try:
                futs = {pool.submit(fill_ai, ctx, ai_fields): i for i, ctx in enumerate(contexts)}
                for fut in as_completed(futs):
                    err = fut.result()
                    if err:
                        ai_failed[futs[fut]] = {f["name"] for f in ai_fields}
                        ai_errors.append(f"Set {futs[fut] + 1}: {err}")
                    done += 1
                    if done % 5 == 0 or done == n:
                        _set_status(run_id, message=f"Writing AI fields ({done}/{n} sets)")
                        _check_cancel(run_id)
            finally:
                # On cancel/error, drop queued calls instead of waiting for all of them.
                pool.shutdown(wait=True, cancel_futures=True)

        # 3. render, sets in parallel worker processes (PyMuPDF is not thread-safe)
        stems = {}
        for t in tpls:
            s = slug(t["name"])
            stems[t["id"]] = s if s not in stems.values() else f"{s}_{t['id']}"
        width = max(4, len(str(n)))
        jobs = [(i, f"set_{i + 1:0{width}d}", {k: (None if v is AI_PENDING else v) for k, v in ctx.values.items()},
                 ctx.seed, ai_failed.get(i, set()), settings["duplicate_levels"] if i in dup else [None])
                for i, ctx in enumerate(contexts)]
        results: dict[int, list] = {}
        progress = 0
        _set_status(run_id, message="Rendering documents")
        workers = max(1, min(RENDER_WORKERS, n))
        pool = ProcessPoolExecutor(max_workers=workers)
        try:
            futs = [pool.submit(render_set, job, tpls, settings, stems, str(out_dir)) for job in jobs]
            for fut in as_completed(futs):
                i, docs = fut.result()
                results[i] = docs
                progress += per_set * len(jobs[i][5])
                _set_status(run_id, progress=progress, message=f"Rendering documents ({progress}/{total})")
                _check_cancel(run_id)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        gt_docs = [d for i in sorted(results) for d in results[i]]

        # 4. ground truth
        _write_ground_truth(out_dir, settings, tpls, specs, contexts, gt_docs, warnings + ai_errors)
        summary = {"documents": total, "forms": len(gt_docs), "combined": settings["combine"], "sets": n,
                   "duplicated_sets": len(dup), "templates": [t["name"] for t in tpls],
                   "linked_fields": sorted(_linked(tpls)), "warnings": warnings, "ai_errors": ai_errors,
                   "ai_calls": n if ai_fields else 0, "seconds": round(time.time() - started, 1),
                   "output_dir": str(out_dir)}
        _set_status(run_id, status="done", progress=total, message="Complete", summary=summary)
    except Cancelled:
        _set_status(run_id, status="cancelled", message="Cancelled")
    except Exception as e:
        traceback.print_exc()
        _set_status(run_id, status="failed", message=str(e))


def _linked(tpls) -> set[str]:
    if len(tpls) < 2:
        return set()
    seen: dict[str, set] = {}
    for t in tpls:
        for f in t["fields"]:
            seen.setdefault(f["name"], set()).add(t["id"])
    return {k for k, v in seen.items() if len(v) > 1}


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return v


def _write_ground_truth(out_dir, settings, tpls, specs, contexts, gt_docs, warnings):
    with open(out_dir / "ground_truth.json", "w") as fh:
        json.dump({"generated_at": datetime.now().isoformat(timespec="seconds"), "settings": settings,
                   "templates": [{"id": t["id"], "name": t["name"], "page_sizes": t["page_sizes"]} for t in tpls],
                   "warnings": warnings, "documents": gt_docs}, fh, indent=1, default=str)
    with open(out_dir / "ground_truth.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["set", "template", "file", "accuracy", "field", "type", "render", "page", "x0", "y0", "x1", "y1",
                    "value", "missing", "handwritten", "handwriting_font",
                    # OCR stress columns, appended so existing consumers keep their column positions
                    "crossed_out", "overflow", "ink_x0", "ink_y0", "ink_x1", "ink_y1", "typo", "intended_value"])
        for d in gt_docs:
            for f in d["fields"]:
                w.writerow([d["set"], d["template"], d["files"][0] if d["files"] else "",
                            "" if d.get("accuracy") is None else d["accuracy"], f["name"], f["type"],
                            f["render"], f.get("merged_page", f["page"]), *f["rect"], _cell(f["value"]),
                            _cell(f["missing"]), _cell(f["handwritten"]),
                            d["style"]["handwriting_font"] if f["handwritten"] else "",
                            f.get("crossed_out", ""), _cell(bool(f.get("overflow"))), *(f.get("ink_rect") or [""] * 4),
                            _cell(bool(f.get("typo"))), f.get("intended_value", "")])
    names = list(specs)
    with open(out_dir / "records.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["set", *names])
        for i, ctx in enumerate(contexts):
            row = []
            for nme in names:
                v = ctx.values.get(nme)
                row.append(_cell(v) if isinstance(v, bool) or v is None else format_value(v, _first_opts(tpls, nme)))
            w.writerow([i + 1, *row])


def _first_opts(tpls, name):
    for t in tpls:
        for f in t["fields"]:
            if f["name"] == name:
                return f.get("options") or {}
    return {}


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------

RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
ACTIVE_STATUSES = ("queued", "running", "cancelling")
STATUS_FILTERS = {"active": ACTIVE_STATUSES, "done": ("done",), "failed": ("failed",), "cancelled": ("cancelled",)}


def run_dir(run_id: str) -> Path:
    """The output folder for a run. Raises ValueError for anything that isn't a well-formed run id, so a
    crafted id such as '..' can never point outside RUNS_DIR (deleting it would remove the whole data folder)."""
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise ValueError("Invalid run id")
    d = (RUNS_DIR / run_id).resolve()
    if d.parent != RUNS_DIR.resolve():
        raise ValueError("Invalid run id")
    return d


# ---- queue: runs execute one at a time, oldest first. Each run already uses every core for rendering,
# so running several side by side only makes them all slower and competes for the AI rate limit.
_queue_cv = threading.Condition()
_worker: threading.Thread | None = None


def _next_queued() -> dict | None:
    return db.query_one("SELECT id FROM runs WHERE status = 'queued' ORDER BY created_at, rowid LIMIT 1")


def _worker_loop() -> None:
    while True:
        with _queue_cv:
            nxt = _next_queued()
            if nxt is None:
                _queue_cv.wait(timeout=30)
                continue
        # claim it: fails if it was cancelled (or claimed) in the meantime
        if db.change("UPDATE runs SET status = 'running', message = 'Starting' WHERE id = ? AND status = 'queued'",
                     [nxt["id"]]):
            try:
                execute(nxt["id"])
            except Exception:  # execute records its own failures; never let the worker die
                traceback.print_exc()


def kick() -> None:
    """Start the queue worker if needed and wake it."""
    global _worker
    with _queue_cv:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_loop, name="run-queue", daemon=True)
            _worker.start()
        _queue_cv.notify_all()


def start(settings: dict) -> dict:
    settings = normalise_settings(settings)
    tpls = load_templates(settings["template_ids"])
    build_specs(tpls, all_types())  # validate before queueing
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    db.execute("INSERT INTO runs (id, created_at, settings, status, total, message) VALUES (?,?,?,?,?,?)",
               [run_id, db.now(), settings, "queued", document_count(settings), "Queued"])
    kick()
    return with_queue_positions([db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))])[0]


def cancel(run_id: str) -> None:
    """A queued run is cancelled straight away; a running one is asked to stop at its next checkpoint."""
    run_dir(run_id)
    db.change("UPDATE runs SET status = 'cancelled', message = 'Cancelled before it started' "
              "WHERE id = ? AND status = 'queued'", [run_id])
    db.change("UPDATE runs SET status = 'cancelling' WHERE id = ? AND status = 'running'", [run_id])


def with_queue_positions(rows: list[dict]) -> list[dict]:
    """Add queue_position (1 = next to start) to queued runs."""
    queued = [r["id"] for r in db.query("SELECT id FROM runs WHERE status = 'queued' ORDER BY created_at, rowid")]
    pos = {rid: i + 1 for i, rid in enumerate(queued)}
    for r in rows:
        r["queue_position"] = pos.get(r["id"])
    return rows


def _haystack(r: dict, names: dict[int, str]) -> str:
    s, summ = r.get("settings") or {}, r.get("summary") or {}
    tnames = summ.get("templates") or [names.get(t, "") for t in s.get("template_ids", [])]
    parts = [r["id"], r["status"], r.get("message", ""), s.get("seed", ""), s.get("scan", ""), *tnames,
             *(summ.get("warnings") or []), *(summ.get("ai_errors") or [])]
    return " ".join(str(p) for p in parts).lower()


def search(q: str = "", status: str = "", limit: int = 25, offset: int = 0) -> dict:
    """One page of runs, newest first. q matches every word against the run id, template names, seed,
    status and messages. status is "", "active", "done", "failed" or "cancelled".
    counts are per status for the current q, so filter chips can show them."""
    limit, offset = max(1, min(200, int(limit))), max(0, int(offset))
    rows = db.query("SELECT * FROM runs ORDER BY created_at DESC, rowid DESC")
    words = q.lower().split()
    if words:
        names = {t["id"]: t["name"] for t in db.query("SELECT id, name FROM templates")}
        rows = [r for r in rows if all(w in _haystack(r, names) for w in words)]
    counts = {"all": len(rows), **{k: sum(r["status"] in v for r in rows) for k, v in STATUS_FILTERS.items()}}
    if status:
        if status not in STATUS_FILTERS:
            raise ValueError(f"Unknown status filter '{status}'")
        rows = [r for r in rows if r["status"] in STATUS_FILTERS[status]]
    return {"items": with_queue_positions(rows[offset:offset + limit]), "total": len(rows), "counts": counts,
            "limit": limit, "offset": offset}


def active() -> list[dict]:
    """Runs that are queued, running or cancelling (for the nav badge and progress polling)."""
    marks = ",".join("?" * len(ACTIVE_STATUSES))
    return with_queue_positions(db.query(f"SELECT * FROM runs WHERE status IN ({marks}) ORDER BY created_at, rowid",
                                         ACTIVE_STATUSES))


def preview(template_ids: list[int], settings: dict) -> bytes:
    """One rendered sample as PDF bytes (live AI fields are filled if AI is available).
    With several templates and combine on, the whole set as one document; otherwise the first template.
    With duplicates on, that sample once per accuracy level."""
    settings = normalise_settings({**settings, "template_ids": template_ids, "count": 1})
    tpls = [templates.get(t) for t in (settings["template_ids"] if settings["combine"] else settings["template_ids"][:1])]
    if None in tpls:
        raise ValueError("Template not found")
    types = all_types()
    specs, _ = build_specs(tpls, types)
    ctx, _ = generate_values(settings["seed"], specs, types, settings)
    ai_fields = ai_fields_for(specs, types)
    failed: set[str] = set()
    if ai_fields and not (ai.available() and fill_ai(ctx, ai_fields) is None):
        failed = {f["name"] for f in ai_fields}
    levels = settings["duplicate_levels"] if settings["duplicate"] else [None]
    doc = pymupdf.open()
    for level in levels:  # with duplicates on, one set at every accuracy level, labelled in the margin
        set_docs = _render_templates(tpls, ctx.values, settings, settings["seed"], failed, level)
        for _, d, gt, _ in set_docs:  # say which fields got crossings-out, typos or overflow
            stress.annotate_preview(d, gt, settings)
        part =_combine(set_docs) if len(set_docs) > 1 else set_docs[0][1]
        if level is not None:
            for page in part:
                page.insert_text((8, 10), f"Preview: accuracy {level}%", fontsize=7, color=(0.8, 0.1, 0.3))
        doc.insert_pdf(part)
    if settings["scan"] != "none" or settings["colour"] != "colour":
        tmp = Path(RUNS_DIR / f".preview-{uuid.uuid4().hex}")
        tmp.mkdir()
        try:
            f = _write_outputs([("preview", doc)], tmp, {**settings, "formats": ["pdf"]}, settings["seed"])[0]
            return Path(f).read_bytes()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return doc.tobytes(garbage=3, deflate=True)


def zip_path(run_id: str) -> Path:
    d = run_dir(run_id)
    z = d.with_name(f"{run_id}.zip")
    if not z.exists():
        shutil.make_archive(str(d), "zip", root_dir=d)
    return z


def delete(run_id: str) -> None:
    d = run_dir(run_id)
    shutil.rmtree(d, ignore_errors=True)
    d.with_name(f"{run_id}.zip").unlink(missing_ok=True)
    db.execute("DELETE FROM runs WHERE id = ?", [run_id])


def recover_interrupted() -> None:
    """At startup: runs that were mid-way failed with the old process. Queued runs never started, so they
    stay queued and the queue worker picks them up."""
    db.execute("UPDATE runs SET status = 'failed', message = 'Interrupted (app restarted)' "
               "WHERE status IN ('running', 'cancelling')")
    if _next_queued():
        kick()
