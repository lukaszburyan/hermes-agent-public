from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


registry_module = load_module("unified_lead_registry_multi_deal", EXECUTION / "unified_lead_registry.py")
UnifiedLeadRegistry = registry_module.UnifiedLeadRegistry


@pytest.fixture
def registry(tmp_path: Path):
    instance = UnifiedLeadRegistry(tmp_path / "registry.sqlite3")
    yield instance
    instance.close()


def register_mail(
    registry: UnifiedLeadRegistry,
    *,
    key: str,
    subject: str,
    body: str,
    thread_id: str = "",
    in_reply_to: str = "",
    references: str = "",
    rfc_message_id: str = "",
    email: str = "identity-093@customer-061.example.com",
    company: str = "Firma",
    facts: dict | None = None,
    contact_name: str = "Jan Kowalski",
):
    metadata = {
        "subject": subject,
        "thread_id": thread_id,
        "in_reply_to": in_reply_to,
        "references": references,
        "rfc_message_id": rfc_message_id,
        "body": body,
    }
    return registry.register_event(
        source_type="zoho_mail",
        source_key=key,
        email=email,
        company=company,
        contact_name=contact_name,
        relation="new",
        content=f"Temat: {subject}\nTreść: {body}",
        facts=facts or {},
        source_metadata=metadata,
    )


def register_sheet(
    registry: UnifiedLeadRegistry,
    *,
    key: str,
    email: str,
    company: str,
    contact_name: str,
    message: str,
    facts: dict | None = None,
):
    """Register a Google Sheets lead (no subject line; the message body is the content)."""
    return registry.register_event(
        source_type="google_sheets",
        source_key=key,
        email=email,
        company=company,
        contact_name=contact_name,
        relation="new",
        content=message,
        facts=facts or {},
        source_metadata={},
    )


def seed_crm_and_telegram(registry: UnifiedLeadRegistry):
    crm = register_mail(
        registry,
        key="seed-crm",
        subject="Automatyzacja CRM",
        body="Automatyzacja leadów w HubSpot i pipeline sprzedażowy.",
        thread_id="thread-crm",
        rfc_message_id="<identity-094@customer-061.example.com>",
        facts={"service": "CRM", "integration": "HubSpot", "pipeline": "sprzedażowy"},
    )
    telegram = register_mail(
        registry,
        key="seed-telegram",
        subject="Integracja Telegrama",
        body="To nowy projekt: bot Telegram, grupa i powiadomienia.",
        thread_id="thread-telegram",
        rfc_message_id="<identity-095@customer-061.example.com>",
        facts={"service": "Telegram", "integration": "bot", "target": "grupa"},
    )
    assert crm["deal_id"] != telegram["deal_id"]
    return crm, telegram


def assert_action(event: dict, expected: str) -> None:
    assert event["routing_action"] == expected
    assert event["routing_decision"]["action"] == expected


def test_01_same_sender_crm_thread_links_deal_a(registry):
    crm, _ = seed_crm_and_telegram(registry)
    event = register_mail(
        registry,
        key="crm-reply",
        subject="Re: Automatyzacja CRM",
        body="Potwierdzam zakres leadów i pipeline.",
        thread_id="thread-crm",
    )
    assert_action(event, "link_existing")
    assert event["deal_id"] == crm["deal_id"]


def test_02_same_sender_telegram_thread_links_deal_b(registry):
    _, telegram = seed_crm_and_telegram(registry)
    event = register_mail(
        registry,
        key="telegram-reply",
        subject="Re: Integracja Telegrama",
        body="Bot ma publikować na grupie.",
        thread_id="thread-telegram",
    )
    assert_action(event, "link_existing")
    assert event["deal_id"] == telegram["deal_id"]


def test_03_new_invoice_topic_creates_deal_c(registry):
    crm, telegram = seed_crm_and_telegram(registry)
    event = register_mail(
        registry,
        key="invoice-new",
        subject="Automatyczne przetwarzanie faktur",
        body="Potrzebujemy OCR faktur i eksportu do księgowości.",
        thread_id="thread-invoices",
        facts={"service": "OCR faktur"},
    )
    assert_action(event, "create_new")
    assert event["deal_id"] not in {crm["deal_id"], telegram["deal_id"]}
    assert event["requires_review"] is False


