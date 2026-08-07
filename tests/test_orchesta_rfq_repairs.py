from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


poller = load_module("poller_orchesta_repairs", EXECUTION / "zoho_mail_poller.py")
registry_module = load_module("registry_orchesta_repairs", EXECUTION / "unified_lead_registry.py")
reply_validation = load_module("reply_validation_orchesta_repairs", EXECUTION / "reply_validation.py")
tenant_config = load_module("tenant_config_orchesta_repairs", EXECUTION / "tenant_config.py")
notify = load_module("notify_orchesta_repairs", EXECUTION / "orchesta_rfq_email_notify.py")
preflight = load_module("preflight_orchesta_repairs", EXECUTION / "rfq_runtime_preflight.py")
final_offer = load_module(
    "final_offer_orchesta_repairs",
    ROOT / "skills" / "rfq-final-offer" / "scripts" / "rfq_final_offer.py",
)
sheets_poller = load_module("sheets_poller_orchesta_repairs", EXECUTION / "google_sheets_lead_poller.py")


def test_old_form_like_subject_is_an_ordinary_email_from_actual_sender():
    envelope = poller.normalize_envelope(
        {
            "messageId": "old-looking-1",
            "folderId": "inbox-1",
            "fromAddress": "Real Customer <customer@example.com>",
            "subject": "Nowe zgłoszenie na bezpłatną konsultację",
        },
        default_folder_id="inbox-1",
    )
    assert envelope["source_type"] == "email"
    assert envelope["from"] == "customer@example.com"
    assert poller.precheck(envelope)["wake_agent"] is True


def test_discovery_paraphrase_without_next_step_is_a_dead_end():
    verdict = reply_validation.validate_reply(
        body="Rozumiem, że zależy Państwu na sprawniejszej obsłudze zapytań.",
        approved_action="ask_discovery_questions",
        conversation_state={"is_first_agent_reply": False, "acknowledgement_already_sent": True},
        saved_data={"company_name_or_website": "Example"},
        missing_data=["monthly_volume", "mailbox_count"],
        tenant_id="orchesta",
    )
    assert "discovery_dead_end" in verdict["errors"]


def test_discovery_request_for_missing_data_passes():
    verdict = reply_validation.validate_reply(
        body="Ile zapytań ofertowych przyjmują Państwo miesięcznie i ile kont pocztowych ma śledzić system?",
        approved_action="ask_discovery_questions",
        conversation_state={"is_first_agent_reply": False, "acknowledgement_already_sent": True},
        saved_data={"company_name_or_website": "Example"},
        missing_data=["monthly_volume", "mailbox_count"],
        tenant_id="orchesta",
    )
    assert verdict["ok"], verdict


