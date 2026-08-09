from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

import google_sheets_lead_poller as sheets
import zoho_mail_poller as mail
import zoho_reply_draft as drafts
from unified_lead_registry import UnifiedLeadRegistry


NATURAL_BODY = """Dzień dobry Panie Michale,

dziękuję za wiadomość.

Taki proces można obsłużyć w Orchesta RFQ: system może zbierać zapytania z maila i formularza, przygotowywać pierwszą odpowiedź i przekazywać handlowcowi najważniejsze informacje.

Nie musicie mieć teraz gotowej listy skrzynek ani decyzji o CRM. Mogę przyjąć wariant startowy dla jednej skrzynki, z CRM jako opcją na drugi etap.

Czy taki wariant będzie dobrym punktem wyjścia do przygotowania zakresu i oferty?"""


def test_lack_of_crm_information_is_unknown_not_rejection():
    text = (
        "Nie mamy jeszcze spisanej liczby skrzynek ani informacji "
        "o integracji z CRM."
    )
    assert mail.extract_crm_decision(text) is None
    assert mail.extract_crm_decision("Nie chcemy integracji z CRM.") is False
    assert mail.extract_crm_decision("Chcemy integrację z CRM.") is True
    assert mail.extract_crm_decision("System ma uwzględniać CRM.") is True
    assert mail.extract_crm_decision("Proszę uwzględnić CRM w zakresie.") is True


def test_validator_accepts_natural_answer_without_forced_template_phrases():
    validated = drafts.validate_generated_body(
        NATURAL_BODY,
        has_attachments=False,
        approved_questions=None,
    )
    assert "Taki proces można obsłużyć" in validated
    assert "Żeby dobrze ocenić zakres" not in validated
    assert "Po tych odpowiedziach" not in validated


def test_validator_rejects_more_than_three_questions_even_when_not_numbered():
    body = """Dzień dobry,

dziękuję za wiadomość.

Taki proces można obsłużyć w Orchesta RFQ. Żeby dobrze ocenić zakres, potrzebuję doprecyzować:

- Ile skrzynek ma obsługiwać system?
- Czy uwzględnić CRM?
- Czy Telegram może być kanałem powiadomień?
- Czy oferta ma być przygotowana dzisiaj?

Po tych odpowiedziach zaproponuję sensowny zakres następnego kroku."""
    with pytest.raises(drafts.DraftGenerationError, match="too_many_questions"):
        drafts.validate_generated_body(body, has_attachments=False, approved_questions=None)


def test_prompt_sets_employee_boundaries_without_dictating_copy():
    prompt = drafts.build_llm_draft_prompt(
        {
            "sender": "michal@example.com",
            "subject": "Wdrożenie Orchesta RFQ",
            "body": "Nie znamy jeszcze liczby skrzynek ani decyzji o CRM.",
            "classification": "new_quote_request",
            "confidence": "high",
            "draft_kind": "first_response",
            "default_questions": ["Ile kont pocztowych ma śledzić system?"],
            "known_facts": {
                "mailbox_count": {"state": "unknown"},
                "crm": {"state": "unknown"},
            },
        }
    )
    assert "Najpierw odpowiedz na faktyczne pytanie" in prompt
    assert "Brak decyzji nigdy nie oznacza odpowiedzi" in prompt
    assert "zaproponuj proste, odwracalne założenie" in prompt
    assert "Użyj zdania:" not in prompt
    assert "Wolno użyć wyłącznie pytań" not in prompt
    assert "Preferuj jedno pytanie lub jedną decyzję" in prompt
    assert "body_text musi być kompletną" in prompt
    assert "połącz je w jeden wariant startowy" in prompt


def test_missing_model_questions_are_embedded_in_complete_body():
    body = "Dzień dobry,\n\nMogę zaproponować prosty wariant startowy:"
    questions = ["Czy zaczynamy od jednej skrzynki?", "Czy CRM zostawić na drugi etap?"]
    complete = drafts.ensure_questions_in_body(body, questions)
    assert complete.count("?") == 2
    assert questions[0] in complete
    assert questions[1] in complete


def test_campaign_origin_context_is_guaranteed_after_salutation():
    body = "Dzień dobry Panie Michale,\n\nTaki proces można obsłużyć w Orchesta RFQ."
    complete = drafts.ensure_campaign_origin_context(body)
    assert complete.startswith("Dzień dobry Panie Michale,")
    assert "zgłoszeniem z kampanii" in complete
    assert "formularz" not in complete.lower()
    assert complete.index("zgłoszeniem z kampanii") < complete.index("Taki proces")


def test_existing_campaign_origin_context_is_not_duplicated():
    body = (
        "Dzień dobry Pani Anno,\n\n"
        "Kontaktuję się, ponieważ wypełniła Pani formularz w naszej kampanii.\n\n"
        "Dziękuję za opis sytuacji."
    )
    assert drafts.ensure_campaign_origin_context(body) == body


def test_complete_declarative_request_does_not_get_duplicate_question():
    body = "Dzień dobry,\n\nJeśli ten wariant jest odpowiedni, proszę o krótką informację."
    complete = drafts.ensure_questions_in_body(body, ["Czy ten wariant jest odpowiedni?"])
    assert complete == body


def test_missing_salutation_is_added_from_contact_context():
    body = "Taki proces można obsłużyć w Orchesta RFQ."
    complete = drafts.ensure_customer_salutation(body, {"contact_name": "Michał Kaczmarek", "body": "Dzień dobry"})
    assert complete.startswith("Dzień dobry Panie Michale,")


