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
command -v pandoc >/dev/null || echo "note: pandoc not found (brew install pandoc) - scanned books need it"
command -v tesseract >/dev/null || echo "note: tesseract not found (brew install tesseract tesseract-lang) - scanned books need it"
