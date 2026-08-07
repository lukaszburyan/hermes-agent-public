from __future__ import annotations

import hashlib
import json
import multiprocessing
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

from unified_lead_registry import UnifiedLeadRegistry  # noqa: E402
from send_reconciliation import reconcile_sent_rows  # noqa: E402
from zoho_pre_offer_send import (  # noqa: E402
    APPROVAL_PHRASE,
    build_pre_offer_payload,
    create_pre_offer_message,
)


def _claim_worker(args: tuple[str, str, int]) -> bool:
    database, deal_id, index = args
    registry = UnifiedLeadRegistry(database)
    try:
        return registry.claim_response(
            deal_id,
            "response:concurrency",
            content_hash="same-content",
            operation_id="operation-concurrency",
            owner=f"worker-{index}",
            lease_seconds=60,
            message_type="clarification_request",
            recipient="client@example.com",
            source_type="mail",
            source_key="mail-concurrency",
            thread_id="thread-concurrency",
            marker="marker-concurrency",
        )
    finally:
        registry.close()


def _open_registry_worker(database: str) -> int:
    registry = UnifiedLeadRegistry(database)
    try:
        return registry.schema_version
    finally:
        registry.close()


def _process_context():
    methods = multiprocessing.get_all_start_methods()
    return multiprocessing.get_context("fork" if "fork" in methods else "spawn")


def _seed(database: Path) -> tuple[UnifiedLeadRegistry, dict, dict]:
    registry = UnifiedLeadRegistry(database)
    event = registry.register_event(
        source_type="mail",
        source_key="mail-concurrency",
        email="client@example.com",
        company="Example",
        contact_name="Anna",
        content="Zapytanie RFQ",
        relation="reply",
        thread_id="thread-concurrency",
        source_metadata={
            "provider": "zoho",
            "account_id": "acc-1",
            "source_message_id": "source-concurrency",
        },
    )
    registry.persist_message_policy(
        "mail",
        "mail-concurrency",
        requested_type="clarification_request",
        effective_type="clarification_request",
        transport_mode="auto_send",
        reasons=["test"],
    )
    context = {
        "registry": registry,
        "source_type": "mail",
        "source_key": "mail-concurrency",
        "deal_id": event["deal_id"],
        "thread_id": "thread-concurrency",
        "operation_id": "operation-concurrency",
        "process_stage": "response:concurrency",
    }
    return registry, event, context


@pytest.mark.parametrize("workers", [2, 10, 50])
def test_exactly_one_process_claims_the_same_response(tmp_path: Path, workers: int):
    database = tmp_path / f"concurrency-{workers}.sqlite3"
    registry, event, _context = _seed(database)
    registry.close()
    ctx = _process_context()
    with ctx.Pool(processes=workers) as pool:
        results = pool.map(
            _claim_worker,
            [(str(database), event["deal_id"], index) for index in range(workers)],
        )
    assert results.count(True) == 1
    reloaded = UnifiedLeadRegistry(database)
    operation = reloaded.outbox_operation("operation-concurrency")
    assert operation is not None
    assert operation["status"] == "claimed"
    assert operation["message_type"] == "clarification_request"
    assert operation["recipient"] == "client@example.com"
    assert operation["contact_identity"] == "email:client@example.com"
    assert operation["source_type"] == "mail"
    assert operation["source_key"] == "mail-concurrency"
    reloaded.close()


def test_two_processes_apply_each_numbered_migration_once(tmp_path: Path):
    database = tmp_path / "migrations.sqlite3"
    legacy = sqlite3.connect(database)
    legacy.execute(
        "CREATE TABLE unified_artifacts ("
        "deal_id TEXT NOT NULL,stage TEXT NOT NULL,status TEXT NOT NULL,content_hash TEXT,"
        "external_draft_id TEXT,external_message_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
        "PRIMARY KEY(deal_id,stage))"
    )
    legacy.commit()
    legacy.close()
    ctx = _process_context()
    with ctx.Pool(processes=2) as pool:
        versions = pool.map(_open_registry_worker, [str(database), str(database)])
    assert versions == [3, 3]
    connection = sqlite3.connect(database)
    rows = connection.execute(
        "SELECT version,name FROM schema_versions WHERE namespace='unified_lead_registry'"
    ).fetchall()
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(unified_artifacts)").fetchall()
    }
    outbox_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='unified_outbox'"
    ).fetchone()
    connection.close()
    assert rows == [
        (1, "durable_outbox_and_claim_metadata"),
        (2, "auditable_security_event_resolution"),
        (3, "durable_contact_identity"),
    ]
    assert {"operation_id", "message_type", "recipient", "contact_identity", "outcome"} <= columns
    assert outbox_exists == (1,)


def test_content_variant_cannot_claim_the_same_event_stage(tmp_path: Path):
    registry, event, _context = _seed(tmp_path / "content-variant.sqlite3")
    assert _claim_worker((str(registry.path), event["deal_id"], 1)) is True
    assert registry.claim_response(
        event["deal_id"],
        "response:concurrency",
        content_hash="different-input",
        operation_id="operation-variant",
        owner="variant-worker",
        message_type="clarification_request",
        recipient="client@example.com",
        source_type="mail",
        source_key="mail-concurrency",
        thread_id="thread-concurrency",
    ) is False
    count = registry.connection.execute("SELECT COUNT(*) FROM unified_outbox").fetchone()[0]
    assert count == 1
    registry.close()


