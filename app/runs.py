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

from . import ai, db, render, scan, templates
from .config import AI_CONCURRENCY, RUNS_DIR
from .datatypes import AI_PENDING, FieldSpec, GenerationError, SetContext, all_types, format_value, raw_json

DEFAULTS = {
    "template_ids": [], "count": 10, "seed": None, "missing_pct": 0.0, "handwritten_pct": 50.0,
    "handwriting_mode": "document", "fonts": [], "ink": "mixed", "typed_font": "helv",
    "scan": "none", "colour": "colour", "dpi": 200, "formats": ["pdf"], "layout": "folders",
    "age_min": 18, "age_max": 95,
}
FORMATS = ("pdf", "png", "tiff")
LAYOUTS = ("folders", "flat", "merged")
MAX_COUNT = 10000
RENDER_WORKERS = max(1, min(8, (os.cpu_count() or 2) - 1))


def normalise_settings(s: dict) -> dict:
    out = {**DEFAULTS, **{k: v for k, v in s.items() if k in DEFAULTS and v is not None}}
    out["template_ids"] = [int(t) for t in out["template_ids"]]
    if not out["template_ids"]:
        raise ValueError("Select at least one template")
    out["count"] = int(out["count"])
    if not 1 <= out["count"] <= MAX_COUNT:
        raise ValueError(f"Number of sets must be between 1 and {MAX_COUNT}")
    out["seed"] = int(out["seed"]) if out.get("seed") not in (None, "") else random.randint(1, 2**31 - 1)
    for k in ("missing_pct", "handwritten_pct"):
        out[k] = max(0.0, min(100.0, float(out[k])))
    out["dpi"] = max(72, min(600, int(out["dpi"])))
    out["formats"] = [f for f in out["formats"] if f in FORMATS] or ["pdf"]
    out["layout"] = out["layout"] if out["layout"] in LAYOUTS else "folders"
    out["scan"] = out["scan"] if out["scan"] in scan.PRESETS else "none"
    out["age_min"], out["age_max"] = int(out["age_min"]), int(out["age_max"])
    return out


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


def render_document(t: dict, values: dict, settings: dict, doc_seed: int, ai_failed: set[str]):
    """Render one template for one set. Returns (pymupdf doc, ground-truth entry)."""
    rng = random.Random(doc_seed)
    style = render.make_doc_style(rng, settings)
    pct = settings["handwritten_pct"]
    doc_hw = rng.random() * 100 < pct
    per_field = settings["handwriting_mode"] == "field"
    doc = pymupdf.open(templates.path(t))
    gt_fields = []
    for f in t["fields"]:
        opts = f.get("options") or {}
        value = values.get(f["name"])
        text = format_value(value, opts)
        miss_pct = opts.get("missing_pct")
        miss_pct = settings["missing_pct"] if miss_pct in (None, "") else float(miss_pct)
        missing = (not opts.get("required")) and rng.random() * 100 < miss_pct
        hw = True if f["render"] == "signature" else (rng.random() * 100 < pct if per_field else doc_hw)
        entry = {"name": f["name"], "type": f["type"], "person": f.get("person", 1), "render": f["render"],
                 "page": f["page"] + 1, "rect": f["rect"], "handwritten": hw, "missing": missing}
        if f["name"] in ai_failed:
            entry.update(value=None, missing=True, error="AI generation failed")
        elif missing or (not text and f["render"] != "checkbox"):
            entry["value"] = None if missing else ""
        else:
            drawn = render.render_field(doc[f["page"]], f, text, value, hw, style, rng)
            entry["value"] = drawn
        if f["render"] == "checkbox" and entry.get("value") is None and not missing and f["name"] not in ai_failed:
            entry["value"] = False
        entry["source_value"] = raw_json(value)
        gt_fields.append(entry)
    return doc, {"handwritten": doc_hw if not per_field else None, "style": style.public(), "fields": gt_fields}


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


