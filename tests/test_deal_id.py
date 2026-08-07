from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

import deal_id
from hermes_rfq_core import DEAL_STATES, MESSAGE_STATES
from unified_lead_registry import UnifiedLeadRegistry


def test_deal_states_match_spec_section_18():
    assert DEAL_STATES == {
        "received", "security_checked", "classified", "awaiting_customer",
        "awaiting_human", "ready_for_offer", "offer_generated", "sent",
        "blocked", "closed",
    }


def test_awaiting_customer_is_a_message_state():
    assert "awaiting_customer" in MESSAGE_STATES
    assert "awaiting_human" in MESSAGE_STATES


def test_format_deal_id():
    assert deal_id.format_deal_id(dt.date(2026, 8, 3), 42) == "RFQ-20260803-0042"
    assert deal_id.format_deal_id("2026-08-03", 7) == "RFQ-20260803-0007"


def test_parse_and_validate_deal_id():
    assert deal_id.parse_deal_id("RFQ-20260803-0042") == ("20260803", 42)
    assert deal_id.parse_deal_id("not-a-deal") is None
    assert deal_id.is_valid_deal_id("RFQ-20260803-0042") is True
    assert deal_id.is_valid_deal_id("RFQ-20260803-42") is False


def test_first_message_subject_format():
    subj = deal_id.first_message_subject("Orchesta", "ABC Sp. z o.o.", "RFQ-20260803-0042")
    assert subj == "Orchesta RFQ | ABC Sp. z o.o. | RFQ-20260803-0042"


def test_extract_deal_id_from_headers_is_case_insensitive():
    assert deal_id.extract_deal_id_from_headers({"X-Hermes-Deal-ID": "RFQ-20260803-0042"}) == "RFQ-20260803-0042"
    assert deal_id.extract_deal_id_from_headers({"x-hermes-deal-id": "RFQ-20260803-0042"}) == "RFQ-20260803-0042"
    assert deal_id.extract_deal_id_from_headers({}) == ""
    assert deal_id.extract_deal_id_from_headers(None) == ""


def test_correlation_candidates_in_spec_order():
    cands = deal_id.correlation_candidates(
        in_reply_to="<a@b>",
        references="<c@d>",
        headers={"X-Hermes-Deal-ID": "RFQ-20260803-0042"},
        subject="Re: Orchesta RFQ | ABC | RFQ-20260803-0042",
    )
    # Order: In-Reply-To, References, X-Hermes-Deal-ID, subject id
    assert cands[0] == "<a@b>"
    assert cands[1] == "<c@d>"
    assert cands[2] == "RFQ-20260803-0042"
    assert cands[3] == "RFQ-20260803-0042"


def _seed_deal(registry: UnifiedLeadRegistry, email: str) -> str:
    event = registry.register_event(
        source_type="mail",
        source_key=f"acc-1:seed-{email}",
        email=email,
        company="ABC",
        contact_name="Krzysztof",
        content="seed",
        relation="new",
        thread_id=f"thread-{email}",
        tenant_id="orchesta",
    )
    return str(event.get("deal_id") or "")


def test_assign_rfq_id_is_per_day_sequential(tmp_path: Path):
    registry = UnifiedLeadRegistry(tmp_path / "reg.sqlite3")
    d1 = _seed_deal(registry, "a@example.com")
    d2 = _seed_deal(registry, "b@example.com")
    d3 = _seed_deal(registry, "c@example.com")
    r1 = registry.assign_rfq_id(d1, date="2026-08-03")
    r2 = registry.assign_rfq_id(d2, date="2026-08-03")
    r3 = registry.assign_rfq_id(d3, date="2026-08-03")
    assert r1 == "RFQ-20260803-0001"
    assert r2 == "RFQ-20260803-0002"
    assert r3 == "RFQ-20260803-0003"
    # persisted
    assert registry.get_rfq_id(d2) == "RFQ-20260803-0002"


def test_assign_rfq_id_is_idempotent(tmp_path: Path):
    registry = UnifiedLeadRegistry(tmp_path / "reg.sqlite3")
    d = _seed_deal(registry, "a@example.com")
    first = registry.assign_rfq_id(d, date="2026-08-03")
    second = registry.assign_rfq_id(d, date="2026-08-03")
    assert first == second


def test_assign_rfq_id_separates_days(tmp_path: Path):
    registry = UnifiedLeadRegistry(tmp_path / "reg.sqlite3")
    d1 = _seed_deal(registry, "a@example.com")
    d2 = _seed_deal(registry, "b@example.com")
    r1 = registry.assign_rfq_id(d1, date="2026-08-03")
    r2 = registry.assign_rfq_id(d2, date="2026-08-04")
    assert r1 == "RFQ-20260803-0001"
    assert r2 == "RFQ-20260804-0001"