def test_04_generic_subject_semantically_links_telegram(registry):
    _, telegram = seed_crm_and_telegram(registry)
    event = register_mail(
        registry,
        key="semantic-telegram",
        subject="Pytanie",
        body="Czy bot może wysyłać powiadomienia na grupę Telegram?",
    )
    assert_action(event, "link_existing")
    assert event["deal_id"] == telegram["deal_id"]


def test_05_generic_subject_semantically_links_crm(registry):
    crm, _ = seed_crm_and_telegram(registry)
    event = register_mail(
        registry,
        key="semantic-crm",
        subject="Prośba o informacje",
        body="Jak będą obsługiwane leady w HubSpot i etapy pipeline sprzedażowego?",
    )
    assert_action(event, "link_existing")
    assert event["deal_id"] == crm["deal_id"]


def test_06_ambiguous_message_without_router_requires_manual_review(registry):
    crm, telegram = seed_crm_and_telegram(registry)
    before = {deal_id: registry.get_deal(deal_id)["facts"] for deal_id in (crm["deal_id"], telegram["deal_id"])}
    event = register_mail(
        registry,
        key="ambiguous-generic",
        subject="Dzień dobry",
        body="Dzień dobry, proszę o informacje.",
    )
    assert_action(event, "review")
    assert event["deal_id"] not in before
    assert event["requires_review"] is True
    assert event["status"] == "review_required"
    assert not any(key.startswith("clarification") for key in event)
    audit = registry.correlation_audit("zoho_mail", "ambiguous-generic")
    assert audit["action"] == "review"
    assert audit["reason_code"] == "llm_router_unavailable"
    assert set(audit["candidate_scores"]) == set(before)
    assert audit["reason_code"]
    assert registry.get_deal(crm["deal_id"])["facts"] == before[crm["deal_id"]]
    assert registry.get_deal(telegram["deal_id"])["facts"] == before[telegram["deal_id"]]


def test_ambiguous_llm_high_confidence_existing_links_known_deal(registry):
    crm, telegram = seed_crm_and_telegram(registry)
    captured = {}

    def router(payload):
        captured.update(payload)
        return {"decision": "existing", "deal_id": crm["deal_id"], "confidence": 0.91, "reason": "pasuje do CRM"}

    registry.routing_llm = router
    event = register_mail(registry, key="llm-existing", subject="Dzień dobry", body="Proszę o informacje.")
    assert_action(event, "link_existing")
    assert event["deal_id"] == crm["deal_id"]
    assert {item["deal_id"] for item in captured["active_deals"]} == {crm["deal_id"], telegram["deal_id"]}
    assert set(captured) == {"current_message", "active_deals", "output_contract"}


def test_ambiguous_llm_high_confidence_new_creates_separate_deal(registry):
    crm, telegram = seed_crm_and_telegram(registry)
    registry.routing_llm = lambda _payload: {
        "decision": "new", "deal_id": "", "confidence": 0.93, "reason": "nowy temat",
    }
    event = register_mail(registry, key="llm-new", subject="Dzień dobry", body="Proszę o informacje.")
    assert_action(event, "create_new")
    assert event["deal_id"] not in {crm["deal_id"], telegram["deal_id"]}


@pytest.mark.parametrize(
    "model_output,reason",
    [
        ("not-json", "llm_invalid_json"),
        ({"decision": "existing", "deal_id": "deal-unknown", "confidence": 0.99, "reason": "x"}, "llm_unknown_deal_id"),
        ({"decision": "existing", "deal_id": "", "confidence": 0.99, "reason": "x"}, "llm_unknown_deal_id"),
    ],
)
def test_invalid_llm_routing_outputs_require_manual_review(registry, model_output, reason):
    seed_crm_and_telegram(registry)
    registry.routing_llm = lambda _payload: model_output
    event = register_mail(registry, key=f"llm-invalid-{reason}-{type(model_output).__name__}", subject="Dzień dobry", body="Proszę o informacje.")
    assert_action(event, "review")
    assert event["reason_code"] == reason
    assert not any(key.startswith("clarification") for key in event)


