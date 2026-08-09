# 05 — Skrypty CLI, interfejsy i testy — archived export

> Historyczny snapshot kodu, nie wykonywalne źródło wydania. Aktywny kod jest w
> `execution/`, a kanoniczne entrypointy w `scripts/`. Stara flaga `--auto-send`
> nie może włączyć transportu.

## Pomoc CLI

### `execution/zoho_mail_poller.py --help`

Exit code: `0`

```text
usage: zoho_mail_poller.py [-h] [--self-test] [--live]
                           [--token-file TOKEN_FILE] [--env-file ENV_FILE]
                           [--state-file STATE_FILE]
                           [--target-email TARGET_EMAIL] [--folder FOLDER]
                           [--limit LIMIT] [--mac-bridge-offline]
                           [--send-telegram] [--extract-attachments]
                           [--extract-python EXTRACT_PYTHON]
                           [--extract-timeout EXTRACT_TIMEOUT] [--auto-draft]
                           [--auto-final-offer]
                           [--final-offer-python FINAL_OFFER_PYTHON]
                           [--final-offer-timeout FINAL_OFFER_TIMEOUT]
                           [--final-offer-vault FINAL_OFFER_VAULT]
                           [--skip-final-offer-obsidian]

Zoho Mail poller for the Hermes RFQ pipeline (read-only by default; draft-only
at most).

options:
  -h, --help            show this help message and exit
  --self-test           Run offline deterministic tests with the bundled
                        fixture.
  --live                Poll the real Zoho mailbox (read-only fetch; draft-
                        only writes if enabled).
  --token-file TOKEN_FILE
  --env-file ENV_FILE
  --state-file STATE_FILE
                        Override the idempotency state file path.
  --target-email TARGET_EMAIL
  --folder FOLDER
  --limit LIMIT
  --mac-bridge-offline  Mark the run degraded (no Obsidian CRM / Apple
                        Calendar).
  --send-telegram       Actually send the composed Telegram briefings (off by
                        default).
  --extract-attachments
                        Download safe attachments for RFQ-class mail and run
                        the deterministic extractor.
  --extract-python EXTRACT_PYTHON
                        Python interpreter for rfq_attachment_extract.py
                        (defaults to the RFQ venv if present).
  --extract-timeout EXTRACT_TIMEOUT
                        Per-attachment extraction timeout in seconds.
  --auto-draft          Auto-create draft-only first responses for green
                        new_quote_request leads (hard-gated by
                        HERMES_ALLOW_DRAFT_CREATE=1).
  --auto-final-offer    Run the rfq-final-offer second stage for quote-thread
                        replies and create draft-only offer/question replies.
  --final-offer-python FINAL_OFFER_PYTHON
                        Python interpreter for the rfq-final-offer skill
                        (defaults to the poller interpreter).
  --final-offer-timeout FINAL_OFFER_TIMEOUT
                        Final-offer skill timeout in seconds.
  --final-offer-vault FINAL_OFFER_VAULT
                        Obsidian vault root for offer JSON/CRM files when
                        available.
  --skip-final-offer-obsidian
                        Do not write final-offer CRM files even if a vault
                        path exists.

```

### `execution/zoho_reply_draft.py --help`

Exit code: `0`

```text
usage: zoho_reply_draft.py [-h] [--self-test] [--build] [--execute]
                           [--input INPUT] [--token-file TOKEN_FILE]
                           [--account-email ACCOUNT_EMAIL]
                           [--i-have-lukasz-approval]

Gated, draft-only Zoho Mail reply-draft creator.

options:
  -h, --help            show this help message and exit
  --self-test           Run offline safety tests (no network).
  --build               Build and print a redacted draft payload from --input
                        (no network).
  --execute             Create the draft in Zoho Mail. HARD-GATED; requires
                        explicit approval.
  --input INPUT         JSON file describing the inbound message and body for
                        --build/--execute.
  --token-file TOKEN_FILE
  --account-email ACCOUNT_EMAIL
  --i-have-lukasz-approval
                        Pass the explicit approval phrase (with --execute).

```

### `execution/mail-lead-pipeline-dry-run.py --help`

Exit code: `0`

```text
usage: mail-lead-pipeline-dry-run.py [-h] [--fixtures FIXTURES]

options:
  -h, --help           show this help message and exit
  --fixtures FIXTURES  Path to JSON fixture cases.

```

### `execution/hermes_rfq_test_autosend_poller.py --help`

Exit code: `0`

```text
usage: hermes_rfq_test_autosend_poller.py [-h] [--live] --run-id RUN_ID
                                          [--allowed-cases ALLOWED_CASES]
                                          [--allowed-senders ALLOWED_SENDERS]
                                          [--confirm CONFIRM]
                                          [--token-file TOKEN_FILE]
                                          [--env-file ENV_FILE]
                                          [--state-file STATE_FILE]
                                          [--target-email TARGET_EMAIL]
                                          [--folder FOLDER] [--limit LIMIT]
                                          [--extract-attachments]
                                          [--extract-python EXTRACT_PYTHON]
                                          [--extract-timeout EXTRACT_TIMEOUT]
                                          [--final-offer-python FINAL_OFFER_PYTHON]
                                          [--final-offer-timeout FINAL_OFFER_TIMEOUT]
                                          [--final-offer-vault FINAL_OFFER_VAULT]
                                          [--skip-final-offer-obsidian]

Test-only Hermes RFQ autosend poller.

options:
  -h, --help            show this help message and exit
  --live
  --run-id RUN_ID
  --allowed-cases ALLOWED_CASES
  --allowed-senders ALLOWED_SENDERS
  --confirm CONFIRM
  --token-file TOKEN_FILE
  --env-file ENV_FILE
  --state-file STATE_FILE
  --target-email TARGET_EMAIL
  --folder FOLDER
  --limit LIMIT
  --extract-attachments
  --extract-python EXTRACT_PYTHON
  --extract-timeout EXTRACT_TIMEOUT
  --final-offer-python FINAL_OFFER_PYTHON
  --final-offer-timeout FINAL_OFFER_TIMEOUT
  --final-offer-vault FINAL_OFFER_VAULT
  --skip-final-offer-obsidian

```

### `execution/rfq_attachment_extract.py --help`

Exit code: `0`

```text
usage: rfq_attachment_extract.py [-h] [--file FILE] [--subject SUBJECT]
                                 [--body BODY]
                                 [--classification CLASSIFICATION]
                                 [--confidence CONFIDENCE]
                                 [--max-pages MAX_PAGES]
                                 [--max-chars MAX_CHARS] [--timeout TIMEOUT]
                                 [--self-test] [--tool-check]

options:
  -h, --help            show this help message and exit
  --file FILE           Attachment path to process.
  --subject SUBJECT     Email subject used for routing context.
  --body BODY           Email body used for routing context.
  --classification CLASSIFICATION
                        Pre-check message class. Non-RFQ classes skip
                        expensive extraction.
  --confidence CONFIDENCE
                        Pre-check classification confidence.
  --max-pages MAX_PAGES
  --max-chars MAX_CHARS
  --timeout TIMEOUT
  --self-test
  --tool-check

```

### `execution/attachment_router.py --help`

Exit code: `0`

```text
usage: attachment_router.py [-h] [--file FILE] [--metadata-file METADATA_FILE]
                            [--subject SUBJECT] [--body BODY] [--self-test]

options:
  -h, --help            show this help message and exit
  --file FILE           Attachment file path to route.
  --metadata-file METADATA_FILE
                        JSON metadata file with attachments to route.
  --subject SUBJECT     Email subject for keyword routing.
  --body BODY           Email body for keyword routing.
  --self-test           Run built-in router tests.

```

### `skills/rfq-final-offer/scripts/rfq_final_offer.py --help`

Exit code: `0`

```text
usage: rfq_final_offer.py [-h] [--input INPUT] [--output-dir OUTPUT_DIR]
                          [--pricing PRICING] [--render-html] [--render-pdf]
                          [--allow-missing-weasyprint] [--write-obsidian]
                          [--vault VAULT] [--delete-pdf-after-success]
                          [--self-test]

Build Orchesta final-offer artifacts without sending email.

options:
  -h, --help            show this help message and exit
  --input INPUT         Input JSON with client, scope, thread, and safety
                        data.
  --output-dir OUTPUT_DIR
                        Artifact output directory.
  --pricing PRICING     Approved pricing JSON path.
  --render-html         Kept for readability; HTML is always rendered for
                        complete offers.
  --render-pdf          Render PDF with WeasyPrint.
  --allow-missing-weasyprint
                        Return a warning instead of failing when WeasyPrint is
                        unavailable.
  --write-obsidian      Write CRM files to the provided Obsidian vault path.
  --vault VAULT         Obsidian vault root for --write-obsidian.
  --delete-pdf-after-success
                        Delete working PDF after success, for draft-attachment
                        runtimes.
  --self-test           Run offline deterministic tests.

```

## Realnie wykonane testy / self-testy

### `python3 /opt/data/execution/zoho_mail_poller.py --self-test`

Exit code: `0`

```text
zoho_mail_poller self-test: ok

```

### `python3 /opt/data/execution/zoho_reply_draft.py --self-test`

Exit code: `0`

```text
zoho_reply_draft self-test: ok

```

### `python3 /opt/data/skills/rfq-final-offer/scripts/rfq_final_offer.py --self-test`

Exit code: `0`

```text
rfq_final_offer self-test: ok

```

## Pliki runtime

### `execution/attachment_router.py`

