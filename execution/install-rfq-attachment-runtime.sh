#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${HERMES_RFQ_RUNTIME_DIR:-/opt/data/rfq-runtime}"
VENV_DIR="${HERMES_RFQ_VENV_DIR:-$RUNTIME_DIR/.venv}"
REQ_FILE="${HERMES_RFQ_REQUIREMENTS:-$SCRIPT_DIR/requirements-rfq-attachments.lock}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "== Orchesta RFQ attachment runtime install =="
echo "runtime_dir=$RUNTIME_DIR"
echo "venv_dir=$VENV_DIR"
echo "marker_pdf=disabled (upstream dependency requires vulnerable Pillow <11)"

mkdir -p "$RUNTIME_DIR"
mkdir -p "$RUNTIME_DIR/pip-cache"
export PIP_CACHE_DIR="$RUNTIME_DIR/pip-cache"

if command -v apt-get >/dev/null 2>&1 && [[ "${HERMES_RFQ_SKIP_APT:-0}" != "1" ]]; then
  echo "== System packages =="
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates \
    libgl1 \
    libglib2.0-0 \
    libmagic1 \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-pol
else
  echo "== System packages skipped =="
fi

echo "== Python virtualenv =="
"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --require-hashes -r "$REQ_FILE"

echo "== Import check =="
"$VENV_DIR/bin/python" - <<'PY'
import importlib

required = {
    "fitz": "pymupdf",
    "openai": "openai",
    "instructor": "instructor",
    "pydantic": "pydantic",
    "PIL": "pillow",
}

for module, package in required.items():
    importlib.import_module(module)
    print(f"{package}: ok")

PY

echo "== Tool check =="
if ! command -v tesseract; then
  echo "tesseract: not found; scanned PDFs will require manual review" >&2
fi
export PATH="$VENV_DIR/bin:$PATH"
export HERMES_RFQ_MARKER_ENABLED=0
"$VENV_DIR/bin/python" "$SCRIPT_DIR/rfq_attachment_extract.py" --tool-check
"$VENV_DIR/bin/python" "$SCRIPT_DIR/rfq_attachment_extract.py" --self-test

echo "rfq attachment runtime: ok"
