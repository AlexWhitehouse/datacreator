"""Run queue, run-id safety, run search/paging and presets (no AI, no real rendering)."""

import threading
import time

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import config, db, runs
from app.main import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def tid(client):
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_text((72, 100), "Surname:")
    r = client.post("/api/templates", files={"file": ("queue.pdf", doc.tobytes())}, data={"name": "Queue test form"})
    t = r.json()["id"]
    fields = [{"name": "surname", "type": "last_name", "page": 0, "rect": [140, 88, 400, 106]}]
    assert client.put(f"/api/templates/{t}", json={"fields": fields}).status_code == 200
    return t


@pytest.fixture
def fake_execute(monkeypatch):
    """Replace the real run with one that records when it ran and waits for permission to finish."""
    calls, gates, lock = [], {}, threading.Lock()

    def execute(run_id):
        with lock:
            calls.append(run_id)
            running_now = db.query("SELECT id FROM runs WHERE status = 'running'")
        gates.setdefault(run_id, threading.Event()).wait(10)
        assert len(running_now) == 1, running_now  # never two at once
        runs._set_status(run_id, status="done", message="Complete")

    monkeypatch.setattr(runs, "execute", execute)
    execute.calls, execute.gates = calls, gates
    execute.release = lambda rid: gates.setdefault(rid, threading.Event()).set()
    return execute


def _status(client, rid):
    return client.get(f"/api/runs/{rid}").json()


