"""LLM calls through the Requesty gateway (OpenAI-compatible API).

Only synthetic data is ever sent: type descriptions and fictional records.
"""

from __future__ import annotations

import json
import re

from openai import BadRequestError, OpenAI

from . import config

SYSTEM = (
    "You create realistic but entirely fictional synthetic data for testing document-processing (OCR) "
    "systems, training staff and populating test environments in a UK healthcare / home-care setting. "
    "People must be fictional, but real organisations, places, medicines and clinical terminology are "
    "preferred for realism. Use UK spelling, formats and conventions. "
    "Respond with JSON only: no commentary and no markdown code fences."
)

_client: OpenAI | None = None


class AiError(Exception):
    pass


def available() -> bool:
    return bool(config.REQUESTY_API_KEY)


def _get_client() -> OpenAI:
    global _client
    if not available():
        raise AiError("AI is not configured: set REQUESTY_API_KEY in .env")
    if _client is None:
        _client = OpenAI(api_key=config.REQUESTY_API_KEY, base_url=config.REQUESTY_BASE_URL,
                         timeout=120, max_retries=3)
    return _client


def _parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    if not starts:
        raise AiError("Model did not return JSON")
    start = min(starts)
    end = max(text.rfind("]"), text.rfind("}"))
    return json.loads(text[start:end + 1])


_effort_supported = True


def _create(user, max_tokens: int, system: str = SYSTEM, temperature: float = 1.0):
    """user: a string, or a list of OpenAI content parts (text / image_url)."""
    global _effort_supported
    kw = {}
    if config.AI_REASONING_EFFORT and _effort_supported:
        kw["reasoning_effort"] = config.AI_REASONING_EFFORT
    try:
        return _get_client().chat.completions.create(
            model=config.REQUESTY_MODEL, max_tokens=max_tokens, temperature=temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **kw)
    except BadRequestError as e:
        if kw and "reason" in str(e).lower():
            _effort_supported = False  # model/gateway doesn't accept it; use the default from now on
            return _create(user, max_tokens, system, temperature)
        raise


def _chat_json(user, max_tokens: int = 4000, system: str = SYSTEM, temperature: float = 1.0):
    last_err = None
    for _ in range(2):
        try:
            resp = _create(user, max_tokens, system, temperature)
            choice = resp.choices[0]
            if choice.finish_reason == "length":
                last_err = AiError("response was cut off (token limit reached)")
                max_tokens = min(max_tokens * 2, 32000)
                continue
            return _parse_json(choice.message.content or "")
        except (json.JSONDecodeError, AiError) as e:
            last_err = e
        except Exception as e:  # network / API errors after the client's own retries
            raise AiError(f"AI request failed: {e}") from e
    raise AiError(f"Could not get a usable AI response: {last_err}")


def generate_list(description: str, n: int) -> list[str]:
    """A list of n distinct values for a data type described in plain English."""
    n = max(1, min(int(n), 500))
    out: list[str] = []
    seen: set[str] = set()
    attempts = 0
    while len(out) < n and attempts < 8:
        attempts += 1
        want = min(100, n - len(out))
        avoid = ""
        if out:
            avoid = "\nDo not repeat any of these existing values: " + json.dumps(out[-150:])
        data = _chat_json(
            f"Data type description: {description}\n"
            f"Generate {want} distinct, varied, realistic values for this data type. Each value is what would "
            f"be written in a single form field.{avoid}\n"
            f"Return a JSON array of {want} strings.",
            max_tokens=min(16000, 2000 + want * 60),
        )
        if not isinstance(data, list):
            raise AiError("Expected a JSON array")
        for v in data:
            s = str(v).strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                out.append(s)
    return out[:n]


def fill_fields(record: dict, fields: list[dict]) -> dict[str, str]:
    """Write free-text values for several fields of one fictional record in a single call.

    fields: [{"name": ..., "prompt": ..., "max_words": int|None}]
    """
    lines = []
    for f in fields:
        limit = f" (at most {int(f['max_words'])} words)" if f.get("max_words") else ""
        lines.append(f'- "{f["name"]}": {f["prompt"]}{limit}')
    data = _chat_json(
        "Here is one fictional record:\n" + json.dumps(record, indent=1, default=str) +
        "\n\nWrite a value for each of these form fields so that it is consistent with the record:\n" +
        "\n".join(lines) +
        "\n\nReturn a JSON object mapping each field name to a string value.",
        max_tokens=min(16000, 2000 + 400 * len(fields)),
    )
    if not isinstance(data, dict):
        raise AiError("Expected a JSON object")
    return {f["name"]: str(data.get(f["name"], "")).strip() for f in fields}