def test_07_two_equally_strong_matches_require_review(registry):
    first = register_mail(
        registry,
        key="crm-a",
        subject="CRM sprzedaż",
        body="HubSpot CRM, leady i pipeline sprzedażowy.",
        thread_id="crm-a-thread",
        facts={"service": "HubSpot CRM", "scope": "leady pipeline"},
    )
    second = register_mail(
        registry,
        key="crm-b",
        subject="Integracja CRM",
        body="Nowy projekt CRM: HubSpot, leady i pipeline sprzedażowy.",
        thread_id="crm-b-thread",
        facts={"service": "HubSpot CRM", "scope": "leady pipeline"},
    )
    event = register_mail(
        registry,
        key="crm-equal",
        subject="Pytanie",
        body="Pytanie o HubSpot CRM, leady i pipeline sprzedażowy.",
    )
    assert_action(event, "review")
    assert event["requires_review"] is True
    assert event["deal_id"] not in {first["deal_id"], second["deal_id"]}
    assert event["routing_decision"]["margin"] < registry_module.ROUTING_REVIEW_MARGIN


def test_08_explicit_new_matter_creates_new_deal(registry):
    crm, telegram = seed_crm_and_telegram(registry)
    event = register_mail(
        registry,
        key="explicit-new",
        subject="Pytanie",
        body="To nowa sprawa: potrzebujemy automatycznego obiegu umów.",
    )
    assert_action(event, "create_new")
    assert event["deal_id"] not in {crm["deal_id"], telegram["deal_id"]}
    assert "explicit_new_matter" in event["routing_decision"]["evidence"]


def test_09_informative_new_subject_without_reply_headers_creates_new_deal(registry):
    crm, telegram = seed_crm_and_telegram(registry)
    event = register_mail(
        registry,
        key="informative-new",
        subject="Synchronizacja stanów magazynowych z ERP",
        body="Chodzi o osobny zakres magazynu i system ERP.",
    )
    assert_action(event, "create_new")
    assert event["deal_id"] not in {crm["deal_id"], telegram["deal_id"]}
    assert event["routing_decision"]["reason"] == "informative_new_topic_low_similarity"


def test_10_new_deal_does_not_inherit_facts_context_or_drafts(registry):
    crm, telegram = seed_crm_and_telegram(registry)
    registry.record_draft(crm["deal_id"], "discovery", draft_id="draft-crm", content_hash="hash-crm")
    registry.record_draft(telegram["deal_id"], "discovery", draft_id="draft-telegram", content_hash="hash-telegram")
    event = register_mail(
        registry,
        key="isolated-invoice",
        subject="Automatyczne przetwarzanie faktur",
        body="Nowy projekt OCR faktur.",
        facts={"service": "OCR", "document": "invoice"},
    )
    assert_action(event, "create_new")
    deal = registry.get_deal(event["deal_id"])
    assert deal["facts"] == {"service": "OCR", "document": "invoice"}
    assert deal["current_draft_id"] is None
    context = registry.context_text(event["deal_id"])
    assert "HubSpot" not in context
    assert "Telegram" not in context


def test_11_thread_id_precedes_semantic_similarity(registry):
    crm, _ = seed_crm_and_telegram(registry)
    event = register_mail(
        registry,
        key="thread-wins",
        subject="Pytanie o Telegram",
        body="Bot Telegram ma wysyłać powiadomienia na grupę.",
        thread_id="thread-crm",
    )
    assert_action(event, "link_existing")
    assert event["deal_id"] == crm["deal_id"]
    assert "hard:thread_id" in event["routing_decision"]["evidence"]


def test_12_in_reply_to_precedes_subject_of_another_deal(registry):
    crm, _ = seed_crm_and_telegram(registry)
    event = register_mail(
        registry,
        key="reply-header-wins",
        subject="Re: Integracja Telegrama",
        body="Odpowiadam w sprawie tego zakresu.",
        in_reply_to="<identity-094@customer-061.example.com>",
        references="<identity-094@customer-061.example.com>",
    )
    assert_action(event, "link_existing")
    assert event["deal_id"] == crm["deal_id"]
    assert "hard:in_reply_to" in event["routing_decision"]["evidence"]


def test_13_conflicting_hard_signals_require_review(registry):
    crm, telegram = seed_crm_and_telegram(registry)
    event = register_mail(
        registry,
        key="hard-conflict",
        subject="Re: Automatyzacja CRM",
        body="Odpowiedź.",
        thread_id="thread-crm",
        in_reply_to="<identity-095@customer-061.example.com>",
    )
    assert_action(event, "review")
    assert event["requires_review"] is True
    assert event["deal_id"] not in {crm["deal_id"], telegram["deal_id"]}
    assert event["routing_decision"]["reason"] == "conflicting_hard_signals"


