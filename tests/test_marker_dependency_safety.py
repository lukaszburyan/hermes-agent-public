from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXTRACTOR = ROOT / "execution" / "rfq_attachment_extract.py"


def load_extractor():
    spec = importlib.util.spec_from_file_location("marker_dependency_safety", EXTRACTOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(ROOT / "execution"))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROOT / "execution"))
    return module


def test_marker_is_disabled_by_default_even_when_binary_is_on_path(monkeypatch):
    module = load_extractor()
    monkeypatch.delenv("HERMES_RFQ_MARKER_ENABLED", raising=False)
    monkeypatch.setenv("MARKER_CMD", "/tmp/legacy-marker-single")

    assert module.marker_commands() == []


def test_marker_requires_exact_explicit_enable(monkeypatch):
    module = load_extractor()
    monkeypatch.setenv("MARKER_CMD", "/tmp/controlled-marker-single")

    monkeypatch.setenv("HERMES_RFQ_MARKER_ENABLED", "true")
    assert module.marker_commands() == []

    monkeypatch.setenv("HERMES_RFQ_MARKER_ENABLED", "1")
    assert module.marker_commands() == [["/tmp/controlled-marker-single"]]
