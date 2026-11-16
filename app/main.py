"""DataCreator: local web app for generating synthetic filled-in documents from blank PDF templates."""

from __future__ import annotations

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import ai, autodetect, config, datatypes, db, render, runs, templates

app = FastAPI(title="DataCreator")
runs.recover_interrupted()


@app.exception_handler(ValueError)
async def value_error(_, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(ai.AiError)
async def ai_error(_, exc: ai.AiError):
    return JSONResponse({"detail": str(exc)}, status_code=502)


# ---------------------------------------------------------------- status / fonts

@app.get("/api/status")
def status():
    return {
        "ai_available": ai.available(), "ai_model": config.REQUESTY_MODEL if ai.available() else None,
        "handwriting_fonts": list(render.handwriting_fonts()), "typed_fonts": render.TYPED_FONTS,
        "date_formats": datatypes.DATE_FORMATS, "scan_presets": list(runs.scan.PRESETS),
        "defaults": runs.DEFAULTS,
    }


@app.get("/api/fonts/{name}.png")
def font_sample(name: str):
    if name not in render.handwriting_fonts():
        raise HTTPException(404)
    return Response(render.font_sample_png(name), media_type="image/png",
                    headers={"Cache-Control": "max-age=86400"})


# ---------------------------------------------------------------- data types

def _type_out(t: datatypes.TypeDef) -> dict:
    d = t.public()
    if d["kind"] == "ai" and d["config"].get("pool"):
        d["config"] = {**d["config"], "pool_size": len(d["config"]["pool"])}
    return d


@app.get("/api/types")
def list_types():
    return [_type_out(t) for t in datatypes.all_types().values()]


def _custom_from_body(body: dict) -> tuple[str, str, str, str, str, dict]:
    key = str(body.get("key", "")).strip()
    label = str(body.get("label") or key).strip()
    kind = str(body.get("kind", ""))
    category = str(body.get("category") or "Custom").strip()
    description = str(body.get("description") or "")
    cfg = body.get("config") or {}
    datatypes.validate_custom(key, kind, cfg)
    return key, label, kind, category, description, cfg


@app.post("/api/types")
def create_type(body: dict = Body(...)):
    key, label, kind, category, description, cfg = _custom_from_body(body)
    if db.query_one("SELECT key FROM data_types WHERE key = ?", (key,)):
        raise ValueError(f"A type called '{key}' already exists")
    ts = db.now()
    db.execute("INSERT INTO data_types (key, label, kind, category, description, config, created_at, updated_at) "
               "VALUES (?,?,?,?,?,?,?,?)", [key, label, kind, category, description, cfg, ts, ts])
    return _type_out(datatypes.all_types()[key])


@app.put("/api/types/{key}")
def update_type(key: str, body: dict = Body(...)):
    if not db.query_one("SELECT key FROM data_types WHERE key = ?", (key,)):
        raise HTTPException(404, "Type not found")
    _, label, kind, category, description, cfg = _custom_from_body({**body, "key": key})
    db.update("data_types", "key", key, {"label": label, "kind": kind, "category": category,
                                         "description": description, "config": cfg, "updated_at": db.now()})
    return _type_out(datatypes.all_types()[key])


@app.delete("/api/types/{key}")
def delete_type(key: str):
    used = [t["name"] for t in db.query("SELECT name, fields FROM templates") if any(f["type"] == key for f in t["fields"])]
    if used:
        raise ValueError(f"'{key}' is used by: {', '.join(used)}")
    db.execute("DELETE FROM data_types WHERE key = ?", [key])
    return {"ok": True}


@app.get("/api/types/examples")
def type_examples():
    """Two example values for every type (live AI types excluded), for the type library view."""
    types = datatypes.all_types()
    out = {}
    for k, t in types.items():
        try:
            out[k] = datatypes.sample_values(t, 2, seed=11)
        except Exception as e:  # e.g. a composite referencing a deleted type
            out[k] = [f"Error: {e}"]
    return out


@app.post("/api/types/sample")
def sample_type(body: dict = Body(...)):
    """Sample values for a saved type (by key) or an unsaved definition."""
    types = datatypes.all_types()
    if body.get("key") in types and not body.get("kind"):
        t = types[body["key"]]
    else:
        kind = body.get("kind")
        if kind not in datatypes.CUSTOM_KINDS:
            raise ValueError("Choose a kind")
        t = datatypes.custom_typedef({"key": "__preview__", "label": "", "kind": kind, "config": body.get("config") or {}})
    try:
        return {"values": datatypes.sample_values(t, int(body.get("n", 6)))}
    except datatypes.GenerationError as e:
        raise ValueError(str(e))


@app.post("/api/ai/list")
def ai_list(body: dict = Body(...)):
    desc = str(body.get("description", "")).strip()
    if not desc:
        raise ValueError("Describe the data you want")
    return {"values": ai.generate_list(desc, int(body.get("n", 100)))}


# ---------------------------------------------------------------- templates

def _template_out(t: dict) -> dict:
    return {k: t[k] for k in ("id", "name", "page_sizes", "fields", "created_at", "updated_at")}


@app.get("/api/templates")
def list_templates():
    return [_template_out(t) for t in db.query("SELECT * FROM templates ORDER BY updated_at DESC")]


@app.post("/api/templates")
async def upload_template(file: UploadFile = File(...), name: str = Form("")):
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise ValueError("PDF is larger than 50 MB")
    nm = name.strip() or (file.filename or "Template").rsplit(".", 1)[0]
    return _template_out(templates.create(nm, data))


def _get_template(tid: int) -> dict:
    t = templates.get(tid)
    if t is None:
        raise HTTPException(404, "Template not found")
    return t


@app.get("/api/templates/{tid}")
def get_template(tid: int):
    return _template_out(_get_template(tid))


@app.put("/api/templates/{tid}")
def save_template(tid: int, body: dict = Body(...)):
    t = _get_template(tid)
    fields = templates.clean_fields(body.get("fields") or [], len(t["page_sizes"]))
    types = datatypes.all_types()
    unknown = sorted({f["type"] for f in fields if f["type"] not in types})
    if unknown:
        raise ValueError(f"Unknown data type(s): {', '.join(unknown) or '(none chosen)'}")
    name = str(body.get("name") or t["name"]).strip()
    db.update("templates", "id", tid, {"name": name, "fields": fields, "updated_at": db.now()})
    return _template_out(templates.get(tid))


@app.post("/api/templates/{tid}/duplicate")
def duplicate_template(tid: int):
    t = _get_template(tid)
    new = templates.create(f"{t['name']} (copy)", templates.path(t).read_bytes(), widgets=templates.load_widgets(t))
    db.update("templates", "id", new["id"], {"fields": t["fields"]})
    return _template_out(templates.get(new["id"]))


@app.delete("/api/templates/{tid}")
def delete_template(tid: int):
    t = _get_template(tid)
    db.execute("DELETE FROM templates WHERE id = ?", [tid])
    templates.delete_files(t)
    return {"ok": True}


@app.get("/api/templates/{tid}/pages/{page}.png")
def page_image(tid: int, page: int, dpi: int = 144):
    t = _get_template(tid)
    if not 0 <= page < len(t["page_sizes"]):
        raise HTTPException(404)
    return Response(templates.page_png(t["filename"], page, max(36, min(300, dpi))), media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"})


@app.post("/api/templates/{tid}/suggest")
def suggest(tid: int, body: dict = Body(...)):
    t = _get_template(tid)
    return templates.suggest(t, int(body["page"]), body["rect"])


@app.post("/api/templates/{tid}/autodetect")
def autodetect_fields(tid: int, body: dict = Body(default={})):
    """Propose fields for a template (nothing is saved). Body: {existing: [fields], pages: [int], use_ai: bool}."""
    _get_template(tid)
    return autodetect.detect(tid, (body or {}).get("existing") or [], (body or {}).get("pages"),
                             use_ai=(body or {}).get("use_ai", True))


@app.post("/api/templates/{tid}/preview")
def preview(tid: int, body: dict = Body(default={})):
    _get_template(tid)
    pdf = runs.preview(tid, body or {})
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": "inline; filename=preview.pdf"})


# ---------------------------------------------------------------- runs

@app.get("/api/runs")
def list_runs():
    return db.query("SELECT * FROM runs ORDER BY created_at DESC LIMIT 100")


@app.post("/api/runs")
def start_run(body: dict = Body(...)):
    return runs.start(body)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    r = db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
    if r is None:
        raise HTTPException(404)
    return r


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    db.execute("UPDATE runs SET status = 'cancelling' WHERE id = ? AND status IN ('queued', 'running')", [run_id])
    return {"ok": True}


@app.get("/api/runs/{run_id}/download")
def download_run(run_id: str):
    r = db.query_one("SELECT status FROM runs WHERE id = ?", (run_id,))
    if r is None or r["status"] != "done":
        raise HTTPException(404, "Run not finished")
    return FileResponse(runs.zip_path(run_id), filename=f"datacreator-{run_id}.zip")


@app.get("/api/runs/{run_id}/ground_truth.json")
def run_ground_truth(run_id: str):
    p = config.RUNS_DIR / run_id / "ground_truth.json"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str):
    r = db.query_one("SELECT status FROM runs WHERE id = ?", (run_id,))
    if r and r["status"] in ("queued", "running", "cancelling"):
        raise ValueError("This run is still active: cancel it and wait for it to stop first")
    runs.delete(run_id)
    return {"ok": True}


app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")