```python
#!/usr/bin/env python3
"""Attachment safety and extraction router for Hermes Mail Lead Pipeline.

This script is intentionally local and deterministic. It does not call Zoho Mail,
Telegram, web search, OpenAI, marker-pdf, or external OCR services. It decides
which downstream extractor should be used after a cheap safety/type probe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
from pathlib import Path
from typing import Any

from hermes_rfq_core import AttachmentLimits, inspect_attachment


BLOCKED_EXTENSIONS = {
    ".7z",
    ".apk",
    ".bat",
    ".cmd",
    ".com",
    ".dmg",
    ".docm",
    ".exe",
    ".gz",
    ".iso",
    ".jar",
    ".js",
    ".msi",
    ".pkg",
    ".ps1",
    ".rar",
    ".scr",
    ".tar",
    ".vbs",
    ".xlsm",
    ".zip",
}

BLOCKED_MIMES = {
    "application/javascript",
    "application/java-archive",
    "application/vnd.microsoft.portable-executable",
    "application/x-7z-compressed",
    "application/x-dosexec",
    "application/x-msdownload",
    "application/x-msdos-program",
    "application/x-rar-compressed",
    "application/x-sh",
    "application/zip",
}

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}

INVOICE_KEYWORDS = {
    "faktura",
    "invoice",
    "rachunek",
    "receipt",
    "proforma",
    "pro-forma",
    "vat",
    "nip",
    "netto",
    "brutto",
    "payment",
    "platnosc",
    "termin platnosci",
    "fv",
}

TECHNICAL_KEYWORDS = {
    "schem",
    "schemat",
    "rysunk",
    "rysunek",
    "drawing",
    "diagram",
    "plan",
    "instalacj",
    "instalacja",
    "rozdzielnia",
    "pompownia",
    "hvac",
    "cad",
    "projekt",
    "specyfikacja",
}

MAX_INLINE_BYTES = 20 * 1024 * 1024


def normalize_mime(value: str | None) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def lower_text(*parts: Any) -> str:
    return " ".join(str(part or "") for part in parts).lower()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sniff_magic(path: Path | None, filename: str, declared_mime: str) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    guessed_mime = normalize_mime(mimetypes.guess_type(filename)[0])

    if path and path.exists():
        header = path.read_bytes()[:16]
        if header.startswith(b"%PDF-"):
            return "application/pdf", "magic_pdf"
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", "magic_png"
        if header.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", "magic_jpeg"
        if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return "image/webp", "magic_webp"
        if header.startswith((b"II*\x00", b"MM\x00*")):
            return "image/tiff", "magic_tiff"
        if header.startswith(b"PK\x03\x04"):
            if suffix in OFFICE_EXTENSIONS:
                return guessed_mime or "application/zip", "magic_ooxml_zip"
            return "application/zip", "magic_zip"

    if declared_mime:
        return declared_mime, "declared_mime"
    if guessed_mime:
        return guessed_mime, "filename_guess"
    if suffix == ".pdf":
        return "application/pdf", "filename_pdf"
    if suffix in IMAGE_EXTENSIONS:
        return f"image/{suffix.lstrip('.')}", "filename_image"
    return "application/octet-stream", "unknown"


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def probe_pdf(path: Path | None, attachment: dict[str, Any]) -> dict[str, Any]:
    probe = {
        "available": False,
        "pages": safe_int(attachment.get("pages")),
        "text_chars_first_pages": safe_int(attachment.get("text_chars_first_pages")),
        "image_count_first_pages": safe_int(attachment.get("image_count_first_pages")),
        "encrypted": bool(attachment.get("encrypted") is True),
        "method": "metadata",
        "error": "",
    }

    if not path or not path.exists():
        return probe

    try:
        import fitz  # type: ignore
    except Exception:
        probe["error"] = "pymupdf_not_installed"
        return probe

    try:
        with fitz.open(path) as document:
            probe["available"] = True
            probe["method"] = "pymupdf_probe"
            probe["pages"] = int(document.page_count)
            probe["encrypted"] = bool(document.is_encrypted)
            if document.is_encrypted:
                return probe

            text_chars = 0
            image_count = 0
            for page_index in range(min(document.page_count, 3)):
                page = document.load_page(page_index)
                text_chars += len(page.get_text("text") or "")
                image_count += len(page.get_images(full=True))
            probe["text_chars_first_pages"] = text_chars
            probe["image_count_first_pages"] = image_count
    except Exception as exc:
        probe["error"] = f"pdf_probe_failed:{exc.__class__.__name__}"

    return probe


def keyword_score(text: str, keywords: set[str]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def pdf_text_quality(probe: dict[str, Any]) -> str:
    pages = max(1, safe_int(probe.get("pages"), 1))
    chars = safe_int(probe.get("text_chars_first_pages"))
    image_count = safe_int(probe.get("image_count_first_pages"))
    per_page = chars / min(pages, 3)
    if chars >= 900 or per_page >= 300:
        return "high"
    if chars >= 200 or per_page >= 80:
        return "medium"
    if chars >= 40 and image_count == 0:
        return "medium"
    return "low"


def blocked_reason(
    attachment: dict[str, Any],
    filename: str,
    suffix: str,
    detected_mime: str,
    size_bytes: int,
    pdf_probe: dict[str, Any] | None = None,
) -> str:
    if attachment.get("encrypted") is True:
        return "attachment_marked_encrypted"
    if attachment.get("too_large") is True or size_bytes > MAX_INLINE_BYTES:
        return "attachment_too_large"
    if suffix in BLOCKED_EXTENSIONS:
        return f"blocked_extension:{suffix}"
    if detected_mime in BLOCKED_MIMES:
        return f"blocked_mime:{detected_mime}"
    if pdf_probe and pdf_probe.get("encrypted"):
        return "pdf_encrypted"
    if not filename.strip():
        return "missing_filename"
    return ""


def route_attachment(
    attachment: dict[str, Any],
    *,
    subject: str = "",
    body: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    filename = str(attachment.get("filename") or (path.name if path else "")).strip()
    suffix = Path(filename).suffix.lower()
    declared_mime = normalize_mime(attachment.get("mime"))
    detected_mime, detection_source = sniff_magic(path, filename, declared_mime)
    size_bytes = safe_int(attachment.get("size_bytes"), path.stat().st_size if path and path.exists() else 0)
    file_hash = sha256_file(path) if path and path.exists() else ""

    byte_probe = inspect_attachment(path, limits=AttachmentLimits.from_env()) if path and path.exists() else {}
    if byte_probe.get("status") in {"blocked", "review", "unsupported"}:
        probe_status = str(byte_probe.get("status"))
        return {
            "filename": filename,
            "safety": "block" if probe_status == "blocked" else "review",
            "status": probe_status,
            "reason": byte_probe.get("reason_code", "attachment_safety_gate"),
            "detected_mime": detected_mime,
            "detection_source": detection_source,
            "size_bytes": size_bytes,
            "sha256": file_hash,
            "document_type": "unsafe_or_unreadable" if probe_status == "blocked" else "unknown",
            "route": "block_and_telegram" if probe_status == "blocked" else "telegram_review_only",
            "extractor": "none",
            "model_use": "none",
            "confidence": "high" if probe_status == "blocked" else "low",
            "crm_storage": "metadata_only",
            "notes": ["Do not execute or extract untrusted attachment content."],
        }

    context_text = lower_text(filename, subject, body, attachment.get("text_sample", ""))
    invoice_score = keyword_score(context_text, INVOICE_KEYWORDS)
    technical_score = keyword_score(context_text, TECHNICAL_KEYWORDS)

    is_pdf = detected_mime == "application/pdf" or suffix in PDF_EXTENSIONS
    is_image = detected_mime.startswith("image/") or suffix in IMAGE_EXTENSIONS
    is_office = suffix in OFFICE_EXTENSIONS
    pdf_probe = probe_pdf(path, attachment) if is_pdf else None

    reason = blocked_reason(attachment, filename, suffix, detected_mime, size_bytes, pdf_probe)
    if reason:
        return {
            "filename": filename,
            "safety": "block",
            "status": "blocked",
            "reason": reason,
            "detected_mime": detected_mime,
            "detection_source": detection_source,
            "size_bytes": size_bytes,
            "sha256": file_hash,
            "document_type": "unsafe_or_unreadable",
            "route": "block_and_telegram",
            "extractor": "none",
            "model_use": "none",
            "confidence": "high",
            "crm_storage": "metadata_only",
            "notes": ["Do not create a customer-facing draft before human review."],
        }

    if is_pdf:
        assert pdf_probe is not None
        quality = pdf_text_quality(pdf_probe)
        if invoice_score >= 1 and quality in {"high", "medium"}:
            document_type = "invoice_or_receipt_pdf"
            route = "pymupdf_invoice_schema"
            extractor = "pymupdf_then_schema"
            model_use = "low_schema_only"
        elif invoice_score >= 1:
            document_type = "scanned_invoice_or_receipt_pdf"
            route = "marker_invoice_schema"
            extractor = "marker-pdf_then_schema"
            model_use = "medium_optional_schema"
        elif quality in {"high", "medium"}:
            document_type = "text_pdf"
            route = "pymupdf_text"
            extractor = "pymupdf"
            model_use = "none"
        else:
            document_type = "scanned_or_complex_pdf"
            route = "marker_ocr"
            extractor = "marker-pdf"
            model_use = "none_or_low_summary"

        return {
            "filename": filename,
            "safety": "allow",
            "status": "safe",
            "reason": "",
            "detected_mime": detected_mime,
            "detection_source": detection_source,
            "size_bytes": size_bytes,
            "sha256": file_hash,
            "document_type": document_type,
            "route": route,
            "extractor": extractor,
            "model_use": model_use,
            "confidence": "medium" if not pdf_probe.get("available") else "high",
            "crm_storage": "summary_only",
            "pdf_probe": pdf_probe,
            "notes": ["Use extracted text/summaries only; do not store the full attachment in CRM."],
        }

    if is_image:
        if invoice_score >= 1:
            document_type = "image_invoice_or_receipt"
            route = "ocr_vision_invoice_schema"
            extractor = "ocr_vision_then_schema"
            model_use = "medium_vision"
        elif technical_score >= 1:
            document_type = "technical_image_or_drawing"
            route = "ocr_vision_technical_summary"
            extractor = "ocr_vision"
            model_use = "medium_vision"
        else:
            document_type = "image"
            route = "ocr_vision_summary"
            extractor = "ocr_vision"
            model_use = "medium_vision"

        return {
            "filename": filename,
            "safety": "allow",
            "status": "safe",
            "reason": "",
            "detected_mime": detected_mime,
            "detection_source": detection_source,
            "size_bytes": size_bytes,
            "sha256": file_hash,
            "document_type": document_type,
            "route": route,
            "extractor": extractor,
            "model_use": model_use,
            "confidence": "medium",
            "crm_storage": "summary_only",
            "notes": ["Use OCR/vision only after safety gate and with the approved runtime/provider."],
        }

    if is_office:
        return {
            "filename": filename,
            "safety": "allow",
            "status": "safe",
            "reason": "",
            "detected_mime": detected_mime,
            "detection_source": detection_source,
            "size_bytes": size_bytes,
            "sha256": file_hash,
            "document_type": "office_document",
            "route": "office_text_extract",
            "extractor": "safe_office_parser",
            "model_use": "none_or_low_summary",
            "confidence": "medium",
            "crm_storage": "summary_only",
            "notes": ["Never execute macros; macro-enabled Office files are blocked by extension."],
        }

    return {
        "filename": filename,
        "safety": "review",
        "status": "unsupported",
        "reason": "unsupported_or_unknown_file_type",
        "detected_mime": detected_mime,
        "detection_source": detection_source,
        "size_bytes": size_bytes,
        "sha256": file_hash,
        "document_type": "unknown",
        "route": "telegram_review_only",
        "extractor": "none",
        "model_use": "none",
        "confidence": "low",
        "crm_storage": "metadata_only",
        "notes": ["Unsupported attachment type; ask for human review before drafting."],
    }


def route_message_attachments(message: dict[str, Any]) -> list[dict[str, Any]]:
    subject = str(message.get("subject", ""))
    body = str(message.get("body", ""))
    results = [
        route_attachment(attachment, subject=subject, body=body)
        for attachment in (message.get("attachments", []) or [])
    ]
    limits = AttachmentLimits.from_env()
    total_bytes = sum(safe_int(item.get("size_bytes")) for item in results)
    if len(results) > limits.max_files or total_bytes > limits.max_total_bytes:
        reason = "attachment_count_limit" if len(results) > limits.max_files else "attachment_total_size_limit"
        for item in results:
            if item.get("status") in {"safe", None}:
                item["status"] = "blocked"
                item["safety"] = "block"
                item["reason"] = reason
                item["route"] = "block_and_telegram"
    return results


def has_blocking_attachment(message: dict[str, Any]) -> bool:
    return any(result["safety"] in {"block", "review"} for result in route_message_attachments(message))


def load_metadata(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("attachments"), list):
        subject = str(payload.get("subject", ""))
        body = str(payload.get("body", ""))
        return [
            {"attachment": item, "subject": subject, "body": body}
            for item in payload["attachments"]
        ]
    raise ValueError("metadata file must be a list or an object with attachments[]")


def self_test() -> int:
    cases = [
        (
            "text_pdf",
            {"filename": "brief.pdf", "mime": "application/pdf", "pages": 3, "text_chars_first_pages": 1800},
            "RFQ",
            "Prosze o wycene systemu.",
            "pymupdf_text",
        ),
        (
            "text_invoice_pdf",
            {"filename": "faktura-fv-12.pdf", "mime": "application/pdf", "pages": 1, "text_chars_first_pages": 900},
            "Faktura",
            "W zalaczniku faktura VAT.",
            "pymupdf_invoice_schema",
        ),
        (
            "scanned_invoice_pdf",
            {"filename": "invoice-scan.pdf", "mime": "application/pdf", "pages": 2, "text_chars_first_pages": 0},
            "Invoice",
            "Scanned invoice attached.",
            "marker_invoice_schema",
        ),
        (
            "technical_image",
            {"filename": "pompownia_str_6.png", "mime": "image/png"},
            "Zapytanie ofertowe",
            "W zalaczniku schemat instalacji pompownia.",
            "ocr_vision_technical_summary",
        ),
        (
            "image_invoice",
            {"filename": "rachunek.jpg", "mime": "image/jpeg"},
            "Rachunek",
            "Przesylam rachunek.",
            "ocr_vision_invoice_schema",
        ),
        (
            "blocked_macro",
            {"filename": "specyfikacja.xlsm", "mime": "application/vnd.ms-excel.sheet.macroenabled.12"},
            "RFQ",
            "Specyfikacja w zalaczniku.",
            "block_and_telegram",
        ),
        (
            "encrypted_pdf",
            {"filename": "brief.pdf", "mime": "application/pdf", "encrypted": True},
            "RFQ",
            "Plik jest zaszyfrowany.",
            "block_and_telegram",
        ),
        (
            "short_text_pdf_without_images",
            {"filename": "brief.pdf", "mime": "application/pdf", "pages": 1, "text_chars_first_pages": 80, "image_count_first_pages": 0},
            "RFQ",
            "Krotki tekstowy brief w PDF.",
            "pymupdf_text",
        ),
    ]

    failures: list[str] = []
    for case_id, attachment, subject, body, expected_route in cases:
        result = route_attachment(attachment, subject=subject, body=body)
        if result["route"] != expected_route:
            failures.append(f"{case_id}: got {result['route']} expected {expected_route}")

    if failures:
        print("attachment-router self-test failures:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(f"attachment-router self-test: ok ({len(cases)} cases)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", action="append", default=[], help="Attachment file path to route.")
    parser.add_argument("--metadata-file", help="JSON metadata file with attachments to route.")
    parser.add_argument("--subject", default="", help="Email subject for keyword routing.")
    parser.add_argument("--body", default="", help="Email body for keyword routing.")
    parser.add_argument("--self-test", action="store_true", help="Run built-in router tests.")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    results: list[dict[str, Any]] = []
    for file_value in args.file:
        path = Path(file_value)
        results.append(
            route_attachment(
                {"filename": path.name, "mime": mimetypes.guess_type(path.name)[0], "size_bytes": path.stat().st_size if path.exists() else 0},
                subject=args.subject,
                body=args.body,
                path=path,
            )
        )

    if args.metadata_file:
        for item in load_metadata(Path(args.metadata_file)):
            if "attachment" in item:
                results.append(
                    route_attachment(
                        item["attachment"],
                        subject=str(item.get("subject", args.subject)),
                        body=str(item.get("body", args.body)),
                    )
                )
            else:
                results.append(route_attachment(item, subject=args.subject, body=args.body))

    if not results:
        parser.error("provide --file, --metadata-file, or --self-test")

    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

```

