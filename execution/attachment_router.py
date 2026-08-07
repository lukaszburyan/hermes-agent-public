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
