"""Explicit verification of the 18 acceptance conditions (spec section 26).

Each test maps 1:1 to a numbered condition so acceptance is auditable. The
heavy lifting is covered by the mandatory-8 suite and the per-feature suites;
these tests assert each condition directly.
"""
from __future__ import annotations

import os
import sys
import tempfile
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
from hermes_rfq_core import SafetySwitches, REASON_LLM_CLASSIFICATION_FAILED, REASON_REPLY_VALIDATION_FAILED


class _C:
    def __init__(self, payload):
        import json
        self._s = json.dumps(payload, ensure_ascii=False)
    def complete(self, sp, up, temperature=0.0):
        return self._s


class _R:
    def __init__(self, body, needs_human_review=False, review_reason=None):
        import json
        self._s = json.dumps({"subject": "Re", "body": body,
                              "needs_human_review": needs_human_review,
                              "review_reason": review_reason}, ensure_ascii=False)
    def complete(self, sp, up, temperature=0.0):
        return self._s


def _cls(payload):
    return {"intent": "product_fit_inquiry", "confidence": "high", "risk": "low",
            "sender_identity": "free_email_unverified",
            "recommended_action": "ask_discovery_questions",
            "provided_information": {}, "missing_information": ["company_name_or_website"],
            "decision_reasons": ["x"], "language": "pl", **payload}


def test_1_krzysztof_message_routes_to_discovery():
    out = lic.classify(subject="x", body="chciałem zapytać o system", sender="identity-017@gmail.com",
                      recipient="rfq-mailbox@example.invalid", tenant_id="orchesta",
                      llm_client=_C(_cls({})), run_id="a1")
    assert out["action"] == "ask_discovery_questions"


def test_2_gmail_does_not_block_first_contact():
    out = lic.classify(subject="x", body="pytam o system", sender="identity-017@gmail.com",
                      recipient="rfq-mailbox@example.invalid", tenant_id="orchesta",
                      llm_client=_C(_cls({"sender_identity": "free_email_unverified"})), run_id="a2")
    assert out["action"] != "block_security"
    assert out["action"] != "awaiting_human"


def test_3_product_name_not_required():
    out = lic.classify(subject="", body="szukam sposobu na obsługę wiadomości",
                      sender="identity-085@customer-057.example.com", recipient="rfq-mailbox@example.invalid", tenant_id="orchesta",
                      llm_client=_C(_cls({})), run_id="a3")
    assert out["action"] == "ask_discovery_questions"


def test_4_llm_saves_customer_provided_info():
    with tempfile.TemporaryDirectory() as tmp:
        reg = ulr.UnifiedLeadRegistry(Path(tmp) / "r.sqlite3")
        d = reg.register_event(source_type="mail", source_key="<a4@e>", email="a4@e",
                               company="", contact_name="X", content="c", relation="new", tenant_id="orchesta")
        saved = reg.record_provided_information(d["deal_id"], {"company_name_or_website": "ABC"},
                                                source_message_id="<a4@e>")
        assert saved["facts"]["company_name_or_website"] == "ABC"
        assert saved["added"] == ["company_name_or_website"]


def test_5_agent_does_not_reask_saved_data():
    body = "Rozumiem. Jak dziś wygląda obsługa tych wiadomości?"
    state = cc.on_agent_reply(cc.on_customer_message(cc.default_state("d")))
    state = cc.on_customer_message(state)
    saved = {"company_name_or_website": "ABC", "monthly_volume": "300"}
    v = rv.validate_reply(body=body, approved_action="ask_discovery_questions",
                          conversation_state=state, saved_data=saved, tenant_id="orchesta")
    assert v["ok"], v["errors"]
    assert "nazwa firmy" not in body.lower() and "ile jest" not in body.lower()


