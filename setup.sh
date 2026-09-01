#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_COMMAND="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_COMMAND" >/dev/null 2>&1; then
  echo "Python was not found. Install Python 3.11 or newer, or set PYTHON_BIN." >&2
  exit 1
fi

if ! "$PYTHON_COMMAND" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "Python 3.11 or newer is required." >&2
  exit 1
fi

if [ ! -d "$PROJECT_DIR/.venv" ]; then
  "$PYTHON_COMMAND" -m venv "$PROJECT_DIR/.venv"
fi

"$PROJECT_DIR/.venv/bin/python" -m pip install -r "$PROJECT_DIR/requirements.txt"

if [ ! -f "$PROJECT_DIR/.env" ]; then
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
  echo "Created .env from .env.example. Add your credentials before using integrations."
fi

echo
echo "Setup complete."
echo "Activate the environment with: source .venv/bin/activate"
echo "Run the test suite with:        .venv/bin/python -m pytest"
echo "Validate the catalog with:      python scripts/catalog.py validate"
echo "Then fill in .env and run:      python scripts/spotify.py auth"
echo "Start playlist research with:   python scripts/classification.py research --playlist \"Playlist name or URL\""
