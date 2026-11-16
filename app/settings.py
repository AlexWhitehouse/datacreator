"""Settings page API: read and change the AI settings that live in .env, without a restart.

The stored API key never leaves the server: GET only says whether one is set (plus its last 4 characters).
Changes rewrite .env in place (unrelated lines and comments are kept, file mode 0600) and update the live
values in app.config, then drop ai.py's cached client so the next AI call uses them.
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Body, HTTPException

from . import ai, config

router = APIRouter(prefix="/api/settings", tags=["settings"])

EFFORTS = ("none", "low", "medium", "high")
MAX_CONCURRENCY = 32

# setting name -> (.env variable, config attribute)
FIELDS = {
    "api_key": ("REQUESTY_API_KEY", "REQUESTY_API_KEY"),
    "base_url": ("REQUESTY_BASE_URL", "REQUESTY_BASE_URL"),
    "model": ("REQUESTY_MODEL", "REQUESTY_MODEL"),
    "concurrency": ("AI_CONCURRENCY", "AI_CONCURRENCY"),
    "reasoning_effort": ("AI_REASONING_EFFORT", "AI_REASONING_EFFORT"),
}

_lock = threading.Lock()


def env_path() -> Path:
    """The .env file to rewrite. DATACREATOR_ENV_FILE overrides it (tests use a temp file)."""
    override = os.environ.get("DATACREATOR_ENV_FILE")
    return Path(override) if override else config.ENV_FILE


# ---------------------------------------------------------------- reading

def _key_hint(key: str) -> str | None:
    # Too short to show any of it without giving most of it away.
    return f"…{key[-4:]}" if len(key) >= 12 else None


def current() -> dict:
    key = config.REQUESTY_API_KEY
    return {
        "key_set": bool(key),
        "key_hint": _key_hint(key) if key else None,
        "base_url": config.REQUESTY_BASE_URL,
        "model": config.REQUESTY_MODEL,
        "concurrency": config.AI_CONCURRENCY,
        "reasoning_effort": config.AI_REASONING_EFFORT,
        "defaults": {
            "base_url": config.DEFAULT_BASE_URL, "model": config.DEFAULT_MODEL,
            "concurrency": config.DEFAULT_CONCURRENCY, "reasoning_effort": config.DEFAULT_REASONING_EFFORT,
        },
        "efforts": list(EFFORTS),
        "max_concurrency": MAX_CONCURRENCY,
        "env_file": str(env_path()),
    }


# ---------------------------------------------------------------- validation

_PRINTABLE = re.compile(r"^[\x21-\x7e]+$")  # visible ASCII, no spaces / newlines (keeps .env one value per line)


def _clean_url(v) -> str:
    v = str(v or "").strip()
    p = urlparse(v)
    if p.scheme not in ("http", "https") or not p.netloc or not _PRINTABLE.match(v):
        raise ValueError("Base URL must be a full http:// or https:// address, e.g. https://router.eu.requesty.ai/v1")
    return v.rstrip("/")


def _clean_model(v) -> str:
    v = str(v or "").strip()
    if not v:
        raise ValueError("Model can't be blank")
    if not _PRINTABLE.match(v) or len(v) > 200:
        raise ValueError("Model must be a single model ID with no spaces, e.g. vertex/claude-haiku-5-5@eu")
    return v


def _clean_concurrency(v) -> int:
    if isinstance(v, int) and not isinstance(v, bool):
        n = v
    elif isinstance(v, str) and v.strip().isdigit():
        n = int(v.strip())
    else:
        n = 0
    if not 1 <= n <= MAX_CONCURRENCY:
        raise ValueError(f"Concurrency must be a whole number from 1 to {MAX_CONCURRENCY}")
    return n


def _clean_effort(v) -> str:
    v = str(v or "").strip().lower()
    if v and v not in EFFORTS:
        raise ValueError("Reasoning effort must be none, low, medium, high, or blank for the model's default")
    return v


def _clean_key(v) -> str:
    v = str(v or "").strip()
    if not v:
        raise ValueError("Paste a key, or use Remove to clear the stored one")
    if not _PRINTABLE.match(v) or len(v) > 500:
        raise ValueError("That doesn't look like an API key: it should be one line with no spaces")
    return v


CLEANERS = {"api_key": _clean_key, "base_url": _clean_url, "model": _clean_model,
            "concurrency": _clean_concurrency, "reasoning_effort": _clean_effort}


def validate(body: dict) -> dict:
    """The settings in body that should change, cleaned. Missing / null fields are left as they are."""
    if not isinstance(body, dict):
        raise ValueError("Expected a JSON object")
    unknown = set(body) - set(CLEANERS) - {"remove_key"}
    if unknown:
        raise ValueError(f"Unknown setting: {', '.join(sorted(unknown))}")
    out = {k: CLEANERS[k](v) for k, v in body.items() if k in CLEANERS and v is not None}
    if body.get("remove_key"):
        if "api_key" in out:
            raise ValueError("Send either a new key or remove_key, not both")
        out["api_key"] = ""
    return out


# ---------------------------------------------------------------- .env rewriting

_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./:@+,=~%-]*$")


def _env_value(v: str) -> str:
    if _SAFE_VALUE.match(v):
        return v
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Set VAR=value for each update, in place. Other lines (and comments) are kept as they are; variables
    that aren't in the file yet are appended at the end. Written atomically with mode 0600."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    out = []
    for line in lines:
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in updates:
            var = m.group(1)
            if var not in pending:
                continue  # a duplicate of a variable we've already written: drop it so it can't win
            out.append(f"{var}={_env_value(pending.pop(var))}")
        else:
            out.append(line)
    if pending:
        if out and out[-1].strip():
            out.append("")
        out.extend(f"{var}={_env_value(v)}" for var, v in pending.items())
    text = "\n".join(out) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".env.", dir=path.parent)  # mkstemp creates it 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)


def apply(changes: dict) -> None:
    """Persist cleaned changes to .env and make them live in this process."""
    if not changes:
        return
    env_updates = {FIELDS[k][0]: str(v) for k, v in changes.items()}
    write_env(env_path(), env_updates)
    for k, v in changes.items():
        var, attr = FIELDS[k]
        setattr(config, attr, v)
        # Keep the process environment in step: load_dotenv never overrides an existing variable, so after an
        # in-place restart (self-update re-execs with this environment) a stale value would beat .env.
        os.environ[var] = str(v)
    ai.reset_client()


# ---------------------------------------------------------------- routes

@router.get("")
def get_settings():
    return current()


@router.put("")
def put_settings(body: dict = Body(...)):
    try:
        changes = validate(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with _lock:
        try:
            apply(changes)
        except OSError as e:
            raise HTTPException(500, f"Couldn't write {env_path().name}: {e.strerror or e}")
    return current()


def _scrub(text: str, *secrets: str) -> str:
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "•••")
    return text


def _gateway_message(e: Exception) -> str:
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        msg = err.get("message")
        if isinstance(msg, str):
            return msg.strip()[:200]
    return ""


def _explain(e: Exception, base_url: str, model: str) -> str:
    """An actionable one-liner for a failed test call."""
    import openai

    status = getattr(e, "status_code", None)
    host = urlparse(base_url).netloc or base_url
    if isinstance(e, openai.APITimeoutError):
        return f"No answer from {host} within 20 seconds. Check the base URL and your network or proxy."
    if isinstance(e, openai.APIConnectionError):
        return f"Couldn't connect to {host}. Check the base URL, and that this machine can reach it."
    said = _gateway_message(e)
    said = f' The gateway said: "{said}".' if said else ""
    if isinstance(e, openai.AuthenticationError) or status == 401:
        return f"The gateway rejected the key (401).{said} Check it was copied in full, or create a new one."
    if isinstance(e, openai.PermissionDeniedError) or status == 403:
        # Requesty answers 403 "Invalid authorization token" for a wrong key, so don't assume it's permissions.
        return (f"The gateway refused the request (403).{said} Check the key is correct and is allowed to use "
                f"{model}.")
    if isinstance(e, openai.NotFoundError) or status == 404:
        return (f"Not found (404). Check the model ID ({model}) and that the base URL ends in the API "
                "version, e.g. /v1.")
    if isinstance(e, openai.RateLimitError) or status == 429:
        return "Rate limited or out of credit (429). Wait a moment, or check the account's limits and balance."
    if status and status >= 500:
        return f"The gateway had a problem ({status}). Try again in a minute."
    msg = getattr(e, "message", None) or str(e)
    return f"{'(' + str(status) + ') ' if status else ''}{msg}"[:400]


@router.post("/test")
def test_connection(body: dict | None = Body(None)):
    """One tiny, bounded AI call with the given settings (falling back to the saved ones). Unsaved values in
    the body let you test before saving; an api_key in the body is used for this call only."""
    from openai import BadRequestError, OpenAI

    body = body or {}
    try:
        base_url = _clean_url(body["base_url"]) if body.get("base_url") else config.REQUESTY_BASE_URL
        model = _clean_model(body["model"]) if body.get("model") else config.REQUESTY_MODEL
        effort = _clean_effort(body["reasoning_effort"]) if "reasoning_effort" in body else config.AI_REASONING_EFFORT
        key = _clean_key(body["api_key"]) if body.get("api_key") else config.REQUESTY_API_KEY
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not key:
        return {"ok": False, "error": "No API key is set. Paste one above, then test again."}

    client = OpenAI(api_key=key, base_url=base_url, timeout=20, max_retries=0)
    kw = {"reasoning_effort": effort} if effort else {}
    note = None
    t0 = time.perf_counter()
    try:
        try:
            resp = client.chat.completions.create(
                model=model, max_tokens=8, temperature=0,
                messages=[{"role": "user", "content": "Reply with the single word OK."}], **kw)
        except BadRequestError as e:
            if not kw or "reason" not in str(e).lower():
                raise
            note = (f'This model doesn\'t accept reasoning effort "{effort}", so DataCreator will leave it '
                    "out automatically.")
            t0 = time.perf_counter()
            resp = client.chat.completions.create(
                model=model, max_tokens=8, temperature=0,
                messages=[{"role": "user", "content": "Reply with the single word OK."}])
    except Exception as e:  # noqa: BLE001 - every failure becomes a message for the user
        return {"ok": False, "error": _scrub(_explain(e, base_url, model), key)}
    finally:
        client.close()
    latency = round((time.perf_counter() - t0) * 1000)
    reply = ""
    try:
        reply = (resp.choices[0].message.content or "").strip()[:40]
    except (AttributeError, IndexError):
        pass
    return {"ok": True, "latency_ms": latency, "model": getattr(resp, "model", None) or model,
            "reply": _scrub(reply, key), "note": note}
