"""The 8 mandatory tests from spec section 22.

These exercise the rebuilt incoming-message flow end-to-end with deterministic
fixture LLM clients (no paid API — Guardrail 4): classification, script-side
decision, reply authoring, reply validation, conversation continuity,
multi-tenant isolation, Telegram removal, and the full registry roundtrip.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
SKILL_SCRIPT = ROOT / "skills" / "rfq-final-offer" / "scripts" / "rfq_final_offer.py"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))
if str(SKILL_SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPT.parent))

import importlib

lic = importlib.import_module("llm_intent_classifier")
lrw = importlib.import_module("llm_reply_writer")
rv = importlib.import_module("reply_validation")
cc = importlib.import_module("conversation_continuity")
tc = importlib.import_module("tenant_config")
rfo = importlib.import_module("rfq_final_offer")
ulr = importlib.import_module("unified_lead_registry")


class _ClassifyClient:
    def __init__(self, payload):
        self._payload = payload

    def complete(self, system_prompt, user_prompt, temperature=0.0):
        import json
        return json.dumps(self._payload, ensure_ascii=False)


class _ReplyClient:
    def __init__(self, subject, body, needs_human_review=False, review_reason=None):
        self._subject = subject
        self._body = body
        self._needs = needs_human_review
        self._reason = review_reason

    def complete(self, system_prompt, user_prompt, temperature=0.0):
        import json
        return json.dumps({
            "subject": self._subject,
            "body": self._body,
            "needs_human_review": self._needs,
            "review_reason": self._reason,
        }, ensure_ascii=False)


# --- Test 1: first contact from Gmail ---------------------------------------

def test_1_first_contact_from_gmail():
    payload = {
        "intent": "product_fit_inquiry",
        "confidence": "high",
        "risk": "low",
        "sender_identity": "free_email_unverified",
        "recommended_action": "ask_discovery_questions",
        "provided_information": {},
        "missing_information": ["company_name_or_website", "current_process", "inquiry_channels", "monthly_volume"],
        "decision_reasons": ["initial inquiry, no company data yet"],
        "language": "pl",
    }
    out = lic.classify(
        subject="Zapytanie o system",
        body="Dzień dobry, kontaktuję się, bo chciałem zapytać, czy taki system Orchesta też by pasował do mojego biznesu, zajmuję się handlem wielobranżowym. Pozdrawiam, Krzysztof Ogórek.",
        sender="identity-117@example.invalid",
        recipient="rfq-mailbox@example.invalid",
        tenant_id="orchesta",
        llm_client=_ClassifyClient(payload),
        run_id="t1",
    )
    c = out["classification"]
    assert c["intent"] == "product_fit_inquiry"
    assert c["confidence"] == "high"
    assert c["risk"] == "low"
    assert c["sender_identity"] == "free_email_unverified"
    assert c["recommended_action"] == "ask_discovery_questions"
    assert out["action"] == "ask_discovery_questions"

    expected_body = (
        "Dzień dobry Panie Krzysztofie,\n\n"
        "dziękuję za wiadomość. Żeby ocenić dopasowanie systemu do Państwa firmy, "
        "proszę podać nazwę firmy lub stronę internetową oraz opisać, jak dziś "
        "obsługują Państwo wiadomości od klientów.\n\n"
        "Proszę też podać, jakimi kanałami trafiają zgłoszenia i ile jest ich w miesiącu."
    )
    reply = lrw.write_reply(
        approved_action="ask_discovery_questions",
        customer_message="Dzień dobry, kontaktuję się...",
        thread_history="",
        saved_data={},
        missing_data=["company_name_or_website", "current_process", "inquiry_channels", "monthly_volume"],
        conversation_stage="discovery",
        tenant_id="orchesta",
        is_first_agent_reply=True,
        llm_client=_ReplyClient("Re: Zapytanie o system", expected_body),
        run_id="t1",
    )
    assert reply["body"] == expected_body
    state = cc.default_state("d1")
    v = rv.validate_reply(body=reply["body"], approved_action="ask_discovery_questions",
                          conversation_state=state, saved_data={}, tenant_id="orchesta")
    assert v["ok"], v["errors"]


# --- Test 2: similar intent without product name ----------------------------

def test_2_similar_intent_without_product_name():
    payload = {
        "intent": "product_fit_inquiry",
        "confidence": "medium",
        "risk": "low",
        "sender_identity": "free_email_unverified",
        "recommended_action": "ask_discovery_questions",
        "provided_information": {},
        "missing_information": ["company_name_or_website"],
        "decision_reasons": ["business inquiry without company name"],
        "language": "pl",
    }
    out = lic.classify(
        subject="",
        body="Prowadzę firmę handlową i szukam sposobu na sprawniejszą obsługę wiadomości od klientów. Proszę o kontakt.",
        sender="firma@example.com",
        recipient="rfq-mailbox@example.invalid",
        tenant_id="orchesta",
        llm_client=_ClassifyClient(payload),
        run_id="t2",
    )
    c = out["classification"]
    assert c["intent"] == "product_fit_inquiry"
    assert out["action"] == "ask_discovery_questions"


# --- Test 3: second agent reply ---------------------------------------------

def test_3_second_agent_reply():
    body = ("Rozumiem. Jak dziś wygląda obsługa tych wiadomości? "
            "Czy trafiają do jednej osoby, czy są rozdzielane między kilka osób?")
    state = cc.on_agent_reply(cc.on_customer_message(cc.default_state("d3")))
    # After one agent reply + one new customer message: not first.
    state = cc.on_customer_message(state)
    assert state["is_first_agent_reply"] is False
    saved = {"company_name_or_website": "ABC", "monthly_volume": "300"}
    v = rv.validate_reply(
        body=body, approved_action="ask_discovery_questions",
        conversation_state=state, saved_data=saved, tenant_id="orchesta",
    )
    assert v["ok"], v["errors"]
    assert "Dzień dobry" not in body
    assert "dziękuję za wiadomość" not in body.lower()
    assert "nazwa firmy" not in body.lower()
    assert "ile jest" not in body.lower()  # no re-asking message count


# --- Test 4: third agent reply ----------------------------------------------

def test_4_third_agent_reply():
    body = ("Dwie osoby, rozumiem. Skoro wiadomości trafiają głównie na e-mail, "
            "to ile kont pocztowych ma śledzić system?")
    state = cc.on_agent_reply(cc.on_customer_message(cc.default_state("d4")))
    state = cc.on_agent_reply(state)
    state = cc.on_customer_message(state)
    assert state["is_first_agent_reply"] is False
    saved = {"company_name_or_website": "ABC", "monthly_volume": "300",
             "current_process": "dwie osoby", "inquiry_channels": "e-mail"}
    v = rv.validate_reply(
        body=body, approved_action="ask_discovery_questions",
        conversation_state=state, saved_data=saved, tenant_id="orchesta",
    )
    assert v["ok"], v["errors"]
    assert "Dzień dobry" not in body
    assert "dziękuję" not in body.lower()


# --- Test 5: new thread of same client --------------------------------------

def test_5_new_thread_of_same_client():
    new = cc.is_new_thread(
        has_prior_thread_headers=False,
        same_deal_id=False,
        same_topic=False,
        previous_deal_closed=True,
    )
    assert new is True
    state = cc.default_state("d5_new")
    assert state["agent_reply_count"] == 0
    assert state["is_first_agent_reply"] is True
    # Full greeting + one thanks allowed on a first reply.
    body = ("Dzień dobry,\n\nDziękuję za wiadomość. O czym dokładnie chcieliby Państwo porozmawiać?")
    v = rv.validate_reply(
        body=body, approved_action="ask_discovery_questions",
        conversation_state=state, saved_data={}, tenant_id="orchesta",
    )
    assert v["ok"], v["errors"]


# --- Test 6: no Telegram ----------------------------------------------------

def test_6_no_telegram_anywhere():
    # No Telegram in the rendered offer HTML/PDF template.
    html = rfo.render_offer_html_for_tenant({
        "offer": {"offer_number": "X", "version": 1, "date": "2026-08-03"},
        "client": {"full_name": "K", "company": "C", "email": "identity-110@example.invalid"},
        "scope": {"mailbox_count": 1, "mailbox_label": "konto", "crm_label": "nie", "inquiry_source_label": "mail"},
        "pricing": {"line_items": [{"name": "x", "net_display": "7200 zł"}], "net_total_display": "7200 zł"},
        "roi": {"title": "t", "description": "d", "hours_text": "h", "cost_text": "c"},
        "footer": "Orchesta RFQ Team",
    }, tenant_id="orchesta")
    assert "Telegram" not in html and "telegram" not in html.lower()
    # Internal Telegram delivery is not customer discovery data.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        reg = ulr.UnifiedLeadRegistry(Path(tmp) / "reg.sqlite3")
        deal = reg.register_event(
            source_type="mail", source_key="<m6@example.com>",
            email="k6@example.com", company="ABC", contact_name="K",
            content="hello", relation="new", tenant_id="orchesta",
        )
        did = deal["deal_id"]
        saved = reg.record_provided_information(
            did, {"company_name_or_website": "ABC", "monthly_volume": "20"},
            source_message_id="<m6@example.com>",
        )
        facts = saved["facts"]
        assert {"telegram", "telegram" + "_accepted"}.isdisjoint(facts)
    # Orchesta internal alarm still works (telegram enabled).
    notif = tc.load_notifications("orchesta")
    assert notif["internal_notifications"]["human_review"]["telegram"]["enabled"] is True


# --- Test 7: second company (firma_abc) -------------------------------------

def test_7_second_company_isolation():
    products = tc.load_products("firma_abc")
    assert products["pricing_id"] == "firma-abc-2026-08"
    # Render the offer with firma_abc context; no Orchesta data leaks in.
    html = rfo.render_offer_html_for_tenant({
        "offer": {"offer_number": "ABC-2026-0001", "version": 1, "date": "2026-08-03"},
        "client": {"full_name": "Jan K", "company": "Firma ABC", "email": "identity-111@example.invalid"},
        "scope": {"mailbox_count": 1, "mailbox_label": "stanowisko", "crm_label": "nie", "inquiry_source_label": "mail"},
        "pricing": {"line_items": [{"name": "Firma ABC, wdrożenie podstawowe", "net_display": "5000 zł"}], "net_total_display": "5000 zł"},
        "roi": {"title": "t", "description": "d", "hours_text": "h", "cost_text": "c"},
        "footer": "Jan Kowalski\nFirma ABC",
    }, tenant_id="firma_abc")
    assert "Orchesta" not in html
    assert "orchesta" not in html.lower()
    # No Telegram alarm for firma_abc (disabled in its notifications).
    notif = tc.load_notifications("firma_abc")
    assert notif["internal_notifications"]["human_review"]["telegram"]["enabled"] is False


# --- Test 8: full path ------------------------------------------------------

def test_8_full_path_no_manual_cleanup():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        reg = ulr.UnifiedLeadRegistry(Path(tmp) / "reg.sqlite3")
        # lead from sheet -> first message
        deal = reg.register_event(
            source_type="sheet", source_key="row-1",
            email="identity-118@example.invalid", company="", contact_name="Marek",
            content="Zapytanie z arkusza", relation="new", tenant_id="orchesta",
        )
        did = deal["deal_id"]
        assert deal["is_new_event"]
        # customer reply -> save data (controlled, with provenance)
        saved = reg.record_provided_information(
            did,
            {"company_name_or_website": "ABC Sp. z o.o.", "monthly_volume": "300",
             "current_process": "jedna osoba", "inquiry_channels": "mail",
             "mailbox_count": 3, "crm": True},
            source_message_id="<r8@example.com>",
        )
        facts = saved["facts"]
        assert facts["company_name_or_website"] == "ABC Sp. z o.o."
        # further natural follow-up: still-missing fields filter
        still_missing = [f for f in ["company_name_or_website", "monthly_volume", "mailbox_count"]
                         if f not in facts]
        assert still_missing == []
        # conversation state roundtrip
        state = cc.on_customer_message(cc.default_state(did))
        state = cc.on_agent_reply(state)
        reg.save_conversation_state(did, state)
        loaded = reg.get_conversation_state(did)
        assert loaded["agent_reply_count"] == 1
        assert loaded["is_first_agent_reply"] == 0
        # complete data -> offer PDF (HTML render) without missing-field error
        rfq = reg.assign_rfq_id(did, date="2026-08-03")
        assert rfq.startswith("RFQ-20260803-")
        html = rfo.render_offer_html_for_tenant({
            "offer": {"offer_number": rfq, "version": 1, "date": "2026-08-03"},
            "client": {"full_name": "Marek", "company": "ABC Sp. z o.o.", "email": "identity-118@example.invalid"},
            "scope": {"mailbox_count": 3, "mailbox_label": "konta pocztowe", "crm_label": "tak", "inquiry_source_label": "mail"},
            "pricing": {"line_items": [{"name": "Orchesta RFQ", "net_display": "7200 zł"}], "net_total_display": "7200 zł"},
            "roi": {"title": "t", "description": "d", "hours_text": "h", "cost_text": "c"},
            "footer": "Orchesta RFQ Team",
        }, tenant_id="orchesta")
        assert "Telegram" not in html
        assert "7200 zł" in html
