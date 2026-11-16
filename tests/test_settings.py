"""Settings API: .env rewriting, validation, secrecy of the key and runtime reload. Never touches the real .env."""
import os
import stat

import pytest

KEY = "sk-test-0123456789abcdefWXYZ"
VARS = ("REQUESTY_API_KEY", "REQUESTY_BASE_URL", "REQUESTY_MODEL", "AI_CONCURRENCY", "AI_REASONING_EFFORT")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A temp .env, with app.config and os.environ restored after each test."""
    from app import config

    path = tmp_path / ".env"
    path.write_text(
        "# DataCreator settings\n"
        "REQUESTY_API_KEY=\n"
        "REQUESTY_BASE_URL=https://router.eu.requesty.ai/v1\n"
        "\n"
        "# keep me: an unrelated setting\n"
        "DATACREATOR_DATA=./somewhere\n"
        "# AI_CONCURRENCY=6            # parallel AI calls during a run\n"
    )
    monkeypatch.setenv("DATACREATOR_ENV_FILE", str(path))
    for attr in VARS:
        monkeypatch.setattr(config, attr, getattr(config, attr))
        monkeypatch.setenv(attr, os.environ.get(attr, ""))
    return path


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


def test_settings_path_is_never_the_real_env():
    from app import config, settings
    assert settings.env_path() != config.ROOT / ".env"


def test_get_defaults(env, client):
    r = client.get("/api/settings").json()
    assert r["key_set"] is False and r["key_hint"] is None
    assert r["base_url"] == "https://router.eu.requesty.ai/v1"
    assert r["defaults"] == {"base_url": "https://router.eu.requesty.ai/v1", "model": "vertex/claude-haiku-5-5@eu",
                             "concurrency": 6, "reasoning_effort": "none"}


def test_key_is_never_returned(env, client):
    r = client.put("/api/settings", json={"api_key": KEY})
    assert r.status_code == 200, r.text
    assert KEY not in r.text
    assert r.json()["key_set"] is True and r.json()["key_hint"] == "…WXYZ"
    g = client.get("/api/settings")
    assert KEY not in g.text and g.json()["key_hint"] == "…WXYZ"
    assert KEY not in client.get("/api/status").text
    # ...but it is stored, privately
    assert f"REQUESTY_API_KEY={KEY}\n" in env.read_text()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_test_connection_never_echoes_key(env, client):
    r = client.post("/api/settings/test", json={}).json()
    assert r["ok"] is False and "No API key" in r["error"]
    # Nothing listens on port 9, so this fails fast with a connection error that must not leak the key.
    r = client.post("/api/settings/test", json={"api_key": KEY, "base_url": "http://127.0.0.1:9/v1"})
    assert r.status_code == 200
    assert r.json()["ok"] is False and "connect" in r.json()["error"].lower()
    assert KEY not in r.text


def test_partial_update_keeps_other_settings_and_comments(env, client):
    client.put("/api/settings", json={"api_key": KEY})
    r = client.put("/api/settings", json={"model": "vertex/other-model@eu"})
    assert r.status_code == 200
    assert r.json()["key_set"] is True and r.json()["model"] == "vertex/other-model@eu"
    text = env.read_text()
    assert f"REQUESTY_API_KEY={KEY}\n" in text
    assert "REQUESTY_BASE_URL=https://router.eu.requesty.ai/v1\n" in text
    assert "REQUESTY_MODEL=vertex/other-model@eu\n" in text
    for kept in ("# DataCreator settings\n", "# keep me: an unrelated setting\n", "DATACREATOR_DATA=./somewhere\n",
                 "# AI_CONCURRENCY=6            # parallel AI calls during a run\n"):
        assert kept in text
    # in-place: the key stays on its original line
    assert text.splitlines()[1] == f"REQUESTY_API_KEY={KEY}"


def test_written_env_round_trips_through_dotenv(env, client):
    from dotenv import dotenv_values

    client.put("/api/settings", json={"api_key": KEY, "concurrency": "12", "reasoning_effort": "",
                                      "base_url": "http://localhost:4000/v1/"})
    vals = dotenv_values(env)
    assert vals["REQUESTY_API_KEY"] == KEY
    assert vals["AI_CONCURRENCY"] == "12"
    assert vals["AI_REASONING_EFFORT"] == ""
    assert vals["REQUESTY_BASE_URL"] == "http://localhost:4000/v1"
    assert vals["DATACREATOR_DATA"] == "./somewhere"


def test_remove_key(env, client):
    client.put("/api/settings", json={"api_key": KEY})
    r = client.put("/api/settings", json={"remove_key": True}).json()
    assert r["key_set"] is False
    assert "REQUESTY_API_KEY=\n" in env.read_text()
    assert client.get("/api/status").json()["ai_available"] is False


@pytest.mark.parametrize("body, msg", [
    ({"base_url": "ftp://example.com"}, "http"),
    ({"base_url": "router.eu.requesty.ai/v1"}, "http"),
    ({"concurrency": 0}, "1 to 32"),
    ({"concurrency": 33}, "1 to 32"),
    ({"concurrency": "six"}, "1 to 32"),
    ({"concurrency": True}, "1 to 32"),
    ({"reasoning_effort": "max"}, "none, low, medium, high"),
    ({"model": ""}, "blank"),
    ({"model": "two words"}, "no spaces"),
    ({"api_key": "abc\nEVIL=1"}, "one line"),
    ({"api_key": ""}, "Remove"),
    ({"surprise": 1}, "Unknown"),
])
def test_validation(env, client, body, msg):
    before = env.read_text()
    r = client.put("/api/settings", json=body)
    assert r.status_code == 400
    assert msg in r.json()["detail"]
    assert env.read_text() == before


def test_runtime_reload(env, client):
    from app import ai, config

    ai._client = object()  # pretend a client was cached with the old settings
    ai._effort_supported = False
    r = client.put("/api/settings", json={"api_key": KEY, "base_url": "https://gw.example.test/v1",
                                          "model": "m/x", "concurrency": 3, "reasoning_effort": "low"})
    assert r.status_code == 200
    assert (config.REQUESTY_API_KEY, config.REQUESTY_BASE_URL, config.REQUESTY_MODEL,
            config.AI_CONCURRENCY, config.AI_REASONING_EFFORT) == (KEY, "https://gw.example.test/v1", "m/x", 3, "low")
    assert ai._client is None and ai._effort_supported is True
    assert ai.available()
    status = client.get("/api/status").json()
    assert status["ai_available"] is True and status["ai_model"] == "m/x"
    c = ai._get_client()
    assert str(c.base_url).rstrip("/") == "https://gw.example.test/v1"
    ai.reset_client()


def test_missing_env_file_is_created(tmp_path, monkeypatch, env, client):
    path = tmp_path / "sub" / "new.env"
    monkeypatch.setenv("DATACREATOR_ENV_FILE", str(path))
    assert client.put("/api/settings", json={"concurrency": 4}).status_code == 200
    assert path.read_text() == "AI_CONCURRENCY=4\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
