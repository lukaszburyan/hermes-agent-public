from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

import reply_validation as rv


FIRST_STATE = {"is_first_agent_reply": True, "acknowledgement_already_sent": False}
FOLLOW_STATE = {"is_first_agent_reply": False, "acknowledgement_already_sent": True}


GOOD_FIRST = (
    "Dzień dobry Panie Krzysztofie,\n\n"
    "dziękuję za wiadomość. Żeby ocenić dopasowanie systemu do Państwa firmy, "
    "proszę podać nazwę firmy lub stronę internetową oraz opisać, jak dziś "
    "obsługują Państwo wiadomości od klientów."
)


def test_good_first_reply_passes():
    res = rv.validate_reply(
        body=GOOD_FIRST, approved_action="ask_discovery_questions",
        conversation_state=FIRST_STATE, saved_data={}, tenant_id="orchesta",
    )
    assert res["ok"] is True
    assert res["errors"] == []


def test_too_long_reply_fails():
    body = "Dzień dobry Panie Krzysztofie,\n\n" + ("słowo " * 200)
    res = rv.validate_reply(
        body=body, approved_action="reply_directly",
        conversation_state=FOLLOW_STATE, saved_data={}, tenant_id="orchesta",
    )
    assert "too_long" in res["errors"]


def test_too_many_questions_fails():
    res = rv.validate_reply(
        body="Dzień dobry Panie,\n\na? b? c? d?", approved_action="ask_discovery_questions",
        conversation_state=FIRST_STATE, saved_data={}, tenant_id="orchesta",
    )
    assert "too_many_questions" in res["errors"]


def test_emoji_fails():
    res = rv.validate_reply(
        body="Dzień dobry Panie 🎉", approved_action="reply_directly",
        conversation_state=FIRST_STATE, saved_data={}, tenant_id="orchesta",
    )
    assert "emojis_present" in res["errors"]


def test_dashes_fail():
    for body, code in [
        ("-- podpis", "double_hyphen_present"),
        ("Dzień dobry – x", "en_dash_present"),
        ("Dzień dobry — x", "em_dash_present"),
    ]:
        res = rv.validate_reply(
            body=body, approved_action="reply_directly",
            conversation_state=FIRST_STATE, saved_data={}, tenant_id="orchesta",
        )
        assert code in res["errors"]


def test_first_reply_without_greeting_fails():
    res = rv.validate_reply(
        body="dziękuję za wiadomość. proszę o dane.",
        approved_action="ask_discovery_questions",
        conversation_state=FIRST_STATE, saved_data={}, tenant_id="orchesta",
    )
    assert "first_reply_missing_greeting" in res["errors"]


def test_followup_that_re_greets_fails():
    res = rv.validate_reply(
        body="Dzień dobry Panie Krzysztofie,\n\nRozumiem, jak dziś wygląda obsługa?",
        approved_action="ask_discovery_questions",
        conversation_state=FOLLOW_STATE, saved_data={}, tenant_id="orchesta",
    )
    assert "followup_re_greets" in res["errors"]


def test_second_thank_you_after_acknowledgement_fails():
    res = rv.validate_reply(
        body="dziękuję za wiadomość. Rozumiem.",
        approved_action="reply_directly",
        conversation_state=FOLLOW_STATE, saved_data={}, tenant_id="orchesta",
    )
    assert "second_thank_you" in res["errors"]


def test_re_asking_saved_field_fails():
    saved = {"company_name_or_website": "ABC"}
    res = rv.validate_reply(
        body="Rozumiem. Na jaką firmę mam przygotować ofertę? Jak dziś wygląda obsługa?",
        approved_action="ask_discovery_questions",
        conversation_state=FOLLOW_STATE, saved_data=saved, tenant_id="orchesta",
    )
    assert any(e.startswith("re_asks_saved_field") for e in res["errors"])


def test_telegram_mention_fails():
    res = rv.validate_reply(
        body="Rozumiem. Telegram to kanał powiadomień.",
        approved_action="reply_directly",
        conversation_state=FOLLOW_STATE, saved_data={}, tenant_id="orchesta",
    )
    assert any(e.startswith("forbidden_term") for e in res["errors"])


def test_other_tenant_data_fails():
    res = rv.validate_reply(
        body="Rozumiem. Orchesta to nasza firma.",
        approved_action="reply_directly",
        conversation_state=FOLLOW_STATE, saved_data={}, tenant_id="firma_abc",
    )
    assert any(e.startswith("other_tenant_data") for e in res["errors"])


def test_non_sendable_action_fails():
    res = rv.validate_reply(
        body="Rozumiem.", approved_action="block_security",
        conversation_state=FOLLOW_STATE, saved_data={}, tenant_id="orchesta",
    )
    assert any(e.startswith("action_not_sendable") for e in res["errors"])


def test_validation_failed_outcome_routes_to_awaiting_human():
    outcome = rv.validation_failed_outcome(errors=["too_long"], run_id="t-1")
    assert outcome["state"] == "awaiting_human"
    assert outcome["decision_reason"] == "reply_validation_failed"
    assert outcome["sent"] is False


def test_short_reply_is_warning_not_failure():
    res = rv.validate_reply(
        body="Dzień dobry Panie,\n\nrozumiem.",  # well under 40 words
        approved_action="reply_directly",
        conversation_state=FIRST_STATE, saved_data={}, tenant_id="orchesta",
    )
    assert "too_short" in res["warnings"]
    # shortness alone does not fail (terse follow-ups are legitimate)
    assert "too_long" not in res["errors"]


def test_followup_rejects_paraphrase_of_saved_customer_data():
    res = rv.validate_reply(
        body=(
            "Rozumiem, że proces jest prowadzony ręcznie w Excelu i obejmuje 40 zapytań miesięcznie. "
            "Czy uwzględnić integrację z CRM w ofercie? Ile kont pocztowych ma śledzić system?"
        ),
        approved_action="ask_discovery_questions",
        conversation_state=FOLLOW_STATE,
        saved_data={"current_process": "skrzynki mailowe + Excel", "monthly_volume": "40"},
        missing_data=["crm", "mailbox_count"],
        tenant_id="orchesta",
    )
    assert "repeats_saved_data:current_process" in res["errors"]
    assert "repeats_saved_data:monthly_volume" in res["errors"]


def test_followup_can_ask_next_fields_without_repeating_saved_data():
    res = rv.validate_reply(
        body="Czy uwzględnić integrację z CRM w ofercie? Ile kont pocztowych ma śledzić system?",
        approved_action="ask_discovery_questions",
        conversation_state=FOLLOW_STATE,
        saved_data={"current_process": "skrzynki mailowe + Excel", "monthly_volume": "40"},
        missing_data=["crm", "mailbox_count"],
        tenant_id="orchesta",
    )
    assert res["ok"], res