### `execution/hermes_monitoring.py`

```python
#!/usr/bin/env python3
"""Read-only monitoring snapshot for the Hermes RFQ SQLite state."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_rfq_core import HermesStateStore, utc_now


METRIC_NAMES = (
    "api_errors", "retries", "duplicates", "security_blocks", "crm_conflicts",
    "human_edited_drafts", "offers_waiting", "pdf_errors", "errors_429", "errors_5xx",
)


def record_poller_heartbeat(store: HermesStateStore) -> None:
    store.connection.execute(
        "INSERT INTO metrics(name,value,updated_at) VALUES('poller_last_run',1,?) ON CONFLICT(name) DO UPDATE SET updated_at=excluded.updated_at",
        (utc_now(),),
    )


def collect_snapshot(store: HermesStateStore, *, backlog_limit: int = 100, heartbeat_ttl_seconds: int = 600) -> dict[str, Any]:
    now = time.time()
    queue_rows = store.connection.execute(
        "SELECT created_at FROM messages WHERE status NOT IN ('done','precheck_skipped') ORDER BY created_at ASC"
    ).fetchall()
    oldest_age = 0
    if queue_rows:
        try:
            oldest = datetime.fromisoformat(str(queue_rows[0]["created_at"])).timestamp()
            oldest_age = max(0, round(now - oldest))
        except ValueError:
            oldest_age = -1
    metrics = {name: 0 for name in METRIC_NAMES}
    for row in store.connection.execute("SELECT name,value FROM metrics").fetchall():
        if row["name"] in metrics:
            metrics[row["name"]] = int(row["value"])
    heartbeat = store.connection.execute("SELECT updated_at FROM metrics WHERE name='poller_last_run'").fetchone()
    heartbeat_age = None
    if heartbeat:
        try:
            heartbeat_age = max(0, round(now - datetime.fromisoformat(heartbeat["updated_at"]).timestamp()))
        except ValueError:
            heartbeat_age = -1
    alarms: list[str] = []
    if len(queue_rows) > backlog_limit:
        alarms.append("backlog_growing")
    if heartbeat_age is None or heartbeat_age > heartbeat_ttl_seconds:
        alarms.append("poller_not_running")
    if metrics["errors_429"] >= 5:
        alarms.append("series_of_429")
    if metrics["errors_5xx"] >= 5:
        alarms.append("series_of_5xx")
    if metrics["security_blocks"]:
        alarms.append("security_block_detected")
    if metrics["duplicates"]:
        alarms.append("draft_duplicate_detected")
    if metrics["pdf_errors"]:
        alarms.append("pdf_error_detected")
    return {
        "queue_count": len(queue_rows),
        "oldest_message_age_seconds": oldest_age,
        "metrics": metrics,
        "heartbeat_age_seconds": heartbeat_age,
        "alarms": alarms,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", default=".tmp/hermes-rfq-state/state.sqlite3")
    parser.add_argument("--backlog-limit", type=int, default=100)
    parser.add_argument("--heartbeat-ttl", type=int, default=600)
    args = parser.parse_args()
    store = HermesStateStore(Path(args.state_file))
    print(json.dumps(collect_snapshot(store, backlog_limit=args.backlog_limit, heartbeat_ttl_seconds=args.heartbeat_ttl), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

```

### `execution/hermes_rfq_core.py`

```python
#!/usr/bin/env python3
"""Deterministic safety primitives shared by the Hermes Orchesta RFQ flows.

This module deliberately contains no mail transport and no model calls.  It is
the small, restart-safe boundary between orchestration and external adapters:
state transitions, operation idempotency, attachment limits,
draft edit detection, offer fingerprints, bridge health, retries, and redacted
structured logging.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import time
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Callable, Iterable


MESSAGE_STATES = {
    "received", "precheck_skipped", "analysis_pending", "identity_resolved",
    "classified", "safety_passed", "blocked", "action_planned", "done",
}
DRAFT_STATES = {
    "not_needed", "planned", "creating", "created", "update_pending",
    "updated", "human_edited", "retryable_failed", "permanent_failed",
}
OFFER_STATES = {
    "not_applicable", "waiting_for_data", "waiting_for_review", "generating",
    "generated", "attachment_pending", "attached", "superseded", "sent_manually",
}
CRM_STATES = {
    "not_needed", "lookup_pending", "matched", "new_record_pending", "written",
    "deferred_offline", "conflict", "failed",
}
OPERATION_STATUSES = {"planned", "in_progress", "succeeded", "retryable_failed", "permanent_failed"}
PUBLIC_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "icloud.com", "me.com", "onet.pl", "wp.pl", "interia.pl", "o2.pl", "yahoo.com",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_id(prefix: str = "HRFQ") -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: Any) -> str:
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    raw = value if isinstance(value, str) else canonical_json(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def redact(value: Any, *, max_text: int = 220) -> Any:
    """Recursively redact secrets and keep logs to short previews."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(word in lowered for word in ("token", "password", "secret", "cookie", "authorization", "api_key", "private_key")):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact(item, max_text=max_text)
        return result
    if isinstance(value, list):
        return [redact(item, max_text=max_text) for item in value[:20]]
    if isinstance(value, str):
        text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~-]+", "Bearer [REDACTED]", value)

[...ucięto w dokumentacji pomocniczej; pełny plik źródłowy: `execution/hermes_rfq_core.py`]
```

### `execution/hermes_rfq_test_autosend_poller.py`

