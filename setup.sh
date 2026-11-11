#!/bin/sh
# Create .venv and install pdf2epub into it (editable). Re-run after changing dependencies.
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e .
echo "ready: ./pdf2epub --help"
command -v epubcheck >/dev/null || echo "note: epubcheck not found (brew install epubcheck) - ./pdf2epub check needs it"
