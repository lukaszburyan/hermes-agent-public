# 06 — Runbook operacyjny — archived export

> Nie wykonywać tych kroków na produkcji. Obowiązują aktualne
> `DEPLOYMENT_RUNBOOK.md` i `ROLLBACK_RUNBOOK.md`, dokładny digest obrazu oraz
> dwa kanoniczne timery systemd.

## Szybka kontrola lokalna

```bash
python3 /opt/data/execution/zoho_mail_poller.py --self-test
python3 /opt/data/execution/zoho_reply_draft.py --self-test
python3 /opt/data/skills/rfq-final-offer/scripts/rfq_final_offer.py --self-test
```

## Tryb mailbox

1. Sprawdź, czy `.env` ma wymagane klucze Zoho/Telegram — nie wypisuj wartości.
2. Uruchom poller najpierw offline/self-test.
3. Dopiero po świeżej zgodzie użyj `--live`.
4. Draft creation wymaga twardej bramki `HERMES_ALLOW_DRAFT_CREATE=1` i frazy approval w narzędziu draftu.
5. Pierwszy realny draft wymaga świeżej zgody Łukasza.

## Finalna oferta

1. Potwierdź, że wątek jest quote-request flow z `mail-lead-pipeline`.
2. Zbierz dane: firma, email, liczba skrzynek i CRM yes/no.
3. Użyj tylko `rfq-final-offer-knowledge/approved/pricing.json` do ceny.
4. Wygeneruj JSON + HTML, PDF tylko gdy WeasyPrint jest dostępny.
5. Zweryfikuj PDF przez kontrolę wymaganych/zakazanych tekstów.
6. Stwórz draft w tym samym threadzie z PDF jako załącznikiem; nie wysyłaj.
7. Wyślij krótki briefing Telegram.

## Debug

- Problem z threadingiem: sprawdź RFC `Message-ID`, `References`, `inReplyTo`, `refHeader`.
- Duplikaty: sprawdź SQLite state `HERMES_RFQ_STATE_FILE` i operation idempotency.
- Załącznik blokuje draft: sprawdź router i powód `blocked/suspicious/encrypted/too_large`.
- CRM/Kalendarz niedostępne: degraded mode, bez udawania, że sprawdzono CRM/calendar.
- Ceny inne niż oczekiwane: sprawdź tylko approved pricing, nie business-profile ani internet.

## Weryfikacja wykonana przy generowaniu tej dokumentacji

- `python3 /opt/data/execution/zoho_mail_poller.py --self-test` → exit `0`
- `python3 /opt/data/execution/zoho_reply_draft.py --self-test` → exit `0`
- `python3 /opt/data/skills/rfq-final-offer/scripts/rfq_final_offer.py --self-test` → exit `0`