def _until(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


def _clear_runs():
    db.execute("DELETE FROM runs")


# ------------------------------------------------------------------ run ids never escape the runs folder

def test_crafted_run_ids_are_rejected_and_data_survives(client):
    canary = config.TEMPLATES_DIR / "canary.txt"
    canary.write_text("still here")
    for bad in ("%2E%2E", "..", "...", "x", "20250101-120000-zzzz", "%2E%2E%2Ftemplates"):
        for method, path in (("DELETE", ""), ("GET", ""), ("GET", "/download"), ("GET", "/ground_truth.json"),
                             ("POST", "/cancel")):
            r = client.request(method, f"/api/runs/{bad}{path}")
            assert r.status_code in (400, 404, 405), (method, bad, path, r.status_code)
    assert canary.read_text() == "still here" and (config.DATA_DIR / "datacreator.db").exists()
    for bad in ("..", "../x", "20250101-120000-abcd/..", "", None):
        with pytest.raises(ValueError):
            runs.run_dir(bad)
    assert runs.run_dir("20250101-120000-abcd") == (config.RUNS_DIR / "20250101-120000-abcd").resolve()


def test_unknown_but_well_formed_run_id_is_404(client):
    assert client.delete("/api/runs/20250101-120000-abcd").status_code == 404


# ------------------------------------------------------------------ queue

def test_runs_execute_one_at_a_time_in_order(client, tid, fake_execute):
    _clear_runs()
    ids = [client.post("/api/runs", json={"template_ids": [tid], "count": 1, "seed": i}).json()["id"] for i in range(3)]
    assert _until(lambda: fake_execute.calls == ids[:1])
    r = _status(client, ids[2])
    assert r["status"] == "queued" and r["queue_position"] == 2
    for rid in ids:
        assert _until(lambda: rid in fake_execute.calls)
        fake_execute.release(rid)
        assert _until(lambda: _status(client, rid)["status"] == "done")
    assert fake_execute.calls == ids


def test_cancelling_a_queued_run_stops_it_before_it_starts(client, tid, fake_execute):
    _clear_runs()
    first = client.post("/api/runs", json={"template_ids": [tid], "count": 1}).json()["id"]
    second = client.post("/api/runs", json={"template_ids": [tid], "count": 1}).json()["id"]
    assert _until(lambda: fake_execute.calls == [first])
    assert client.post(f"/api/runs/{second}/cancel").status_code == 200
    assert _status(client, second)["status"] == "cancelled"          # immediately, not "cancelling"
    fake_execute.release(first)
    assert _until(lambda: _status(client, first)["status"] == "done")
    time.sleep(0.2)
    assert fake_execute.calls == [first]                              # the cancelled one never ran
    assert client.delete(f"/api/runs/{second}").status_code == 200   # and isn't "still active"


def test_cancel_during_claim_is_not_overwritten(client, tid):
    """execute() must not set status back to running: a cancel that lands right after the claim wins."""
    _clear_runs()
    run = runs.normalise_settings({"template_ids": [tid], "count": 1})
    rid = "20250101-120000-c0de"
    db.execute("INSERT INTO runs (id, created_at, settings, status, total, message) VALUES (?,?,?,?,?,?)",
               [rid, db.now(), run, "cancelling", 1, ""])
    runs.execute(rid)
    assert _status(client, rid)["status"] == "cancelled"


def test_restart_requeues_queued_runs_and_fails_running_ones(client, tid, fake_execute):
    _clear_runs()
    s = runs.normalise_settings({"template_ids": [tid], "count": 1})
    for rid, st in (("20250101-120000-0001", "running"), ("20250101-120001-0002", "queued")):
        db.execute("INSERT INTO runs (id, created_at, settings, status, total, message) VALUES (?,?,?,?,?,?)",
                   [rid, f"2025-01-01T12:00:0{rid[-1]}+00:00", s, st, 1, ""])
    fake_execute.release("20250101-120001-0002")
    runs.recover_interrupted()
    assert _status(client, "20250101-120000-0001")["status"] == "failed"
    assert _until(lambda: _status(client, "20250101-120001-0002")["status"] == "done")


# ------------------------------------------------------------------ search and paging

def test_search_filters_and_pages(client, tid):
    _clear_runs()
    s = runs.normalise_settings({"template_ids": [tid], "count": 1})
    for i in range(30):
        st = ("done", "failed", "cancelled")[i % 3]
        db.execute("INSERT INTO runs (id, created_at, settings, status, total, message, summary) VALUES (?,?,?,?,?,?,?)",
                   [f"20250101-1200{i:02d}-{i:04x}", f"2025-01-01T12:00:{i:02d}+00:00", {**s, "seed": 1000 + i}, st,
                    1, "Disk full" if i == 7 else "", {"templates": ["Queue test form"]} if st == "done" else {}])
    page = client.get("/api/runs?limit=10").json()
    assert page["total"] == 30 and len(page["items"]) == 10
    assert page["items"][0]["id"].endswith("-001d")                       # newest first
    assert client.get("/api/runs?limit=10&offset=25").json()["items"][-1]["id"].endswith("-0000")
    assert page["counts"] == {"all": 30, "active": 0, "done": 10, "failed": 10, "cancelled": 10}
    assert [r["id"][-4:] for r in client.get("/api/runs?q=disk").json()["items"]] == ["0007"]
    assert client.get("/api/runs?q=1007").json()["total"] == 1             # by seed
    assert client.get("/api/runs?q=queue+test&status=done").json()["total"] == 10  # template name + status
    assert client.get("/api/runs?q=QUEUE").json()["total"] == 30          # names resolved for runs without a summary
    assert client.get("/api/runs?status=nonsense").status_code == 400
    assert client.get("/api/runs/active").json() == []
    _clear_runs()


# ------------------------------------------------------------------ presets

def test_presets_crud(client, tid):
    p = client.post("/api/presets", json={"name": "  Fax   stress ", "settings": {
        "template_ids": [tid], "scan": "fax", "count": 50, "not_a_setting": 1}}).json()
    assert p["name"] == "Fax stress" and p["settings"]["scan"] == "fax" and "not_a_setting" not in p["settings"]
    assert client.post("/api/presets", json={"name": "fax STRESS", "settings": {}}).status_code == 400  # case-insensitive
    assert client.post("/api/presets", json={"name": " ", "settings": {}}).status_code == 400
    q = client.put(f"/api/presets/{p['id']}", json={"settings": {"scan": "heavy"}}).json()
    assert q["name"] == "Fax stress" and q["settings"] == {"scan": "heavy", "template_ids": []}
    other = client.post("/api/presets", json={"name": "Baseline", "settings": {}}).json()
    assert client.put(f"/api/presets/{other['id']}", json={"name": "fax stress"}).status_code == 400
    assert [x["name"] for x in client.get("/api/presets").json()] == ["Baseline", "Fax stress"]
    assert client.put("/api/presets/9999", json={"name": "x"}).status_code == 404
    for x in (p, other):
        client.delete(f"/api/presets/{x['id']}")
    assert client.get("/api/presets").json() == []