```python
#!/usr/bin/env python3
"""Test-only Hermes RFQ poller that sends replies instead of creating drafts.

This is not a production mode. It processes only subjects containing one
synthetic run id, optionally narrowed to case ids such as A,B. It uses the same
classifier, question generation, final-offer skill, pricing, PDF validation and
pipeline state as `zoho_mail_poller.py`, but posts replies through Zoho's reply
endpoint so multi-turn tests do not leave draft blockers behind.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable
import urllib.error
import urllib.request


ROOT_DIR = Path(__file__).resolve().parents[1]
EXECUTION_DIR = ROOT_DIR / "execution"
sys.path.insert(0, str(EXECUTION_DIR))

from pipeline_state import PipelineState  # noqa: E402
from zoho_mail_poller import (  # noqa: E402
    DEFAULT_FOLDER,
    DEFAULT_LIMIT,
    DEFAULT_TARGET_EMAIL,
    HttpZohoClient,
    attachment_record_from_upload,
    build_extract_runner,
    build_final_offer_input,
    configured_auto_draft_classes,
    default_extract_python,
    default_final_offer_vault,
    discovery_questions_for_message,
    final_offer_candidate,
    final_offer_skill_script,
    load_classifier,
    poll,
    read_text_file,
    text_or_file,
)
from zoho_reply_draft import (  # noqa: E402
    HttpDraftPoster,
    build_draft_payload,
    first_response_body,
    generate_draft_body_from_env,
    validate_generated_body,
)


CONFIRMATION = "AUTOSEND TEST HERMES RFQ"
RUN_ID_RE = re.compile(r"^HRFQ-AUTO-\d{8}-\d{6}$")
CASE_ID_RE = re.compile(r"\[HRFQ-AUTO-\d{8}-\d{6}-([A-Z])\]")
DEFAULT_ALLOWED_SENDERS = {
    "test-customer-2@example.invalid",
    "test-customer-1@example.invalid",
    "identity-001@gmail.com",
    "identity-002@gmail.com",
    "notifications@example.invalid",
}


def parse_csv_set(value: str, *, upper: bool = False) -> set[str]:
    items = {item.strip() for item in (value or "").split(",") if item.strip()}
    return {item.upper() for item in items} if upper else {item.lower() for item in items}


def subject_case_id(subject: str) -> str:
    match = CASE_ID_RE.search(subject or "")
    return match.group(1) if match else ""


def allowed_subject(subject: str, run_id: str, allowed_cases: set[str]) -> bool:
    if run_id not in (subject or ""):
        return False
    return not allowed_cases or subject_case_id(subject) in allowed_cases


class FilteringZohoClient:
    """Wrap a Zoho client and expose only the selected synthetic test run."""

    def __init__(self, inner: HttpZohoClient, *, run_id: str, allowed_cases: set[str]) -> None:
        self.inner = inner
        self.run_id = run_id
        self.allowed_cases = allowed_cases

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def list_accounts(self) -> list[dict[str, Any]]:
        return self.inner.list_accounts()

    def list_folders(self, account_id: str) -> list[dict[str, Any]]:
        return self.inner.list_folders(account_id)

    def list_messages(self, account_id: str, folder_id: str, limit: int) -> list[dict[str, Any]]:
        raw = self.inner.list_messages(account_id, folder_id, limit)
        return [
            item
            for item in raw
            if allowed_subject(str(item.get("subject") or ""), self.run_id, self.allowed_cases)
        ]


class HttpTestReplyPoster(HttpDraftPoster):
    """Zoho writer that sends a reply to a received message."""

    def send_reply(self, account_id: str, source_message_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        url = f"{self.api_base}/accounts/{account_id}/messages/{source_message_id}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Zoho-oauthtoken {self._access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8", errors="replace")
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"error": raw[:1000]}


def reply_payload_from_draft_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "fromAddress": payload["fromAddress"],
        "toAddress": payload["toAddress"],
        "subject": payload["subject"],
        "content": payload["content"],
        "mailFormat": payload.get("mailFormat", "html"),
        "askReceipt": "no",
        "action": "reply",
        **({"attachments": payload["attachments"]} if payload.get("attachments") else {}),
    }


def assert_test_allowed(envelope: dict[str, Any], *, run_id: str, allowed_cases: set[str], allowed_senders: set[str]) -> str:
    subject = str(envelope.get("subject") or "")
    sender = str(envelope.get("from") or "").lower()
    case_id = subject_case_id(subject)
    if not RUN_ID_RE.match(run_id):
        raise PermissionError("bad_test_run_id")
    if not allowed_subject(subject, run_id, allowed_cases):
        raise PermissionError("subject_not_allowed_for_test_autosend")
    if sender not in allowed_senders:
        raise PermissionError("sender_not_allowed_for_test_autosend")
    return case_id


def build_test_reply_creator(
    account_email: str,
    poster: HttpTestReplyPoster,
    *,
    run_id: str,
    allowed_cases: set[str],
    allowed_senders: set[str],
) -> Callable[..., dict[str, Any]]:
    def creator(
        account_id: str,
        envelope: dict[str, Any],
        result: dict[str, Any],
        rfc_id: str,
        references: str,
        *,
        classifier_message: dict[str, Any] | None = None,
        attachment_routes: list[dict[str, Any]] | None = None,
        extraction_summaries: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        case_id = assert_test_allowed(envelope, run_id=run_id, allowed_cases=allowed_cases, allowed_senders=allowed_senders)
        classifier_message = classifier_message or {}
        questions = discovery_questions_for_message(str(classifier_message.get("body") or ""), envelope)
        if questions:
            draft_context = {
                "sender": envelope.get("from", ""),
                "subject": envelope.get("subject", ""),
                "body": classifier_message.get("body", ""),
                "classification": result.get("classification", ""),
                "confidence": result.get("confidence", ""),
                "draft_kind": result.get("draft_kind", "first_response"),
                "attachment_routes": attachment_routes or [],
                "attachment_summaries": extraction_summaries or [],
                "default_questions": questions,
            }
            generated = generate_draft_body_from_env(draft_context)
            body = validate_generated_body(
                str(generated.get("body_text") or ""),
                has_attachments=bool(attachment_routes or extraction_summaries),
                approved_questions=questions,
            )
        else:
            generated = {"generator": "deterministic_complete_scope", "questions": []}
            body = first_response_body([])
        draft_payload = build_draft_payload(
            account_email=account_email,
            to_address=envelope.get("from", ""),
            inbound_subject=envelope.get("subject", ""),
            rfc_message_id=rfc_id,
            references=references or "",
            body_text=body,
            draft_kind="first_response",
        )
        status, response = poster.send_reply(account_id, str(envelope.get("message_id") or ""), reply_payload_from_draft_payload(draft_payload))
        return {
            "action": "created" if status in {200, 201} else "error",
            "status": status,
            "response": response,
            "test_autosent": True,
            "case_id": case_id,
            "draft_generation": {
                key: generated.get(key)
                for key in ("generator", "model", "salutation", "attachment_summary_used", "questions", "safety_notes")
                if generated.get(key) not in (None, "", [])
            },
        }

    return creator


def build_test_final_offer_creator(
    account_email: str,
    poster: HttpTestReplyPoster,
    *,
    run_id: str,
    allowed_cases: set[str],
    allowed_senders: set[str],
    vault: Path | None = None,
    python_exec: str | None = None,
    timeout: int = 240,
) -> Callable[..., dict[str, Any]]:
    script_path = final_offer_skill_script()
    runner_python = python_exec or sys.executable

    def creator(
        account_id: str,
        envelope: dict[str, Any],
        result: dict[str, Any],
        rfc_id: str,
        references: str,
        *,
        headers: dict[str, str],
        classifier_message: dict[str, Any],
        thread_history_text: str = "",
        attachment_routes: list[dict[str, Any]],
        raw_attachments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        case_id = assert_test_allowed(envelope, run_id=run_id, allowed_cases=allowed_cases, allowed_senders=allowed_senders)
        input_payload = build_final_offer_input(
            envelope=envelope,
            headers=headers,
            result=result,
            body_text=str(classifier_message.get("body") or ""),
            thread_history_text=thread_history_text,
            attachment_routes=attachment_routes,
            raw_attachments=raw_attachments,
            account_email=account_email,
        )
        with tempfile.TemporaryDirectory(prefix="hermes-final-offer-send-test-") as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "input.json"
            output_dir = tmp_path / "out"
            input_path.write_text(json.dumps(input_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            command = [
                runner_python,
                str(script_path),
                "--input",
                str(input_path),
                "--output-dir",
                str(output_dir),
                "--render-html",
                "--render-pdf",
            ]
            if vault is not None:
                command.extend(["--write-obsidian", "--vault", str(vault)])
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return {"action": "error", "status_name": "timeout", "error": "rfq_final_offer_timeout"}
            manifest_path = output_dir / "manifest.json"
            if not manifest_path.exists():
                return {
                    "action": "error",
                    "status_name": "skill_failed",
                    "error": (completed.stderr or completed.stdout or "manifest_missing")[-500:],
                }
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {"action": "error", "status_name": "skill_bad_manifest", "error": "manifest_bad_json"}

            status_name = str(manifest.get("status") or "")
            telegram_text = text_or_file(manifest.get("telegram", ""))

            if status_name == "awaiting_data":
                body = read_text_file(output_dir / "mail_missing_data.txt")
                if not body:
                    return {"action": "blocked", "status_name": status_name, "manifest": manifest, "telegram_text": telegram_text}
                draft_payload = build_draft_payload(
                    account_email=account_email,
                    to_address=envelope.get("from", ""),
                    inbound_subject=envelope.get("subject", ""),
                    rfc_message_id=rfc_id,
                    references=references or "",
                    body_text=body,
                    draft_kind="final_offer",
                )
                status, response = poster.send_reply(
                    account_id,
                    str(envelope.get("message_id") or ""),
                    reply_payload_from_draft_payload(draft_payload),
                )
                return {
                    "action": "awaiting_data_draft_created" if status in {200, 201} else "error",
                    "status": status,
                    "status_name": status_name,
                    "response": response,
                    "manifest": manifest,
                    "telegram_text": telegram_text,
                    "questions": manifest.get("questions") or [],
                    "test_autosent": True,
                    "case_id": case_id,
                }

            if status_name != "offer_draft_created":
                return {"action": "blocked", "status_name": status_name, "manifest": manifest, "telegram_text": telegram_text}

            pdf_path = Path(str(manifest.get("pdf") or ""))
            mail_body = read_text_file(manifest.get("mail_final_offer", ""))
            if not pdf_path.exists():
                return {"action": "error", "status_name": "pdf_missing", "manifest": manifest, "telegram_text": telegram_text}
            if not mail_body:
                return {"action": "error", "status_name": "mail_body_missing", "manifest": manifest, "telegram_text": telegram_text}
            upload_status, upload_response = poster.upload_attachment(account_id, pdf_path)
            if upload_status not in {200, 201}:
                try:
                    pdf_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return {
                    "action": "error",
                    "status": upload_status,
                    "status_name": "attachment_upload_failed",
                    "manifest": manifest,
                    "telegram_text": telegram_text,
                    "error": str(upload_response)[:300],
                }
            attachment_record = attachment_record_from_upload(upload_response)
            draft_payload = build_draft_payload(
                account_email=account_email,
                to_address=envelope.get("from", ""),
                inbound_subject=envelope.get("subject", ""),
                rfc_message_id=rfc_id,
                references=references or "",
                body_text=mail_body,
                draft_kind="final_offer",
                attachments=[attachment_record],
            )
            status, response = poster.send_reply(
                account_id,
                str(envelope.get("message_id") or ""),
                reply_payload_from_draft_payload(draft_payload),
            )
            try:
                pdf_path.unlink(missing_ok=True)
            except OSError:
                pass
            return {
                "action": "created" if status in {200, 201} else "error",
                "status": status,
                "status_name": status_name,
                "response": response,
                "manifest": manifest,
                "telegram_text": telegram_text,
                "offer_number": manifest.get("offer_number"),
                "price_net_display": manifest.get("price_net_display"),
                "pdf_attached": status in {200, 201},
                "pdf_removed": not pdf_path.exists(),
                "test_autosent": True,
                "case_id": case_id,
            }

    return creator


def main() -> int:
    parser = argparse.ArgumentParser(description="Test-only Hermes RFQ autosend poller.")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--allowed-cases", default="A,B")
    parser.add_argument("--allowed-senders", default=",".join(sorted(DEFAULT_ALLOWED_SENDERS)))
    parser.add_argument("--confirm", default="")
    parser.add_argument("--token-file", default=str(ROOT_DIR / ".tmp" / "zoho_mail_tokens.json"))
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--state-file", default=None)
    parser.add_argument("--target-email", default=DEFAULT_TARGET_EMAIL)
    parser.add_argument("--folder", default=DEFAULT_FOLDER)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--extract-attachments", action="store_true")
    parser.add_argument("--extract-python", default=None)
    parser.add_argument("--extract-timeout", type=int, default=180)
    parser.add_argument("--final-offer-python", default=None)
    parser.add_argument("--final-offer-timeout", type=int, default=240)
    parser.add_argument("--final-offer-vault", default=default_final_offer_vault())
    parser.add_argument("--skip-final-offer-obsidian", action="store_true")
    args = parser.parse_args()

    if not args.live:
        parser.error("test autosend requires --live")
    if args.confirm != CONFIRMATION:
        raise PermissionError(f"missing exact confirmation: {CONFIRMATION}")
    if not RUN_ID_RE.match(args.run_id):
        raise ValueError("run id must look like HRFQ-AUTO-YYYYMMDD-HHMMSS")

    allowed_cases = parse_csv_set(args.allowed_cases, upper=True)
    allowed_senders = parse_csv_set(args.allowed_senders) or set(DEFAULT_ALLOWED_SENDERS)

    classifier = load_classifier()
    inner_client = HttpZohoClient(token_file=args.token_file, env_file=args.env_file)
    client = FilteringZohoClient(inner_client, run_id=args.run_id, allowed_cases=allowed_cases)
    state = PipelineState(args.state_file)
    poster = HttpTestReplyPoster(token_file=args.token_file)

    extract_runner = None
    if args.extract_attachments:
        extract_runner = build_extract_runner(
            client,
            python_exec=args.extract_python or default_extract_python(),
            timeout=args.extract_timeout,
        )

    vault = None
    if not args.skip_final_offer_obsidian and args.final_offer_vault:
        candidate_vault = Path(args.final_offer_vault)
        if candidate_vault.exists():
            vault = candidate_vault

    summary = poll(
        client,
        state,
        classifier,
        target_email=args.target_email,
        folder_name=args.folder,
        limit=args.limit,
        base_context={"mac_bridge_available": False, "test_autosend": True, "run_id": args.run_id},
        send_telegram=None,
        extract_runner=extract_runner,
        auto_draft=True,
        draft_creator=build_test_reply_creator(
            args.target_email,
            poster,
            run_id=args.run_id,
            allowed_cases=allowed_cases,
            allowed_senders=allowed_senders,
        ),
        auto_final_offer=True,
        final_offer_creator=build_test_final_offer_creator(
            args.target_email,
            poster,
            run_id=args.run_id,
            allowed_cases=allowed_cases,
            allowed_senders=allowed_senders,
            vault=vault,
            python_exec=args.final_offer_python or sys.executable,
            timeout=args.final_offer_timeout,
        ),
        auto_draft_classes=configured_auto_draft_classes(),
        run_id=f"{args.run_id}-autosend",
    )
    state.save()
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

```

