#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import time
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / 'execution' / 'unified_lead_registry.py'
SHEETS = ROOT / 'execution' / 'google_sheets_lead_poller.py'
MAIL = ROOT / 'execution' / 'zoho_mail_poller.py'
NOTIFY = ROOT / 'execution' / 'orchesta_rfq_email_notify.py'
MAIL_WRAPPER = ROOT / 'scripts' / 'orchesta-rfq-mail-poller.sh'
SHEETS_WRAPPER = ROOT / 'scripts' / 'google-sheets-lead-poller.sh'
SHEETS_MODULE = ROOT / 'execution' / 'google_sheets_lead_poller.py'
SOURCE_HEALTH = ROOT / 'execution' / 'source_health.py'
STATE = ROOT / 'execution' / 'pipeline_state.py'


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sheet_and_mail_events_resolve_to_one_deal_and_one_pre_offer_response():
    registry_mod = load_module('unified_lead_registry_test_1', REGISTRY)
    with tempfile.TemporaryDirectory() as tmp:
        registry = registry_mod.UnifiedLeadRegistry(Path(tmp) / 'state.sqlite3')
        sheet = registry.register_event(
            source_type='google_sheets',
            source_key='sheet:main:2',
            identity-155@example.invalid',
            company='Example Sp. z o.o.',
            contact_name='Anna Example',
            content='Proszę o ofertę Orchesta RFQ dla formularza i maila.',
            relation='new',
            source_metadata={'spreadsheet_id': 'main', 'sheet_name': 'Arkusz1', 'row': 2},
        )
        assert sheet['is_new_event'] is True
        assert sheet['is_cross_source_duplicate'] is False
        assert registry.claim_response(sheet['deal_id'], 'response:sheet-main-2', content_hash='questions-v1') is True
        registry.record_response(sheet['deal_id'], 'response:sheet-main-2', message_id='sent-1', content_hash='questions-v1')

        mail = registry.register_event(
            source_type='mail',
            source_key='zoho-message-1',
            identity-156@example.invalid',
            company='Example Sp. z o.o.',
            contact_name='Anna Example',
            content='Proszę o ofertę Orchesta RFQ dla formularza i maila.',
            relation='new',
            thread_id='thread-1',
        )
        assert mail['deal_id'] == sheet['deal_id']
        assert mail['is_cross_source_duplicate'] is True
        assert registry.claim_response(mail['deal_id'], 'response:sheet-main-2', content_hash='questions-v1') is False
        assert registry.get_deal(mail['deal_id'])['status'] == 'waiting_for_customer'
        assert registry.get_deal(mail['deal_id'])['last_response_id'] == 'sent-1'


def test_reply_merges_prior_sheet_facts_and_advances_to_offer_ready():
    registry_mod = load_module('unified_lead_registry_test_2', REGISTRY)
    with tempfile.TemporaryDirectory() as tmp:
        registry = registry_mod.UnifiedLeadRegistry(Path(tmp) / 'state.sqlite3')
        initial = registry.register_event(
            source_type='google_sheets', source_key='sheet:main:9',
            email='marta@example.com', company='Marta Tech Sp. z o.o.', contact_name='Marta Wójcik',
            content='Zapytania przychodzą przez mail i formularz.', relation='new',
            facts={'inquiry_source': 'both'},
            source_metadata={'spreadsheet_id': 'main', 'sheet_name': 'Arkusz1', 'row': 9},
        )
        reply = registry.register_event(
            source_type='mail', source_key='zoho-reply-9', email='marta@example.com',
            company='', contact_name='', content='2 konta pocztowe, CRM tak, Telegram może być.',
            relation='reply', thread_id='customer-thread-9',
            facts={'mailbox_count': 2, 'crm': True},
            source_metadata={'in_reply_to': '<identity-157@example.invalid>'},
        )
        assert reply['deal_id'] == initial['deal_id']
        context = registry.context_for_deal(initial['deal_id'])
        assert context['company'] == 'Marta Tech Sp. z o.o.'
        assert context['contact_name'] == 'Marta Wójcik'
        assert context['facts'] == {
            'crm': True,
            'inquiry_source': 'both',
            'mailbox_count': 2,
        }
        assert 'Marta Tech Sp. z o.o.' in registry.context_text(initial['deal_id'])
        registry.record_offer(initial['deal_id'], draft_id='offer-draft-9', price_net_display='4 920 zł', scope='2 konta, CRM')
        deal = registry.get_deal(initial['deal_id'])
        assert deal['status'] == 'offer_ready'
        assert deal['final_draft_id'] == 'offer-draft-9'
        sheet_refs = registry.sheet_refs_with_pending_status()
        assert sheet_refs[0]['desired_status'] == 'oferta gotowa'


def test_sheet_outbound_thread_binding_routes_real_reply_and_marks_send_automatic():
    registry_mod = load_module('unified_lead_registry_sheet_thread_test', REGISTRY)
    with tempfile.TemporaryDirectory() as tmp:
        registry = registry_mod.UnifiedLeadRegistry(Path(tmp) / 'state.sqlite3')
        initial = registry.register_event(
            source_type='google_sheets', source_key='sheet:main:threaded',
            email='anna@example.com', company='Anna Test Sp. z o.o.', contact_name='Anna',
            content='Potrzebujemy obsługi dwóch skrzynek i CRM.', relation='new',
            facts={'mailbox_count': 2, 'crm': True},
        )
        registry.record_response(
            initial['deal_id'], 'response:sheet:main:threaded',
            message_id='zoho-sent-1', content_hash='reply-v1',
        )
        registry.bind_thread(
            initial['deal_id'], provider='zoho', account_id='account-1', thread_id='zoho-sent-1',
        )
        registry.observe_conversation_message(
            initial['deal_id'], account_id='account-1', thread_id='zoho-sent-1',
            message_id='zoho-sent-1', direction='outbound', origin='hermes_automatic',
            occurred_at='2026-08-04T12:00:00+00:00',
            metadata={'rfc_message_id': '<sheet-outbound@example.com>'},
        )

        reply = registry.register_event(
            source_type='mail', source_key='zoho-inbound-1', email='anna@example.com',
            company='', contact_name='', content='Obsługujemy 40 zapytań miesięcznie.',
            relation='reply', thread_id='zoho-sent-1', facts={'monthly_volume': 40},
            source_metadata={
                'provider': 'zoho', 'account_id': 'account-1', 'thread_id': 'zoho-sent-1',
                'in_reply_to': '<sheet-outbound@example.com>',
            },
        )

        assert reply['deal_id'] == initial['deal_id']
        assert reply['routing_action'] == 'link_existing'
        assert 'hard:thread_id' in reply['routing_decision']['evidence']
        assert registry.known_automatic_message_ids(initial['deal_id']) == {'zoho-sent-1'}
        registry.close()