def test_14_human_takeover_in_deal_a_does_not_stop_deal_b(registry):
    crm, telegram = seed_crm_and_telegram(registry)
    registry.pause_automation(
        crm["deal_id"],
        account_id="acc-1",
        thread_id="thread-crm",
        reason="human_takeover",
        actor="test",
    )
    assert registry.evaluate_automation(
        crm["deal_id"], account_id="acc-1", thread_id="thread-crm"
    )["allowed"] is False
    assert registry.evaluate_automation(
        telegram["deal_id"], account_id="acc-1", thread_id="thread-telegram"
    )["allowed"] is True
    assert registry.claim_response(
        telegram["deal_id"], "response:telegram", content_hash="telegram-response"
    ) is True


def test_15_closed_deal_does_not_block_new_matter(registry):
    old = register_mail(
        registry,
        key="old-invoice",
        subject="Automatyczne przetwarzanie faktur",
        body="OCR faktur dla księgowości.",
        thread_id="old-invoice-thread",
    )
    registry.set_status(old["deal_id"], "closed")
    event = register_mail(
        registry,
        key="new-after-closed",
        subject="Automatyczne przetwarzanie faktur",
        body="Nowy projekt OCR faktur dla innej spółki.",
    )
    assert_action(event, "create_new")
    assert event["deal_id"] != old["deal_id"]


def test_16_same_sender_can_have_three_or_more_active_deals(registry):
    crm, telegram = seed_crm_and_telegram(registry)
    invoice = register_mail(
        registry,
        key="third-deal",
        subject="Automatyczne przetwarzanie faktur",
        body="Nowy projekt: OCR faktur i księgowość.",
    )
    erp = register_mail(
        registry,
        key="fourth-deal",
        subject="Synchronizacja magazynu z ERP",
        body="Kolejne zapytanie: integracja ERP i stanów magazynowych.",
    )
    ids = {crm["deal_id"], telegram["deal_id"], invoice["deal_id"], erp["deal_id"]}
    assert len(ids) == 4
    rows = registry.connection.execute(
        "SELECT deal_id FROM unified_deals WHERE primary_email=? AND status NOT IN ('closed','lost','cancelled','archived')",
        ("identity-093@customer-061.example.com",),
    ).fetchall()
    assert ids <= {str(row["deal_id"]) for row in rows}


def test_17_reprocessing_same_message_does_not_create_second_deal(registry):
    seed_crm_and_telegram(registry)
    first = register_mail(
        registry,
        key="idempotent-new",
        subject="Automatyczne przetwarzanie faktur",
        body="Nowy projekt OCR faktur.",
    )
    count_before = registry.connection.execute("SELECT COUNT(*) AS n FROM unified_deals").fetchone()["n"]
    second = register_mail(
        registry,
        key="idempotent-new",
        subject="Automatyczne przetwarzanie faktur",
        body="Nowy projekt OCR faktur.",
    )
    count_after = registry.connection.execute("SELECT COUNT(*) AS n FROM unified_deals").fetchone()["n"]
    assert second["duplicate"] is True
    assert second["deal_id"] == first["deal_id"]
    assert count_after == count_before


def test_18_low_confidence_llm_routing_does_not_change_existing_facts(registry):
    crm, telegram = seed_crm_and_telegram(registry)
    before = {
        crm["deal_id"]: registry.get_deal(crm["deal_id"]),
        telegram["deal_id"]: registry.get_deal(telegram["deal_id"]),
    }
    registry.routing_llm = lambda _payload: {
        "decision": "existing", "deal_id": crm["deal_id"],
        "confidence": 0.60, "reason": "niepewne",
    }
    event = register_mail(
        registry,
        key="low-confidence-isolated",
        subject="Wiadomość",
        body="Proszę o informacje.",
        facts={"untrusted_guess": "must-not-leak"},
    )
    assert_action(event, "review")
    assert event["reason_code"] == "llm_low_confidence"
    for deal_id, snapshot in before.items():
        current = registry.get_deal(deal_id)
        assert current["facts"] == snapshot["facts"]
        assert current["current_draft_id"] == snapshot["current_draft_id"]
        assert current["scope_display"] == snapshot["scope_display"]
    assert registry.get_deal(event["deal_id"])["facts"] == {"untrusted_guess": "must-not-leak"}