def render_set(job, tpls, settings, stems, out_dir) -> tuple[int, list[dict]]:
    """Render and write every template for one set. Runs in a worker process."""
    i, set_name, values, set_seed, ai_failed = job
    out_dir = Path(out_dir)
    set_docs = []
    for ti, t in enumerate(tpls):
        doc_seed = (set_seed * 31 + ti * 7 + 1) & 0x7FFFFFFF
        doc, gt = render_document(t, values, settings, doc_seed, ai_failed)
        set_docs.append((t, doc, gt, doc_seed))
    if settings["layout"] == "folders":
        base = out_dir / set_name
        base.mkdir(exist_ok=True)
        outputs = [(_write_outputs([(stems[t["id"]], doc)], base, settings, s), t, gt) for t, doc, gt, s in set_docs]
    elif settings["layout"] == "flat":
        outputs = [(_write_outputs([(f"{set_name}_{stems[t['id']]}", doc)], out_dir, settings, s), t, gt)
                   for t, doc, gt, s in set_docs]
    else:
        merged = pymupdf.open()
        offset = 0
        for _, doc, gt, _ in set_docs:
            merged.insert_pdf(doc)
            for f in gt["fields"]:  # page numbers in the merged file are offset by preceding templates
                f["merged_page"] = f["page"] + offset
            offset += doc.page_count
        files = _write_outputs([(set_name, merged)], out_dir, settings, set_docs[0][3])
        outputs = [(files, t, gt) for t, _, gt, _ in set_docs]
    return i, [{"set": i + 1, "template": t["name"], "template_id": t["id"],
                "files": [str(Path(f).relative_to(out_dir)) for f in files], **gt} for files, t, gt in outputs]


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
            raise ValueError("These templates use live AI types but REQUESTY_API_KEY is not set")
        out_dir.mkdir(parents=True, exist_ok=True)
        n, seed = settings["count"], settings["seed"]
        total = n * len(tpls)
        _set_status(run_id, status="running", total=total, progress=0, message="Generating data")

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
            pool = ThreadPoolExecutor(max_workers=AI_CONCURRENCY)
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
                 ctx.seed, ai_failed.get(i, set())) for i, ctx in enumerate(contexts)]
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
                progress += len(tpls)
                _set_status(run_id, progress=progress, message=f"Rendering documents ({progress}/{total})")
                _check_cancel(run_id)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        gt_docs = [d for i in sorted(results) for d in results[i]]

        # 4. ground truth
        _write_ground_truth(out_dir, settings, tpls, specs, contexts, gt_docs, warnings + ai_errors)
        summary = {"documents": len(gt_docs), "sets": n, "templates": [t["name"] for t in tpls],
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
        w.writerow(["set", "template", "file", "field", "type", "render", "page", "x0", "y0", "x1", "y1",
                    "value", "missing", "handwritten", "handwriting_font"])
        for d in gt_docs:
            for f in d["fields"]:
                w.writerow([d["set"], d["template"], d["files"][0] if d["files"] else "", f["name"], f["type"],
                            f["render"], f.get("merged_page", f["page"]), *f["rect"], _cell(f["value"]),
                            _cell(f["missing"]), _cell(f["handwritten"]),
                            d["style"]["handwriting_font"] if f["handwritten"] else ""])
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

def start(settings: dict) -> dict:
    settings = normalise_settings(settings)
    tpls = load_templates(settings["template_ids"])
    build_specs(tpls, all_types())  # validate before queueing
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    db.execute("INSERT INTO runs (id, created_at, settings, status, total, message) VALUES (?,?,?,?,?,?)",
               [run_id, db.now(), settings, "queued", settings["count"] * len(tpls), "Queued"])
    threading.Thread(target=execute, args=(run_id,), daemon=True).start()
    return db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))


def preview(template_id: int, settings: dict) -> bytes:
    """One rendered sample of a template as PDF bytes (live AI fields are filled if AI is available)."""
    settings = normalise_settings({**settings, "template_ids": [template_id], "count": 1})
    t = templates.get(template_id)
    if t is None:
        raise ValueError("Template not found")
    types = all_types()
    specs, _ = build_specs([t], types)
    ctx, _ = generate_values(settings["seed"], specs, types, settings)
    ai_fields = ai_fields_for(specs, types)
    failed: set[str] = set()
    if ai_fields:
        if ai.available() and fill_ai(ctx, ai_fields) is None:
            pass
        else:
            failed = {f["name"] for f in ai_fields}
    doc, _ = render_document(t, ctx.values, settings, settings["seed"] + 1, failed)
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
    d = RUNS_DIR / run_id
    z = RUNS_DIR / f"{run_id}.zip"
    if not z.exists():
        shutil.make_archive(str(d), "zip", root_dir=d)
    return z


def delete(run_id: str) -> None:
    shutil.rmtree(RUNS_DIR / run_id, ignore_errors=True)
    (RUNS_DIR / f"{run_id}.zip").unlink(missing_ok=True)
    db.execute("DELETE FROM runs WHERE id = ?", [run_id])


def recover_interrupted() -> None:
    db.execute("UPDATE runs SET status = 'failed', message = 'Interrupted (app restarted)' "
               "WHERE status IN ('queued', 'running', 'cancelling')")
