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

load_dotenv(ROOT / ".env")

REQUESTY_API_KEY = os.environ.get("REQUESTY_API_KEY", "").strip()
REQUESTY_BASE_URL = os.environ.get("REQUESTY_BASE_URL", "https://router.eu.requesty.ai/v1").strip()
REQUESTY_MODEL = os.environ.get("REQUESTY_MODEL", "vertex/claude-haiku-5-5@eu").strip()
AI_CONCURRENCY = int(os.environ.get("AI_CONCURRENCY", "6"))
# "none" skips the model's hidden reasoning: faster and cheaper for simple form-filling. Blank = model default.
AI_REASONING_EFFORT = os.environ.get("AI_REASONING_EFFORT", "none").strip()

for d in (DATA_DIR, TEMPLATES_DIR, RUNS_DIR):
    d.mkdir(parents=True, exist_ok=True)