def test_routing_decision_serializes_all_audit_fields(registry):
    seed_crm_and_telegram(registry)
    event = register_mail(
        registry,
        key="audit-shape",
        subject="Pytanie",
        body="Czy bot Telegram może wysyłać powiadomienia na grupę?",
    )
    decision = event["routing_decision"]
    assert set(decision) >= {
        "action",
        "deal_id",
        "confidence",
        "margin",
        "evidence",
        "candidate_scores",
        "reason",
    }
    audit = registry.correlation_audit("zoho_mail", "audit-shape")
    assert audit["selected_deal_id"] == event["deal_id"]
    assert audit["candidate_scores"] == decision["candidate_scores"]
    assert audit["candidate_deal_ids"] == list(decision["candidate_scores"])




def test_generic_exact_subject_is_not_a_hard_deal_anchor(registry):
    generic = register_mail(
        registry,
        key="generic-subject-deal",
        subject="Pytanie",
        body="Ogólna prośba o informacje.",
    )
    crm = register_mail(
        registry,
        key="generic-subject-crm",
        subject="Automatyzacja CRM",
        body="Nowy projekt HubSpot i pipeline sprzedażowy.",
    )
    event = register_mail(
        registry,
        key="generic-subject-inbound",
        subject="Pytanie",
        body="Dzień dobry, proszę o informacje.",
    )
    assert_action(event, "review")
    assert event["deal_id"] not in {generic["deal_id"], crm["deal_id"]}
    assert "hard:exact_normalized_subject" not in event["routing_decision"]["evidence"]


def test_new_mail_scope_is_not_linked_to_sheet_deal_by_email_alone(registry):
    sheet = registry.register_event(
        source_type="google_sheets",
        source_key="sheet:new-scope:1",
        email="identity-093@customer-061.example.com",
        company="Firma",
        contact_name="Jan Kowalski",
        content="Automatyzacja CRM, HubSpot i pipeline sprzedażowy.",
        relation="new",
        facts={"service": "CRM"},
        source_metadata={"subject": "Automatyzacja CRM"},
    )
    event = registry.register_event(
        source_type="mail",
        source_key="mail:new-scope:1",
        email="identity-093@customer-061.example.com",
        company="Firma",
        contact_name="Jan Kowalski",
        content="Potrzebujemy OCR faktur i eksportu do księgowości.",
        relation="new",
        facts={"service": "OCR faktur"},
        source_metadata={"subject": "Automatyczne przetwarzanie faktur"},
    )
    assert_action(event, "create_new")
    assert event["deal_id"] != sheet["deal_id"]
    assert event["is_cross_source_duplicate"] is False








def test_multiple_explicit_identifiers_conflict_even_with_matching_thread(registry):
    crm, telegram = seed_crm_and_telegram(registry)
    event = register_mail(
        registry,
        key="thread-plus-two-identifiers",
        subject="Re: Automatyzacja CRM",
        body=f"Porównuję {crm['deal_id']} oraz {telegram['deal_id']}.",
        thread_id="thread-crm",
    )
    assert_action(event, "review")
    assert event["reason_code"] == "conflicting_hard_signals"


def test_explicit_new_matter_overrides_exact_subject_without_reply_headers(registry):
    crm, telegram = seed_crm_and_telegram(registry)
    event = register_mail(
        registry,
        key="same-subject-explicit-new",
        subject="Automatyzacja CRM",
        body="To nowa sprawa i osobny projekt automatyzacji CRM dla innego procesu.",
    )
    assert_action(event, "create_new")
    assert event["deal_id"] not in {crm["deal_id"], telegram["deal_id"]}


def test_cross_source_same_boilerplate_with_new_informative_subject_is_not_duplicate(registry):
    original = registry.register_event(
        source_type="google_sheets",
        source_key="sheet:boilerplate:1",
        email="identity-093@customer-061.example.com",
        company="Firma",
        contact_name="Jan Kowalski",
        content="Proszę o kontakt.",
        relation="new",
        source_metadata={"subject": "Automatyzacja CRM"},
    )
    event = registry.register_event(
        source_type="mail",
        source_key="mail:boilerplate:1",
        email="identity-093@customer-061.example.com",
        company="Firma",
        contact_name="Jan Kowalski",
        content="Proszę o kontakt.",
        relation="new",
        source_metadata={"subject": "Automatyczne przetwarzanie faktur"},
    )
    assert event["is_cross_source_duplicate"] is False
    assert event["deal_id"] != original["deal_id"]