def test_complete_discovery_skips_pre_offer_and_routes_to_final(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    registry = registry_module.UnifiedLeadRegistry(tmp_path / "registry.sqlite3")
    event = registry.register_event(
        source_type="mail",
        source_key="complete-1",
        email="anna@example.com",
        company="Example Sp. z o.o.",
        contact_name="Anna Example",
        content="Orchesta RFQ",
        relation="new",
        tenant_id="orchesta",
        facts={
            "company_name_or_website": "Example Sp. z o.o.",
            "current_process": "jedna osoba",
            "inquiry_channels": "email",
            "monthly_volume": "40",
            "mailbox_count": 2,
            "crm": True,
        },
    )
    monkeypatch.setattr(poller, "_followup_auto_reply_enabled", lambda: True)
    generated = poller.make_llm_followup_body_generator(
        unified_registry=registry,
        tenant_id="orchesta",
        run_id="complete-test",
    )({
        "classification": "existing_thread_reply",
        "deal_id": event["deal_id"],
        "body": "Potwierdzam podane dane.",
        "message_id": "complete-reply-1",
    })
    assert generated["skip_pre_offer_send"] is True
    assert generated["ready_for_final_offer"] is True
    assert generated["body_text"] == ""


def test_content_trigger_cannot_bypass_tenant_discovery_completeness():
    result = {
        "classification": "existing_thread_reply",
        "confidence": "high",
        "draft_kind": "final_offer",
    }
    envelope = {"subject": "Re: Orchesta RFQ"}
    body = "Proszę o ofertę dla dwóch skrzynek i CRM."

    assert poller.final_offer_candidate(result, envelope, body) is True
    assert poller.final_offer_stage_eligible(
        result,
        envelope,
        body,
        has_registry_deal=True,
        deal_ready_for_final_offer=False,
    ) is False
    assert poller.final_offer_stage_eligible(
        result,
        envelope,
        body,
        has_registry_deal=True,
        deal_ready_for_final_offer=True,
    ) is True


@pytest.mark.parametrize(
    "text,code",
    [
        ("Prosimy o rabat.", "discount"),
        ("Poproszę rozliczenie w EUR.", "foreign_currency"),
        ("Czy możliwa jest płatność etapami?", "installment_payment"),
        ("Potrzebujemy indywidualnego SLA.", "custom_sla"),
        ("To duży kontrakt.", "large_or_custom_contract"),
    ],
)
def test_commercial_exceptions_require_manual_review(text: str, code: str):
    assert code in tenant_config.commercial_exceptions("orchesta", text)


def test_ordinary_offer_has_no_commercial_exception():
    assert tenant_config.commercial_exceptions(
        "orchesta", "Proszę o ofertę na dwie skrzynki w PLN, CRM tak."
    ) == []


def test_commercial_exception_blocks_final_offer_safety():
    data = {
        "safety": {
            "thread_headers_valid": True,
            "sender_matches_thread": True,
            "attachments_safe": True,
            "prompt_injection_detected": False,
            "classification_confidence": "high",
        },
        "commercial_exception": ["discount"],
    }
    blocks = final_offer.validate_safety(data, final_offer.DEFAULT_PRICING_PATH)
    assert "commercial_exception.discount" in blocks


def test_each_internal_event_is_a_fresh_email_with_unique_subject():
    summary = {
        "source": "mailbox",
        "briefings": [{
            "message_id": "offer-event-1",
            "rfq_id": "RFQ-20260804-0007",
            "classification": "existing_thread_reply",
            "sender": {"email": "anna@example.com", "relationship": "active_thread"},
            "client": {"company": "Example Sp. z o.o.", "contact_name": "Anna Example"},
            "draft": {"action": "created"},
            "final_offer": {
                "action": "created",
                "pdf_attached": True,
                "price_net_display": "10 200 zł",
                "scope_display": "2 konta pocztowe, CRM",
                "draft_id": "draft-123",
            },
        }],
    }
    events = notify.build_notifications(summary)
    assert [item[2].split(" | ")[2] for item in events] == ["PDF prepared", "final offer draft ready"]
    assert len({item[2] for item in events}) == 2
    for _key, _briefing, subject, body in events:
        assert subject.startswith("Orchesta RFQ | RFQ-20260804-0007 |")
        assert "Oferta nie została wysłana klientowi" in body
        payload = notify.build_email_payload(
            from_address="rfq-mailbox@example.invalid",
            recipient=notify.DEFAULT_RECIPIENT,
            subject=subject,
            text=body,
        )
        assert "inReplyTo" not in payload
        assert "refHeader" not in payload
        assert "threadId" not in payload


def test_each_internal_event_is_a_separate_exactly_once_telegram(tmp_path: Path):
    summary = {
        "source": "mailbox",
        "briefings": [{
            "message_id": "offer-event-telegram-1",
            "deal_id": "deal-telegram-1",
            "classification": "existing_thread_reply",
            "sender": {"email": "anna@example.com", "relationship": "active_thread"},
            "client": {"company": "Example Sp. z o.o.", "contact_name": "Anna Example"},
            "draft": {"action": "created"},
            "final_offer": {
                "action": "created", "pdf_attached": True, "draft_id": "draft-telegram-1",
                "price_net_display": "10 200 zł", "scope_display": "2 konta pocztowe",
            },
        }],
    }
    calls: list[dict[str, str]] = []

    def fake_sender(*, subject: str, text: str):
        calls.append({"subject": subject, "text": text})
        return 200, {"success": True, "platform": "telegram", "message_id": str(700 + len(calls))}

    state = tmp_path / "notification-ledger.json"
    first = notify.deliver_telegram_notifications(summary, state_file=state, sender=fake_sender)
    second = notify.deliver_telegram_notifications(summary, state_file=state, sender=fake_sender)
    assert first["status"] == "sent"
    assert first["sent_count"] == 2
    assert [item["external_message_id"] for item in first["sent"]] == ["701", "702"]
    assert [call["subject"].split(" | ")[2] for call in calls] == ["PDF prepared", "final offer draft ready"]
    assert second["status"] == "already_delivered"
    assert len(calls) == 2


@pytest.mark.parametrize(
    "body,error",
    [
        ("Poproszę dane -- i wrócę z następnym krokiem.", "double_hyphen_present"),
        ("Poproszę dane – i wrócę z następnym krokiem.", "en_dash_present"),
        ("Poproszę dane — i wrócę z następnym krokiem.", "em_dash_present"),
    ],
)
def test_customer_reply_rejects_forbidden_dashes(body: str, error: str):
    verdict = reply_validation.validate_reply(
        body=body,
        approved_action="reply_directly",
        conversation_state={"is_first_agent_reply": False, "acknowledgement_already_sent": True},
        saved_data={},
        tenant_id="orchesta",
    )
    assert error in verdict["errors"]


def test_natural_customer_reply_passes_style_validation():
    verdict = reply_validation.validate_reply(
        body="Rozumiem. Proszę przesłać nazwę firmy, a przygotuję konkretny następny krok.",
        approved_action="reply_directly",
        conversation_state={"is_first_agent_reply": False, "acknowledgement_already_sent": True},
        saved_data={},
        tenant_id="orchesta",
    )
    assert verdict["ok"], verdict


def test_runtime_preflight_checks_dependencies_tenant_files_and_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(preflight.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(
        preflight,
        "smoke_test_pdf",
        lambda _path: {"ok": True, "header": "%PDF-", "size_bytes": 1234, "page_count": 4},
    )
    result = preflight.check_runtime("orchesta", tmp_path / "work")
    assert result["ok"], result
    assert result["pdf_smoke"]["ok"] is True
    assert result["pdf_smoke"]["header"] == "%PDF-"
    assert result["pdf_smoke"]["size_bytes"] > 0
    assert result["pdf_smoke"]["page_count"] > 0


def test_runtime_preflight_generates_and_reopens_pdf_in_isolated_runtime(tmp_path: Path):
    python = ROOT / "rfq-runtime" / ".venv" / "bin" / "python"
    if not python.exists():
        pytest.skip("isolated RFQ runtime is not installed locally")
    completed = subprocess.run(
        [str(python), str(EXECUTION / "rfq_runtime_preflight.py"), "--tenant", "orchesta", "--work-dir", str(tmp_path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["pdf_smoke"]["header"] == "%PDF-"
    assert result["pdf_smoke"]["size_bytes"] > 0
    assert result["pdf_smoke"]["page_count"] in {3, 4}


def test_runtime_preflight_fails_before_customer_processing_when_dependency_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        preflight.importlib.util,
        "find_spec",
        lambda name: None if name == "pypdf" else object(),
    )
    result = preflight.check_runtime("orchesta", tmp_path / "work")
    assert result["ok"] is False
    assert "missing_dependency:pypdf" in result["errors"]
    assert result["pdf_smoke"] == {"ok": False, "error": "not_run"}


def test_mailbox_wrapper_supports_fail_closed_controlled_live_selection():
    wrapper = (ROOT / "scripts" / "orchesta-rfq-mail-poller.sh").read_text(encoding="utf-8")
    assert "HERMES_CONTROLLED_SOURCE_MESSAGE_ID" in wrapper
    assert "HERMES_CONTROLLED_SENDER" in wrapper
    assert "HERMES_CONTROLLED_SEND_TELEGRAM" in wrapper
    assert '--controlled-source-message-id "$CONTROLLED_SOURCE_MESSAGE_ID"' in wrapper
    assert '--controlled-sender "$CONTROLLED_SENDER"' in wrapper
    assert "POLLER_ARGS+=(--send-telegram)" in wrapper


def test_sheets_controlled_selection_requires_all_exact_identifiers():
    with pytest.raises(RuntimeError, match="controlled_sheet_selection_incomplete:email,test_id,expected_status"):
        sheets_poller.controlled_selection(
            type("Args", (), {"controlled_row_number": 7})()
        )


def test_sheets_controlled_selection_validates_only_exact_test_row():
    headers = list(sheets_poller.REQUIRED_HEADERS)
    row = {name: "" for name in headers}
    row.update({
        "Email": "controlled+rfq@example.com",
        "Firma": "Testowa Fabryka RFQ",
        "Wiadomość": "[TEST HERMES] HERMES-E2E-20260804-001 Proszę o kontakt.",
        "Hermes status": "test_created",
    })
    rows = [headers, [row[name] for name in headers]]
    selection = {
        "row": 2,
        "email": "controlled+rfq@example.com",
        "test_id": "HERMES-E2E-20260804-001",
        "expected_status": "test_created",
    }
    selected = sheets_poller.validate_controlled_row(
        rows,
        {name: index for index, name in enumerate(headers)},
        selection,
    )
    assert selected["Firma"] == "Testowa Fabryka RFQ"
    assert sheets_poller.controlled_test_subject(selected) == "[TEST HERMES] HERMES-E2E-20260804-001 | Orchesta RFQ"
    with pytest.raises(RuntimeError, match="controlled_sheet_email_mismatch"):
        sheets_poller.validate_controlled_row(
            rows,
            {name: index for index, name in enumerate(headers)},
            {**selection, "email": "other@example.com"},
        )


def test_sheets_wrapper_passes_fail_closed_row_selection():
    wrapper = (ROOT / "scripts/google-sheets-lead-poller.sh").read_text(encoding="utf-8")
    assert "HERMES_CONTROLLED_SHEETS_ROW" in wrapper
    assert "HERMES_CONTROLLED_SHEETS_EMAIL" in wrapper
    assert "HERMES_CONTROLLED_SHEETS_TEST_ID" in wrapper
    assert "HERMES_CONTROLLED_SHEETS_EXPECTED_STATUS" in wrapper
    assert '--controlled-row-number "$CONTROLLED_ROW"' in wrapper
    assert '--controlled-email "$CONTROLLED_EMAIL"' in wrapper
    assert '--controlled-test-id "$CONTROLLED_TEST_ID"' in wrapper
    assert '--controlled-expected-status "$CONTROLLED_STATUS"' in wrapper
    alias = (ROOT / "scripts/run_poller.sh").read_text(encoding="utf-8")
    assert 'exec "$ROOT/scripts/google-sheets-lead-poller.sh" "$@"' in alias


def test_active_wrappers_use_hermes_app_runtime_and_isolated_rfq_runtime():
    for relative in ("scripts/google-sheets-lead-poller.sh", "scripts/orchesta-rfq-mail-poller.sh"):
        wrapper = (ROOT / relative).read_text(encoding="utf-8")
        assert "/opt/hermes/.venv/bin/python" in wrapper
    mail_wrapper = (ROOT / "scripts/orchesta-rfq-mail-poller.sh").read_text(encoding="utf-8")
    assert "HERMES_RFQ_VENV_PYTHON" in mail_wrapper
    assert "rfq-runtime/.venv/bin/python" in mail_wrapper
    for relative in ("scripts/run_mail_poller.sh", "deploy/run_mail_poller.sh"):
        alias = (ROOT / relative).read_text(encoding="utf-8")
        assert 'exec "$ROOT/scripts/orchesta-rfq-mail-poller.sh" "$@"' in alias


def test_active_sheets_wrapper_loads_deployment_flags_before_poller():
    wrapper = (ROOT / "scripts/google-sheets-lead-poller.sh").read_text(encoding="utf-8")
    source_position = wrapper.index('. "$ENV_FILE"')
    poller_position = wrapper.index('timeout --signal=TERM 240s "$PYTHON"')
    assert source_position < poller_position


def complete_offer_data() -> dict:
    return {
        "offer_number": "ORCH-RFQ-2026-PDF-TEST",
        "date": "2026-08-04",
        "sequence": 7,
        "client": {
            "first_name": "Anna",
            "last_name": "Example",
            "company": "Example Sp. z o.o.",
            "email": "anna@example.com",
        },
        "scope": {"mailbox_count": 2, "crm": True, "inquiry_source": "mail", "has_sample_requests": True},
        "thread": {"thread_id": "thread-pdf", "source_message_id": "<pdf@example.com>"},
        "safety": {
            "thread_headers_valid": True,
            "sender_matches_thread": True,
            "attachments_safe": True,
            "prompt_injection_detected": False,
            "classification_confidence": "high",
        },
    }


def test_pdf_control_requires_all_approved_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pricing = json.loads(final_offer.DEFAULT_PRICING_PATH.read_text(encoding="utf-8"))
    context = final_offer.build_offer_context(complete_offer_data(), pricing)
    pdf = tmp_path / "offer.pdf"
    pdf.write_bytes(b"%PDF-test")
    valid_text = " ".join([
        context["client"]["full_name"],
        context["client"]["company"],
        context["offer"]["offer_number"],
        context["offer"]["version"],
        context["pricing"]["net_total_display"],
        "2 konta pocztowe",
        "Płatność 100% z góry",
        "Wdrożenie 14 dni",
        "14 dni gwarancji",
        "Orchesta RFQ Team",
    ])
    monkeypatch.setattr(final_offer, "extract_pdf_text_and_pages", lambda _path: (valid_text, 3, []))
    assert final_offer.validate_pdf(pdf, context)["ok"] is True
    monkeypatch.setattr(final_offer, "extract_pdf_text_and_pages", lambda _path: (valid_text + " {{ puste }}", 3, []))
    assert "forbidden_text:empty_jinja" in final_offer.validate_pdf(pdf, context)["errors"]
    monkeypatch.setattr(final_offer, "extract_pdf_text_and_pages", lambda _path: (valid_text, 3, ["empty_page"]))
    assert "empty_page" in final_offer.validate_pdf(pdf, context)["errors"]


def test_pdf_control_rejects_missing_empty_and_invalid_files(tmp_path: Path):
    pricing = json.loads(final_offer.DEFAULT_PRICING_PATH.read_text(encoding="utf-8"))
    context = final_offer.build_offer_context(complete_offer_data(), pricing)
    missing = tmp_path / "missing.pdf"
    assert final_offer.validate_pdf(missing, context)["errors"] == ["pdf_missing"]
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    assert final_offer.validate_pdf(empty, context)["errors"] == ["pdf_empty"]
    invalid = tmp_path / "invalid.pdf"
    invalid.write_bytes(b"not a pdf")
    assert final_offer.validate_pdf(invalid, context)["errors"] == ["pdf_invalid_header"]


def test_final_offer_verifies_attachment_from_created_zoho_draft_when_upload_has_no_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    captured_input = {}

    class Poster:
        name = ""
        content = b""

        def upload_attachment(self, _account_id: str, path: Path):
            self.name = path.name
            self.content = path.read_bytes()
            return 200, {
                "data": {
                    "storeName": "store-test",
                    "attachmentName": self.name,
                    "attachmentPath": "/test/path",
                }
            }

        def __call__(self, _account_id: str, _payload: dict):
            return 201, {"data": {"messageId": "draft-verified-1"}}

    class Verifier:
        def __init__(self, poster: Poster):
            self.poster = poster

        def list_folders(self, _account_id: str):
            return [{"folderId": "drafts-1", "folderName": "Drafts", "folderType": "Drafts"}]

        def get_attachment_info(self, _account_id: str, _folder_id: str, _message_id: str):
            return [{
                "attachmentName": self.poster.name,
                "attachmentSize": len(self.poster.content),
                "attachmentId": "attachment-1",
            }]

        def get_attachment_content(
            self,
            _account_id: str,
            _folder_id: str,
            _message_id: str,
            _attachment_id: str,
        ):
            return self.poster.content

    def fake_skill(command, **_kwargs):
        input_path = Path(command[command.index("--input") + 1])
        captured_input.update(json.loads(input_path.read_text(encoding="utf-8")))
        output_dir = Path(command[command.index("--output-dir") + 1])
        output_dir.mkdir(parents=True)
        pdf = output_dir / "ORCH-RFQ-2026-TEST.pdf"
        pdf.write_bytes(b"%PDF-1.7\ncontrolled-test-pdf\n")
        mail = output_dir / "mail_final_offer.txt"
        mail.write_text("Dzień dobry,\n\nOferta znajduje się w załączniku.", encoding="utf-8")
        (output_dir / "manifest.json").write_text(
            json.dumps({
                "status": "offer_draft_created",
                "pdf": str(pdf),
                "mail_final_offer": str(mail),
                "offer_number": "ORCH-RFQ-2026-TEST",
                "price_net_display": "13 200 zł",
                "version": 1,
            }),
            encoding="utf-8",
        )
        return type("Completed", (), {"stdout": "", "stderr": "", "returncode": 0})()

    from zoho_reply_draft import APPROVAL_PHRASE

    poster = Poster()
    monkeypatch.setenv("HERMES_ALLOW_DRAFT_CREATE", "1")
    monkeypatch.setattr(poller.subprocess, "run", fake_skill)
    creator = poller.build_final_offer_creator(
        "rfq-mailbox@example.invalid",
        poster,
        APPROVAL_PHRASE,
        verification_client=Verifier(poster),
        verification_attempts=1,
    )
    result = creator(
        "account-1",
        {"from": "controlled@example.com", "subject": "Orchesta RFQ", "message_id": "inbound-1"},
        {"classification": "existing_thread_reply"},
        "<inbound-1@example.com>",
        "",
        headers={"message-id": "<inbound-1@example.com>"},
        classifier_message={"body": "Dwa konta, CRM tak, 40 zapytań miesięcznie."},
        attachment_routes=[],
        raw_attachments=[],
        deal_contact_name="Anna Example",
        deal_company="Example Sp. z o.o.",
        deal_facts={"mailbox_count": 2, "crm": True, "inquiry_source": "mail"},
        deal_id_value="deal-unique-1",
        rfq_id="RFQ-20260804-0042",
    )
    assert result["action"] == "created", result
    assert result["pdf_attached"] is True
    assert result["draft_id"] == "draft-verified-1"
    assert result["attachment_confirmation"]["verification"] == "download_sha256"
    assert result["attachment_confirmation"]["remote_size"] == len(poster.content)
    assert captured_input["deal_id"] == "deal-unique-1"
    assert captured_input["rfq_id"] == "RFQ-20260804-0042"
    assert captured_input["sequence"] == 42
    assert captured_input["offer_number"] == "ORCH-RFQ-2026-0042"


def test_draft_attachment_checksum_mismatch_fails_closed():
    expected = b"%PDF-expected"

    class Verifier:
        def list_folders(self, _account_id: str):
            return [{"folderId": "drafts-1", "folderType": "Drafts"}]

        def get_attachment_info(self, *_args):
            return [{
                "attachmentName": "offer.pdf",
                "attachmentSize": len(expected),
                "attachmentId": "attachment-1",
            }]

        def get_attachment_content(self, *_args):
            return b"%PDF-unexpect"

    result = poller.verify_draft_attachment(
        Verifier(),
        "account-1",
        "draft-1",
        expected_name="offer.pdf",
        expected_size=len(expected),
        expected_sha256=poller.hashlib.sha256(expected).hexdigest(),
        attempts=1,
    )
    assert result["ok"] is False
    assert result["status_name"] == "attachment_checksum_mismatch"
