#!/usr/bin/env python3
"""Offline 10x-peak load and idempotency test for the durable RFQ transport."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from message_policy import AUTO_SEND_TYPES, FINAL_OFFER
from unified_lead_registry import OUTBOX_TERMINAL_STATUSES, UnifiedLeadRegistry
from zoho_pre_offer_send import APPROVAL_PHRASE, build_pre_offer_payload, create_pre_offer_message


EXPECTED_PEAK_EVENTS_PER_TICK = 50


class ControlledPoster:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[str] = []

    def send_reply(self, _account_id: str, source_message_id: str, _payload: dict[str, Any]):
        with self._lock:
            self.calls.append(source_message_id)
        return 201, {"data": {"messageId": f"controlled-sent-{source_message_id}"}}


@contextmanager
def controlled_transport_environment(recipients: list[str]) -> Iterator[None]:
    values = {
        "HERMES_ALLOW_PRE_OFFER_SEND": "1",
        "HERMES_TRANSPORT_KILL_SWITCH": "0",
        "HERMES_TEST_MODE": "1",
        "HERMES_TEST_RECIPIENT_ALLOWLIST": ",".join(recipients),
    }
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def seed_events(database: Path, event_count: int, *, run_id: str) -> list[dict[str, str]]:
    registry = UnifiedLeadRegistry(database)
    events: list[dict[str, str]] = []
    operational_types = sorted(AUTO_SEND_TYPES)
    operational_index = 0
    try:
        for index in range(event_count):
            if index % 5 == 0:
                message_type = FINAL_OFFER
            else:
                message_type = operational_types[operational_index % len(operational_types)]
                operational_index += 1
            source_key = f"{run_id}-event-{index:05d}"
            recipient = f"controlled-load-{index:05d}@example.com"
            event = registry.register_event(
                source_type="mail",
                source_key=source_key,
                email=recipient,
                company=f"Controlled Load {index:05d}",
                contact_name="Test",
                content="Controlled offline load test",
                relation="reply",
                thread_id=f"load-thread-{index:05d}",
                source_metadata={
                    "provider": "zoho",
                    "account_id": "controlled-account",
                    "source_message_id": f"load-source-{index:05d}",
                },
            )
            registry.persist_message_policy(
                "mail",
                source_key,
                requested_type=message_type,
                effective_type=message_type,
                transport_mode="draft_only" if message_type == FINAL_OFFER else "auto_send",
                reasons=["controlled_offline_load_test"],
            )
            events.append(
                {
                    "deal_id": event["deal_id"],
                    "source_key": source_key,
                    "recipient": recipient,
                    "message_type": message_type,
                    "thread_id": f"load-thread-{index:05d}",
                    "source_message_id": f"load-source-{index:05d}",
                    "operation_id": f"{run_id}-operation-{index:05d}",
                }
            )
    finally:
        registry.close()
    return events


def _safe_operational_body(message_type: str) -> str:
    return {
        "acknowledgement": "Dziękuję za wiadomość. Potwierdzam jej otrzymanie.",
        "clarification_request": "Proszę o doprecyzowanie liczby skrzynek objętych procesem.",
        "missing_data_request": "Do przygotowania materiału potrzebuję informacji o używanym CRM.",
        "follow_up": "Wracam do wiadomości i proszę o brakującą informację o zakresie.",
        "ready_for_offer_notice": "Dane są kompletne. Przekazuję sprawę do przygotowania oferty.",
    }[message_type]


def process_event(database: Path, event: dict[str, str], poster: ControlledPoster) -> dict[str, Any]:
    registry = UnifiedLeadRegistry(database)
    try:
        stage = f"response:{event['source_key']}"
        claimed = registry.claim_response(
            event["deal_id"],
            stage,
            content_hash=f"input-{event['source_key']}",
            operation_id=event["operation_id"],
            owner=f"load-worker:{threading.get_ident()}",
            message_type=event["message_type"],
            recipient=event["recipient"],
            source_type="mail",
            source_key=event["source_key"],
            thread_id=event["thread_id"],
            marker=f"load-marker-{event['source_key']}",
        )
        if not claimed:
            return {"action": "duplicate"}
        context = {
            "registry": registry,
            "source_type": "mail",
            "source_key": event["source_key"],
            "deal_id": event["deal_id"],
            "thread_id": event["thread_id"],
            "operation_id": event["operation_id"],
            "process_stage": stage,
        }
        if event["message_type"] == FINAL_OFFER:
            payload = {
                "fromAddress": "rfq-mailbox@example.invalid",
                "toAddress": event["recipient"],
                "subject": "Finalna oferta — kontrolowany test",
                "content": "Końcowa cena 7 200 PLN. Zakres: wdrożenie. Termin 14 dni. Płatność 50/50.",
                "mailFormat": "html",
                "action": "reply",
                "attachments": [
                    {"attachmentName": "controlled-offer.pdf", "contentType": "application/pdf"}
                ],
            }
            return create_pre_offer_message(
                "controlled-account",
                source_message_id=event["source_message_id"],
                payload=payload,
                poster=poster,
                approval=APPROVAL_PHRASE,
                message_kind=FINAL_OFFER,
                threaded=True,
                durable_context=context,
                draft_fallback=lambda **_kwargs: {
                    "action": "created",
                    "draft_id": f"controlled-draft-{event['source_key']}",
                },
                human_notifier=lambda _event: None,
            )
        payload = build_pre_offer_payload(
            account_email="rfq-mailbox@example.invalid",
            to_address=event["recipient"],
            inbound_subject="Kontrolowany test obciążenia",
            body_text=_safe_operational_body(event["message_type"]),
            message_kind=event["message_type"],
            threaded=True,
        )
        return create_pre_offer_message(
            "controlled-account",
            source_message_id=event["source_message_id"],
            payload=payload,
            poster=poster,
            approval=APPROVAL_PHRASE,
            message_kind=event["message_type"],
            threaded=True,
            durable_context=context,
        )
    finally:
        registry.close()


def run_load_test(*, event_count: int, workers: int, database: Path) -> dict[str, Any]:
    run_id = f"load-{os.getpid()}-{time.time_ns()}"
    events = seed_events(database, event_count, run_id=run_id)
    recipients = [event["recipient"] for event in events]
    poster = ControlledPoster()
    errors: list[str] = []
    sqlite_lock_errors = 0
    tracemalloc.start()
    started = time.perf_counter()
    with controlled_transport_environment(recipients):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(process_event, database, event, poster) for event in events]
            results = []
            for future in futures:
                try:
                    results.append(future.result())
                except sqlite3.OperationalError as exc:
                    sqlite_lock_errors += int("lock" in str(exc).lower())
                    errors.append(f"OperationalError:{str(exc)[:160]}")
                except Exception as exc:
                    errors.append(f"{exc.__class__.__name__}:{str(exc)[:160]}")
        first_duration = time.perf_counter() - started
        poster_calls_after_first = len(poster.calls)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            duplicate_results = list(pool.map(lambda event: process_event(database, event, poster), events))
    _current_memory, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    connection = sqlite3.connect(database)
    rows = connection.execute(
        "SELECT status,message_type,COUNT(*) FROM unified_outbox "
        "WHERE operation_id LIKE ? GROUP BY status,message_type",
        (f"{run_id}-operation-%",),
    ).fetchall()
    final_offer_sent = connection.execute(
        "SELECT COUNT(*) FROM unified_outbox "
        "WHERE operation_id LIKE ? AND status='sent' AND message_type='final_offer'",
        (f"{run_id}-operation-%",),
    ).fetchone()[0]
    connection.close()
    by_status_type = {f"{status}:{message_type}": count for status, message_type, count in rows}
    terminal = sum(count for status, _message_type, count in rows if status in OUTBOX_TERMINAL_STATUSES)
    queue_length = event_count - terminal
    expected_final_drafts = sum(event["message_type"] == FINAL_OFFER for event in events)
    expected_operational = event_count - expected_final_drafts
    duplicate_count = sum(result.get("action") == "duplicate" for result in duplicate_results)
    external_duplicates = len(poster.calls) - len(set(poster.calls))
    report = {
        "status": "ok",
        "mode": "offline_controlled_no_network",
        "run_id": run_id,
        "expected_peak_events_per_tick": EXPECTED_PEAK_EVENTS_PER_TICK,
        "load_multiplier": round(event_count / EXPECTED_PEAK_EVENTS_PER_TICK, 2),
        "events": event_count,
        "workers": workers,
        "duration_seconds": round(first_duration, 4),
        "events_per_second": round(event_count / max(first_duration, 0.000001), 2),
        "peak_python_memory_bytes": peak_memory,
        "queue_length_after_run": queue_length,
        "sqlite_lock_errors": sqlite_lock_errors,
        "errors": errors,
        "operational_expected": expected_operational,
        "operational_sent": poster_calls_after_first,
        "final_offer_drafts_expected": expected_final_drafts,
        "final_offer_drafts_created": int(by_status_type.get("draft_created:final_offer", 0)),
        "final_offer_sent": int(final_offer_sent),
        "duplicate_replays_blocked": duplicate_count,
        "duplicate_external_sends": external_duplicates,
        "outbox_by_status_type": by_status_type,
        "first_result_actions": {
            action: sum(result.get("action") == action for result in results)
            for action in sorted({str(result.get("action")) for result in results})
        },
    }
    checks = (
        not errors,
        sqlite_lock_errors == 0,
        queue_length == 0,
        poster_calls_after_first == expected_operational,
        report["final_offer_drafts_created"] == expected_final_drafts,
        final_offer_sent == 0,
        duplicate_count == event_count,
        external_duplicates == 0,
        len(poster.calls) == poster_calls_after_first,
    )
    if not all(checks):
        report["status"] = "error"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=EXPECTED_PEAK_EVENTS_PER_TICK * 10)
    parser.add_argument("--workers", type=int, default=50)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.events < 1 or args.workers < 1:
        raise SystemExit("events and workers must be positive")
    if args.database:
        report = run_load_test(event_count=args.events, workers=args.workers, database=args.database)
    else:
        with tempfile.TemporaryDirectory(prefix="hermes-load-test-") as temporary:
            report = run_load_test(
                event_count=args.events,
                workers=args.workers,
                database=Path(temporary) / "registry.sqlite3",
            )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