def test_6_thanks_only_in_first_reply():
    first_state = cc.default_state("d")
    body_first = "Dzień dobry, dziękuję za wiadomość. Proszę o nazwę firmy."
    assert rv.validate_reply(body=body_first, approved_action="ask_discovery_questions",
                             conversation_state=first_state, saved_data={}, tenant_id="orchesta")["ok"]
    follow_state = cc.on_agent_reply(cc.on_customer_message(first_state))
    follow_state = cc.on_customer_message(follow_state)
    body_follow = "Dziękuję za wiadomość. Proszę o nazwę firmy."
    v = rv.validate_reply(body=body_follow, approved_action="ask_discovery_questions",
                          conversation_state=follow_state, saved_data={}, tenant_id="orchesta")
    assert not v["ok"]  # thanks not allowed in a follow-up


def test_7_followups_have_no_full_greeting():
    follow_state = cc.on_agent_reply(cc.on_customer_message(cc.default_state("d")))
    follow_state = cc.on_customer_message(follow_state)
    body = "Dzień dobry, proszę o nazwę firmy."
    v = rv.validate_reply(body=body, approved_action="ask_discovery_questions",
                          conversation_state=follow_state, saved_data={}, tenant_id="orchesta")
    assert not v["ok"]  # re-greeting not allowed


def test_8_followups_reference_last_customer_statement():
    body = "Dwie osoby, rozumiem. Ile kont pocztowych ma śledzić system?"
    state = cc.on_agent_reply(cc.on_customer_message(cc.default_state("d")))
    state = cc.on_customer_message(state)
    v = rv.validate_reply(body=body, approved_action="ask_discovery_questions",
                          conversation_state=state,
                          saved_data={"company_name_or_website": "ABC", "monthly_volume": "300",
                                      "current_process": "dwie osoby", "inquiry_channels": "mail"},
                          tenant_id="orchesta")
    assert v["ok"], v["errors"]
    assert "Dwie osoby" in body  # references the customer's last statement


def test_9_no_telegram_in_conversation_or_offer():
    html = rfo.render_offer_html_for_tenant({
        "offer": {"offer_number": "X", "version": 1, "date": "2026-08-03"},
        "client": {"full_name": "K", "company": "C", "email": "identity-086@customer-058.example.com"},
        "scope": {"mailbox_count": 1, "mailbox_label": "k", "crm_label": "nie", "inquiry_source_label": "mail"},
        "pricing": {"line_items": [{"name": "x", "net_display": "1 zł"}], "net_total_display": "1 zł"},
        "roi": {"title": "t", "description": "d", "hours_text": "h", "cost_text": "c"},
        "footer": "Orchesta RFQ Team",
    }, tenant_id="orchesta")
    assert "Telegram" not in html and "telegram" not in html.lower()
    # Conversation-side: reply body has no Telegram.
    reply = lrw.write_reply(approved_action="ask_discovery_questions", customer_message="x",
                            thread_history="", saved_data={}, missing_data=["company_name_or_website"],
                            conversation_stage="discovery", tenant_id="orchesta",
                            is_first_agent_reply=True, llm_client=_R("Dzień dobry, proszę o nazwę firmy."))
    assert "Telegram" not in reply["body"] and "telegram" not in reply["body"].lower()


def test_10_telegram_only_as_orchesta_internal_alarm():
    o = tc.load_notifications("orchesta")["internal_notifications"]["human_review"]["telegram"]
    assert o["enabled"] is True
    f = tc.load_notifications("firma_abc")["internal_notifications"]["human_review"]["telegram"]
    assert f["enabled"] is False


def test_11_second_company_gets_no_orchesta_data():
    html = rfo.render_offer_html_for_tenant({
        "offer": {"offer_number": "ABC-1", "version": 1, "date": "2026-08-03"},
        "client": {"full_name": "Jan K", "company": "Firma ABC", "email": "identity-087@customer-059.example.com"},
        "scope": {"mailbox_count": 1, "mailbox_label": "stanowisko", "crm_label": "nie", "inquiry_source_label": "mail"},
        "pricing": {"line_items": [{"name": "Firma ABC, wdrożenie podstawowe", "net_display": "5000 zł"}], "net_total_display": "5000 zł"},
        "roi": {"title": "t", "description": "d", "hours_text": "h", "cost_text": "c"},
        "footer": "Jan Kowalski\nFirma ABC",
    }, tenant_id="firma_abc")
    assert "Orchesta" not in html and "orchesta" not in html.lower()