### `execution/hermes_shadow_compare.py`

```python
#!/usr/bin/env python3
"""Compare Hermes shadow decisions with human review labels.

Shadow input is JSONL produced by the poller, one summary object per run.
Human labels are JSONL with at least ``message_id`` and ``expected_action``.
Optional fields compare classification, recipient, and risk without storing
message bodies.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _shadow_messages(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for run in rows:
        for briefing in run.get("briefings", []) or []:
            message_id = str(briefing.get("message_id") or "")
            if not message_id:
                continue
            axes = briefing.get("classification_axes") or {}
            draft = briefing.get("draft") or {}
            form = briefing.get("form_submission") or {}
            result[message_id] = {
                "message_id": message_id,
                "run_id": run.get("run_id"),
                "classification": briefing.get("classification"),
                "action": axes.get("action") or draft.get("action"),
                "risk": axes.get("risk"),
                "recipient": draft.get("recipient") or form.get("customer_email"),
                "correlation_id": briefing.get("correlation_id") or form.get("correlation_id"),
            }
    return result


def compare(shadow_rows: list[dict[str, Any]], human_rows: list[dict[str, Any]]) -> dict[str, Any]:
    shadow = _shadow_messages(shadow_rows)
    comparisons: list[dict[str, Any]] = []
    missing_shadow = 0
    mismatches = 0
    for human in human_rows:
        message_id = str(human.get("message_id") or "")
        observed = shadow.get(message_id)
        if not observed:
            missing_shadow += 1
            comparisons.append({"message_id": message_id, "status": "missing_shadow"})
            continue
        differences: dict[str, dict[str, Any]] = {}
        fields = {
            "action": human.get("expected_action"),
            "classification": human.get("expected_classification"),
            "risk": human.get("expected_risk"),
            "recipient": human.get("expected_recipient"),
        }
        for field, expected in fields.items():
            if expected is not None and expected != observed.get(field):
                differences[field] = {"expected": expected, "observed": observed.get(field)}
        status = "match" if not differences else "mismatch"
        if differences:
            mismatches += 1
        comparisons.append({
            "message_id": message_id,
            "run_id": observed.get("run_id"),
            "status": status,
            "differences": differences,
        })
    reviewed = len(comparisons) - missing_shadow
    return {
        "reviewed": reviewed,
        "matches": sum(1 for item in comparisons if item["status"] == "match"),
        "mismatches": mismatches,
        "missing_shadow": missing_shadow,
        "coverage": (reviewed / len(human_rows)) if human_rows else 0.0,
        "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Hermes shadow JSONL with handlowiec labels.")
    parser.add_argument("--shadow-log", required=True, type=Path)
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = compare(_read_jsonl(args.shadow_log), _read_jsonl(args.decisions))
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

```

### `execution/install-rfq-attachment-runtime.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${HERMES_RFQ_RUNTIME_DIR:-/opt/data/rfq-runtime}"
VENV_DIR="${HERMES_RFQ_VENV_DIR:-$RUNTIME_DIR/.venv}"
MARKER_VENV_DIR="${HERMES_RFQ_MARKER_VENV_DIR:-$RUNTIME_DIR/.venv-marker}"
REQ_FILE="${HERMES_RFQ_REQUIREMENTS:-$SCRIPT_DIR/requirements-rfq-attachments.txt}"
MARKER_REQ_FILE="${HERMES_RFQ_MARKER_REQUIREMENTS:-$SCRIPT_DIR/requirements-rfq-marker.txt}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "== Orchesta RFQ attachment runtime install =="
echo "runtime_dir=$RUNTIME_DIR"
echo "venv_dir=$VENV_DIR"
echo "marker_venv_dir=$MARKER_VENV_DIR"

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
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV_DIR/bin/python" -m pip install --extra-index-url https://download.pytorch.org/whl/cpu -r "$REQ_FILE"

echo "== Marker virtualenv =="
"$PYTHON_BIN" -m venv "$MARKER_VENV_DIR"
"$MARKER_VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
"$MARKER_VENV_DIR/bin/python" -m pip install --extra-index-url https://download.pytorch.org/whl/cpu -r "$MARKER_REQ_FILE"

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

"$MARKER_VENV_DIR/bin/python" - <<'PY'
import importlib

importlib.import_module("marker")
print("marker-pdf: ok")
PY

echo "== Tool check =="
command -v tesseract || true
export PATH="$VENV_DIR/bin:$PATH"
export HERMES_RFQ_MARKER_VENV_DIR="$MARKER_VENV_DIR"
"$VENV_DIR/bin/python" "$SCRIPT_DIR/rfq_attachment_extract.py" --tool-check
"$VENV_DIR/bin/python" "$SCRIPT_DIR/rfq_attachment_extract.py" --self-test

echo "rfq attachment runtime: ok"

