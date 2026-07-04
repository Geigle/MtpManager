#!/usr/bin/env bash
# Always use the project venv — no need to activate it manually.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  echo "Creating virtual environment..."
  PYTHON=""
  if [[ "$(uname -s)" == "Darwin" ]]; then
    # CLT Python 3.9 breaks tkinter on macOS 26+; prefer Homebrew 3.13.
    for candidate in /opt/homebrew/bin/python3.13 /usr/local/bin/python3.13; do
      if [[ -x "$candidate" ]]; then
        PYTHON="$candidate"
        break
      fi
    done
  fi
  PYTHON="${PYTHON:-python3}"
  echo "Using: $PYTHON"
  "$PYTHON" -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

exec .venv/bin/python mm.py "$@"