def test_timeout_after_transport_acceptance_stays_outcome_unknown_and_cannot_reclaim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    class TimeoutPoster:
        def send_reply(self, *_args, **_kwargs):
            return 599, {"error": "transport_timeout_or_unavailable"}

    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    registry, event, context = _seed(tmp_path / "timeout.sqlite3")
    assert _claim_worker((str(registry.path), event["deal_id"], 1)) is True
    payload = build_pre_offer_payload(
        account_email="rfq-mailbox@example.invalid",
        to_address="client@example.com",
        inbound_subject="Zapytanie RFQ",
        body_text="Proszę o doprecyzowanie liczby skrzynek.",
        message_kind="clarification_request",
        threaded=True,
    )
    result = create_pre_offer_message(
        "acc-1",
        source_message_id="source-concurrency",
        payload=payload,
        poster=TimeoutPoster(),
        approval=APPROVAL_PHRASE,
        message_kind="clarification_request",
        threaded=True,
        durable_context=context,
    )
    assert result["action"] == "error"
    assert registry.outbox_operation("operation-concurrency")["status"] == "outcome_unknown"
    assert registry.unresolved_outcome_unknown()
    assert _claim_worker((str(registry.path), event["deal_id"], 2)) is False
    registry.close()


def test_transport_exception_after_post_starts_is_outcome_unknown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    class RaisingPoster:
        def send_reply(self, *_args, **_kwargs):
            raise TimeoutError("connection_lost_after_request")

    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    registry, event, context = _seed(tmp_path / "exception.sqlite3")
    assert _claim_worker((str(registry.path), event["deal_id"], 1)) is True
    payload = build_pre_offer_payload(
        account_email="rfq-mailbox@example.invalid",
        to_address="client@example.com",
        inbound_subject="Zapytanie RFQ",
        body_text="Proszę o doprecyzowanie liczby skrzynek.",
        message_kind="clarification_request",
        threaded=True,
    )
    result = create_pre_offer_message(
        "acc-1",
        source_message_id="source-concurrency",
        payload=payload,
        poster=RaisingPoster(),
        approval=APPROVAL_PHRASE,
        message_kind="clarification_request",
        threaded=True,
        durable_context=context,
    )
    operation = registry.outbox_operation("operation-concurrency")
    expected_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert result["outcome_unknown"] is True
    assert operation["status"] == "outcome_unknown"
    assert operation["content_hash"] == expected_hash
    assert registry.security_events("send_outcome_unknown")
    registry.close()


def test_timeout_after_acceptance_reconciles_exact_marker_without_second_send(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    class AcceptedThenTimeoutPoster:
        def __init__(self):
            self.calls = 0

        def send_reply(self, *_args, **_kwargs):
            self.calls += 1
            return 599, {"error": "connection_lost_after_acceptance"}

    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    registry, event, context = _seed(tmp_path / "accepted-timeout.sqlite3")
    assert _claim_worker((str(registry.path), event["deal_id"], 1)) is True
    payload = build_pre_offer_payload(
        account_email="rfq-mailbox@example.invalid",
        to_address="client@example.com",
        inbound_subject="Zapytanie RFQ",
        body_text="Proszę o doprecyzowanie liczby skrzynek.",
        message_kind="clarification_request",
        threaded=True,
    )
    poster = AcceptedThenTimeoutPoster()
    result = create_pre_offer_message(
        "acc-1",
        source_message_id="source-concurrency",
        payload=payload,
        poster=poster,
        approval=APPROVAL_PHRASE,
        message_kind="clarification_request",
        threaded=True,
        durable_context=context,
    )
    operation = registry.outbox_operation("operation-concurrency")
    assert result["action"] == "error"
    assert operation is not None and operation["status"] == "outcome_unknown"
    assert poster.calls == 1

    reconciliation = reconcile_sent_rows(
        operation,
        marker=operation["marker"],
        sent_rows=[{
            "messageId": "zoho-accepted-controlled-1",
            "content": f"Sent once <!-- hermes-send-marker:{operation['marker']} -->",
        }],
        row_matches=lambda _row: True,
        row_id=lambda row: str(row["messageId"]),
    )
    assert reconciliation == {
        "resolved": True,
        "status": "matched",
        "retryable": False,
        "external_message_id": "zoho-accepted-controlled-1",
        "age_seconds": reconciliation["age_seconds"],
    }
    registry.record_response(
        event["deal_id"],
        "response:concurrency",
        message_id=reconciliation["external_message_id"],
        content_hash="ignored-after-durable-binding",
        operation_id="operation-concurrency",
    )
    assert registry.outbox_operation("operation-concurrency")["status"] == "sent"
    assert _claim_worker((str(registry.path), event["deal_id"], 2)) is False

    restarted = create_pre_offer_message(
        "acc-1",
        source_message_id="source-concurrency",
        payload=payload,
        poster=poster,
        approval=APPROVAL_PHRASE,
        message_kind="clarification_request",
        threaded=True,
        durable_context=context,
    )
    assert restarted["action"] == "blocked"
    assert poster.calls == 1
    registry.close()