```

### `execution/mail-lead-pipeline-dry-run.py`

```python
#!/usr/bin/env python3
"""Dry-run routing tests for the Hermes mail lead pipeline.

This script uses synthetic fixtures only. It does not read Zoho Mail, write drafts,
touch OAuth tokens, inspect real calendars, call web search, or send messages.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from attachment_router import route_message_attachments


RFQ_PATTERNS = [
    r"\brfq\b",
    r"\brequest for quote\b",
    r"\bwycen[aeęy]\b",
    r"\bofert[aeęy]\b",
    r"\bproposal\b",
    r"\bpricing\b",
    r"\bquote\b",
    r"prosze o oferte",
    r"prosz[eę] o ofert[eę]",
    r"zapytan(?:ie|ia) ofertow",
    r"formularz(?:a|em)? kontaktow",
    r"szybk[aiąe] odpowied",
    r"mniej niz 5 minut",
    r"mniej ni[zż] 5 minut",
    r"<\s*5 minut",
    r"speed[- ]?to[- ]?lead",
    r"uzupelnia(?:nie)? crm",
    r"uzupe[lł]nia(?:nie)? crm",
    r"pierwsz[aeą] odpowied",
    r"automatyzacj[aeę] (?:obslugi|obs[lł]ugi )?zapytan",
    r"automatyzacj[aeę] (?:obslugi|obs[lł]ugi )?zapyta[nń]",
    r"obs[lł]ug[aeę] zapytan",
    r"obs[lł]ug[aeę] zapyta[nń]",
    r"zapytania z (?:maila|e-?maila|formularza|inboxa)",
    r"zapytania? przychodz[ąa]",
    r"system[^\n.!?]{0,120}(?:sledz|śledz)[^\n.!?]{0,80}kont",
    r"kont[oa]? pocztow[ey]",
    r"lead(?:y|ow|ów)? inbound",
    r"inbound lead",
    r"handoff do crm",
    r"agent do zapytan",
    r"agent do zapyta[nń]",
    r"pytania poglebiajace",
    r"pytania pog[lł][eę]biaj[aą]ce",
    r"automatyzacj[^\n.!?]{0,100}zapyt",
    r"agent[^\n.!?]{0,100}zapyt",
    r"crm[^\n.!?]{0,100}zapyt",
    r"inbound[^\n.!?]{0,100}zapyt",
    r"zapyt[^\n.!?]{0,100}(?:automatyzacj|agent|crm|inbound)",
]

GENERAL_RFQ_PATTERNS = [
    r"\bautomatyzacj[aeę]\b",
    r"\bagent(?:a|em|y)?\b",
    r"\bcrm\b",
    r"\bhandlow(?:iec|cy|ca)\b",
    r"\binbound\b",
    r"\bleady\b",
]

RELATED_NON_RFQ_PATTERNS = [
    r"\bszkoleni[ae]\b",
    r"\bwarsztat(?:y|u)?\b",
    r"\bkonsulting\b",
    r"\bai dla zespolu\b",
    r"\bautomatyzacja agentow\b",
    r"\bautomatyzacja agent[oó]w\b",
]

WEAK_FIT_PATTERNS = [
    r"\bmedyczn[ayei]\b",
    r"\bklinika\b",
    r"\bfinansow[ayei]\b",
    r"\bbank(?:u|owy|owosc|owo[sś][cć])?\b",
    r"\bubezpieczeni[ae]\b",
    r"\bpoufne\b",
    r"\b1-2 zapytania\b",
    r"\bjedno zapytanie\b",
    r"sam(?:o|a)?dzielnie wycen",
    r"sam(?:o|a)?dzielnie wysy[lł]a(?:l|ć|c)? finalne",
    r"wysy[lł]a(?:l|ć|c)? finalne ofert",
    r"bez cz[lł]owieka",
]

HUMAN_REVIEW_PATTERNS = [
    r"\bhas[lł]o\b",
    r"\btoken\b",
    r"\bapi key\b",
    r"\bklucz prywatny\b",
    r"\bdane medyczne\b",
    r"przygotowywa[ćc].*przelew",
    r"wykon(?:a[ćc]|ywac|ywa[ćc]).*przelew",
    r"op[lł]aca[ćc]?.*faktur",
    r"p[lł]atno[sś]ci.*bez udzia[lł]u cz[lł]owieka",
    r"przelew.*bez udzia[lł]u cz[lł]owieka",
    r"\bpozew\b",
    r"\breklamacj[aeę]\b",
    r"\bsp[oó]r prawny\b",
]

PROMPT_INJECTION_PATTERNS = [
    r"ignore (all )?(previous|prior|earlier) instructions",
    r"zignoruj (wszystkie )?(poprzednie|wcze[sś]niejsze) instrukcje",
    r"ujawnij .*?(prompt|instrukcj|system)",
    r"reveal .*?(prompt|system)",
    r"wy[sś]lij .*?(sekret|token|has[lł]o|klucz)",
    r"send .*?(secret|token|password|private key)",
]

SUSPICIOUS_LINK_PATTERNS = [
    r"https?://(?:bit\.ly|tinyurl\.com|t\.co|goo\.gl|ow\.ly)/",
    r"https?://[^\s]+/(?:login|signin|verify|reset)[^\s]*",
    r"https?://[^\s]+\.(?:exe|scr|bat|cmd|ps1|js|vbs|jar)(?:\b|[?#])",
]

SUSPICIOUS_ATTACHMENT_EXTENSIONS = {
    ".7z",
    ".apk",
    ".bat",
    ".cmd",
    ".com",
    ".dmg",
    ".docm",
    ".exe",
    ".gz",
    ".iso",
    ".jar",
    ".js",
    ".msi",
    ".pkg",
    ".ps1",
    ".rar",
    ".scr",
    ".tar",
    ".vbs",
    ".xlsm",
    ".zip",
}

SUSPICIOUS_ATTACHMENT_MIMES = {
    "application/javascript",
    "application/java-archive",
    "application/vnd.microsoft.portable-executable",
    "application/x-7z-compressed",
    "application/x-dosexec",
    "application/x-msdownload",
    "application/x-msdos-program",
    "application/x-rar-compressed",
    "application/x-sh",
    "application/zip",
}

ATTACHMENT_EXTRACTION_CLASSES = {
    "new_quote_request",
    "quote_draft_ready",
    "existing_client_request",
    "existing_thread_reply",
    "same_domain_new_person",
}

ADMIN_PATTERNS = [
    r"\binvoice\b",
    r"\bfaktura\b",
    r"\bpayment\b",
    r"\bsubscription\b",
    r"\brenewal\b",
    r"\bbilling\b",
    r"\breceipt\b",
]

AUTOMATED_PATTERNS = [
    r"\bnewsletter\b",
    r"\bunsubscribe\b",
    r"\bwebinar\b",
    r"\bmailer-daemon\b",
]

FORMAL_INTENT_PATTERNS = {
    "complaint": [r"\breklamacj", r"complaint", r"nie dziala", r"nie działa"],
    "security": [r"\bincydent", r"security report", r"naruszen", r"podatno[sś]"],
    "legal": [r"\bpozew\b", r"\bprawny\b", r"legal notice", r"wezwan"],
    "data_request": [r"usu[nń]cie danych", r"delete my data", r"dost[eę]p do danych", r"data subject"],
    "billing": [r"\bfaktura\b", r"\bp[lł]atno", r"\bbilling\b", r"\binvoice\b"],
}


def domain_from_email(address: str) -> str:
    if "@" not in address:
        return ""
    return address.rsplit("@", 1)[1].strip().lower()


def has_any(patterns: list[str], text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def header_value(headers: dict[str, Any], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return str(value)
    return ""


def is_automated(message: dict[str, Any], combined: str) -> bool:
    sender = str(message.get("from", "")).lower()
    headers = message.get("headers", {}) or {}
    if sender.startswith(("noreply@", "no-reply@", "mailer-daemon@", "bounce@")):
        return True
    if header_value(headers, "List-Unsubscribe"):
        return True

[...ucięto w dokumentacji pomocniczej; pełny plik źródłowy: `execution/mail-lead-pipeline-dry-run.py`]
```

### `execution/pipeline_state.py`

```python
#!/usr/bin/env python3
"""SQLite-backed restart-safe state for the Hermes RFQ pipeline.

`processed_messages.json` is intentionally no longer used.  The public
PipelineState methods remain compatible with the poller, while operations and
state transitions are durable and queryable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from hermes_rfq_core import HermesStateStore, run_id, utc_now


DEFAULT_STATE_FILE = ".tmp/hermes-rfq-state/state.sqlite3"


def default_state_file() -> Path:
    return Path(os.environ.get("HERMES_RFQ_STATE_FILE", DEFAULT_STATE_FILE))