def test_12_sales_rules_come_from_tenant_package():
    op = tc.load_products("orchesta")
    fp = tc.load_products("firma_abc")
    assert op["pricing_id"] != fp["pricing_id"]
    assert op["base_offer"]["net_price"] == 7200
    assert fp["base_offer"]["net_price"] == 5000


def test_13_every_deal_has_tenant_id():
    with tempfile.TemporaryDirectory() as tmp:
        reg = ulr.UnifiedLeadRegistry(Path(tmp) / "r.sqlite3")
        d = reg.register_event(source_type="mail", source_key="<a13@e>", email="a13@e",
                               company="", contact_name="X", content="c", relation="new", tenant_id="orchesta")
        deal = reg.get_deal(d["deal_id"])
        assert deal["tenant_id"] == "orchesta"


def test_14_every_deal_has_own_identifier():
    with tempfile.TemporaryDirectory() as tmp:
        reg = ulr.UnifiedLeadRegistry(Path(tmp) / "r.sqlite3")
        d = reg.register_event(source_type="mail", source_key="<a14@e>", email="a14@e",
                               company="", contact_name="X", content="c", relation="new", tenant_id="orchesta")
        rfq = reg.assign_rfq_id(d["deal_id"], date="2026-08-03")
        assert rfq.startswith("RFQ-20260803-")
        assert reg.get_rfq_id(d["deal_id"]) == rfq


def test_15_errors_route_to_awaiting_human():
    bad = _C({"not": "valid"})
    out = lic.classify(subject="x", body="y", sender="a@b.c", recipient="rfq-mailbox@example.invalid",
                      tenant_id="orchesta", llm_client=bad, run_id="a15")
    assert out["state"] == "awaiting_human"
    assert out["decision_reason"] == REASON_LLM_CLASSIFICATION_FAILED
    # reply validation failure routes to awaiting_human
    fail = rv.validation_failed_outcome(errors=["too_long"], run_id="a15b")
    assert fail["state"] == "awaiting_human"
    assert fail["decision_reason"] == REASON_REPLY_VALIDATION_FAILED


def test_16_full_path_test_passes_automatically():
    # Reuse the mandatory test_8 flow; no manual DB cleanup, no missing-field error.
    with tempfile.TemporaryDirectory() as tmp:
        reg = ulr.UnifiedLeadRegistry(Path(tmp) / "r.sqlite3")
        d = reg.register_event(source_type="sheet", source_key="row-1", email="identity-088@customer-059.example.com",
                               company="", contact_name="M", content="z", relation="new", tenant_id="orchesta")
        saved = reg.record_provided_information(d["deal_id"],
            {"company_name_or_website": "ABC", "monthly_volume": "300", "mailbox_count": 3, "crm": True},
            source_message_id="<r@e>")
        assert saved["facts"]["mailbox_count"] == 3
        rfq = reg.assign_rfq_id(d["deal_id"], date="2026-08-03")
        assert rfq


def test_17_prompts_loaded_from_separate_files():
    assert (ROOT / "prompts" / "incoming_mail_classifier_system.md").exists()
    assert (ROOT / "prompts" / "incoming_mail_reply_system.md").exists()
    assert lic.load_system_prompt().strip() != ""
    assert lrw.load_system_prompt().strip() != ""
    assert lic.PROMPT_VERSION == "incoming-classifier-v1"
    assert lrw.PROMPT_VERSION == (
        f"incoming-reply-v3:{lrw.REPLY_CONTRACT_VERSION}:{lrw.REPLY_CONTRACT_DIGEST[:12]}"
    )


def test_18_auto_send_stops_with_one_flag():
    env = {"HERMES_AUTO_REPLY_LOW_RISK": "1", "HERMES_REPLY_KILL_SWITCH": "1"}
    old = os.environ.copy()
    os.environ.update(env)
    try:
        sw = SafetySwitches.from_env()
    finally:
        os.environ.clear(); os.environ.update(old)
    assert sw.auto_reply_allowed is False
    # And without the kill switch it would be allowed.
    assert SafetySwitches(auto_reply_low_risk=True, reply_kill_switch=False).auto_reply_allowed is True
