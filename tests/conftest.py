"""Runs before any test module is imported, so app.config can never pick up the real data/ folder or
the real .env API key, whatever order test files import the app in."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["DATACREATOR_DATA"] = tempfile.mkdtemp(prefix="datacreator-tests-")
os.environ["DATACREATOR_ENV_FILE"] = str(Path(os.environ["DATACREATOR_DATA"]) / "test.env")  # never the real .env
os.environ["REQUESTY_API_KEY"] = ""  # never call the AI from tests (load_dotenv won't override this)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
