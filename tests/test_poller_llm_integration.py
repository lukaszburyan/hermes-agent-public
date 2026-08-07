#!/usr/bin/env python3
"""Integration tests for the LLM wiring in google_sheets_lead_poller (P1).

Covers spec sections 9-17 and 24-25:
  * LLM intent classifier runs in shadow alongside the deterministic one.
  * LLM reply writer + script-side validation produce the sendable body.
  * Kill-switch gate: LLM auto-send only when auto_reply_allowed is on.
  * Validation failure routes the deal to awaiting_human (no auto-send).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

EXECUTION_DIR = Path(__file__).resolve().parent.parent / "execution"
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

import google_sheets_lead_poller as gsp  # noqa: E402
import llm_intent_classifier as lic  # noqa: E402
import llm_reply_writer as lrw  # noqa: E402
import reply_validation as rv  # noqa: E402


def _reload_poller_with_env(monkeypatch, env: dict[str, str]):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import importlib
    importlib.reload(gsp)
    return gsp


@pytest.fixture
def lead() -> dict[str, str]:
    return {
        "Imię": "Jan Kowalski",
        "Email": "jan.kowalski@example.com",
        "Firma": "Firma ABC",
        "Wiadomość": "Dzień dobry, proszę o ofertę na 50 skrzynek pocztowych.",
        "Źródło": "META ADS",
    }


class _FixClient:
    def __init__(self, response: str) -> None:
        self._response = response

    def complete(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.0) -> str:
        return self._response


def test_llm_disabled_uses_deterministic_path(monkeypatch, lead):
    """Default (HERMES_LLM_INTENT_ENABLED unset): no shadow classify, no LLM send."""
    monkeypatch.delenv("HERMES_LLM_INTENT_ENABLED", raising=False)
    gsp = _reload_poller_with_env(monkeypatch, {})
    assert gsp._llm_enabled() is False
    assert gsp._llm_shadow_classify(lead, {}, tenant_id="orchesta", run_id="r1") is None
    # Kill-switch gate: even if switches existed, llm disabled -> no llm send.
    assert gsp._get_llm_client() is not None  # GatewayLLMClient constructs fine
    # make_llm_body_generator raises when llm disabled (no send happens).
    gen = gsp.make_llm_body_generator(lead=lead, result={}, tenant_id="orchesta", run_id="r1")
    with pytest.raises(gsp.ReplyValidationError):
        gen({"body": "x"})


def test_llm_shadow_classify_runs_when_enabled(monkeypatch, lead):
    """HERMES_LLM_INTENT_ENABLED=1: shadow classify returns the LLM outcome."""
    monkeypatch.setenv("HERMES_LLM_INTENT_ENABLED", "1")
    gsp = _reload_poller_with_env(monkeypatch, {"HERMES_LLM_INTENT_ENABLED": "1"})
    assert gsp._llm_enabled() is True

    # Schema is additionalProperties:false; only schema fields. missing_information
    # non-empty -> decide_action returns ask_discovery_questions.
    classify_response = (
        '{"intent":"pricing_request","confidence":"high","risk":"low",'
        '"sender_identity":"free_email_unverified","provided_information":{"mailbox_count":"50"},'
        '"missing_information":["users_count","sector","deployment_timeline"],'
        '"recommended_action":"ask_discovery_questions","decision_reasons":["rfq signal"],"language":"pl"}'
    )
    monkeypatch.setattr(gsp._lic, "GatewayLLMClient", lambda *a, **k: _FixClient(classify_response))
    outcome = gsp._llm_shadow_classify(lead, {}, tenant_id="orchesta", run_id="r1")
    assert outcome is not None
    assert outcome["action"] == "ask_discovery_questions"
    assert outcome["prompt_version"] == lic.PROMPT_VERSION


def test_make_llm_body_generator_valid_reply(monkeypatch, lead):
    """LLM body generator returns a validated body for a clean reply."""
    monkeypatch.setenv("HERMES_LLM_INTENT_ENABLED", "1")
    gsp = _reload_poller_with_env(monkeypatch, {"HERMES_LLM_INTENT_ENABLED": "1"})
    # A first reply with a greeting, ~50 words, no forbidden terms, no dashes.
    body = (
        "Dzień dobry Panie Janie,\n\n"
        "dziękuję za zgłoszenie dotyczące Orchesta RFQ. Jak obecnie wygląda u Państwa proces obsługi "
        "zapytań? Z jakich kanałów trafiają do Państwa zapytania ofertowe?"
    )
    reply_response = '{"subject":"x","body":' + _json_str(body) + ',"needs_human_review":false}'
    # _get_llm_client() uses _lic.GatewayLLMClient for both classify and reply.
    monkeypatch.setattr(gsp._lic, "GatewayLLMClient", lambda *a, **k: _FixClient(reply_response))
    gen = gsp.make_llm_body_generator(lead=lead, result={}, tenant_id="orchesta", run_id="r1")
    out = gen({"body": synthesize_body_proxy(lead)})
    assert "Jak obecnie wygląda u Państwa proces obsługi zapytań?" in out["body_text"]
    assert "Z jakich kanałów trafiają do Państwa zapytania ofertowe?" in out["body_text"]
    assert "zgłoszeniem z kampanii" in out["body_text"]
    assert "formularza kampanii" not in out["body_text"]
    assert out["generator"] == "llm_reply_writer"
    assert out["model"] == "gpt-plus"
    assert out["validated"] is True


def test_sheet_llm_receives_saved_and_missing_offer_data(monkeypatch, lead):
    monkeypatch.setenv("HERMES_LLM_INTENT_ENABLED", "1")
    gsp = _reload_poller_with_env(monkeypatch, {"HERMES_LLM_INTENT_ENABLED": "1"})
    captured: dict[str, object] = {}
    body = (
        "Dzień dobry Panie Janie,\n\n"
        "dziękuję za zgłoszenie dotyczące Orchesta RFQ. Jak obecnie wygląda u Państwa proces obsługi "
        "zapytań? Z jakich kanałów trafiają do Państwa zapytania ofertowe?"
    )

    def fake_write_reply(**kwargs):
        captured.update(kwargs)
        return {"body": body, "needs_human_review": False, "prompt_version": "test"}

    monkeypatch.setattr(gsp._lrw, "write_reply", fake_write_reply)
    gen = gsp.make_llm_body_generator(lead=lead, result={}, tenant_id="orchesta", run_id="r1")
    gen({"body": synthesize_body_proxy(lead)})

    assert captured["saved_data"] == {
        "company_name_or_website": "Firma ABC",
        "mailbox_count": 50,
    }
    assert captured["missing_data"][:2] == ["current_process", "inquiry_channels"]
    assert "company_name_or_website" not in captured["missing_data"]
    assert "mailbox_count" not in captured["missing_data"]


def test_sheet_llm_dead_end_is_retried_once_then_rejected(monkeypatch, lead):
    monkeypatch.setenv("HERMES_LLM_INTENT_ENABLED", "1")
    gsp = _reload_poller_with_env(monkeypatch, {"HERMES_LLM_INTENT_ENABLED": "1"})
    calls = 0

    def fake_write_reply(**_kwargs):
        nonlocal calls
        calls += 1
        return {
            "body": "Dzień dobry Panie Janie,\n\ndziękuję za wiadomość. Mamy wystarczające informacje.",
            "needs_human_review": False,
        }

    monkeypatch.setattr(gsp._lrw, "write_reply", fake_write_reply)
    gen = gsp.make_llm_body_generator(lead=lead, result={}, tenant_id="orchesta", run_id="r1")
    with pytest.raises(gsp.ReplyValidationError, match="discovery_dead_end"):
        gen({"body": synthesize_body_proxy(lead)})
    assert calls == 2


def test_make_llm_body_generator_rejects_forbidden_term(monkeypatch, lead):
    """Reply containing a forbidden term (telegram) raises ReplyValidationError."""
    monkeypatch.setenv("HERMES_LLM_INTENT_ENABLED", "1")
    gsp = _reload_poller_with_env(monkeypatch, {"HERMES_LLM_INTENT_ENABLED": "1"})
    body = (
        "Dzień dobry Panie Janie,\n\n"
        "dziękuję za zgłoszenie. Proszę o podanie liczby użytkowników oraz sektora. "
        "Możemy też kontynuować na Telegram. Pozdrawiam serdecznie, zespół Orchesta."
    )
    reply_response = '{"subject":"x","body":' + _json_str(body) + ',"needs_human_review":false}'
    monkeypatch.setattr(gsp._lic, "GatewayLLMClient", lambda *a, **k: _FixClient(reply_response))
    gen = gsp.make_llm_body_generator(lead=lead, result={}, tenant_id="orchesta", run_id="r1")
    with pytest.raises(gsp.ReplyValidationError):
        gen({"body": synthesize_body_proxy(lead)})


def test_make_llm_body_generator_rejects_too_long(monkeypatch, lead):
    """Reply over the hard word limit raises ReplyValidationError."""
    monkeypatch.setenv("HERMES_LLM_INTENT_ENABLED", "1")
    gsp = _reload_poller_with_env(monkeypatch, {"HERMES_LLM_INTENT_ENABLED": "1"})
    words = " ".join(f"słowo{i}" for i in range(150))
    body = "Dzień dobry Panie Janie,\n\n" + words + "\n\nPozdrawiam, zespół Orchesta."
    reply_response = '{"subject":"x","body":' + _json_str(body) + ',"needs_human_review":false}'
    monkeypatch.setattr(gsp._lic, "GatewayLLMClient", lambda *a, **k: _FixClient(reply_response))
    gen = gsp.make_llm_body_generator(lead=lead, result={}, tenant_id="orchesta", run_id="r1")
    with pytest.raises(gsp.ReplyValidationError):
        gen({"body": synthesize_body_proxy(lead)})


def test_kill_switch_gate_blocks_llm_send(monkeypatch, lead):
    """reply_kill_switch=1 OR auto_reply_low_risk=0 -> use_llm_send is False."""
    monkeypatch.setenv("HERMES_LLM_INTENT_ENABLED", "1")
    # auto_reply_low_risk off -> auto_reply_allowed False
    gsp = _reload_poller_with_env(
        monkeypatch,
        {"HERMES_LLM_INTENT_ENABLED": "1", "HERMES_AUTO_REPLY_LOW_RISK": "0", "HERMES_REPLY_KILL_SWITCH": "0"},
    )
    sw = gsp._SafetySwitches.from_env()
    assert sw.auto_reply_allowed is False
    # kill switch on
    gsp2 = _reload_poller_with_env(
        monkeypatch,
        {"HERMES_LLM_INTENT_ENABLED": "1", "HERMES_AUTO_REPLY_LOW_RISK": "1", "HERMES_REPLY_KILL_SWITCH": "1"},
    )
    sw2 = gsp2._SafetySwitches.from_env()
    assert sw2.auto_reply_allowed is False


def test_kill_switch_gate_allows_llm_send(monkeypatch, lead):
    """auto_reply_low_risk=1 AND reply_kill_switch=0 -> auto_reply_allowed True."""
    monkeypatch.setenv("HERMES_LLM_INTENT_ENABLED", "1")
    gsp = _reload_poller_with_env(
        monkeypatch,
        {"HERMES_LLM_INTENT_ENABLED": "1", "HERMES_AUTO_REPLY_LOW_RISK": "1", "HERMES_REPLY_KILL_SWITCH": "0"},
    )
    sw = gsp._SafetySwitches.from_env()
    assert sw.auto_reply_allowed is True
    assert sw.llm_intent_enabled is True
    assert sw.llm_shadow_mode is True  # default


def test_service_account_path_selected_when_env_set(monkeypatch, tmp_path):
    """load_google_services takes the SA branch when HERMES_GOOGLE_SA_FILE is set.

    We assert the branch selection without touching the network: stub the SA
    Credentials factory to return a sentinel and stub build(); the call must
    NOT raise FileNotFoundError for the nonexistent OAuth token file (proving the
    SA branch was taken).
    """
    sa_path = tmp_path / "service_account.json"
    sa_path.write_text(
        '{"type":"service_account","project_id":"x","private_key_id":"k","private_key":"'
        "<FAKE_SERVICE_ACCOUNT_PRIVATE_KEY_PEM>"
        '","client_email":"identity-126@example.invalid","client_id":"1","token_uri":"https://oauth2.googleapis.com/token"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_GOOGLE_SA_FILE", str(sa_path))
    monkeypatch.setattr(gsp, "build", lambda *a, **k: object())

    sentinel = object()
    import google.oauth2.service_account as sa_mod

    class _FakeCreds:
        def refresh(self, request):  # noqa: ANN001 - no network
            return None

    monkeypatch.setattr(
        sa_mod.Credentials, "from_service_account_file",
        classmethod(lambda cls, path, scopes=None: _FakeCreds()),
    )
    sheets, drive = gsp.load_google_services(str(tmp_path / "nonexistent_token.json"))
    assert sheets is sentinel or sheets is not None
    assert drive is not None


# --- helpers -----------------------------------------------------------------


def _json_str(s: str) -> str:
    import json
    return json.dumps(s, ensure_ascii=False)


def synthesize_body_proxy(lead: dict[str, str]) -> str:
    return lead.get("Wiadomość", "")
