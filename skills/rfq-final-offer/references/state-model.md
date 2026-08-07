# Model stanów i operacji

Stan jest utrzymywany w SQLite (`HERMES_RFQ_STATE_FILE`, domyślnie `.tmp/hermes-rfq-state/state.sqlite3`). `processed_messages.json` nie jest źródłem prawdy.

## Stany domenowe

```text
message: received -> precheck_skipped | analysis_pending -> identity_resolved -> classified -> safety_passed | blocked -> action_planned -> done
draft: not_needed | planned -> creating -> created -> update_pending -> updated | human_edited | retryable_failed | permanent_failed
offer: not_applicable | waiting_for_data | waiting_for_review -> generating -> generated -> attachment_pending -> attached | superseded | sent_manually
crm: not_needed | lookup_pending -> matched | new_record_pending -> written | deferred_offline | conflict | failed
```

Każde przejście zapisuje `previous_status`, `new_status`, `reason_code`, `timestamp` i `run_id` w `state_transitions`. Po restarcie poller wznawia stan, który nie jest potwierdzonym `done`/`succeeded`.

## Operacje

Tabela `operations` ma unikalny klucz `(message_id, action_type)` i status `planned`, `in_progress`, `succeeded`, `retryable_failed` albo `permanent_failed`. Przechowuje `thread_id`, `correlation_id`, `external_draft_id`, `offer_version`, `input_hash`, `content_hash`, `retry_count`, `last_error`, `created_at` i `updated_at`.

Przed utworzeniem draftu system sprawdza udaną operację z `external_draft_id`, a także istniejące drafty w wątku. Timeout po zapisie jest rozwiązywany przez ponowne odczytanie draftów, nie przez ślepe ponowienie.

## Tryb shadow i wyłączniki

`HERMES_SHADOW_MODE=1` pobiera i klasyfikuje wiadomości oraz generuje treść w pamięci, ale nie zapisuje draftów ani ofert. Niezależnie działają:

- `HERMES_DRAFTS_ENABLED`
- `HERMES_OFFER_GENERATION_ENABLED`
- `HERMES_ATTACHMENT_EXTRACTION_ENABLED`
- `HERMES_CRM_WRITE_ENABLED`

Wyłączenie jednego modułu nie zatrzymuje pollera; wynik trafia do raportu wewnętrznego z powodem.
