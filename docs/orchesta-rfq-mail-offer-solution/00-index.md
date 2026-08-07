# 00 — Indeks rozwiązania

> Archiwalny eksport. Aktualny system używa systemd-only schedulerów i trwałej
> polityki: operacyjne wiadomości auto-send, finalna oferta draft-only.

## Cel biznesowy

Rozwiązanie obsługuje inbound dla Orchesta RFQ:

1. obserwuje skrzynkę `rfq-mailbox@example.invalid` w Zoho Mail,
2. odróżnia realne zapytania od szumu,
3. czyta wiadomość i bezpieczne załączniki,
4. klasyfikuje sprawę i ocenia fit,
5. wysyła zatwierdzoną wiadomość operacyjną albo blokuje transport i zgłasza temat do oceny,
6. aktualizuje CRM/Obsidian przez Mac bridge, jeśli dostępny,
7. gdy klient poda komplet danych, generuje finalną ofertę: JSON + prezentację HTML/PDF + draft maila z załącznikiem.

## Komponenty

- Skill routingu: `mail-lead-pipeline`.
- Skill finalnej oferty: `rfq-final-offer`.
- Dyrektywa architektoniczna: `directives/mail-lead-pipeline.md`.
- Runtime pocztowy: `execution/zoho_mail_poller.py`.
- Stan/idempotencja: `execution/pipeline_state.py`.
- Draft Zoho: `execution/zoho_reply_draft.py`.
- Załączniki: `execution/attachment_router.py`, `execution/rfq_attachment_extract.py`.
- Finalna oferta: `skills/rfq-final-offer/scripts/rfq_final_offer.py`.
- Wiedza zatwierdzona: `skills/rfq-final-offer/rfq-final-offer-knowledge/approved/`.

## Historyczny status automatyzacji cron

Ten fragment opisuje stan sprzed audytu i nie jest aktualnym schedulerem.
Wydanie produkcyjne dopuszcza wyłącznie dwa kanoniczne timery systemd opisane w
`DEPLOYMENT_RUNBOOK.md`; wewnętrzne joby Hermesa i root/user cron nie mogą uruchamiać pollerów.

## Reguły bezpieczeństwa

- Brak automatycznej wysyłki maili.
- Drafty tylko po spełnieniu bramek klasyfikacji, fitu i threading headers.
- Sekrety tylko w `.env`/token store, nigdy w docs, CRM, memory ani repo.
- Załączniki są nieufne; routing deterministic przed OCR/modelami.
- Mac bridge/CRM może być niedostępny — wtedy runtime działa w degraded mode i raportuje brak CRM/Kalendarza.