class PipelineState:
    """Compatibility facade over :class:`HermesStateStore`."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_state_file()
        self.store = HermesStateStore(self.path)

    def is_processed(self, message_id: str) -> bool:
        row = self.store.get_message(str(message_id))
        return bool(row and row["status"] in {"done", "precheck_skipped", "blocked"})

    def get(self, message_id: str) -> dict[str, Any] | None:
        row = self.store.get_message(str(message_id))
        if not row:
            return None
        try:
            record = json.loads(row.get("record_json") or "{}")
        except json.JSONDecodeError:
            record = {}
        record["status"] = row.get("status")
        return record

    def mark_processed(self, message_id: str, record: dict[str, Any] | None = None) -> dict[str, Any]:
        key = str(message_id).strip()
        if not key:
            raise ValueError("message_id must be a non-empty string")
        clean = dict(record or {})
        clean.setdefault("processed_at", utc_now())
        operations = self.store.connection.execute("SELECT status FROM operations WHERE message_id=?", (key,)).fetchall()
        action = str(clean.get("draft_action") or "")
        if action in {"final_offer_blocked", "blocked_low_confidence", "blocked_missing_thread_headers", "blocked_existing_draft_present"}:
            status = "blocked"
        elif operations and any(row["status"] in {"planned", "in_progress", "retryable_failed"} for row in operations):
            status = "analysis_pending"
        else:
            status = "done"
        self.store.record_message(key, clean, status=status, run_id_value=str(clean.get("processed_run_id") or run_id()))
        return clean

    def plan_operation(self, message_id: str, action_type: str, **kwargs: Any) -> dict[str, Any]:
        return self.store.plan_operation(str(message_id), action_type, **kwargs)

    def operation(self, message_id: str, action_type: str) -> dict[str, Any] | None:
        return self.store.operation(str(message_id), action_type)

    def operation_succeeded(self, message_id: str, action_type: str) -> bool:
        return self.store.operation_succeeded(str(message_id), action_type)

    def update_operation(self, message_id: str, action_type: str, status: str, **kwargs: Any) -> dict[str, Any]:
        return self.store.update_operation(str(message_id), action_type, status, **kwargs)

    def count(self) -> int:
        row = self.store.connection.execute("SELECT COUNT(*) AS count FROM messages WHERE status IN ('done','precheck_skipped')").fetchone()
        return int(row["count"] if row else 0)

    def save(self) -> Path:
        # SQLite is committed transaction-by-transaction.  Keep this method for
        # callers of the former JSON facade and ensure the file is private.
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return self.path


def self_test() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "nested" / "state.sqlite3"
        state = PipelineState(state_path)
        if state.count() != 0:
            failures.append("fresh state should be empty")
        state.mark_processed("msg-1", {"classification": "new_quote_request", "processed_run_id": "r1"})
        state.mark_processed("msg-2", {"classification": "newsletter_automated_spam", "wake_agent": False})
        state.plan_operation("msg-1", "customer_draft", input_hash="input", content_hash="content")
        state.update_operation("msg-1", "customer_draft", "succeeded", external_draft_id="draft-1")
        if not state.operation_succeeded("msg-1", "customer_draft"):
            failures.append("succeeded operation was not durable")
        reloaded = PipelineState(state_path)
        if not reloaded.is_processed("msg-1") or reloaded.count() != 2:
            failures.append("SQLite state did not survive restart")
        if reloaded.operation("msg-1", "customer_draft")["external_draft_id"] != "draft-1":
            failures.append("operation external id did not survive restart")
        if len(reloaded.store.list_transitions("message", "msg-1")) < 1:
            failures.append("state transition log missing")
        try:
            reloaded.mark_processed("   ", {})
            failures.append("empty message_id should raise")
        except ValueError:
            pass
    if failures:
        print("pipeline_state self-test failures:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("pipeline_state self-test: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or self-test Hermes SQLite state.")
    parser.add_argument("--state-file", default=None)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    state = PipelineState(args.state_file)
    if args.show:
        rows = state.store.connection.execute("SELECT message_id,status,thread_id,correlation_id,updated_at FROM messages ORDER BY updated_at DESC").fetchall()
        print(json.dumps({"state_file": str(state.path), "count": state.count(), "messages": [dict(row) for row in rows]}, ensure_ascii=False, indent=2))
        return 0
    parser.error("provide --self-test or --show")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

```

### `execution/requirements-rfq-attachments.txt`

```text
pymupdf>=1.26,<2
openai>=2,<3
instructor>=1.13,<2
pydantic>=2.8,<3
pillow>=10.1,<11

```

### `execution/requirements-rfq-marker.txt`

```text
marker-pdf==1.10.2
surya-ocr>=0.17.1,<0.18
pillow>=10.1,<11

```

### `execution/rfq_attachment_extract.py`

```python
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

[...ucięto w dokumentacji pomocniczej; pełny plik źródłowy: `execution/rfq_attachment_extract.py`]
```

### `execution/verify-local-workspace.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$(pwd)}"

required=(
  "AGENTS.md"
  "README.md"
  "directives/hermes-hostinger-setup.md"
  "docs/hermes-access-request.md"
  "docs/hermes-configuration-status.md"
  "USER.md"
  "MEMORY.md"
  "SOUL.md"
  "execution/secret-scan.sh"
  "execution/vps-health-check.sh"
  "execution/hermes-gateway-troubleshoot.sh"
  "execution/nightly-github-sync.sh"
  "execution/security-audit.sh"
  "skills/vps-health-check/SKILL.md"
  "skills/hermes-gateway-troubleshooting/SKILL.md"
  "skills/nightly-github-sync/SKILL.md"
  "skills/security-audit/SKILL.md"
  "skills/open-dashboard-runbook/SKILL.md"
  "skills/mail-lead-pipeline/SKILL.md"
  "skills/mail-lead-pipeline/references/business-profile.md"
  "skills/mail-lead-pipeline/references/classification.md"
  "skills/mail-lead-pipeline/references/draft-style.md"
  "skills/mail-lead-pipeline/references/calendar-availability.md"
  "skills/mail-lead-pipeline/references/research-and-crm.md"
  "skills/mail-lead-pipeline/references/telegram-escalation.md"
  "skills/mail-lead-pipeline/references/attachment-processing.md"
  "skills/mail-lead-pipeline/templates/crm-inbound-note.md"
  "directives/mail-lead-pipeline.md"
  "docs/mail-lead-pipeline-prd.md"
  "execution/mail-lead-pipeline-dry-run.py"
  "execution/attachment_router.py"
  "execution/rfq_attachment_extract.py"
  "execution/install-rfq-attachment-runtime.sh"
  "execution/requirements-rfq-attachments.txt"
  "execution/requirements-rfq-marker.txt"
  "tests/fixtures/mail-lead-pipeline/cases.json"
)

missing=0
for path in "${required[@]}"; do
  if [[ ! -e "$ROOT/$path" ]]; then
    echo "missing: $path" >&2
    missing=1
  fi
done

if [[ "$missing" -ne 0 ]]; then
  exit 1
fi

"$ROOT/execution/secret-scan.sh" "$ROOT"

python3 "$ROOT/execution/attachment_router.py" --self-test >/dev/null

RFQ_PYTHON="${HERMES_RFQ_VENV_DIR:-}/bin/python"
if [[ -z "${HERMES_RFQ_VENV_DIR:-}" || ! -x "$RFQ_PYTHON" ]]; then
  if [[ -x "$ROOT/rfq-runtime/.venv/bin/python" ]]; then
    RFQ_PYTHON="$ROOT/rfq-runtime/.venv/bin/python"
  elif [[ -x "/opt/data/rfq-runtime/.venv/bin/python" ]]; then
    RFQ_PYTHON="/opt/data/rfq-runtime/.venv/bin/python"
  else
    RFQ_PYTHON="python3"
  fi
fi

"$RFQ_PYTHON" "$ROOT/execution/rfq_attachment_extract.py" --self-test >/dev/null

python3 "$ROOT/execution/mail-lead-pipeline-dry-run.py" \
  --fixtures "$ROOT/tests/fixtures/mail-lead-pipeline/cases.json" >/dev/null

if [[ -d "$ROOT/.git" ]]; then
  if git -C "$ROOT" ls-files --error-unmatch .env >/dev/null 2>&1; then
    echo "verify-local-workspace: .env is tracked by git" >&2
    exit 1
  fi
fi

echo "verify-local-workspace: ok"

```

### `execution/zoho_mail_poller.py`

```python
#!/usr/bin/env python3
"""Zoho Mail poller for the Hermes Mail Lead Pipeline.

This poller implements the cheap two-stage design from
`directives/mail-lead-pipeline.md`:

1. Cheap pre-check on inbox list metadata only (sender, subject, attachment
   flag). Obvious automated/noise mail is recorded and skipped before any
   message body, header, or attachment metadata is fetched.
2. For messages that wake the agent, fetch the message body, RFC headers, and
   attachment metadata (still read-only), run the deterministic classifier,
   route attachment metadata for safety/extractor decisions, and compose an
   internal briefing plus a short Telegram update.

Hard safety properties of this poller:

- it sends only gated, validated, price-free pre-offer messages; final offers
  always use Zoho ``mode: draft`` via the gated `zoho_reply_draft.py`,
- it never marks mailbox messages read or mutates the inbox,
- idempotency comes from local state (`pipeline_state.py`), not from mailbox
  flags,
- Telegram is composed by default and only sent with an explicit flag.

Opt-in behaviours (off by default; used by the production 2-minute cron):

- ``--extract-attachments`` downloads safe attachment bytes for RFQ-class
  messages and runs the deterministic `rfq_attachment_extract.py` worker so
  briefings and LLM-authored drafts can summarise invoices/documents. Unsafe
  attachments are never downloaded (the local router gate decides).
- ``--auto-send`` sends a validated, price-free response for eligible messages.
  ``--auto-final-offer`` creates the final draft only after runtime and PDF
  controls pass.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from email.parser import Parser
from email.utils import parseaddr
from html import unescape
from pathlib import Path
from typing import Any, Callable

EXECUTION_DIR = Path(__file__).resolve().parent
ROOT_DIR = EXECUTION_DIR.parent
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

from attachment_router import route_message_attachments  # noqa: E402
from hermes_rfq_core import (  # noqa: E402
    visible_html_to_text,
    sha256_text,
    SafetySwitches,
    retry_decision,
)

DEFAULT_TARGET_EMAIL = "rfq-mailbox@example.invalid"
DEFAULT_FOLDER = "Inbox"
DEFAULT_LIMIT = 50
DEFAULT_TOKEN_FILE = ".tmp/zoho_mail_tokens.json"
DRAFT_FOLDER_NAMES = ("Drafts", "Wersje robocze")

# Pre-check signals. These run on cheap list metadata only (sender + subject),
# never on the message body. They mirror a conservative subset of the full
# classifier so that obvious automated mail never triggers expensive fetches.
AUTOMATED_SENDER_PREFIXES = (
    "noreply@",
    "no-reply@",
    "no_reply@",
    "donotreply@",
    "do-not-reply@",
    "mailer-daemon@",
    "bounce@",
    "bounces@",
    "postmaster@",
    "notifications@",
    "notification@",
    "newsletter@",
    "news@",
    "mailer@",
    "automated@",
)

AUTOMATED_SUBJECT_PATTERNS = (
    r"\bnewsletter\b",
    r"\bunsubscribe\b",
    r"\bwypisz si[eę]\b",
    r"\bwebinar\b",
    r"\bdigest\b",
    r"\bautoresponder\b",
    r"\bauto-?reply\b",
    r"\bout of office\b",
    r"\bautomatyczna odpowied[zź]\b",
    r"\bdelivery status notification\b",
    r"\bundeliverable\b",
    r"\bmail delivery failed\b",
)

# Classifier outcomes that, in a normal run, would justify preparing a
# customer-facing reply draft. The poller only marks them as "would create";
# the actual draft is created by the gated creator after approval.
DRAFTABLE_CLASSES = {
    "new_quote_request",
    "quote_draft_ready",
    "new_general_business_inquiry",
    "existing_client_request",
    "existing_thread_reply",
    "same_domain_new_person",
}

GREEN_FIT_CLASSES = {"new_quote_request", "quote_draft_ready", "existing_client_request"}
YELLOW_FIT_CLASSES = {
    "new_general_business_inquiry",
    "existing_thread_reply",
    "same_domain_new_person",
    "unknown_review_needed",
}

# Auto-draft policy (used only with --auto-draft). Deliberately conservative:
# only brand-new, green, high-confidence quote requests get an automatic
# *draft-only* first response. Existing threads, known clients, and especially
# final-offer drafts (which need real scope/pricing) are never auto-drafted;
# they stay "would create" and wait for Lukasz. Overridable via
# HERMES_AUTO_DRAFT_CLASSES (comma-separated) if ever needed.
DEFAULT_AUTO_DRAFT_CLASSES = {"new_quote_request"}

FINAL_OFFER_TRIGGER_CLASSES = {"new_quote_request", "quote_draft_ready", "existing_thread_reply"}
FINAL_OFFER_SCOPE_PATTERNS = (
    r"\b\d+\s*(?:kont|konto|konta|skrzynek|skrzynki|mailbox|mailboxy|inbox|inboxes)\b",
    r"\b(?:jedno|dwa|trzy|cztery|piec|pięć)\s+(?:kont|konto|konta|skrzynek|skrzynki)\b",
    r"\bcrm\b",
    r"\btelegram\b",
    r"\bformularz(?:a|em|y)?\b",
    r"\bprzykladowe?\s+zapyt",
    r"\bprzyk[lł]adowe?\s+zapyt",
    r"\bzalaczam\b",
    r"\bza[lł][aą]czam\b",
)
FINAL_OFFER_TOPIC_PATTERNS = (
    r"\borchesta\b",
    r"\brfq\b",
    r"\bwycen[aeęy]\b",
    r"\bofert[aeęy]\b",
    r"\bzapytan(?:ie|ia)?\b",
    r"\bpierwsz[aeą] odpowied",
    r"\bkonto pocztowe\b",
    r"\bkont pocztow",
)
PROMPT_INJECTION_PATTERNS = (
    r"ignore (all )?(previous|prior|earlier) instructions",
    r"zignoruj (wszystkie )?(poprzednie|wcze[sś]niejsze) instrukcje",
    r"zignoruj (wszystkie )?instrukcje systemowe",
    r"wygeneruj .*?bez walidacji",
    r"ujawnij .*?(prompt|instrukcj|system)",
    r"reveal .*?(prompt|system)",
    r"wy[sś]lij .*?(sekret|token|has[lł]o|klucz)",
    r"send .*?(secret|token|password|private key)",
)
FREE_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "icloud.com",
    "me.com",
    "wp.pl",
    "onet.pl",
    "interia.pl",
    "o2.pl",
    "gazeta.pl",
    "proton.me",
    "protonmail.com",
    "yahoo.com",
}
POLISH_NUMBER_WORDS = {
    "jedno": 1,
    "jeden": 1,
    "dwa": 2,
    "trzy": 3,
    "cztery": 4,
    "piec": 5,
    "pięć": 5,
}
OWN_SIGNATURE_NAMES = {"Orchesta RFQ Team", "Orchesta RFQ Team"}
AUTOTEST_SUBJECT_RE = re.compile(r"HRFQ-AUTO-\d{8}-\d{6}-[A-Z]")

