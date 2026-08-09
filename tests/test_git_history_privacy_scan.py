from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.git_history_privacy_scan import (
    classify_email,
    classify_phone,
    phone_candidate,
)


def test_email_classifier_recognizes_reserved_and_local_placeholders() -> None:
    assert classify_email("buyer@example.com") == "placeholder_or_public"
    assert classify_email("buyer@tenant.hermes.local") == "placeholder_or_public"
    assert classify_email("buyer@vps.example.invalid") == "placeholder_or_public"
    assert classify_email("identity-090@customer-060.example.com") == "review"


def test_phone_classifier_keeps_structurally_valid_placeholders_out_of_review() -> None:
    assert phone_candidate("+48 111 111 111") is True
    assert classify_phone("+48 111 111 111") == "placeholder"
    assert classify_phone("+48 501 234 567") == "review"


def test_phone_candidate_rejects_non_phone_length() -> None:
    assert phone_candidate("12345") is False
    assert phone_candidate("+48 501 234 567 890 123") is False
