# Monitoring i retry

`execution/hermes_monitoring.py` daje read-only snapshot: liczba wiadomości w kolejce, wiek najstarszej, czas analizy i zapisu draftu, błędy API, ponowienia, duplikaty, blokady bezpieczeństwa, konflikty CRM, ręczne poprawki, oczekujące oferty i błędy PDF. Metryki są inkrementowane w SQLite.

Alarmy obejmują: brak uruchomienia pollera, rosnący backlog, serię 429, serię 5xx, uszkodzoną bazę stanu, brak zatwierdzonego cennika, użycie pola wysyłki, sekret w logu, duplikat draftu i błąd PDF. Każdy wpis logu przechodzi przez centralną redakcję z `hermes_rfq_core.log_event`.

Shadow mode zapisuje podsumowania JSONL pollera bez draftów, ofert i wysyłki. Handlowiec oznacza decyzje w osobnym JSONL, np. `{"message_id":"...","expected_action":"customer_draft","expected_classification":"existing_thread_reply"}`. Porównanie wykonuje `execution/hermes_shadow_compare.py`; raport pokazuje dopasowania, rozbieżności i pokrycie etykietami, bez zapisywania treści wiadomości.

Dla 429 i 5xx obowiązuje ograniczony exponential backoff z jitterem. Błędy 4xx payloadu są permanentne i nie są ponawiane bez zmiany danych. Poller obsługuje błąd pojedynczej wiadomości jako stan operacji, więc nie zatrzymuje całej kolejki.
