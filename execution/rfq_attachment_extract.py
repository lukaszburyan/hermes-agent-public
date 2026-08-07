#!/usr/bin/env python3
"""Safe attachment extraction worker for Orchesta RFQ.

The deterministic router remains the first gate. This worker only extracts from
attachments whose route is allowed. Suspicious or unsupported files return a
Telegram-only decision and never reach OCR, marker-pdf, OpenAI, or instructor.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, List, Optional

try:
    from pydantic import BaseModel, ConfigDict, Field, field_validator
except Exception:  # pragma: no cover - runtime can still use deterministic extraction.
    class _FallbackBaseModel:
        def __init__(self, **kwargs: Any) -> None:
            fields: dict[str, Any] = {}
            for cls in reversed(type(self).mro()):
                fields.update(getattr(cls, "__annotations__", {}))
            for name in fields:
                if name == "model_config":
                    continue
                default = getattr(type(self), name, None)
                if isinstance(default, (list, dict, set)):
                    default = default.copy()
                setattr(self, name, kwargs.pop(name, default))
            for name, value in kwargs.items():
                setattr(self, name, value)

        def model_dump(self) -> dict[str, Any]:
            dumped: dict[str, Any] = {}
            for key, value in vars(self).items():
                if isinstance(value, _FallbackBaseModel):
                    dumped[key] = value.model_dump()
                elif isinstance(value, list):
                    dumped[key] = [item.model_dump() if isinstance(item, _FallbackBaseModel) else item for item in value]
                else:
                    dumped[key] = value
            return dumped

    BaseModel = _FallbackBaseModel  # type: ignore
    ConfigDict = lambda **kwargs: kwargs  # type: ignore

    def Field(default: Any = None, default_factory: Any = None, **_: Any) -> Any:  # type: ignore
        return default_factory() if default_factory is not None else default

    def field_validator(*_: Any, **__: Any) -> Any:  # type: ignore
        def decorator(func: Any) -> Any:
            return func

        return decorator

from attachment_router import route_attachment


DEFAULT_MAX_TEXT_CHARS = 12000
DEFAULT_MAX_PAGES = 8
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MARKER_TIMEOUT_SECONDS = 30
DEFAULT_VISION_MODEL = "gpt-5.4-mini"
DEFAULT_SCHEMA_MODEL = "gpt-5.4-nano"
DEFAULT_VISION_DETAIL = "low"
INVOICE_CURRENCY = "PLN"

DEFAULT_ATTACHMENT_EXTRACTION_CLASSES = {
    "new_quote_request",
    "quote_draft_ready",
    "existing_client_request",
    "existing_thread_reply",
    "same_domain_new_person",
}


class InvoiceLineItem(BaseModel):  # type: ignore[misc]
    model_config = ConfigDict(extra="ignore")

    description: Optional[str] = None
    quantity: Optional[str] = None
    unit: Optional[str] = None
    net_amount: Optional[str] = None
    vat_rate: Optional[str] = None
    gross_amount: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("net_amount", "gross_amount", mode="before")
    @classmethod
    def clean_line_amount(cls, value: Any) -> Optional[str]:
        return normalize_amount(value)


class InvoiceSchema(BaseModel):  # type: ignore[misc]
    """Structured invoice fields used by Hermes briefings and CRM summaries."""

    model_config = ConfigDict(extra="ignore")

    seller_name: Optional[str] = None
    buyer_name: Optional[str] = None
    seller_nip: Optional[str] = None
    buyer_nip: Optional[str] = None
    invoice_number: Optional[str] = None
    issue_date: Optional[str] = None
    due_date: Optional[str] = None
    service_period: Optional[str] = None
    net_amount: Optional[str] = None
    vat_amount: Optional[str] = None
    gross_amount: Optional[str] = None
    currency: str = INVOICE_CURRENCY
    bank_account: Optional[str] = None
    line_items: List[InvoiceLineItem] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_fields: List[str] = Field(default_factory=list)
    validation_notes: List[str] = Field(default_factory=list)

    @field_validator("seller_nip", "buyer_nip", mode="before")
    @classmethod
    def clean_nip(cls, value: Any) -> Optional[str]:
        if value in (None, ""):
            return None
        digits = re.sub(r"\D", "", str(value))
        return digits if len(digits) == 10 else str(value).strip()

    @field_validator("net_amount", "vat_amount", "gross_amount", mode="before")
    @classmethod
    def clean_amount(cls, value: Any) -> Optional[str]:
        return normalize_amount(value)

    @field_validator("currency", mode="before")
    @classmethod
    def clean_currency(cls, value: Any) -> str:
        text = str(value or INVOICE_CURRENCY).strip().upper()
        return text or INVOICE_CURRENCY


@dataclass
class CommandResult:
    ok: bool
    stdout: str
    stderr: str
    elapsed_seconds: float
    returncode: int | None
    error: str = ""


def normalize_amount(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("\xa0", " ")
    text = re.sub(r"[^0-9,.\- ]", "", text).strip()
    if not text:
        return None
    text = text.replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return str(Decimal(text).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        return str(value).strip()


def run_command(command: list[str], timeout: int = DEFAULT_TIMEOUT_SECONDS) -> CommandResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        return CommandResult(
            ok=completed.returncode == 0,
            stdout=completed.stdout[-20000:],
            stderr=completed.stderr[-12000:],
            elapsed_seconds=round(time.monotonic() - started, 3),
            returncode=completed.returncode,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            ok=False,
            stdout=(exc.stdout or "")[-20000:] if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr or "")[-12000:] if isinstance(exc.stderr, str) else "",
            elapsed_seconds=round(time.monotonic() - started, 3),
            returncode=None,
            error="timeout",
        )


def truncate_text(text: str, max_chars: int = DEFAULT_MAX_TEXT_CHARS) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...[truncated]"


def extract_text_pymupdf(path: Path, max_pages: int, max_chars: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        import fitz  # type: ignore
    except Exception as exc:
        return {"ok": False, "error": f"pymupdf_import_failed:{exc.__class__.__name__}"}

    try:
        with fitz.open(path) as document:
            if document.is_encrypted:
                return {"ok": False, "error": "pdf_encrypted"}
            page_count = int(document.page_count)
            chunks: list[str] = []
            for page_index in range(min(page_count, max_pages)):
                page = document.load_page(page_index)
                chunks.append(page.get_text("text") or "")
                if sum(len(chunk) for chunk in chunks) >= max_chars:
                    break
        text = truncate_text("\n".join(chunks), max_chars=max_chars)
        return {
            "ok": True,
            "extractor": "pymupdf",
            "text": text,
            "chars": len(text),
            "pages_seen": min(page_count, max_pages),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    except Exception as exc:
        return {"ok": False, "error": f"pymupdf_extract_failed:{exc.__class__.__name__}"}


def marker_commands() -> list[list[str]]:
    # marker-pdf/surya currently require Pillow <11, which has known security
    # vulnerabilities. Keep this extractor disabled unless a separately
    # maintained, vulnerability-scanned runtime is explicitly enabled.
    if os.environ.get("HERMES_RFQ_MARKER_ENABLED", "0").strip() != "1":
        return []

    configured = os.environ.get("MARKER_CMD", "").strip()
    if configured:
        return [configured.split()]

    commands: list[list[str]] = []
    marker_venv = os.environ.get("HERMES_RFQ_MARKER_VENV_DIR", "/opt/data/rfq-runtime/.venv-marker").strip()
    marker_bin = Path(marker_venv) / "bin"
    for name in ("marker_single", "marker"):
        local_path = marker_bin / name
        if local_path.exists():
            return [[str(local_path)]]

    candidate_bin_dirs = [
        Path(sys.executable).parent,
        Path(sys.prefix) / "bin",
        Path(sys.executable).resolve().parent,
    ]
    for name in ("marker_single", "marker"):
        for bin_dir in candidate_bin_dirs:
            local_path = bin_dir / name
            if local_path.exists():
                commands.append([str(local_path)])
        path = shutil.which(name)
        if path:
            commands.append([path])
    unique: list[list[str]] = []
    seen: set[str] = set()
    for command in commands:
        key = " ".join(command)
        if key not in seen:
            unique.append(command)
            seen.add(key)
    return unique


def collect_marker_output(output_dir: Path, max_chars: int) -> str:
    candidates: list[Path] = []
    for pattern in ("*.md", "*.txt", "*.json"):
        candidates.extend(output_dir.rglob(pattern))
    chunks: list[str] = []
    for candidate in sorted(candidates, key=lambda item: (item.suffix != ".md", len(str(item)))):
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if not text.strip():
            continue
        chunks.append(f"# {candidate.name}\n{text}")
        if sum(len(chunk) for chunk in chunks) >= max_chars:
            break
    return truncate_text("\n\n".join(chunks), max_chars=max_chars)


def extract_with_marker(path: Path, max_chars: int, timeout: int) -> dict[str, Any]:
    commands = marker_commands()
    if not commands:
        return {"ok": False, "error": "marker_command_not_found"}

    attempts: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="hermes-rfq-marker-") as tmp:
        tmp_dir = Path(tmp)
        output_dir = tmp_dir / "out"
        output_dir.mkdir(parents=True, exist_ok=True)
        input_dir = tmp_dir / "in"
        input_dir.mkdir(parents=True, exist_ok=True)
        copied_input = input_dir / path.name
        shutil.copy2(path, copied_input)
        for base_command in commands:
            command_name = Path(base_command[0]).name
            input_path = str(path) if command_name == "marker_single" else str(input_dir)
            command = [
                *base_command,
                input_path,
                "--output_dir",
                str(output_dir),
                "--output_format",
                "markdown",
            ]
            result = run_command(command, timeout=timeout)
            text = collect_marker_output(output_dir, max_chars=max_chars)
            attempts.append({"command": base_command[0], **asdict(result), "output_chars": len(text)})
            if result.ok and text:
                return {
                    "ok": True,
                    "extractor": "marker-pdf",
                    "text": text,
                    "chars": len(text),
                    "elapsed_seconds": result.elapsed_seconds,
                    "attempts": attempts,
                }
            if result.error == "timeout":
                break
        return {"ok": False, "error": "marker_failed_or_empty", "attempts": attempts}


def extract_ocr_tesseract(path: Path, timeout: int, max_chars: int) -> dict[str, Any]:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        return {"ok": False, "error": "tesseract_not_found"}
    language = os.environ.get("ORCHESTA_RFQ_TESSERACT_LANG", "pol+eng")
    result = run_command([tesseract, str(path), "stdout", "-l", language, "--psm", "6"], timeout=timeout)
    if not result.ok and language != "eng":
        result = run_command([tesseract, str(path), "stdout", "-l", "eng", "--psm", "6"], timeout=timeout)
    return {
        "ok": result.ok,
        "extractor": "tesseract",
        "text": truncate_text(result.stdout, max_chars=max_chars) if result.ok else "",
        "chars": len(result.stdout or ""),
        "elapsed_seconds": result.elapsed_seconds,
        "error": result.error or ("" if result.ok else result.stderr),
    }


def extract_scanned_pdf_tesseract(path: Path, max_pages: int, timeout: int, max_chars: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        import fitz  # type: ignore
    except Exception as exc:
        return {"ok": False, "error": f"pymupdf_import_failed:{exc.__class__.__name__}"}

    chunks: list[str] = []
    page_results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="hermes-rfq-pdf-ocr-") as tmp:
        try:
            with fitz.open(path) as document:
                if document.is_encrypted:
                    return {"ok": False, "error": "pdf_encrypted"}
                pages_seen = min(document.page_count, max_pages)
                for page_index in range(pages_seen):
                    page = document.load_page(page_index)
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    image_path = Path(tmp) / f"page-{page_index + 1}.png"
                    pixmap.save(image_path)
                    ocr = extract_ocr_tesseract(image_path, timeout=timeout, max_chars=max_chars)
                    page_results.append({"page": page_index + 1, **{k: v for k, v in ocr.items() if k != "text"}})
                    if ocr.get("text"):
                        chunks.append(f"## Page {page_index + 1}\n{ocr['text']}")
                    if sum(len(chunk) for chunk in chunks) >= max_chars:
                        break
        except Exception as exc:
            return {"ok": False, "error": f"pdf_render_ocr_failed:{exc.__class__.__name__}"}

    text = truncate_text("\n\n".join(chunks), max_chars=max_chars)
    return {
        "ok": bool(text),
        "extractor": "pymupdf_render_tesseract_fallback",
        "text": text,
        "chars": len(text),
        "pages_seen": len(page_results),
        "page_results": page_results,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


def configured_attachment_extraction_classes() -> set[str]:
    configured = os.environ.get("ORCHESTA_RFQ_ATTACHMENT_EXTRACT_CLASSES", "").strip()
    if not configured:
        return set(DEFAULT_ATTACHMENT_EXTRACTION_CLASSES)
    return {item.strip() for item in configured.split(",") if item.strip()}


def attachment_extraction_allowed(classification: str, confidence: str = "") -> bool:
    if not classification:
        return True
    if confidence.strip().lower() == "low":
        return False
    return classification.strip() in configured_attachment_extraction_classes()


def openai_vision_summary(path: Path, route: str, max_chars: int) -> dict[str, Any]:
    if not env_flag("ORCHESTA_RFQ_ALLOW_EXTERNAL_VISION"):
        return {"ok": False, "skipped": True, "error": "external_vision_not_enabled"}
    if not os.environ.get("OPENAI_API_KEY"):
        return {"ok": False, "skipped": True, "error": "openai_api_key_missing"}

    try:
        from openai import OpenAI
    except Exception as exc:
        return {"ok": False, "error": f"openai_import_failed:{exc.__class__.__name__}"}

    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data_url = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
    if route == "ocr_vision_technical_summary":
        instruction = (
            "Opisz po polsku widoczne elementy techniczne, etykiety, parametry i braki "
            "potrzebne do przygotowania wyceny. Badz zwiezly i nie zgaduj."
        )
    elif route == "ocr_vision_invoice_schema":
        instruction = (
            "Odczytaj z obrazu faktury najwazniejsze pola: sprzedawca, nabywca, NIP, "
            "numer faktury, daty, kwoty netto/VAT/brutto, waluta, rachunek bankowy. "
            "Jesli czegos nie widac, napisz null."
        )
    else:
        instruction = "Opisz po polsku, co jest widoczne na obrazie i co jest istotne dla odpowiedzi na mail."

    model = os.environ.get("ORCHESTA_RFQ_VISION_MODEL", DEFAULT_VISION_MODEL)
    detail = os.environ.get("ORCHESTA_RFQ_VISION_DETAIL", DEFAULT_VISION_DETAIL).strip().lower()
    if detail not in {"low", "high", "auto"}:
        detail = DEFAULT_VISION_DETAIL
    started = time.monotonic()
    try:
        client = OpenAI()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction},
                        {"type": "image_url", "image_url": {"url": data_url, "detail": detail}},
                    ],
                }
            ],
            temperature=0,
        )
        text = response.choices[0].message.content or ""
        return {
            "ok": True,
            "extractor": "openai_vision",
            "model": model,
            "detail": detail,
            "text": truncate_text(text, max_chars=max_chars),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    except Exception as exc:
        return {"ok": False, "error": f"openai_vision_failed:{exc.__class__.__name__}"}


def first_match(patterns: list[str], text: str, flags: int = re.IGNORECASE) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return match.group(1).strip(" :\t\r\n")
    return None


def deterministic_invoice_extract(text: str) -> InvoiceSchema:
    normalized = text.replace("\xa0", " ")
    nips = re.findall(r"(?:NIP|VAT(?: ID)?)[^\d]{0,8}([0-9][0-9\-\s]{8,20}[0-9])", normalized, re.IGNORECASE)
    bank = first_match([r"\b(?:nr rachunku|rachunek|konto|account)[^\d]{0,20}([0-9\s]{20,40})"], normalized)
    invoice_number = first_match(
        [
            r"(?:faktura\s+vat\s+(?:nr|numer)|invoice\s+(?:no|number)|nr\s+faktury)[^\w\/\-]{0,12}([A-Z0-9][A-Z0-9\/\-. ]{3,80})",
            r"\bFV[^\w]{0,4}([A-Z0-9\/\-.]{3,80})",
        ],
        normalized,
    )
    issue_date = first_match(
        [
            r"(?:data\s+wystawienia|issue\s+date)[^\d]{0,20}(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
            r"(?:wystawiono|issued)[^\d]{0,20}(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
        ],
        normalized,
    )
    due_date = first_match(
        [
            r"(?:termin\s+p[łl]atno[śs]ci|due\s+date|p[łl]atne\s+do)[^\d]{0,20}(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
        ],
        normalized,
    )
    gross_amount = first_match(
        [
            r"(?:razem\s+do\s+zap[łl]aty|do\s+zap[łl]aty|kwota\s+brutto|gross)[^0-9]{0,35}([0-9][0-9\s.,]{1,18})\s*(?:PLN|z[łl])?",
        ],
        normalized,
    )
    net_amount = first_match(
        [
            r"(?:warto[śs][ćc]\s+netto|kwota\s+netto|net)[^0-9]{0,35}([0-9][0-9\s.,]{1,18})\s*(?:PLN|z[łl])?",
        ],
        normalized,
    )
    vat_amount = first_match(
        [
            r"(?:kwota\s+vat|podatek\s+vat|vat)[^0-9]{0,35}([0-9][0-9\s.,]{1,18})\s*(?:PLN|z[łl])?",
        ],
        normalized,
    )

    cleaned_nips = [re.sub(r"\D", "", nip) for nip in nips]
    schema = InvoiceSchema(
        seller_nip=cleaned_nips[0] if len(cleaned_nips) >= 1 else None,
        buyer_nip=cleaned_nips[1] if len(cleaned_nips) >= 2 else None,
        invoice_number=invoice_number,
        issue_date=issue_date,
        due_date=due_date,
        net_amount=normalize_amount(net_amount),
        vat_amount=normalize_amount(vat_amount),
        gross_amount=normalize_amount(gross_amount),
        bank_account=bank,
        currency=INVOICE_CURRENCY if "PLN" in normalized.upper() or "ZŁ" in normalized.upper() else INVOICE_CURRENCY,
    )
    required = ["invoice_number", "seller_nip", "buyer_nip", "issue_date", "due_date", "gross_amount"]
    missing = [field for field in required if getattr(schema, field) in (None, "", [])]
    found_count = len(required) - len(missing)
    schema.missing_fields = missing
    schema.confidence = round(found_count / len(required), 2)
    schema.validation_notes = ["deterministic_regex_extract"]
    return schema


def instructor_invoice_extract(text: str) -> dict[str, Any]:
    if not env_flag("ORCHESTA_RFQ_ALLOW_SCHEMA_MODEL"):
        return {"ok": False, "skipped": True, "error": "schema_model_not_enabled"}
    if not os.environ.get("OPENAI_API_KEY"):
        return {"ok": False, "skipped": True, "error": "openai_api_key_missing"}
    try:
        import instructor
        from openai import OpenAI
    except Exception as exc:
        return {"ok": False, "error": f"instructor_import_failed:{exc.__class__.__name__}"}

    model = os.environ.get("ORCHESTA_RFQ_SCHEMA_MODEL", DEFAULT_SCHEMA_MODEL)
    started = time.monotonic()
    try:
        client = instructor.from_openai(OpenAI())
        schema = client.chat.completions.create(
            model=model,
            response_model=InvoiceSchema,
            messages=[
                {
                    "role": "system",
                    "content": "Extract invoice fields. Return null for missing fields. Do not invent values.",
                },
                {"role": "user", "content": truncate_text(text, max_chars=16000)},
            ],
            temperature=0,
        )
        return {
            "ok": True,
            "extractor": "instructor_openai",
            "model": model,
            "schema": schema.model_dump(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    except Exception as exc:
        return {"ok": False, "error": f"instructor_extract_failed:{exc.__class__.__name__}"}


def schema_from_text(text: str) -> dict[str, Any]:
    deterministic = deterministic_invoice_extract(text)
    modeled = instructor_invoice_extract(text)
    return {
        "deterministic_schema": deterministic.model_dump(),
        "instructor_schema": modeled,
    }


def extract_attachment(
    path: Path,
    *,
    subject: str = "",
    body: str = "",
    classification: str = "",
    confidence: str = "",
    max_pages: int = DEFAULT_MAX_PAGES,
    max_chars: int = DEFAULT_MAX_TEXT_CHARS,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    started = time.monotonic()
    attachment = {
        "filename": path.name,
        "mime": mimetypes.guess_type(path.name)[0],
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }
    route = route_attachment(attachment, subject=subject, body=body, path=path)
    result: dict[str, Any] = {
        "filename": path.name,
        "status": "ok",
        "telegram_only": False,
        "classification_gate": {
            "classification": classification,
            "confidence": confidence,
            "extraction_allowed": attachment_extraction_allowed(classification, confidence),
        },
        "route": route,
        "extraction": {},
    }

    if route["safety"] in {"block", "review"}:
        result["status"] = "telegram_only"
        result["telegram_only"] = True
        result["extraction"] = {"skipped": True, "reason": route.get("reason", "safety_gate")}
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return result

    if not attachment_extraction_allowed(classification, confidence):
        result["status"] = "skipped_by_classification"
        result["extraction"] = {
            "skipped": True,
            "reason": "message_not_classified_as_orchesta_rfq",
            "model_calls": "none",
        }
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return result

    route_name = route["route"]
    extraction: dict[str, Any]
    if route_name in {"pymupdf_text", "pymupdf_invoice_schema"}:
        extraction = extract_text_pymupdf(path, max_pages=max_pages, max_chars=max_chars)
    elif route_name in {"marker_ocr", "marker_invoice_schema"}:
        marker_timeout = int(os.environ.get("HERMES_RFQ_MARKER_TIMEOUT_SECONDS", DEFAULT_MARKER_TIMEOUT_SECONDS))
        extraction = extract_with_marker(path, max_chars=max_chars, timeout=min(timeout, marker_timeout))
        if not extraction.get("ok"):
            fallback = extract_scanned_pdf_tesseract(
                path,
                max_pages=min(max_pages, 3),
                timeout=min(timeout, 120),
                max_chars=max_chars,
            )
            fallback["marker_attempts"] = extraction.get("attempts", [])
            fallback["marker_error"] = extraction.get("error", "")
            extraction = fallback
    elif route_name in {"ocr_vision_summary", "ocr_vision_technical_summary", "ocr_vision_invoice_schema"}:
        ocr = extract_ocr_tesseract(path, timeout=min(timeout, 120), max_chars=max_chars)
        vision = openai_vision_summary(path, route_name, max_chars=max_chars)
        combined_text = truncate_text(
            "\n\n".join(part for part in [ocr.get("text", ""), vision.get("text", "")] if part),
            max_chars=max_chars,
        )
        empty_reasons: list[str] = []
        if not str(ocr.get("text") or "").strip():
            empty_reasons.append("ocr_empty" if ocr.get("ok") else f"ocr_failed:{ocr.get('error', 'unknown')}")
        if not str(vision.get("text") or "").strip():
            if vision.get("skipped"):
                empty_reasons.append(f"vision_skipped:{vision.get('error', 'unknown')}")
            elif not vision.get("ok"):
                empty_reasons.append(f"vision_failed:{vision.get('error', 'unknown')}")
        extraction = {
            "ok": bool(combined_text.strip()),
            "extractor": "tesseract_plus_optional_vision",
            "ocr": ocr,
            "vision": vision,
            "text": combined_text,
            "chars": len(combined_text),
        }
        if not combined_text.strip():
            extraction["empty_reason"] = ";".join(empty_reasons) or "ocr_vision_empty"
    else:
        result["status"] = "telegram_only"
        result["telegram_only"] = True
        result["extraction"] = {"skipped": True, "reason": "unsupported_route"}
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return result

    text = str(extraction.get("text") or "")
    if route_name in {"pymupdf_invoice_schema", "marker_invoice_schema", "ocr_vision_invoice_schema"} and text:
        extraction["invoice"] = schema_from_text(text)

    result["extraction"] = extraction
    if not extraction.get("ok"):
        result["status"] = "extractor_error"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def tool_check() -> int:
    tools = {
        "marker_commands": [" ".join(command) for command in marker_commands()],
        "tesseract": shutil.which("tesseract") or "",
        "external_vision_enabled": env_flag("ORCHESTA_RFQ_ALLOW_EXTERNAL_VISION"),
        "schema_model_enabled": env_flag("ORCHESTA_RFQ_ALLOW_SCHEMA_MODEL"),
        "openai_api_key_present": bool(os.environ.get("OPENAI_API_KEY")),
        "vision_model": os.environ.get("ORCHESTA_RFQ_VISION_MODEL", DEFAULT_VISION_MODEL),
        "schema_model": os.environ.get("ORCHESTA_RFQ_SCHEMA_MODEL", DEFAULT_SCHEMA_MODEL),
        "vision_detail": os.environ.get("ORCHESTA_RFQ_VISION_DETAIL", DEFAULT_VISION_DETAIL),
        "attachment_extract_classes": sorted(configured_attachment_extraction_classes()),
    }
    print(json.dumps(tools, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def self_test() -> int:
    sample = """
    FAKTURA VAT NR FV/12/2026
    Sprzedawca: Test Energia Sp. z o.o. NIP 123-456-78-90
    Nabywca: Orchesta Sp. z o.o. NIP 987-654-32-10
    Data wystawienia: 24.06.2026
    Termin płatności: 01.07.2026
    Wartość netto 1000,00 PLN
    Kwota VAT 230,00 PLN
    Razem do zapłaty 1230,00 PLN
    Nr rachunku 12 3456 7890 1234 5678 9012 3456
    """
    schema = deterministic_invoice_extract(sample)
    failures: list[str] = []
    if schema.invoice_number != "FV/12/2026":
        failures.append(f"invoice_number={schema.invoice_number}")
    if schema.seller_nip != "1234567890":
        failures.append(f"seller_nip={schema.seller_nip}")
    if schema.buyer_nip != "9876543210":
        failures.append(f"buyer_nip={schema.buyer_nip}")
    if schema.gross_amount != "1230.00":
        failures.append(f"gross_amount={schema.gross_amount}")
    if failures:
        print("rfq_attachment_extract self-test failures:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("rfq_attachment_extract self-test: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="Attachment path to process.")
    parser.add_argument("--subject", default="", help="Email subject used for routing context.")
    parser.add_argument("--body", default="", help="Email body used for routing context.")
    parser.add_argument("--classification", default="", help="Pre-check message class. Non-RFQ classes skip expensive extraction.")
    parser.add_argument("--confidence", default="", help="Pre-check classification confidence.")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_TEXT_CHARS)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--tool-check", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.tool_check:
        return tool_check()
    if not args.file:
        parser.error("--file is required unless --self-test or --tool-check is used")

    output = extract_attachment(
        Path(args.file),
        subject=args.subject,
        body=args.body,
        classification=args.classification,
        confidence=args.confidence,
        max_pages=args.max_pages,
        max_chars=args.max_chars,
        timeout=args.timeout,
    )
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
