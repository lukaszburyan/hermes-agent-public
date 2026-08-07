from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

from message_policy import (  # noqa: E402
    AUTO_SEND_TYPES,
    FINAL_OFFER,
    MANUAL_REVIEW,
    MESSAGE_TYPES,
    decide_message_policy,
    operational_autosend_enabled,
)


def test_required_message_types_and_transport_modes_are_stable():
    assert MESSAGE_TYPES == {
        "acknowledgement",
        "clarification_request",
        "missing_data_request",
        "follow_up",
        "ready_for_offer_notice",
        "final_offer",
    }
    assert FINAL_OFFER not in AUTO_SEND_TYPES
    for message_type in AUTO_SEND_TYPES:
        assert decide_message_policy(message_type).transport_mode == "auto_send"
    assert decide_message_policy(FINAL_OFFER).transport_mode == "draft_only"


def test_operational_transport_requires_the_exact_deployment_gate():
    assert operational_autosend_enabled({}) is False
    assert operational_autosend_enabled({"HERMES_OPERATIONAL_AUTOSEND_ENABLED": "true"}) is False
    assert operational_autosend_enabled({"HERMES_OPERATIONAL_AUTOSEND_ENABLED": "1"}) is True


@pytest.mark.parametrize(
    "body",
    [
        "Cena końcowa: 7200 PLN netto.",
        "Cena końcowa: 7 200 zł netto.",
        "Cena końcowa: 7.200 EUR.",
        "Cena końcowa: 7,200 USD.",
        "Inwestycja wynosi €5000.",
        "Inwestycja wynosi $5000.",
        "Abonament: 1499 miesięcznie.",
        "Przyznajemy rabat 10%.",
        "Cena wynosi siedem tysięcy dwieście złotych.",
        "Inwestycja wynosi piętnaście tysięcy pięćset złotych.",
        "Koszt to dwanaście tysięcy euro.",
        "Warunki płatności: 50% zaliczki i 50% po odbiorze.",
        "Oferta jest ważna przez 14 dni.",
    ],
)
def test_commercial_content_can_only_upgrade_an_operational_type_to_final_offer(body: str):
    decision = decide_message_policy("clarification_request", body_text=body)
    assert decision.effective_type == FINAL_OFFER
    assert decision.transport_mode == "draft_only"


@pytest.mark.parametrize(
    "body",
    [
        "Telefon kontaktowy: +48 720 000 200.",
        "Spotkanie odbędzie się 7.08.2026 o 12:00.",
        "Element ma wymiary 7200 x 5000 mm.",
        "Potrzebujemy 1499 sztuk śrub.",
        "Numer zamówienia: 7200.",
        "Przekazuję komplet danych do przygotowania oferty.",
    ],
)
def test_non_financial_numbers_and_ready_notice_do_not_become_final_offer(body: str):
    decision = decide_message_policy("ready_for_offer_notice", body_text=body)
    assert decision.effective_type == "ready_for_offer_notice"
    assert decision.transport_mode == "auto_send"


def test_offer_pdf_or_acceptance_call_forces_draft_only():
    attachment = decide_message_policy(
        "acknowledgement",
        body_text="Dokument w załączeniu.",
        attachments=[{"attachmentName": "Oferta_ORCHESTA_2026.pdf"}],
    )
    acceptance = decide_message_policy(
        "follow_up",
        body_text="Proszę o akceptację finalnej oferty.",
    )
    assert attachment.effective_type == FINAL_OFFER
    assert acceptance.effective_type == FINAL_OFFER


def test_safe_extracted_offer_attachment_content_forces_draft_only():
    decision = decide_message_policy(
        "clarification_request",
        body_text="Dziękuję za materiały.",
        attachments=[
            {
                "attachmentName": "dokument.pdf",
                "safe_text": "Finalna oferta. Inwestycja wynosi 12 500 PLN netto.",
            }
        ],
    )
    assert decision.effective_type == FINAL_OFFER
    assert "offer_attachment_content" in decision.reasons


def test_unknown_type_is_manual_review_and_final_deal_stage_cannot_be_downgraded():
    assert decide_message_policy("model_invented_type").effective_type == MANUAL_REVIEW
    decision = decide_message_policy("acknowledgement", deal_stage="offer_ready")
    assert decision.effective_type == FINAL_OFFER
    assert decision.transport_mode == "draft_only"


def test_ready_notice_is_operational_only_before_offer_draft_and_content_still_wins():
    safe = decide_message_policy(
        "ready_for_offer_notice",
        body_text="Mamy komplet danych. Przekazuję sprawę do przygotowania oferty.",
        deal_stage="ready_for_final_offer",
    )
    assert safe.effective_type == "ready_for_offer_notice"
    assert safe.transport_mode == "auto_send"

    priced = decide_message_policy(
        "ready_for_offer_notice",
        body_text="Mamy komplet danych. Cena wynosi 7200 PLN.",
        deal_stage="ready_for_final_offer",
    )
    assert priced.effective_type == FINAL_OFFER
    assert priced.transport_mode == "draft_only"

    after_draft = decide_message_policy(
        "ready_for_offer_notice",
        body_text="Mamy komplet danych.",
        deal_stage="offer_draft_created",
    )
    assert after_draft.effective_type == FINAL_OFFER