def test_standalone_vocative_is_normalized_without_duplicate_greeting():
    body = "Panie Michale,\n\ndziękuję za wiadomość."
    complete = drafts.ensure_customer_salutation(body, {"contact_name": "Michał Kaczmarek", "body": "Dzień dobry"})
    assert complete == "Dzień dobry Panie Michale,\n\ndziękuję za wiadomość."
    assert complete.count("Panie Michale") == 1


def test_llm_payload_schema_rejects_empty_or_wrongly_typed_quality_fields():
    with pytest.raises(drafts.DraftGenerationError, match="body_missing"):
        drafts.validate_llm_payload({})
    with pytest.raises(drafts.DraftGenerationError, match="answered_customer_need"):
        drafts.validate_llm_payload({"body_text": NATURAL_BODY, "answered_customer_need": "false"})
    with pytest.raises(drafts.DraftGenerationError, match="too_many_questions"):
        drafts.validate_llm_payload(
            {
                "body_text": NATURAL_BODY,
                "answered_customer_need": True,
                "questions": ["a?", "b?", "c?", "d?"],
            }
        )


def test_forbidden_discovery_rules_apply_to_questions_not_answers():
    assert drafts.forbidden_discovery_questions("Obsługa RFQ może ruszyć od jednej skrzynki.") == []
    assert drafts.forbidden_discovery_questions("Czy interesuje Państwa obsługa RFQ?")


def test_blocked_attachment_is_not_exposed_to_customer_composer():
    context = {
        "body": "W załączniku przesyłam plik.",
        "attachment_routes": [
            {"filename": "bezpieczny.pdf", "route": "document", "safety": "allow"},
            {"filename": "podejrzany.exe", "route": "blocked", "safety": "block"},
        ],
        "attachment_summaries": [],
    }
    visible = drafts.customer_attachment_context(context)
    assert [item["filename"] for item in visible] == ["bezpieczny.pdf"]


def test_questions_and_examples_do_not_become_declared_offer_facts():
    assert mail.known_offer_facts("Czy system integruje się z CRM?")["crm"]["state"] == "not_asked"
    assert mail.known_offer_facts("Czy system obsłuży 10 skrzynek?")["mailbox_count"]["state"] == "not_asked"
    assert mail.known_offer_facts("Mamy jedną skrzynkę.")["mailbox_count"] == {"state": "known", "value": 1}


def test_sheet_pre_offer_send_uses_intelligent_body_generator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def list_accounts(self):
            return [{"accountId": "account-1", "primaryEmailAddress": "rfq-mailbox@example.invalid"}]

    class FakePoster:
        def __init__(self, *_args, **_kwargs):
            pass

        def send_message(self, _account_id: str, payload: dict[str, object]):
            captured["payload"] = payload
            return 201, {"data": {"messageId": "sent-intelligent-1"}}

    def generator(context: dict[str, object]):
        captured["context"] = context
        return {
            "body_text": NATURAL_BODY,
            "generator": "test_employee_composer",
            "model": "test-model",
            "safety_notes": [],
        }

    monkeypatch.setattr(sheets, "HttpZohoClient", FakeClient)
    monkeypatch.setattr(sheets, "HttpPreOfferPoster", FakePoster)
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    monkeypatch.setenv("ZOHO_MAIL_ACCOUNT_EMAIL", "rfq-mailbox@example.invalid")

    lead = {
        "Imię": "Michał Kaczmarek",
        "Email": "identity-018@gmail.com",
        "Firma": "NovaLead Logistics Sp. z o.o.",
        "Wiadomość": (
            "Chcemy wdrożyć Orchesta RFQ do obsługi zapytań z maila i formularza. "
            "Nie mamy jeszcze spisanej liczby skrzynek ani informacji o integracji z CRM."
        ),
        "Źródło": "META ADS",
    }
    registry = UnifiedLeadRegistry(tmp_path / "unified.sqlite3")
    source_key = "sheet:test:3"
    event = registry.register_event(
        source_type="google_sheets", source_key=source_key,
        email=lead["Email"], company=lead["Firma"], contact_name=lead["Imię"],
        content=lead["Wiadomość"], relation="new",
    )
    registry.persist_message_policy(
        "google_sheets", source_key, requested_type="missing_data_request",
        effective_type="missing_data_request", transport_mode="auto_send", reasons=["test"],
    )
    assert registry.claim_response(
        event["deal_id"], "response:sheet:test:3", content_hash="sheet-test-hash",
        operation_id="response:sheet:test:3", owner="test-owner",
        message_type="missing_data_request", recipient=lead["Email"],
        source_type="google_sheets", source_key=source_key,
        marker="response:sheet:test:3",
    )
    result = sheets.send_sheet_zoho_response(
        lead=lead,
        result={"classification": "new_quote_request", "confidence": "high"},
        row_number=3,
        digest="a" * 64,
        zoho_token_file="unused-token.json",
        env_file="unused.env",
        body_generator=generator,
        durable_context={
            "registry": registry,
            "source_type": "google_sheets",
            "source_key": source_key,
            "deal_id": event["deal_id"],
            "operation_id": "response:sheet:test:3",
            "process_stage": "response:sheet:test:3",
        },
        message_type="missing_data_request",
    )

    assert result["action"] == "sent"
    assert result["draft_generation"]["generator"] == "test_employee_composer"
    context = captured["context"]
    assert isinstance(context, dict)
    assert context["source_type"] == "google_sheets"
    assert context["company"] == "NovaLead Logistics Sp. z o.o."
    assert context["known_facts"]["crm"]["state"] == "unknown_confirmed"
    payload_text = json.dumps(captured["payload"], ensure_ascii=False)
    assert "zgłoszeniem z kampanii" in payload_text
    assert "formularza kampanii" not in payload_text
    assert "Taki proces można obsłużyć" in payload_text
    registry.close()
