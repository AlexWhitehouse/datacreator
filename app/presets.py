"""Named run presets: saved Generate settings, e.g. "OCR baseline" or "Fax stress test"."""

from __future__ import annotations

import sqlite3

from . import db
from .runs import DEFAULTS

MAX_NAME = 80


def _clean_settings(settings) -> dict:
    """Keep only known run settings. Templates are part of a preset but may be empty (pick them when using it).
    Values are checked properly when a run starts, so an old preset never blocks the Generate page."""
    if not isinstance(settings, dict):
        raise ValueError("Preset settings must be an object")
    out = {k: v for k, v in settings.items() if k in DEFAULTS}
    out["template_ids"] = [int(t) for t in out.get("template_ids") or [] if str(t).lstrip("-").isdigit()]
    return out


def _clean_name(name) -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("Give the preset a name")
    if len(name) > MAX_NAME:
        raise ValueError(f"Preset names can be at most {MAX_NAME} characters")
    return name


def list_all() -> list[dict]:
    return db.query("SELECT * FROM presets ORDER BY name COLLATE NOCASE")


def get(pid: int) -> dict | None:
    return db.query_one("SELECT * FROM presets WHERE id = ?", (pid,))


def create(name, settings) -> dict:
    name, settings = _clean_name(name), _clean_settings(settings)
    ts = db.now()
    try:
        pid = db.execute("INSERT INTO presets (name, settings, created_at, updated_at) VALUES (?,?,?,?)",
                         [name, settings, ts, ts])
    except sqlite3.IntegrityError:
        raise ValueError(f"A preset called '{name}' already exists") from None
    return get(pid)


def update(pid: int, name=None, settings=None) -> dict | None:
    if get(pid) is None:
        return None
    values = {"updated_at": db.now()}
    if name is not None:
        values["name"] = _clean_name(name)
    if settings is not None:
        values["settings"] = _clean_settings(settings)
    try:
        db.update("presets", "id", pid, values)
    except sqlite3.IntegrityError:
        raise ValueError(f"A preset called '{values['name']}' already exists") from None
    return get(pid)


def delete(pid: int) -> None:
    db.execute("DELETE FROM presets WHERE id = ?", [pid])