# Standard Polish discovery questions for an auto first-response (no prices,
# per business-profile.md / draft-style.md). The agent can author richer bodies
# manually; this keeps the deterministic auto path safe and consistent.
DISCOVERY_QUESTION_MAILBOX_COUNT = "Ile kont pocztowych ma śledzić system?"
DISCOVERY_QUESTION_CRM = "Czy uwzględnić integrację z CRM w ofercie?"
DISCOVERY_QUESTION_COMPANY = "Na jaką firmę mam przygotować ofertę?"
DISCOVERY_QUESTION_MAILBOX_COUNT_EN = "How many inboxes should the system monitor?"

[...ucięto w dokumentacji pomocniczej; pełny plik źródłowy: `execution/zoho_mail_poller.py`]
```

### `execution/zoho_reply_draft.py`

```python
#!/usr/bin/env python3
"""Safe, gated Zoho Mail reply-draft creator for the Hermes RFQ pipeline.

This module turns a classified inbound message plus an agent-authored body into a
Zoho Mail *draft* that is threaded as a reply to the original message. It exists
so that, once Lukasz approves, Hermes can place a review-ready draft in the right
thread without ever sending mail.

Hard safety properties (enforced in code AND tests):

- DRAFT ONLY. The payload always uses ``mode: draft`` and never the Zoho
  send-reply endpoint. Any attempt to include a send-style field is rejected.
- THREADED. A customer-facing reply requires the inbound RFC ``Message-ID``.
  Without it, building the payload fails so the caller escalates to Telegram
  instead of starting a new thread.
- NO PRICES IN DISCOVERY. First-response / discovery drafts are rejected if the
  body contains price-like tokens (``business-profile.md`` rule).
- APPROVAL GATE. Actually creating a draft requires BOTH the environment flag
  ``HERMES_ALLOW_DRAFT_CREATE=1`` and the explicit approval phrase. Building and
  previewing payloads is always safe and offline; creation is not.

This file never sends email and must never be wired to a send endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from html import escape
from pathlib import Path
from typing import Any, Callable

DEFAULT_TARGET_EMAIL = "rfq-mailbox@example.invalid"
DEFAULT_TOKEN_FILE = ".tmp/zoho_mail_tokens.json"
DEFAULT_DRAFT_MODEL = "openai-codex/gpt-5.5"
DEFAULT_DRAFT_LLM_TIMEOUT_SECONDS = 120

# Explicit, hard-to-trigger-by-accident approval phrase. The CLI only supplies it
# when --i-have-lukasz-approval is passed AND the environment flag is set.
APPROVAL_PHRASE = "LUKASZ-APPROVED-DRAFT-CREATE"

SIGNATURE = "Orchesta RFQ Team\n\n+48 000 000 000\nLinkedIn: example.invalid/orchesta-rfq"

# Draft kinds that must never contain pricing (first response / discovery).
NO_PRICE_DRAFT_KINDS = {"first_response", "context_reply", "discovery"}

# Draft kinds this creator is allowed to build at all. Review-only classes never
# reach this module.
ALLOWED_DRAFT_KINDS = {"first_response", "context_reply", "discovery", "final_offer"}

# Fields that would indicate a send (not a draft). Their presence is a hard error.
SEND_FORBIDDEN_KEYS = {"action", "send", "sendMail", "sendReply", "deliver", "schedule"}

PRICE_PATTERNS = (
    r"\bz[lł]\b",
    r"\bz[lł]otych\b",
    r"\bpln\b",
    r"\bnetto\b",
    r"\bbrutto\b",
    r"\bcena\b",
    r"\bcennik\b",
    r"\binwestycja\b",
    r"\d[\d\s.,]*\s*(?:z[lł]|pln)\b",
)

EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002190-\U000021FF\U00002B00-\U00002BFF]",
    flags=re.UNICODE,
)

INTERNAL_CUSTOMER_TERM_PATTERNS = (
    (r"\bocr\b", "ocr"),
    (r"\bvision\b", "vision"),
    (r"\btesseract\b", "tesseract"),
    (r"\bekstraktor\w*\b", "extractor"),
    (r"\bextractor\w*\b", "extractor"),
    (r"\bparser\w*\b", "parser"),
    (r"\bzaszumion\w*\b", "noisy_ocr"),
    (r"\bdostepne podsumowanie\b", "internal_summary"),
    (r"\bpodsumowanie z\b", "internal_summary"),
    (r"\bczesciow\w+\s+(?:odczyt|rozpoznanie|podsumowanie)\b", "partial_internal_read"),
    (r"\bnie zakladam\b.{0,80}\brozpozn", "internal_uncertainty"),
)


class DraftSafetyError(ValueError):
    """Raised when a draft payload would violate a hard safety rule."""


class DraftGenerationError(RuntimeError):
    """Raised when the LLM draft body cannot be generated or validated."""


def clean_reply_subject(subject: str) -> str:
    base = re.sub(r"^\s*(re|odp|fwd|fw)\s*:\s*", "", subject or "", flags=re.IGNORECASE).strip()
    return f"Re: {base}" if base else "Re:"


def contains_price(text: str) -> bool:
    return any(re.search(pattern, text or "", re.IGNORECASE) for pattern in PRICE_PATTERNS)


def contains_emoji(text: str) -> bool:
    return bool(EMOJI_PATTERN.search(text or ""))


def ensure_signature(body: str) -> str:
    body = (body or "").rstrip()
    if "Orchesta RFQ Team" in body:
        return body
    return f"{body}\n\n{SIGNATURE}"


def strip_signature(body: str) -> str:
    """Keep the signature programmatic and single-source."""
    text = (body or "").rstrip()
    for marker in ("\n--\nOrchesta RFQ Team", "\nOrchesta RFQ Team"):
        if marker in text:
            return text.split(marker, 1)[0].rstrip()
    if "Orchesta RFQ Team" in text:
        return text.split("Orchesta RFQ Team", 1)[0].rstrip().rstrip("-").rstrip()
    return text


def customer_facing_internal_terms(text: str) -> list[str]:
    normalized = _normalize_for_checks(text)
    found: list[str] = []
    for pattern, label in INTERNAL_CUSTOMER_TERM_PATTERNS:
        if re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL):
            found.append(label)
    return found


def draft_update_guard(last_system_content_hash: str, current_content: str) -> dict[str, str]:
    """Return a safe update decision; never overwrite a changed human draft."""
    current_hash = hashlib.sha256((current_content or "").encode("utf-8")).hexdigest()
    if last_system_content_hash and current_hash != last_system_content_hash:
        return {"status": "human_edited", "content_hash": current_hash, "reason_code": "draft_content_changed_by_human"}
    return {"status": "update_allowed", "content_hash": current_hash, "reason_code": "draft_content_matches_system"}


def build_ref_header(references: str, rfc_message_id: str) -> str:
    """Build the threaded References/refHeader value.

    Combines any prior References with the inbound Message-ID, de-duplicated and
    order-preserving, as Zoho's save-draft API expects for reply drafts.
    """
    tokens: list[str] = []
    for token in re.findall(r"<[^>]+>", references or ""):
        if token not in tokens:
            tokens.append(token)
    if rfc_message_id and rfc_message_id not in tokens:
        tokens.append(rfc_message_id)
    return " ".join(tokens)


def build_draft_payload(
    *,
    account_email: str,
    to_address: str,
    inbound_subject: str,
    rfc_message_id: str,
    references: str = "",
    body_text: str,
    draft_kind: str,
    mail_format: str = "html",
    attachments: list[dict[str, Any]] | None = None,
    threaded: bool = True,
    subject_override: str | None = None,
    source_sender: str = "",
) -> dict[str, Any]:
    """Build a threaded, draft-only Zoho save-draft payload.

    Raises DraftSafetyError if any hard safety rule would be violated. The caller
    is expected to escalate to Telegram on failure instead of forcing a draft.
    """
    if draft_kind not in ALLOWED_DRAFT_KINDS:
        raise DraftSafetyError(f"draft_kind {draft_kind!r} is not allowed for customer-facing drafts")

    to_address = (to_address or "").strip()
    if not to_address or "@" not in to_address:
        raise DraftSafetyError("a valid recipient address is required")

    rfc_message_id = (rfc_message_id or "").strip()
    if threaded and not rfc_message_id:
        # Threading is mandatory: without the inbound Message-ID we cannot make a
        # proper reply draft, so we refuse instead of starting a new thread.
        raise DraftSafetyError("missing inbound RFC Message-ID; cannot create a threaded reply draft")

    if not (body_text or "").strip():
        raise DraftSafetyError("draft body must not be empty")

    approved_final_footer = draft_kind == "final_offer" and contains_approved_final_footer(body_text)

    if contains_emoji(body_text) and not approved_final_footer:
        raise DraftSafetyError("draft body must not contain emoji")

    leaked_terms = customer_facing_internal_terms(body_text)
    if leaked_terms:
        raise DraftSafetyError(f"draft body must not expose internal extraction terms: {sorted(set(leaked_terms))}")

    if re.search(r"(?m)^\s*--\s*$", body_text or "") and not approved_final_footer:
        raise DraftSafetyError("draft body must not contain a standalone signature delimiter")

    if draft_kind in NO_PRICE_DRAFT_KINDS and contains_price(body_text):
        raise DraftSafetyError(f"{draft_kind} drafts must not contain pricing (business-profile rule)")

    body_with_signature = ensure_signature(body_text)

    if mail_format == "html":
        content = escape(body_with_signature).replace("\n", "<br>")

[...ucięto w dokumentacji pomocniczej; pełny plik źródłowy: `execution/zoho_reply_draft.py`]
```