def test_invalid_or_conflicting_sheet_identity_requires_review_without_draft():
    registry_mod = load_module('unified_lead_registry_test_3', REGISTRY)
    with tempfile.TemporaryDirectory() as tmp:
        registry = registry_mod.UnifiedLeadRegistry(Path(tmp) / 'state.sqlite3')
        event = registry.register_event(
            source_type='google_sheets', source_key='sheet:main:3', email='not-an-email',
            company='A Sp. z o.o.', contact_name='A', content='Oferta RFQ', relation='new',
            source_metadata={'spreadsheet_id': 'main', 'sheet_name': 'Arkusz1', 'row': 3},
        )
        assert event['requires_review'] is True
        assert event['reason_code'] == 'invalid_email'
        assert registry.claim_draft(event['deal_id'], 'discovery', content_hash='x') is False
        assert registry.get_deal(event['deal_id'])['status'] == 'review_required'


def test_notification_recipient_is_hard_allowlisted_and_offer_contains_required_details():
    notify = load_module('orchesta_rfq_email_notify_unified_test', NOTIFY)
    try:
        notify.validate_internal_recipient('customer@example.com')
        raise AssertionError('external notification recipient should be blocked')
    except PermissionError:
        pass
    assert notify.validate_internal_recipient(identity-158@example.invalid') == identity-158@example.invalid'
    built = notify.build_notification({
        'source': 'mailbox',
        'woken': 1,
        'final_offer_drafts_created': 1,
        'briefings': [{
            'rfq_id': 'RFQ-20260804-0007',
            'classification': 'existing_thread_reply',
            'sender': {'email': 'anna@example.com', 'relationship': 'active_thread'},
            'client': {'company': 'Example Sp. z o.o.', 'contact_name': 'Anna Example'},
            'draft': {'action': 'created', 'external_draft_id': 'draft-offer-1'},
            'final_offer': {
                'action': 'created', 'price_net_display': '4 920 zł',
                'scope_display': '2 konta pocztowe, CRM',
                'draft_id': 'draft-offer-1', 'pdf_attached': True,
                'draft_location': 'Zoho Mail > Drafts, wątek Oferta RFQ',
            },
        }],
    })
    assert built is not None
    subject, text = built
    assert 'Example Sp. z o.o.' in text
    assert 'Anna Example' in text
    assert '2 konta pocztowe, CRM' in text
    assert '4 920 zł' in text
    assert 'Zoho Mail > Drafts' in text


def test_internal_notification_truthfully_reports_sent_pre_offer_message():
    notify = load_module('orchesta_rfq_email_notify_sent_test', NOTIFY)
    built = notify.build_notification({
        'source': 'google_sheets',
        'woken': 1,
        'responses_sent': 1,
        'briefings': [{
            'classification': 'new_quote_request',
            'sender': {'email': 'anna@example.com', 'relationship': 'new_domain'},
            'message': {'action': 'sent', 'external_message_id': 'sent-1'},
            'draft': {'action': 'sent', 'external_draft_id': ''},
            'next_step': 'Czekam na odpowiedź klienta.',
        }],
    })
    assert built is not None
    _, text = built
    assert 'odpowiedziałem autonomicznie' in text
    assert 'Nie wysłałem żadnej odpowiedzi do klienta automatycznie' not in text


def test_notification_delivery_ledger_retries_failure_and_deduplicates_success():
    notify = load_module('orchesta_rfq_email_notify_ledger_test', NOTIFY)
    summary = {
        'run_id': 'run-final-1',
        'source': 'mailbox',
        'final_offer_drafts_created': 1,
        'final_offer_notifications_ready': 1,
        'briefings': [{
            'classification': 'existing_thread_reply',
            'sender': {'email': 'anna@example.com', 'relationship': 'active_thread'},
            'client': {'company': 'Example Sp. z o.o.', 'contact_name': 'Anna'},
            'draft': {'action': 'created', 'external_draft_id': 'offer-draft-ledger-1'},
            'final_offer': {
                'action': 'created', 'pdf_attached': True, 'price_net_display': '4 920 zł',
                'scope_display': '2 konta, CRM', 'draft_location': 'Zoho Mail > Drafts',
            },
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        calls = []

        def sender(**kwargs):
            calls.append(kwargs)
            return (500, {'error': 'temporary'}) if len(calls) == 1 else (200, {'data': {'messageId': 'internal-notice-1'}})

        state_file = Path(tmp) / 'notification-ledger.json'
        assert notify.deliver_notification(summary, recipient=notify.DEFAULT_RECIPIENT, token_file='unused', env_file='unused', state_file=state_file, sender=sender)['status'] == 'failed'
        assert notify.deliver_notification(summary, recipient=notify.DEFAULT_RECIPIENT, token_file='unused', env_file='unused', state_file=state_file, sender=sender)['status'] == 'sent'
        assert notify.deliver_notification(summary, recipient=notify.DEFAULT_RECIPIENT, token_file='unused', env_file='unused', state_file=state_file, sender=sender)['status'] == 'already_delivered'
        assert len(calls) == 3


def test_blocked_sheet_lead_generates_one_internal_email_across_run_ids():
    notify = load_module('orchesta_rfq_email_notify_blocked_sheet_test', NOTIFY)
    summary = {
        'run_id': 'sheet-run-1',
        'source': 'google_sheets',
        'woken': 1,
        'briefings': [{
            'message_id': 'sheets-row-17',
            'correlation_id': 'sheets:17:stable-content-hash',
            'classification': 'unknown_review_needed',
            'sender': {'email': 'lead@example.com', 'relationship': 'new_or_unknown'},
            'draft': {'action': 'none', 'notify_only': True, 'source_message_id': 'sheets-row-17'},
            'message': {'action': 'none'},
            'sheet': {'row': 17, 'status': 'wymaga sprawdzenia', 'company': 'Example'},
            'next_step': 'Sprawdź rekord ręcznie; automatyczna odpowiedź została zablokowana.',
        }],
    }
    built = notify.build_notification(summary)
    assert built is not None
    assert 'nie odpisywałem' in built[1]

    calls = []
    with tempfile.TemporaryDirectory() as tmp:
        state_file = Path(tmp) / 'notification-ledger.json'

        def sender(**kwargs):
            calls.append(kwargs)
            return 200, {'data': {'messageId': 'internal-blocked-17'}}

        first = notify.deliver_notification(
            summary, recipient=notify.DEFAULT_RECIPIENT, token_file='unused',
            env_file='unused', state_file=state_file, sender=sender,
        )
        repeated = dict(summary, run_id='sheet-run-2')
        second = notify.deliver_notification(
            repeated, recipient=notify.DEFAULT_RECIPIENT, token_file='unused',
            env_file='unused', state_file=state_file, sender=sender,
        )
        assert first['status'] == 'sent'
        assert second['status'] == 'already_delivered'
        assert len(calls) == 1


def test_all_terminal_failure_actions_are_email_notifiable():
    notify = load_module('orchesta_rfq_email_notify_terminal_failures_test', NOTIFY)
    for action in ('auto_draft_failed', 'auto_send_failed', 'deferred_content_unavailable',
                   'final_offer_blocked', 'final_offer_failed', 'send_outcome_unknown',
                   'send_reconcile_pending', 'sent_guard_failed', 'would_create_pending_approval'):
        summary = {
            'source': 'mailbox',
            'briefings': [{
                'message_id': f'message-{action}',
                'classification': 'new_quote_request',
                'sender': {'email': 'lead@example.com', 'relationship': 'new_or_unknown'},
                'draft': {'action': action, 'source_message_id': f'message-{action}', 'notify_only': True},
                'next_step': 'Sprawdź wiadomość ręcznie.',
            }],
        }
        assert notify.build_notification(summary) is not None, action


def test_notification_skips_automated_spam_and_plain_noop():
    notify = load_module('orchesta_rfq_email_notify_noise_test', NOTIFY)
    summary = {
        'run_id': 'noise-1',
        'source': 'mailbox',
        'woken': 1,
        'briefings': [{
            'message_id': 'newsletter-1',
            'classification': 'newsletter_automated_spam',
            'sender': {'email': 'newsletter@example.com', 'relationship': 'unknown'},
            'draft': {'action': 'none', 'notify_only': True},
            'telegram': {'action': 'none', 'sent': False},
            'next_step': 'Brak działania.',
        }],
    }
    assert notify.build_notification(summary) is None


def test_mailbox_content_fetch_deferred_creates_actionable_briefing_once():
    mail = load_module('zoho_mail_content_deferred_test', MAIL)
    state_mod = load_module('pipeline_state_content_deferred_test', STATE)
    dataset = {
        'accounts': [{'accountId': 'acc-1', 'primaryEmailAddress': mail.DEFAULT_TARGET_EMAIL}],
        'folders': [{'folderId': 'inbox-1', 'folderName': 'Inbox'}],
        'messages': [{
            'messageId': 'mail-content-failure-1', 'folderId': 'inbox-1',
            'threadId': 'thread-content-failure-1', 'fromAddress': 'lead@example.com',
            'subject': 'Prośba o wycenę Orchesta RFQ', 'hasAttachment': '0',
            'receivedTime': '1800000000000',
        }],
    }

    class UnavailableClient(mail.FakeZohoClient):
        def get_content(self, account_id, folder_id, message_id):
            raise mail.ZohoContentUnavailable('temporary Zoho outage')

    with tempfile.TemporaryDirectory() as tmp:
        summary = mail.poll(
            UnavailableClient(dataset),
            state_mod.PipelineState(Path(tmp) / 'mail.sqlite3'),
            mail.load_classifier(),
            run_id='deferred-run-1',
        )
    assert summary['content_fetch_deferred'] == 1
    assert len(summary['briefings']) == 1
    briefing = summary['briefings'][0]
    assert briefing['message_id'] == 'mail-content-failure-1'
    assert briefing['draft']['action'] == 'deferred_content_unavailable'
    assert 'nie mogłem odczytać' in briefing['telegram_text'].lower()


def test_production_wrapper_has_no_technical_mailbox_source_flag():
    wrapper = MAIL_WRAPPER.read_text(encoding='utf-8')
    assert 'MAILBOX_' + 'FORM_SOURCE' not in wrapper




def test_ambiguous_internal_email_outcome_is_never_retried():
    notify = load_module('orchesta_rfq_email_notify_ambiguous_test', NOTIFY)
    summary = {
        'run_id': 'mail-run-ambiguous-1',
        'source': 'mailbox',
        'briefings': [{
            'message_id': 'mail-ambiguous-1',
            'classification': 'unknown_review_needed',
            'sender': {'email': 'lead@example.com', 'relationship': 'new_or_unknown'},
            'draft': {'action': 'blocked_low_confidence', 'source_message_id': 'mail-ambiguous-1', 'notify_only': True},
            'next_step': 'Sprawdź wiadomość ręcznie.',
        }],
    }
    calls = []
    with tempfile.TemporaryDirectory() as tmp:
        state_file = Path(tmp) / 'notification-ledger.json'

        def ambiguous_sender(**kwargs):
            calls.append(kwargs)
            raise TimeoutError('connection lost after request')

        first = notify.deliver_notification(
            summary, recipient=notify.DEFAULT_RECIPIENT, token_file='unused',
            env_file='unused', state_file=state_file, sender=ambiguous_sender,
        )
        repeated = notify.deliver_notification(
            dict(summary, run_id='mail-run-ambiguous-2'),
            recipient=notify.DEFAULT_RECIPIENT, token_file='unused',
            env_file='unused', state_file=state_file, sender=ambiguous_sender,
        )
    assert first['status'] == 'outcome_unknown'
    assert repeated['status'] == 'outcome_unknown'
    assert len(calls) == 1


def test_synthetic_599_internal_email_outcome_is_never_retried():
    notify = load_module('orchesta_rfq_email_notify_599_test', NOTIFY)
    summary = {
        'source': 'mailbox',
        'briefings': [{
            'message_id': 'mail-599-1',
            'classification': 'unknown_review_needed',
            'sender': {'email': 'lead@example.com', 'relationship': 'new_or_unknown'},
            'draft': {'action': 'blocked_low_confidence', 'source_message_id': 'mail-599-1', 'notify_only': True},
            'next_step': 'Sprawdź wiadomość ręcznie.',
        }],
    }
    calls = []

    def ambiguous_sender(**kwargs):
        calls.append(kwargs)
        return 599, {'error': 'transport_timeout_or_unavailable'}

    with tempfile.TemporaryDirectory() as tmp:
        state_file = Path(tmp) / 'notification-ledger.json'
        first = notify.deliver_notification(
            summary, recipient=notify.DEFAULT_RECIPIENT, token_file='unused',
            env_file='unused', state_file=state_file, sender=ambiguous_sender,
        )
        repeated = notify.deliver_notification(
            dict(summary, run_id='mail-run-599-replay'),
            recipient=notify.DEFAULT_RECIPIENT, token_file='unused',
            env_file='unused', state_file=state_file, sender=ambiguous_sender,
        )
    assert first['status'] == 'outcome_unknown'
    assert repeated['status'] == 'outcome_unknown'
    assert len(calls) == 1


def test_two_material_versions_of_one_sheet_row_each_notify_once():
    notify = load_module('orchesta_rfq_email_notify_sheet_version_test', NOTIFY)
    calls = []

    def sender(**kwargs):
        calls.append(kwargs)
        return 201, {'data': {'messageId': f'internal-sheet-{len(calls)}'}}

    def summary(digest, run_id):
        return {
            'run_id': run_id,
            'source': 'google_sheets',
            'briefings': [{
                'message_id': 'sheets-row-17',
                'correlation_id': f'sheets:17:{digest}',
                'classification': 'unknown_review_needed',
                'sender': {'email': 'lead@example.com', 'relationship': 'new_or_unknown'},
                'draft': {'action': 'blocked_low_confidence', 'source_message_id': 'sheets-row-17', 'notify_only': True},
                'sheet': {'row': 17, 'status': 'wymaga sprawdzenia'},
                'next_step': 'Sprawdź wiersz ręcznie.',
            }],
        }

    with tempfile.TemporaryDirectory() as tmp:
        state_file = Path(tmp) / 'notification-ledger.json'
        version_one = notify.deliver_notification(
            summary('hash-one', 'sheet-run-1'), recipient=notify.DEFAULT_RECIPIENT,
            token_file='unused', env_file='unused', state_file=state_file, sender=sender,
        )
        replay_one = notify.deliver_notification(
            summary('hash-one', 'sheet-run-2'), recipient=notify.DEFAULT_RECIPIENT,
            token_file='unused', env_file='unused', state_file=state_file, sender=sender,
        )
        version_two = notify.deliver_notification(
            summary('hash-two', 'sheet-run-3'), recipient=notify.DEFAULT_RECIPIENT,
            token_file='unused', env_file='unused', state_file=state_file, sender=sender,
        )
        replay_two = notify.deliver_notification(
            summary('hash-two', 'sheet-run-4'), recipient=notify.DEFAULT_RECIPIENT,
            token_file='unused', env_file='unused', state_file=state_file, sender=sender,
        )
    assert version_one['status'] == 'sent'
    assert replay_one['status'] == 'already_delivered'
    assert version_two['status'] == 'sent'
    assert replay_two['status'] == 'already_delivered'
    assert len(calls) == 2


def test_internal_email_event_identity_is_per_message_not_per_thread():
    notify = load_module('orchesta_rfq_email_notify_thread_identity_test', NOTIFY)
    calls = []

    def sender(**kwargs):
        calls.append(kwargs)
        return 201, {'data': {'messageId': f'internal-{len(calls)}'}}

    def summary(message_id, run_id):
        return {
            'run_id': run_id,
            'source': 'mailbox',
            'briefings': [{
                'message_id': message_id,
                'correlation_id': 'shared-thread-1',
                'classification': 'unknown_review_needed',
                'sender': {'email': 'lead@example.com', 'relationship': 'new_or_unknown'},
                'draft': {'action': 'blocked_low_confidence', 'source_message_id': message_id, 'notify_only': True},
                'next_step': 'Sprawdź wiadomość ręcznie.',
            }],
        }

    with tempfile.TemporaryDirectory() as tmp:
        state_file = Path(tmp) / 'notification-ledger.json'
        one = notify.deliver_notification(
            summary('message-1', 'run-1'), recipient=notify.DEFAULT_RECIPIENT,
            token_file='unused', env_file='unused', state_file=state_file, sender=sender,
        )
        two = notify.deliver_notification(
            summary('message-2', 'run-2'), recipient=notify.DEFAULT_RECIPIENT,
            token_file='unused', env_file='unused', state_file=state_file, sender=sender,
        )
    assert one['status'] == 'sent'
    assert two['status'] == 'sent'
    assert len(calls) == 2


def test_internal_email_contains_every_event_in_large_run():
    notify = load_module('orchesta_rfq_email_notify_large_run_test', NOTIFY)
    briefings = []
    for index in range(12):
        message_id = f'bulk-message-{index + 1}'
        briefings.append({
            'message_id': message_id,
            'classification': 'unknown_review_needed',
            'sender': {'email': f'lead{index + 1}@example.com', 'relationship': 'new_or_unknown'},
            'draft': {'action': 'blocked_low_confidence', 'source_message_id': message_id, 'notify_only': True},
            'next_step': 'Sprawdź wiadomość ręcznie.',
        })
    built = notify.build_notifications({'source': 'mailbox', 'briefings': briefings})
    assert len(built) == 12
    bodies = [item[3] for item in built]
    for index in range(12):
        assert sum(f'lead{index + 1}@example.com' in text for text in bodies) == 1


def test_known_preflight_email_failure_is_retryable():
    notify = load_module('orchesta_rfq_email_notify_preflight_test', NOTIFY)
    summary = {
        'source': 'mailbox',
        'briefings': [{
            'message_id': 'preflight-message-1',
            'classification': 'unknown_review_needed',
            'sender': {'email': 'lead@example.com', 'relationship': 'new_or_unknown'},
            'draft': {'action': 'blocked_low_confidence', 'source_message_id': 'preflight-message-1', 'notify_only': True},
            'next_step': 'Sprawdź wiadomość ręcznie.',
        }],
    }
    calls = []

    def sender(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise notify.DeliveryNotAttemptedError('missing_token_file')
        return 201, {'data': {'messageId': 'internal-after-retry'}}

    with tempfile.TemporaryDirectory() as tmp:
        state_file = Path(tmp) / 'notification-ledger.json'
        first = notify.deliver_notification(
            summary, recipient=notify.DEFAULT_RECIPIENT, token_file='unused',
            env_file='unused', state_file=state_file, sender=sender,
        )
        second = notify.deliver_notification(
            summary, recipient=notify.DEFAULT_RECIPIENT, token_file='unused',
            env_file='unused', state_file=state_file, sender=sender,
        )
    assert first['status'] == 'failed'
    assert second['status'] == 'sent'
    assert len(calls) == 2


def test_pre_action_block_is_known_failure_not_unknown_send_outcome():
    mail = load_module('zoho_mail_pre_action_block_test', MAIL)
    state_mod = load_module('pipeline_state_pre_action_block_test', STATE)
    dataset = {
        'accounts': [{'accountId': 'acc-1', 'primaryEmailAddress': mail.DEFAULT_TARGET_EMAIL}],
        'folders': [
            {'folderId': 'inbox-1', 'folderName': 'Inbox', 'folderType': 'Inbox'},
            {'folderId': 'sent-1', 'folderName': 'Sent', 'folderType': 'Sent'},
        ],
        'messages': [{
            'messageId': 'pre-action-message-1', 'folderId': 'inbox-1',
            'threadId': 'pre-action-thread-1', 'fromAddress': 'Anna <anna@example.com>',
            'subject': 'Zapytanie ofertowe o Orchesta RFQ', 'hasAttachment': '0',
            'receivedTime': '1800000000000',
            '_content': '<p>Proszę o informacje o systemie do obsługi zapytań ofertowych.</p>',
            '_header': 'Message-ID: <pre-action-message-1@example.com>\n',
        }],
    }

    def blocked_sender(*args, **kwargs):
        return {'action': 'blocked', 'status': None, 'error': 'pre_action_claim_lost'}

    with tempfile.TemporaryDirectory() as tmp:
        result = mail.poll(
            mail.FakeZohoClient(dataset),
            state_mod.PipelineState(Path(tmp) / 'mail.sqlite3'),
            mail.load_classifier(), run_id='pre-action-block-run',
            auto_send=True, send_creator=blocked_sender,
        )
    assert result['responses_sent'] == 0
    assert result['send_outcome_unknown'] == 0
    assert result['send_reconcile_pending'] == 0
    assert result['briefings'][0]['draft']['action'] == 'blocked_pre_action_guard'


def test_mail_policy_blocked_final_offer_is_notified_as_final_block_not_unknown():
    mail = load_module('zoho_mail_final_offer_policy_block_test', MAIL)
    state_mod = load_module('pipeline_state_final_offer_policy_block_test', STATE)
    dataset = {
        'accounts': [{'accountId': 'acc-1', 'primaryEmailAddress': mail.DEFAULT_TARGET_EMAIL}],
        'folders': [
            {'folderId': 'inbox-1', 'folderName': 'Inbox', 'folderType': 'Inbox'},
            {'folderId': 'sent-1', 'folderName': 'Sent', 'folderType': 'Sent'},
        ],
        'messages': [{
            'messageId': 'policy-final-message-1', 'folderId': 'inbox-1',
            'threadId': 'policy-final-thread-1', 'fromAddress': 'Anna <anna@example.com>',
            'subject': 'Zapytanie ofertowe o Orchesta RFQ', 'hasAttachment': '0',
            'receivedTime': '1800000000000',
            '_content': '<p>Proszę o informacje o systemie do obsługi zapytań ofertowych.</p>',
            '_header': 'Message-ID: <policy-final-message-1@example.com>\n',
        }],
    }

    def blocked_final_sender(*args, **kwargs):
        return {
            'action': 'blocked',
            'status': None,
            'reason': 'final_offer_autosend_forbidden',
            'draft_id': 'controlled-policy-final-draft-1',
        }

    with tempfile.TemporaryDirectory() as tmp:
        result = mail.poll(
            mail.FakeZohoClient(dataset),
            state_mod.PipelineState(Path(tmp) / 'mail.sqlite3'),
            mail.load_classifier(), run_id='policy-final-block-run',
            auto_send=True, send_creator=blocked_final_sender,
        )
    assert result['responses_sent'] == 0
    assert result['send_outcome_unknown'] == 0
    assert result['final_offer_blocked'] == 1
    assert result['briefings'][0]['draft']['action'] == 'final_offer_blocked'
    assert result['briefings'][0]['reason_code'] == 'final_offer_autosend_forbidden'


def test_single_allowlisted_internal_email_channel_and_complete_telegram_output():
    mail_text = MAIL.read_text(encoding='utf-8')
    assert 'HERMES_OFFER_NOTIFY_EMAIL' not in mail_text
    for wrapper in (MAIL_WRAPPER, SHEETS_WRAPPER):
        text = wrapper.read_text(encoding='utf-8')
        assert '_notifiable_briefing' in text
        assert "briefings', [])[:5]" not in text
        assert 'briefings", [])[:5]' not in text


def test_customer_automation_sends_pre_offer_messages_but_final_offer_stays_draft_only():
    sheets_text = SHEETS.read_text(encoding='utf-8')
    mail_text = MAIL.read_text(encoding='utf-8')
    final_offer_text = (ROOT / 'execution' / 'zoho_reply_draft.py').read_text(encoding='utf-8')
    assert 'zoho_pre_offer_send' in sheets_text
    assert 'zoho_pre_offer_send' in mail_text
    assert '"--auto-send"' in sheets_text
    assert '"--auto-send"' in mail_text
    assert 'cannot enable transport' in sheets_text
    assert 'cannot enable transport' in mail_text
    for wrapper in (MAIL_WRAPPER, SHEETS_WRAPPER):
        text = wrapper.read_text(encoding='utf-8')
        assert 'POLLER_ARGS+=(--auto-send)' not in text
        assert '. "$ENV_FILE"' in text
        assert '--auto-draft' not in text
        assert 'NOTIFY_PENDING_DIR' in text
        assert '--state-file' in text
    # The final-offer transport remains physically separate and draft-only.
    assert '"mode": "draft"' in final_offer_text
    assert 'zoho_pre_offer_send' not in final_offer_text


def test_mail_wrapper_preflights_and_uses_isolated_final_offer_runtime():
    text = MAIL_WRAPPER.read_text(encoding='utf-8')
    assert 'RFQ_PYTHON="${HERMES_RFQ_VENV_PYTHON:-$ROOT/rfq-runtime/.venv/bin/python}"' in text
    assert 'rfq_runtime_preflight.py' in text
    assert '--final-offer-python "$RFQ_PYTHON"' in text


def test_registry_event_is_idempotent_after_restart():
    registry_mod = load_module('unified_lead_registry_test_4', REGISTRY)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'state.sqlite3'
        first = registry_mod.UnifiedLeadRegistry(path)
        one = first.register_event(
            source_type='mail', source_key='msg-1', email='a@example.com', company='A',
            contact_name='Anna', content='Orchesta RFQ', relation='new',
        )
        first.close()
        second = registry_mod.UnifiedLeadRegistry(path)
        two = second.register_event(
            source_type='mail', source_key='msg-1', email='a@example.com', company='A',
            contact_name='Anna', content='Orchesta RFQ', relation='new',
        )
        assert two['deal_id'] == one['deal_id']
        assert two['is_new_event'] is False
        assert second.event_count() == 1


def test_retryable_draft_failure_can_be_claimed_again_without_duplicate():
    registry_mod = load_module('unified_lead_registry_retry_test', REGISTRY)
    with tempfile.TemporaryDirectory() as tmp:
        registry = registry_mod.UnifiedLeadRegistry(Path(tmp) / 'state.sqlite3')
        event = registry.register_event(
            source_type='mail', source_key='msg-retry', email='retry@example.com',
            company='Retry Sp. z o.o.', contact_name='Renata', content='Proszę o ofertę Orchesta RFQ', relation='new',
        )
        assert registry.claim_draft(event['deal_id'], 'discovery', content_hash='v1') is True
        registry.fail_draft(event['deal_id'], 'discovery', error='temporary Zoho timeout')
        repeated = registry.register_event(
            source_type='mail', source_key='msg-retry', email='retry@example.com',
            company='Retry Sp. z o.o.', contact_name='Renata', content='Proszę o ofertę Orchesta RFQ', relation='new',
        )
        assert repeated['requires_review'] is False
        assert registry.claim_draft(event['deal_id'], 'discovery', content_hash='v1') is True
        registry.record_draft(event['deal_id'], 'discovery', draft_id='draft-after-retry', content_hash='v1')
        assert registry.claim_draft(event['deal_id'], 'discovery', content_hash='v1') is False
        assert registry.get_deal(event['deal_id'])['status'] == 'draft_ready'


def test_source_health_alerts_only_after_persistent_failure_and_on_recovery():
    health_mod = load_module('source_health_test', SOURCE_HEALTH)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'health.json'
        assert health_mod.record(path, source='mailbox', ok=False, detail='timeout', threshold=3) == ''
        assert health_mod.record(path, source='mailbox', ok=False, detail='timeout', threshold=3) == ''
        alert = health_mod.record(path, source='mailbox', ok=False, detail='timeout', threshold=3)
        assert '3 kolejne' in alert
        assert 'mailbox' in alert
        recovery = health_mod.record(path, source='mailbox', ok=True, detail='', threshold=3)
        assert 'działa ponownie' in recovery
        assert health_mod.record(path, source='mailbox', ok=True, detail='', threshold=3) == ''


def _single_message_dataset(message: dict) -> dict:
    return {
        'accounts': [{'accountId': 'acc-1', 'primaryEmailAddress': 'rfq-mailbox@example.invalid'}],
        'folders': [
            {'folderId': 'inbox-1', 'folderName': 'Inbox'},
            {'folderId': 'drafts-1', 'folderName': 'Drafts'},
            {'folderId': 'sent-1', 'folderName': 'Sent', 'folderType': 'Sent'},
        ],
        'messages': [{
            'folderId': 'inbox-1', 'threadId': 'thread-test-1', 'hasAttachment': '0',
            'receivedTime': str(int(time.time() * 1000)),
            '_attachmentinfo': [], **message,
        }],
    }




def test_mail_reply_uses_sheet_deal_and_records_final_offer_for_sheet_sync():
    poller = load_module('zoho_mail_poller_offer_registry_test', MAIL)
    registry_mod = load_module('unified_registry_offer_test', REGISTRY)
    state_mod = load_module('pipeline_state_offer_test', STATE)
    dataset = _single_message_dataset({
        'messageId': 'mail-reply-offer-1',
        'fromAddress': 'marta@example.com',
        'subject': 'Re: Pytania do wdrożenia Orchesta RFQ',
        '_content': '<p>Dwa konta pocztowe. CRM tak.</p>',
        '_header': 'Message-ID: <reply-offer-1@example.com>\nIn-Reply-To: <prior@example.com>\nReferences: <prior@example.com>\n',
    })
    with tempfile.TemporaryDirectory() as tmp:
        registry = registry_mod.UnifiedLeadRegistry(Path(tmp) / 'unified.sqlite3')
        initial = registry.register_event(
            source_type='google_sheets', source_key='sheet:main:9', email='marta@example.com',
            company='Marta Tech Sp. z o.o.', contact_name='Marta Wójcik',
            content='Zapytania trafiają przez mail i formularz.', relation='new',
            facts={
                'current_process': 'proces ręczny w Excelu',
                'inquiry_channels': 'both',
                'inquiry_source': 'both',
                'monthly_volume': 40,
            },
            source_metadata={'spreadsheet_id': 'main', 'sheet_name': 'Arkusz1', 'row': 9},
        )
        state = state_mod.PipelineState(Path(tmp) / 'mail.sqlite3')
        captured_history = []

        def final_offer_creator(*args, **kwargs):
            captured_history.append(kwargs.get('thread_history_text', ''))
            return {
                'action': 'created', 'status': 201, 'status_name': 'offer_draft_created',
                'response': {'data': {'messageId': 'offer-draft-live'}},
                'telegram_text': '', 'price_net_display': '4 920 zł',
                'scope_display': '2 konta pocztowe, CRM: tak, źródło: oba',
                'draft_location': 'Zoho Mail > Drafts, wątek: Orchesta RFQ',
                'pdf_attached': True, 'manifest': {'version': 1},
            }

        summary = poller.poll(
            poller.FakeZohoClient(dataset), state, poller.load_classifier(),
            auto_final_offer=True, final_offer_creator=final_offer_creator,
            unified_registry=registry,
        )
        assert summary['final_offer_pdf_created'] == 1
        assert captured_history and 'Marta Tech Sp. z o.o.' in captured_history[0]
        deal = registry.get_deal(initial['deal_id'])
        assert deal['status'] == 'offer_ready'
        assert deal['final_draft_id'] == 'offer-draft-live'
        assert registry.sheet_refs_with_pending_status()[0]['desired_status'] == 'oferta gotowa'
        notice = summary['briefings'][0]['telegram_text']
        assert 'Marta Tech Sp. z o.o.' in notice
        assert 'Marta Wójcik' in notice
        assert '4 920 zł' in notice
        assert 'Zoho Mail > Drafts' in notice


def test_final_offer_without_confirmed_pdf_is_not_recorded_or_announced_ready():
    poller = load_module('zoho_mail_poller_unconfirmed_pdf_test', MAIL)
    registry_mod = load_module('unified_registry_unconfirmed_pdf_test', REGISTRY)
    state_mod = load_module('pipeline_state_unconfirmed_pdf_test', STATE)
    dataset = _single_message_dataset({
        'messageId': 'mail-offer-no-pdf-1',
        'fromAddress': 'ewa@example.com',
        'subject': 'Re: Orchesta RFQ',
        '_content': '<p>Firma Example. Dwa konta pocztowe. CRM tak.</p>',
        '_header': 'Message-ID: <offer-no-pdf@example.com>\nIn-Reply-To: <prior@example.com>\nReferences: <prior@example.com>\n',
    })
    with tempfile.TemporaryDirectory() as tmp:
        registry = registry_mod.UnifiedLeadRegistry(Path(tmp) / 'unified.sqlite3')
        event = registry.register_event(
            source_type='google_sheets', source_key='sheet:main:19', email='ewa@example.com',
            company='Example Sp. z o.o.', contact_name='Ewa', content='Zapytania z maila.', relation='new',
            facts={
                'current_process': 'proces ręczny w Excelu',
                'inquiry_channels': 'mail',
                'inquiry_source': 'mail',
                'monthly_volume': 40,
            },
        )
        state = state_mod.PipelineState(Path(tmp) / 'mail.sqlite3')
        telegram_calls = []

        def final_offer_creator(*args, **kwargs):
            return {
                'action': 'created', 'status': 201, 'status_name': 'offer_draft_created',
                'response': {'data': {'messageId': 'draft-without-confirmed-pdf'}},
                'price_net_display': '4 920 zł', 'scope_display': '2 konta, CRM',
                'draft_location': 'Zoho Mail > Drafts', 'pdf_attached': False,
                'manifest': {'version': 1},
            }

        summary = poller.poll(
            poller.FakeZohoClient(dataset), state, poller.load_classifier(),
            auto_final_offer=True, final_offer_creator=final_offer_creator,
            unified_registry=registry, send_telegram=lambda text: telegram_calls.append(text) or True,
        )
        assert summary['final_offer_notifications_ready'] == 0
        assert summary['final_offer_failed'] == 1
        assert registry.get_deal(event['deal_id'])['status'] != 'offer_ready'
        assert all('Oferta Orchesta RFQ gotowa' not in text for text in telegram_calls)


class _FakeExecute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _FakeSheetValues:
    def __init__(self, rows):
        self.rows = rows
        self.updates = []

    def get(self, **kwargs):
        return _FakeExecute({'values': self.rows})

    def update(self, **kwargs):
        values = kwargs['body']['values']
        target = kwargs['range'].split('!', 1)[-1]
        self.updates.append((target, values))
        start = target.split(':', 1)[0]
        letters = ''.join(ch for ch in start if ch.isalpha()).upper()
        row_number = int(''.join(ch for ch in start if ch.isdigit()) or '1')
        col_index = 0
        for char in letters:
            col_index = col_index * 26 + (ord(char) - ord('A') + 1)
        col_index -= 1
        while len(self.rows) < row_number:
            self.rows.append([])
        target_row = self.rows[row_number - 1]
        required = col_index + len(values[0])
        if len(target_row) < required:
            target_row.extend([''] * (required - len(target_row)))
        target_row[col_index:required] = list(values[0])
        return _FakeExecute({'updatedRange': kwargs['range']})


class _FakeSheetsService:
    def __init__(self, rows):
        self.sheet_values = _FakeSheetValues(rows)

    def spreadsheets(self):
        return self

    def values(self):
        return self.sheet_values


class _FakeDriveService:
    def files(self):
        return self

    def get(self, **kwargs):
        return _FakeExecute({'id': kwargs.get('fileId'), 'name': 'Test Leads', 'webViewLink': 'https://example.invalid/sheet', 'capabilities': {'canEdit': True}})


def test_invalid_google_sheet_email_is_review_only_and_never_sent():
    sheets = load_module('google_sheets_invalid_test', SHEETS_MODULE)
    headers = list(sheets.REQUIRED_HEADERS)
    row_map = {
        'Data': '2026-07-28', 'Imię': 'Błędny Kontakt', 'Email': 'not-an-email',
        'Firma': 'Sprzeczna Firma', 'Źródło': 'Meta Ads',
        'Wiadomość': 'Proszę o ofertę Orchesta RFQ dla dwóch skrzynek i CRM.',
    }
    row = [row_map.get(name, '') for name in headers]
    service = _FakeSheetsService([headers, row])
    with tempfile.TemporaryDirectory() as tmp:
        called = []
        original = getattr(sheets, 'send_sheet_zoho_response')
        original_loader = getattr(sheets, 'load_google_services')
        setattr(sheets, 'send_sheet_zoho_response', lambda *a, **kw: called.append(True))
        setattr(sheets, 'load_google_services', lambda token_file: (service, _FakeDriveService()))
        try:
            args = Namespace(
                spreadsheet_id='sheet-test', sheet_name='Arkusz1',
                state_file=str(Path(tmp) / 'state.json'), registry_file=str(Path(tmp) / 'unified.sqlite3'),
                process_test_status=False, process_test_records=False, auto_send=True, auto_draft=False, dry_run=False,
                token_file=str(Path(tmp) / 'google-token.json'),
                zoho_token_file=str(Path(tmp) / 'token.json'), env_file=str(Path(tmp) / '.env'),
                summary_json=str(Path(tmp) / 'summary.json'),
            )
            summary = sheets.process(args)
        finally:
            setattr(sheets, 'send_sheet_zoho_response', original)
            setattr(sheets, 'load_google_services', original_loader)
        assert called == []
        assert summary['woken'] == 1
        assert summary['drafts_created'] == 0
        assert summary['briefings'][0]['sheet']['status'] == 'wymaga sprawdzenia'
        assert 'niepoprawny' in summary['briefings'][0]['telegram_text'].lower()
        status_index = headers.index('Hermes status')
        assert service.sheet_values.rows[1][status_index] == 'wymaga sprawdzenia'


def test_google_sheet_lead_sends_once_and_persists_sent_metadata():
    sheets = load_module('google_sheets_idempotency_test', SHEETS_MODULE)
    headers = list(sheets.REQUIRED_HEADERS)
    row_map = {
        'Data': '2026-07-28', 'Imię': 'Anna Example', 'Email': 'anna.sheet@example.com',
        'Firma': 'Example Automatyka Sp. z o.o.', 'Źródło': 'Meta Ads',
        'Wiadomość': 'Proszę o ofertę Orchesta RFQ. Zapytania są z maila i formularza. Mamy 2 konta pocztowe i używamy CRM.',
    }
    service = _FakeSheetsService([headers, [row_map.get(name, '') for name in headers]])
    with tempfile.TemporaryDirectory() as tmp:
        calls = []
        original_send = getattr(sheets, 'send_sheet_zoho_response')
        original_loader = getattr(sheets, 'load_google_services')

        def send_response(*args, **kwargs):
            calls.append(kwargs)
            return {
                'action': 'sent', 'status': 200,
                'sent_id': 'sheet-sent-one', 'external_message_id': 'sheet-sent-one',
                'account_id': 'account-1', 'thread_id': 'sheet-sent-one',
                'rfc_message_id': '<sheet-sent-one@example.com>', 'body_text': 'Discovery reply',
            }

        setattr(sheets, 'send_sheet_zoho_response', send_response)
        setattr(sheets, 'load_google_services', lambda token_file: (service, _FakeDriveService()))
        try:
            args = Namespace(
                spreadsheet_id='sheet-test', sheet_name='Arkusz1',
                state_file=str(Path(tmp) / 'state.json'), registry_file=str(Path(tmp) / 'unified.sqlite3'),
                process_test_status=False, auto_send=True, auto_draft=False, dry_run=False,
                token_file=str(Path(tmp) / 'google-token.json'),
                zoho_token_file=str(Path(tmp) / 'zoho-token.json'), env_file=str(Path(tmp) / '.env'),
            )
            first = sheets.process(args)
            second = sheets.process(args)
        finally:
            setattr(sheets, 'send_sheet_zoho_response', original_send)
            setattr(sheets, 'load_google_services', original_loader)
        assert first['responses_sent'] == 1
        assert first['drafts_created'] == 0
        assert first['briefings'][0]['sheet']['status'] == 'oczekuje na klienta'
        assert first['briefings'][0]['message']['external_message_id'] == 'sheet-sent-one'
        assert second['responses_sent'] == 0
        assert second['woken'] == 0
        assert len(calls) == 1
        assert service.sheet_values.rows[1][headers.index('Hermes draft id')] == ''
        assert service.sheet_values.rows[1][headers.index('Hermes sent id')] == 'sheet-sent-one'
        registry = sheets.UnifiedLeadRegistry(Path(tmp) / 'unified.sqlite3')
        deal = registry.connection.execute("SELECT * FROM unified_deals").fetchone()
        assert deal['status'] == 'waiting_for_customer'
        assert deal['last_response_id'] == 'sheet-sent-one'


def test_google_sheet_policy_blocked_final_offer_is_a_draft_not_outcome_unknown():
    sheets = load_module('google_sheets_final_offer_block_test', SHEETS_MODULE)
    headers = list(sheets.REQUIRED_HEADERS)
    row_map = {
        'Data': '2026-08-06', 'Imię': 'Anna Final', 'Email': 'anna.final@example.com',
        'Firma': 'Final Example Sp. z o.o.', 'Źródło': 'Meta Ads',
        'Wiadomość': 'Proszę o ofertę Orchesta RFQ dla dwóch skrzynek i CRM.',
    }
    service = _FakeSheetsService([headers, [row_map.get(name, '') for name in headers]])
    with tempfile.TemporaryDirectory() as tmp:
        original_send = sheets.send_sheet_zoho_response
        original_loader = sheets.load_google_services
        sheets.send_sheet_zoho_response = lambda *args, **kwargs: {
            'action': 'blocked',
            'reason': 'final_offer_autosend_forbidden',
            'draft_id': 'controlled-final-draft-1',
        }
        sheets.load_google_services = lambda token_file: (service, _FakeDriveService())
        try:
            args = Namespace(
                spreadsheet_id='sheet-test', sheet_name='Arkusz1',
                state_file=str(Path(tmp) / 'state.json'), registry_file=str(Path(tmp) / 'unified.sqlite3'),
                process_test_status=False, auto_send=True, auto_draft=False, dry_run=False,
                token_file=str(Path(tmp) / 'google-token.json'),
                zoho_token_file=str(Path(tmp) / 'zoho-token.json'), env_file=str(Path(tmp) / '.env'),
            )
            summary = sheets.process(args)
        finally:
            sheets.send_sheet_zoho_response = original_send
            sheets.load_google_services = original_loader

        assert summary['responses_sent'] == 0
        assert summary['send_outcome_unknown'] == 0
        assert summary['final_offer_drafts_created'] == 1
        assert summary['briefings'][0]['sheet']['status'] == 'wymaga sprawdzenia'
        assert 'utworzyłem draft' in summary['briefings'][0]['next_step'].lower()
        assert summary['briefings'][0]['draft']['action'] == 'created'
        assert summary['briefings'][0]['draft']['external_draft_id'] == 'controlled-final-draft-1'
        assert service.sheet_values.rows[1][headers.index('Hermes draft id')] == 'controlled-final-draft-1'
        assert service.sheet_values.rows[1][headers.index('Hermes sent id')] == ''


def test_google_sheet_dry_run_never_calls_sender():
    sheets = load_module('google_sheets_dry_run_test', SHEETS_MODULE)
    headers = list(sheets.REQUIRED_HEADERS)
    row_map = {
        'Data': '2026-07-29', 'Imię': 'Dry Run', 'Email': 'dry.run@example.com',
        'Firma': 'Dry Run Sp. z o.o.', 'Źródło': 'Meta Ads',
        'Wiadomość': 'Proszę o ofertę Orchesta RFQ dla maila i formularza.',
    }
    service = _FakeSheetsService([headers, [row_map.get(name, '') for name in headers]])
    with tempfile.TemporaryDirectory() as tmp:
        calls = []
        original_send = sheets.send_sheet_zoho_response
        original_loader = sheets.load_google_services
        setattr(sheets, 'send_sheet_zoho_response', lambda *a, **kw: calls.append(True))
        setattr(sheets, 'load_google_services', lambda token_file: (service, _FakeDriveService()))
        try:
            args = Namespace(
                spreadsheet_id='sheet-test', sheet_name='Arkusz1',
                state_file=str(Path(tmp) / 'state.json'), registry_file=str(Path(tmp) / 'unified.sqlite3'),
                process_test_status=False, auto_send=True, auto_draft=False, dry_run=True,
                token_file=str(Path(tmp) / 'google-token.json'),
                zoho_token_file=str(Path(tmp) / 'zoho-token.json'), env_file=str(Path(tmp) / '.env'),
            )
            summary = sheets.process(args)
        finally:
            setattr(sheets, 'send_sheet_zoho_response', original_send)
            setattr(sheets, 'load_google_services', original_loader)
        assert calls == []
        assert summary['responses_sent'] == 0
        assert summary['sheet_rows_updated'] == 0


def test_controlled_sheet_run_leaves_every_other_row_untouched():
    sheets = load_module('google_sheets_controlled_row_test', SHEETS_MODULE)
    headers = list(sheets.REQUIRED_HEADERS)
    other = {
        'Data': '2026-08-04', 'Imię': 'Realny Inny Rekord', 'Email': 'untouched@example.com',
        'Firma': 'Nietknięta Firma', 'Źródło': 'Meta Ads',
        'Wiadomość': 'Proszę o kontakt w innej sprawie.',
    }
    controlled = {
        'Data': '2026-08-04', 'Imię': 'Test Kontrolowany', 'Email': 'controlled+rfq@example.com',
        'Firma': 'Testowa Fabryka RFQ', 'Źródło': 'Test kontrolowany',
        'Wiadomość': '[TEST HERMES] HERMES-E2E-20260804-ROW3 Proszę o ofertę Orchesta RFQ dla dwóch skrzynek i CRM.',
        'Hermes status': 'test_created',
    }
    other_row = [other.get(name, '') for name in headers]
    controlled_row = [controlled.get(name, '') for name in headers]
    service = _FakeSheetsService([headers, other_row, controlled_row])
    untouched_before = list(other_row)
    with tempfile.TemporaryDirectory() as tmp:
        calls = []
        original_send = sheets.send_sheet_zoho_response
        original_loader = sheets.load_google_services
        original_telegram = sheets.send_telegram_notification

        def send_response(*args, **kwargs):
            calls.append(kwargs)
            return {'action': 'sent', 'status': 200, 'sent_id': 'controlled-sent-1', 'external_message_id': 'controlled-sent-1'}

        sheets.send_sheet_zoho_response = send_response
        sheets.load_google_services = lambda token_file: (service, _FakeDriveService())
        sheets.send_telegram_notification = lambda _text: False
        try:
            args = Namespace(
                spreadsheet_id='sheet-test', sheet_name='Arkusz1',
                state_file=str(Path(tmp) / 'state.json'), registry_file=str(Path(tmp) / 'unified.sqlite3'),
                process_test_status=True, auto_send=True, auto_draft=False, dry_run=False,
                token_file=str(Path(tmp) / 'google-token.json'),
                zoho_token_file=str(Path(tmp) / 'zoho-token.json'), env_file=str(Path(tmp) / '.env'),
                controlled_row_number=3, controlled_email='controlled+rfq@example.com',
                controlled_test_id='HERMES-E2E-20260804-ROW3', controlled_expected_status='test_created',
            )
            summary = sheets.process(args)
        finally:
            sheets.send_sheet_zoho_response = original_send
            sheets.load_google_services = original_loader
            sheets.send_telegram_notification = original_telegram

        assert summary['woken'] == 1
        assert summary['responses_sent'] == 1
        assert summary['controlled_selection']['unselected_rows_untouched'] == 1
        assert len(calls) == 1
        assert service.sheet_values.rows[1] == untouched_before
        assert all(target.endswith('3') for target, _values in service.sheet_values.updates)
        registry = sheets.UnifiedLeadRegistry(Path(tmp) / 'unified.sqlite3')
        assert registry.connection.execute('SELECT COUNT(*) FROM unified_deals').fetchone()[0] == 1
