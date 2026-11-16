#!/usr/bin/env bash
# DataCreator dev install: clones the repo (or updates an existing clone), sets up .venv with the
# app and test requirements, creates .env from the example, writes sample forms and runs the tests.
#
#   curl -fsSL https://raw.githubusercontent.com/AlexWhitehouse/datacreator/main/install.sh | bash
#
# Options (environment variables):
#   DATACREATOR_DIR=path   where to clone (default: ./datacreator)
#   DATACREATOR_REF=name   branch or tag to check out (default: main)
#   SKIP_TESTS=1           don't run the test suite at the end
set -euo pipefail

main() {
  local repo="https://github.com/AlexWhitehouse/datacreator.git"
  local dir="${DATACREATOR_DIR:-datacreator}"
  local ref="${DATACREATOR_REF:-main}"

  say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
  die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

  for cmd in git python3 curl; do
    command -v "$cmd" >/dev/null 2>&1 || die "$cmd is required but not installed."
  done
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
    || die "Python 3.11 or newer is required (found $(python3 -V 2>&1))."

  if [ -d "$dir/.git" ]; then
    say "Updating existing clone in $dir"
    git -C "$dir" fetch --quiet origin "$ref"
    git -C "$dir" checkout --quiet "$ref"
    git -C "$dir" pull --quiet --ff-only origin "$ref"
  elif [ -e "$dir" ]; then
    die "$dir already exists and is not a git clone. Set DATACREATOR_DIR to another path."
  else
    say "Cloning into $dir"
    git clone --quiet --branch "$ref" "$repo" "$dir"
  fi
  cd "$dir"

  say "Installing Python packages into .venv (app + dev tools)"
  ./run.sh --setup --dev

  if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    say "Created .env (add REQUESTY_API_KEY to turn on AI features)"
  fi

  say "Writing sample forms to samples/"
  .venv/bin/python scripts/make_sample_forms.py samples >/dev/null

  if [ -z "${SKIP_TESTS:-}" ]; then
    say "Running tests"
    .venv/bin/python -m pytest -q tests
  fi

  say "Done. Start the app with:"
  echo "    cd $(pwd) && ./run.sh"
  echo "    then open http://127.0.0.1:8765"
}

main "$@"