def test_duplicate_retry_preserves_material_scope_change_guard(registry):
    crm, _ = seed_crm_and_telegram(registry)
    baseline = register_mail(
        registry,
        key="material-change-baseline",
        subject="Re: Automatyzacja CRM",
        body="Zakres obejmuje jedną skrzynkę.",
        thread_id="thread-crm",
        facts={"mailbox_count": 1},
    )
    assert baseline["deal_id"] == crm["deal_id"]
    first = register_mail(
        registry,
        key="material-change-retry",
        subject="Re: Automatyzacja CRM",
        body="Zmieniamy zakres z jednej skrzynki na dwie.",
        thread_id="thread-crm",
        facts={"mailbox_count": 2},
    )
    assert first["deal_id"] == crm["deal_id"]
    assert first["material_scope_change"] is True
    duplicate = register_mail(
        registry,
        key="material-change-retry",
        subject="Re: Automatyzacja CRM",
        body="Zmieniamy zakres z jednej skrzynki na dwie.",
        thread_id="thread-crm",
        facts={"mailbox_count": 2},
    )
    assert duplicate["duplicate"] is True
    assert duplicate["material_scope_change"] is True
    assert duplicate["material_scope_changes"] == first["material_scope_changes"]
    assert duplicate["fact_conflicts"] == first["fact_conflicts"]


def test_cross_source_one_word_topics_do_not_deduplicate_by_body_alone(registry):
    original = registry.register_event(
        source_type="google_sheets",
        source_key="sheet:one-word:1",
        email="identity-093@customer-061.example.com",
        company="Firma",
        contact_name="Jan Kowalski",
        content="Proszę o kontakt.",
        relation="new",
        source_metadata={"subject": "CRM"},
    )
    event = registry.register_event(
        source_type="mail",
        source_key="mail:one-word:1",
        email="identity-093@customer-061.example.com",
        company="Firma",
        contact_name="Jan Kowalski",
        content="Proszę o kontakt.",
        relation="new",
        source_metadata={"subject": "Faktury"},
    )
    assert event["is_cross_source_duplicate"] is False
    assert event["deal_id"] != original["deal_id"]


def test_sheet_lead_same_email_different_topic_creates_new_deal(registry):
    """A sheet lead (no subject) from an email that already has a deal, but with a
    clearly different topic and informative content, must create a NEW deal, not
    be flagged as an ambiguous duplicate. Regression for
    the "Marek vs Piotr" case where a construction RFQ from the same gmail as a
    logistics deal was wrongly routed to manual review because sheet leads have no subject
    so is_informative_subject(subject) was False."""
    piotr = register_sheet(
        registry,
        key="sheet:piotr:1",
        email="identity-093@customer-061.example.com",
        company="Nowak-Logistics Sp. z o.o.",
        contact_name="Piotr",
        message="Prowadzę firmę logistyczną i szukamy systemu do obsługi zapytań od klientów. Obecnie zapytania obsługujemy mailem. Proszę o kontakt i ofertę.",
    )
    marek = register_sheet(
        registry,
        key="sheet:marek:1",
        email="identity-093@customer-061.example.com",
        company="Wiśniewski Budownictwo Sp. z o.o.",
        contact_name="Marek",
        message=(
            "Dzień dobry, prowadzę firmę budowlaną (Wiśniewski Budownictwo). Otrzymujemy sporo zapytań "
            "od inwestorów i generalnych wykonawców — obecnie wszystko ląduje na skrzynkach mailowych "
            "i w Excelu, przez co gubimy terminy i część zapytań pozostaje bez odpowiedzi. Szukamy "
            "systemu, który zcentralizuje zapytania. Proszę o informację czy Orchesta RFQ nadaje "
            "się do firmy budowlanej i jak wygląda wdrożenie. Pozdrawiam, Marek Wiśniewski."
        ),
    )
    assert_action(marek, "create_new")
    assert marek["deal_id"] != piotr["deal_id"]
    assert marek["requires_review"] is False
    assert marek["status"] != "review_required"
    audit = registry.correlation_audit("google_sheets", "sheet:marek:1")
    assert audit["action"] == "create_new"
    assert audit["reason_code"] == "informative_new_topic_low_similarity"
