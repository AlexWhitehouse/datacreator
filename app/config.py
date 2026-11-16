import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
STATIC_DIR = ROOT / "static"
FONTS_DIR = APP_DIR / "fonts"
DATA_DIR = Path(os.environ.get("DATACREATOR_DATA", ROOT / "data"))
TEMPLATES_DIR = DATA_DIR / "templates"
RUNS_DIR = DATA_DIR / "runs"
DB_PATH = DATA_DIR / "datacreator.db"

# Settings live in .env (edited from the Settings page, or by hand). Tests point this at a temp file.
ENV_FILE = Path(os.environ.get("DATACREATOR_ENV_FILE") or ROOT / ".env")
load_dotenv(ENV_FILE)

DEFAULT_BASE_URL = "https://router.eu.requesty.ai/v1"
DEFAULT_MODEL = "vertex/claude-haiku-5-5@eu"
DEFAULT_CONCURRENCY = 6
DEFAULT_REASONING_EFFORT = "none"

REQUESTY_API_KEY = os.environ.get("REQUESTY_API_KEY", "").strip()
REQUESTY_BASE_URL = os.environ.get("REQUESTY_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
REQUESTY_MODEL = os.environ.get("REQUESTY_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
AI_CONCURRENCY = int(os.environ.get("AI_CONCURRENCY", "").strip() or DEFAULT_CONCURRENCY)
# "none" skips the model's hidden reasoning: faster and cheaper for simple form-filling. Blank = model default.
# These module values are the live settings: app/settings.py updates them at runtime, so read them as
# config.X at call time rather than importing the names.
AI_REASONING_EFFORT = os.environ.get("AI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT).strip()

for d in (DATA_DIR, TEMPLATES_DIR, RUNS_DIR):
    d.mkdir(parents=True, exist_ok=True)
