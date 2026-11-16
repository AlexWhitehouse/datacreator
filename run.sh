#!/usr/bin/env bash
# Start DataCreator at http://127.0.0.1:${PORT:-8765}. Sets up the Python environment on first run.
#   ./run.sh           set up if needed, then start the server
#   ./run.sh --setup   set up (or update) the environment and exit; add --dev for the test tools
set -euo pipefail
cd "$(dirname "$0")"

SETUP_ONLY=; DEV=
for arg in "$@"; do
  case "$arg" in
    --setup) SETUP_ONLY=1 ;;
    --dev) DEV=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ ! -x .venv/bin/python ]; then
  echo "Setting up Python environment..."
  if ! python3 -m venv .venv 2>/dev/null; then
    # Some distros ship Python without ensurepip: create the venv bare and bootstrap pip.
    rm -rf .venv
    python3 -m venv --without-pip .venv
    curl -fsSL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python - -q
  fi
  FRESH=1
fi

if [ -n "${FRESH:-}" ] || [ -n "$SETUP_ONLY" ]; then
  .venv/bin/pip install -q --disable-pip-version-check -r requirements.txt
  [ -z "$DEV" ] || .venv/bin/pip install -q --disable-pip-version-check -r requirements-dev.txt
fi
[ -z "$SETUP_ONLY" ] || exit 0

[ -f .env ] || echo "Note: no .env file. AI data types are disabled until you create one (see .env.example)."

PORT="${PORT:-8765}"
echo "DataCreator running at http://127.0.0.1:${PORT}  (Ctrl+C to stop)"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
