#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / 'execution') not in sys.path:
    sys.path.insert(0, str(ROOT / 'execution'))
DRY_RUN = ROOT / 'execution' / 'mail-lead-pipeline-dry-run.py'
FINAL = ROOT / 'skills' / 'rfq-final-offer' / 'scripts' / 'rfq_final_offer.py'
POLLER = ROOT / 'execution' / 'zoho_mail_poller.py'
STATE = ROOT / 'execution' / 'pipeline_state.py'


def load_dry_run():
    spec = importlib.util.spec_from_file_location('mail_lead_pipeline_dry_run', DRY_RUN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def classify(subject: str, body: str, sender: str = 'test@example.com', context: dict | None = None) -> dict:
    module = load_dry_run()
    return module.classify({
        'id': subject,
        'message': {'from': sender, 'subject': subject, 'body': body, 'headers': {}, 'attachments': []},
        'context': context or {},
    })


def assert_class(subject: str, body: str, expected: str):
    got = classify(subject, body)
    assert got['classification'] == expected, f"{subject}: got {got['classification']} expected {expected}; result={got}"


def run_final_offer(input_data: dict, *args: str) -> dict:
    with tempfile.TemporaryDirectory(prefix='rfq_regression_') as tmp:
        tmp_path = Path(tmp)
        input_path = tmp_path / 'input.json'
        out_dir = tmp_path / 'out'
        input_path.write_text(json.dumps(input_data, ensure_ascii=False), encoding='utf-8')
        proc = subprocess.run(
            [sys.executable, str(FINAL), '--input', str(input_path), '--output-dir', str(out_dir), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        return json.loads(proc.stdout)


def complete_offer_input() -> dict:
    return {
        'offer_number': 'ORCH-RFQ-2026-TEST',
        'date': '2026-07-27',
        'sequence': 999,
        'account_email': 'rfq-mailbox@example.invalid',
        'language': 'pl',
        'client': {
            'first_name': 'Tomasz',
            'last_name': 'Nowak',
            'full_name': 'Tomasz Nowak',
            'company': 'Audit Components Sp. z o.o.',
            'email': 'tomasz.nowak@example.com',
        },
        'scope': {
            'mailbox_count': 3,
            'crm': True,
            'inquiry_source': 'mail',
            'has_sample_requests': True,
        },
        'thread': {'thread_id': 'thread-test', 'source_message_id': '<test@example.com>'},
        'safety': safe_offer_safety(),
        'customer_expectations': safe_customer_expectations(),
    }


def safe_offer_safety() -> dict:
    return {
        'thread_headers_valid': True,
        'sender_matches_thread': True,
        'attachments_safe': True,
        'prompt_injection_detected': False,
        'classification_confidence': 'high',
    }


def safe_customer_expectations() -> dict:
    return {
        'expects_sms': False,
        'expects_zoho': False,
        'expects_full_automatic_technical_pricing': False,
        'expects_auto_send_final_offers': False,
    }


def test_related_and_weak_fit_cases_do_not_become_quote_requests():
    cases = [
        ('Warsztaty z automatyzacji', 'Chcemy warsztat o automatyzacji agentów, bez wdrożenia RFQ.', 'related_non_rfq_topic'),
        ('Sales consulting', 'Looking for sales consulting, not quote request automation.', 'related_non_rfq_topic'),
        ('Automatyzacja wycen', 'Obsługa zapytań raz na kwartał, bez powtarzalnego źródła.', 'weak_fit_review_only'),
        ('Automatyzacja wycen', 'Broker ubezpieczeniowy chce finalne oferty bez udziału człowieka.', 'weak_fit_review_only'),
        ('Oferta', 'Dostałem ofertę i mam pytanie, ale nie wiem, czy to do Was.', 'unknown_review_needed'),
        ('Dane', 'Proszę o usunięcie danych i informację o dostępie do danych.', 'human_review_only'),
    ]
    for subject, body, expected in cases:
        assert_class(subject, body, expected)


def test_rfq_draft_inbox_language_is_detected_as_quote_request():
    assert_class(
        'Drafty odpowiedzi',
        'Czy możecie robić drafty odpowiedzi do zapytań z inboxa bez wysyłki automatycznej?',
        'new_quote_request',
    )


def test_final_offer_render_pdf_passes_validation_when_weasyprint_available():
    pytest.importorskip('pypdf')
    manifest = run_final_offer(complete_offer_input(), '--render-pdf')
    assert manifest['status'] == 'offer_draft_created', manifest
    assert manifest['pdf_validation']['ok'] is True, manifest['pdf_validation']
    assert manifest['pdf_size_bytes'] > 0


def test_final_offer_without_pdf_is_blocked_not_success():
    manifest = run_final_offer(complete_offer_input())
    assert manifest['status'] == 'blocked', manifest
    assert manifest['reason'] == 'pdf_required_for_final_offer', manifest


def test_google_sheets_form_lead_asks_questions_before_final_offer_after_reply():
    sheets = load_module('google_sheets_lead_poller_regression', ROOT / 'execution' / 'google_sheets_lead_poller.py')
    classifier = load_module('zoho_mail_poller_for_sheets_regression', POLLER).load_classifier()
    lead = {
        'Data': '2026-07-28',
        'Imię': 'Anna',
        'Email': "anna.formularz" + "@" + "example.pl",
        'Telefon': '+48 500 000 000',
        'Firma': 'Formularzowy Test Sp. z o.o.',
        'Wiadomość': 'Proszę o ofertę Orchesta RFQ. Zapytania wpadają przez formularz na Dysku Google i czasem mailem, ale nie wiem co jeszcze podać.',
        'Źródło': 'Google Forms / Dysk Google',
    }
    digest = sheets.lead_hash(lead)
    result = sheets.classify_lead(classifier, lead, 2, digest)
    assert result['classification'] == 'new_quote_request', result
    assert result['confidence'] == 'high', result
    assert result['offer_status'] == 'missing_data', result
    assert sheets.draftable_from_sheet(result, lead) is True

    initial_offer_input = {
        'offer_number': 'ORCH-RFQ-2026-SHEET-TEST',
        'date': '2026-07-28',
        'sequence': 1001,
        'account_email': 'rfq-mailbox@example.invalid',
        'language': 'pl',
        'client': {
            'first_name': 'Anna',
            'last_name': '',
            'company': lead['Firma'],
            'email': lead['Email'],
        },
        'scope': {'inquiry_source': 'both'},
        'thread': {'thread_id': 'sheets-row-2', 'source_message_id': '<sheets-2@hermes.local>'},
        'safety': safe_offer_safety(),
        'customer_expectations': safe_customer_expectations(),
    }
    first_manifest = run_final_offer(initial_offer_input)
    assert first_manifest['status'] == 'awaiting_data', first_manifest
    assert first_manifest['questions'] == [
        'Ile kont pocztowych ma śledzić system?',
        'Czy uwzględnić integrację z CRM w ofercie?',
    ], first_manifest
    assert 'inwestycja' not in first_manifest['mail_missing_data'].lower()

    completed_after_exchange = json.loads(json.dumps(initial_offer_input))
    completed_after_exchange['scope'].update({
        'mailbox_count': 2,
        'crm': True,
        'has_sample_requests': True,
    })
    completed_after_exchange['thread']['source_message_id'] = '<sheets-reply-2@hermes.local>'
    pytest.importorskip('pypdf')
    final_manifest = run_final_offer(completed_after_exchange, '--render-pdf')
    assert final_manifest['status'] == 'offer_draft_created', final_manifest
    assert final_manifest['price_net'] > 0
    assert final_manifest['pdf_validation']['ok'] is True, final_manifest['pdf_validation']


def test_mail_thread_asks_questions_then_creates_final_offer_after_reply():
    poller = load_module('zoho_mail_poller_mail_conversation_regression', POLLER)
    classifier = poller.load_classifier()
    initial_body = (
        'Dzień dobry, z tej strony Marta Wójcik. Firma: Mailowy Test Sp. z o.o. '
        'Interesuje nas Orchesta RFQ do zapytań z maila i formularza, ale nie mamy jeszcze pełnego zakresu.'
    )
    initial_result = classifier.classify({
        'id': 'mail-conversation-initial',
        'message': {
            'from': 'identity-123@example.invalid',
            'subject': 'Oferta Orchesta RFQ',
            'body': initial_body,
            'headers': {'Message-ID': '<identity-124@example.invalid>'},
            'attachments': [],
        },
        'context': {'mac_bridge_available': False},
    })
    assert initial_result['classification'] == 'new_quote_request', initial_result
    assert initial_result['confidence'] == 'high', initial_result
    initial_envelope = poller.normalize_envelope({
        'messageId': 'mail-initial',
        'folderId': 'inbox-1',
        'threadId': 'thread-mail-conversation',
        'fromAddress': 'identity-123@example.invalid',
        'subject': 'Oferta Orchesta RFQ',
    }, default_folder_id='inbox-1')
    initial_offer_input = poller.build_final_offer_input(
        envelope=initial_envelope,
        headers={'Message-ID': '<identity-124@example.invalid>'},
        result=initial_result,
        body_text=initial_body,
        attachment_routes=[],
        raw_attachments=[],
        account_email='rfq-mailbox@example.invalid',
    )
    first_manifest = run_final_offer(initial_offer_input)
    assert first_manifest['status'] == 'awaiting_data', first_manifest
    assert len(first_manifest['questions']) <= 2
    assert 'Ile kont pocztowych ma śledzić system?' in first_manifest['questions']
    assert initial_offer_input['client']['company'] == 'Mailowy Test Sp. z o.o.', initial_offer_input
    assert any(
        phrase in first_manifest['mail_missing_data']
        for phrase in (
            'Po tej odpowiedzi przygotuję ofertę.',
            'Jak tylko odpowiesz, przygotuję ofertę.',
        )
    )

    reply_body = '2 konta pocztowe, CRM tak. Mamy przykładowe zapytania.'
    reply_envelope = poller.normalize_envelope({
        'messageId': 'mail-reply',
        'folderId': 'inbox-1',
        'threadId': 'thread-mail-conversation',
        'fromAddress': 'identity-123@example.invalid',
        'subject': 'Re: Oferta Orchesta RFQ',
    }, default_folder_id='inbox-1')
    completed_offer_input = poller.build_final_offer_input(
        envelope=reply_envelope,
        headers={'Message-ID': '<identity-125@example.invalid>', 'In-Reply-To': '<identity-124@example.invalid>', 'References': '<identity-124@example.invalid>'},
        result={'classification': 'existing_thread_reply', 'confidence': 'high'},
        body_text=reply_body,
        thread_history_text=initial_body,
        attachment_routes=[],
        raw_attachments=[],
        account_email='rfq-mailbox@example.invalid',
    )
    assert completed_offer_input['client']['company'] == 'Mailowy Test Sp. z o.o.', completed_offer_input
    assert completed_offer_input['scope']['mailbox_count'] == 2, completed_offer_input
    assert completed_offer_input['scope']['crm'] is True, completed_offer_input
    pytest.importorskip('pypdf')
    final_manifest = run_final_offer(completed_offer_input, '--render-pdf')
    assert final_manifest['status'] == 'offer_draft_created', final_manifest
    assert final_manifest['pdf_validation']['ok'] is True, final_manifest['pdf_validation']
    assert final_manifest['price_net_display'].endswith('zł')


def test_telegram_transport_failure_does_not_abort_poll_or_drop_state():
    poller = load_module('zoho_mail_poller_audit', POLLER)
    state_mod = load_module('pipeline_state_audit', STATE)
    dataset = {
        'accounts': [{'accountId': 'acc-1', 'primaryEmailAddress': 'rfq-mailbox@example.invalid'}],
        'folders': [{'folderId': 'inbox-1', 'folderName': 'Inbox'}],
        'messages': [{
            'messageId': 'm-telegram-failure',
            'folderId': 'inbox-1',
            'fromAddress': 'test@example.com',
            'subject': 'Niejasna sprawa',
            'hasAttachment': '0',
            '_content': '<p>Nie wiem czy to do Was, proszę o kontakt.</p>',
            '_header': 'Message-ID: <m-telegram-failure@example.com>\n',
            '_attachmentinfo': [],
        }],
    }
    client = poller.FakeZohoClient(dataset)
    classifier = poller.load_classifier()
    with tempfile.TemporaryDirectory(prefix='rfq_state_') as tmp:
        state = state_mod.PipelineState(Path(tmp) / 'state.sqlite3')
        def broken_sender(text: str) -> bool:
            raise TimeoutError('synthetic telegram timeout with access_token=SHOULD_NOT_LEAK')
        summary = poller.poll(client, state, classifier, send_telegram=broken_sender)
        state.save()
    assert summary['woken'] == 1, summary
    assert summary['telegram_sent'] == 0, summary
    assert summary['briefings'][0]['telegram']['sent'] is False, summary['briefings'][0]
    dumped = json.dumps(summary, ensure_ascii=False)
    assert 'SHOULD_NOT_LEAK' not in dumped, dumped


if __name__ == '__main__':
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
            except Exception as exc:
                failures.append(f'{name}: {exc}')
    if failures:
        for failure in failures:
            print('FAIL:', failure)
        raise SystemExit(1)
    print('orchesta audit regression tests: ok')
