#!/usr/bin/env python3
"""Deal identifier and thread correlation helpers (spec section 19).

Each deal gets a human-readable id of the form ``RFQ-YYYYMMDD-NNNN`` (e.g.
``RFQ-20260803-0042``), exposed via the ``X-Hermes-Deal-ID`` header and the
first message subject. Reply correlation checks signals in this order
(spec section 19):

  1. In-Reply-To
  2. References
  3. X-Hermes-Deal-ID
  4. the id in the subject
  5. other helper data

Deals are never correlated by email / company / subject alone.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

X_HERMES_DEAL_ID_HEADER = "X-Hermes-Deal-ID"

# RFQ-YYYYMMDD-NNNN
_DEAL_ID_RE = re.compile(r"^RFQ-(\d{8})-(\d{4})$")


def format_deal_id(date: dt.date | str, sequence: int) -> str:
    """Return ``RFQ-YYYYMMDD-NNNN`` for a given date and per-day sequence."""
    if isinstance(date, dt.date):
        yyyymmdd = date.strftime("%Y%m%d")
    else:
        yyyymmdd = str(date or "").replace("-", "")[:8]
    if len(yyyymmdd) != 8:
        raise ValueError(f"deal id date must be YYYYMMDD or a date; got {date!r}")
    return f"RFQ-{yyyymmdd}-{int(sequence):04d}"


def parse_deal_id(rfq_id: str) -> tuple[str, int] | None:
    """Return ``(yyyymmdd, sequence)`` or None if not a valid RFQ id."""
    if not rfq_id:
        return None
    match = _DEAL_ID_RE.match(str(rfq_id).strip().upper())
    if not match:
        return None
    return match.group(1), int(match.group(2))


def is_valid_deal_id(rfq_id: str) -> bool:
    return parse_deal_id(rfq_id) is not None


def first_message_subject(tenant_name: str, company_name: str, rfq_id: str) -> str:
    """Spec section 19: ``Orchesta RFQ | Nazwa firmy | RFQ-20260803-0042``."""
    tenant = (tenant_name or "").strip() or "RFQ"
    company = (company_name or "").strip() or "—"
    deal = (rfq_id or "").strip()
    if not deal:
        raise ValueError("rfq_id is required for the first message subject")
    return f"{tenant} RFQ | {company} | {deal}"


def extract_deal_id_from_headers(headers: dict[str, str] | None) -> str:
    """Find the X-Hermes-Deal-ID header (case-insensitive)."""
    if not headers:
        return ""
    target = X_HERMES_DEAL_ID_HEADER.lower()
    for key, value in headers.items():
        if str(key).lower() == target:
            return str(value or "").strip()
    return ""


def correlation_candidates(*, in_reply_to: str, references: str, headers: dict[str, str] | None, subject: str) -> list[str]:
    """Return correlation signals in the spec order (strongest first).

    Used to look up the deal a reply belongs to. Empty signals are skipped.
    """
    candidates: list[str] = []
    for value in (in_reply_to, references):
        if value:
            candidates.append(str(value).strip())
    x_hermes = extract_deal_id_from_headers(headers)
    if x_hermes:
        candidates.append(x_hermes)
    if subject:
        match = re.search(r"RFQ-\d{8}-\d{4}", subject)
        if match:
            candidates.append(match.group(0))
    return candidates


def self_test() -> int:
    failures: list[str] = []
    if format_deal_id(dt.date(2026, 8, 3), 42) != "RFQ-20260803-0042":
        failures.append("format_deal_id wrong")
    if format_deal_id("2026-08-03", 7) != "RFQ-20260803-0007":
        failures.append("format_deal_id from string wrong")
    if parse_deal_id("RFQ-20260803-0042") != ("20260803", 42):
        failures.append("parse_deal_id wrong")
    if parse_deal_id("not-a-deal") is not None:
        failures.append("parse_deal_id should reject non-deal")
    if not is_valid_deal_id("RFQ-20260803-0042"):
        failures.append("is_valid_deal_id wrong")
    subj = first_message_subject("Orchesta", "ABC Sp. z o.o.", "RFQ-20260803-0042")
    if subj != "Orchesta RFQ | ABC Sp. z o.o. | RFQ-20260803-0042":
        failures.append(f"first_message_subject wrong: {subj}")
    headers = {"X-Hermes-Deal-ID": "RFQ-20260803-0042", "In-Reply-To": "<x@y>"}
    if extract_deal_id_from_headers(headers) != "RFQ-20260803-0042":
        failures.append("extract_deal_id_from_headers wrong")
    if extract_deal_id_from_headers({"x-hermes-deal-id": "RFQ-20260803-0042"}) != "RFQ-20260803-0042":
        failures.append("header lookup should be case-insensitive")
    cands = correlation_candidates(
        in_reply_to="<a@b>", references="<c@d>", headers={"X-Hermes-Deal-ID": "RFQ-20260803-0042"},
        subject="Re: Orchesta RFQ | ABC | RFQ-20260803-0042",
    )
    if cands[0] != "<a@b>" or cands[1] != "<c@d>" or cands[2] != "RFQ-20260803-0042" or cands[3] != "RFQ-20260803-0042":
        failures.append(f"correlation order wrong: {cands}")
    if failures:
        for failure in failures:
            print(f"deal_id self-test FAIL: {failure}")
        return 1
    print("deal_id self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
