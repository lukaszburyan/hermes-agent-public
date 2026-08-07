# 01 — Architektura i logika end-to-end

## Dyrektywa źródłowa

---
name: mail-lead-pipeline
description: Use when designing, configuring, or operating Hermes inbound Zoho Mail pipeline for Orchesta RFQ with OAuth/API access, lean research, safe pre-offer auto-send, final-offer drafts, Obsidian CRM through Mac bridge, and internal escalation.
---

# Mail Lead Pipeline Directive

## Decisions Locked

- Primary offer: Orchesta RFQ / speed-to-lead plus Quote Draft System.
- Mail integration path: Zoho Mail account `rfq-mailbox@example.invalid` through OAuth/API or another draft-safe Zoho adapter.
- Email transport: safe price-free pre-offer messages may be sent automatically after validation; final offers are drafts with validated PDFs and are never sent automatically.
- Offer artifact: versioned offer JSON plus validated PDF attachment when offer generation is enabled; otherwise a text draft or internal report.
- Calendar behavior: read-only free/busy slots only.
- Runtime split: VPS/cloud for Zoho Mail/Hermes/Telegram; Mac bridge for Obsidian CRM and Apple Calendar.
- Research mode: Lean by default, about 2 minutes maximum.

## Verification Plan

Test:
- classifier routes Orchesta RFQ, adjacent topics, weak fit, admin, and automated mail correctly,
- no model runs when pre-check finds only obvious automated/noise messages,
- attachment router chooses PyMuPDF, rendered-page Tesseract, OCR/vision, or Telegram-only without external calls; marker-pdf is disabled until its dependency chain is vulnerability-free,
- a safe pre-offer response is sent only for eligible classes and a final offer is saved only as a draft,
- every inbound customer mail draft is a reply draft in the original thread,
- no final offer is sent,
- pricing appears only in final offer draft scenarios,
- Telegram blocks draft for weak fit, unknown topic, low confidence, and sensitive cases,
- CRM behavior is explicit when Mac bridge is unavailable,
- no secrets enter docs, logs, Memory Wiki, Obsidian CRM, or Git.

Done when:
- local dry-run fixtures pass,
- OAuth scopes are visible and minimal,
- a test Zoho Mail message creates at most one eligible pre-offer send, while a complete case creates a final draft but no final-offer send,
- the test draft appears as a reply to the original message/thread,
- Telegram/internal briefing points to the draft or reason for blocking,
- secret scan passes.

## Required OAuth Shape

Use the narrowest scopes that support the chosen behavior:

- Zoho Mail account read access for `rfq-mailbox@example.invalid`.
- Zoho Mail folder read access to identify Inbox, Drafts, Sent, Spam, and Trash.
- Zoho Mail message read/search access for inbound messages and safe attachments.
- Zoho Mail header/original message read access to retrieve RFC `Message-ID` and `References` for threaded drafts.
- Zoho Mail draft creation access.
- Zoho Mail tag/label read access for future routing.
- Calendar freebusy access only.
- MacBook Apple Calendar read-only busy-block export through local bridge, if available.

Current conservative scope set:

```text
ZohoMail.accounts.READ,ZohoMail.folders.READ,ZohoMail.messages.READ,ZohoMail.messages.CREATE,ZohoMail.tags.READ
```

Important: Zoho Mail `messages.CREATE` is required for drafts and customer sends. The pipeline must enforce the split between validated price-free pre-offer sends and draft-only final offers in code, skills, prompts, tests, and operational review.

Recommended Calendar scope:

- `https://www.googleapis.com/auth/calendar.freebusy`

Avoid:
- broad Calendar edit scopes,
- mailbox settings scopes,
- forwarding/delegate scopes,
- permanent delete scopes,
- any send-only automation path.

## Runtime Architecture

Use a two-stage design:

1. Cheap pre-check:
   - scheduled every few minutes,
   - checks for new unprocessed Zoho Mail message IDs,
   - filters obvious automated/noise messages,
   - records attachment metadata without opening risky files,
   - emits `{"wakeAgent": false}` when nothing needs reasoning.
2. Agent run:
   - loads `skills/mail-lead-pipeline`,
   - reads `references/business-profile.md`,
   - reads the thread and classifies the message before expensive attachment work,
   - routes attachments through the deterministic safety/type router,
   - runs full OCR/vision/marker/model extraction only for `new_quote_request`, `quote_draft_ready`, `existing_client_request`, `existing_thread_reply`, or `same_domain_new_person`,
   - reads only safe attachment text or summaries,
   - retrieves the inbound RFC `Message-ID` and prior `References`,
   - classifies the email,
   - runs lean research only when needed,
   - checks Obsidian CRM through Mac bridge when available,
   - reads Google Calendar freebusy and Apple Calendar busy blocks when available,
   - sends a validated price-free pre-offer reply or creates a draft-only final offer, as warranted,
   - writes a short, human Telegram/internal briefing,
   - writes short CRM notes only through Mac bridge.

Store message state and operations in SQLite. Do not rely on marking Zoho Mail messages read as the source of truth unless Lukasz approves mailbox mutations.

## V1 Runtime Scripts

The first production-leaning implementation of the two-stage design lives in:

- `execution/zoho_mail_poller.py` - two-stage Zoho poller. It is read-only by default; pre-offer send and final-draft creation remain separately gated by capability switches and operation idempotency. `--self-test` runs fully offline; `--live` polls the real mailbox; internal notifications are opt-in.
- `execution/pipeline_state.py` - SQLite state/operation store (default `.tmp/hermes-rfq-state/state.sqlite3`, override with `HERMES_RFQ_STATE_FILE`). Operations are unique per `message_id + action_type`; a retry reconciles existing drafts before creating another one.
- `execution/zoho_reply_draft.py` - gated, draft-only, threaded reply-draft creator. Building/previewing payloads is offline and safe; creation is hard-gated behind `HERMES_ALLOW_DRAFT_CREATE=1` plus an explicit approval phrase, always uses `mode: draft`, requires the inbound RFC `Message-ID`, and rejects pricing in first-response/discovery drafts. Do not enable `--execute` without a fresh explicit `OK`.

The poller decides whether attachment extraction is allowed for a class but does not run it; the heavy extraction stays in `execution/rfq_attachment_extract.py`, invoked separately only for allowed classes.

Synthetic live-test subjects matching `HRFQ-AUTO-YYYYMMDD-HHMMSS-X` are reserved for the test-only autosend harness. The production cron must ignore them without writing them to the processed-message state; otherwise production draft creation races the test harness and blocks final-offer turns with `blocked_existing_draft_present`. Only `execution/hermes_rfq_test_autosend_poller.py` should process those subjects, with `base_context.test_autosend=true` and explicit confirmation.

## Approval Gates

Require explicit `OK` before:

- starting OAuth login/device flow,
- storing or rotating OAuth tokens,
- enabling a VPS cron/job against the real mailbox,
- creating the first real Zoho Mail draft,
- changing Zoho Mail filters/settings/labels,
- creating calendar events/holds,
- granting macOS Calendar/Automation access to terminal helpers,
- exposing any webhook publicly.

Production activation still requires the operational gate above. Once enabled, only validated price-free pre-offer messages may be sent automatically; final offers remain draft-only.

## Zoho Draft Threading

Inbound replies must not become new threads.

For every final-offer reply draft:
- read the Zoho `messageId`, folder ID, RFC `Message-ID`, and existing `References`/thread header,
- save the draft with `mode: draft`,
- set `inReplyTo` to the inbound RFC `Message-ID`,
- set `refHeader` to the prior `References` plus the inbound RFC `Message-ID` when available,
- keep the subject as a reply to the inbound subject,
- do not call Zoho's send-reply endpoint for a final offer,
- if headers are unavailable or ambiguous, block the draft and send Telegram instead.

Reference: Zoho's Save Draft API supports `inReplyTo` and `refHeader` for reply scenarios. The separate reply endpoint is restricted to validated, price-free pre-offer communication.

## Business Rules

Business source of truth:

- `skills/mail-lead-pipeline/references/business-profile.md`

Locked rules:

- Use Orchesta and Orchesta RFQ.
- Do not use "Zlote 5 minut" in drafts.
- "<5 minut" refers only to first-response speed.
- First response and discovery drafts never include prices.
- A normal pricing question without enough data gets discovery questions, not a price.
- Final offer drafts may include internal pricing as "inwestycja" only when minimum data exists.
- Weak fit, unknown topic, sensitive data, or low confidence means Telegram only.
- Attachment OCR/vision follows `skills/mail-lead-pipeline/references/attachment-processing.md`.
- Runtime attachment extraction uses `execution/rfq_attachment_extract.py` from the isolated `/opt/data/rfq-runtime/.venv` environment on the Hermes VPS.
- Pass classification and confidence to `execution/rfq_attachment_extract.py`; non-RFQ classes skip full extraction and use Telegram/briefing only.

## Skill Selection

Use:
- `mail-lead-pipeline` for routing and safety,
- `business-profile.md` for offer and pricing,
- `research-and-crm.md` for lean research, deal grouping, and Obsidian CRM,
- `telegram-escalation.md` for Telegram gates,
- `attachment-processing.md` and `execution/attachment_router.py` for file safety and OCR/extractor routing,
- `execution/rfq_attachment_extract.py` for allowed extraction after the safety gate,
- `execution/zoho_mail_poller.py` for the read-only pre-check/classify/briefing loop,
- `execution/pipeline_state.py` for processed-message idempotency,
- `execution/zoho_reply_draft.py` for gated, draft-only threaded reply drafts,
- Zoho Mail OAuth/API tooling for mailbox and draft operations,
- Calendar freebusy only for slots.

Do not use:
- unvalidated native Hermes Email auto-reply paths,
- any final-offer auto-send tool,
- broad mailbox mutation tools,
- public webhooks for v1.


## Przepływ główny

1. Pre-check Zoho Mail pobiera nowe ID wiadomości i odrzuca oczywisty spam/newslettery/autorespondery.
2. Agent ładuje skill `mail-lead-pipeline` i profil biznesowy.
3. Wiadomość jest klasyfikowana do jednej klasy głównej i pól niezależnych: `source_type`, `intent`, `conversation_relation`, `risk`, `fit`, `offer_status`, `action`.
4. Załączniki przechodzą router bezpieczeństwa; pełna ekstrakcja tylko dla klas RFQ/istniejącego wątku/same-domain.
5. Lean research: najpierw body, załączniki, CRM, domena; web tylko gdy potrzebny.
6. Draft jest tworzony tylko dla dozwolonych klas. Zwykły inbound = reply draft w tym samym wątku.
7. Gdy są kompletne dane ofertowe, skill `rfq-final-offer` buduje JSON, HTML/PDF i draft z PDF.
8. Telegram dostaje krótki briefing albo eskalację.

## Stany decyzyjne

- `new_quote_request` → first response/discovery draft.
- `quote_draft_ready` → oferta tekstowa / final offer flow po danych.
- `new_general_business_inquiry` → krótka klaryfikacja, jeśli RFQ/speed-to-lead.
- `related_non_rfq_topic`, `weak_fit_review_only`, `human_review_only`, `unknown_review_needed` → Telegram/manual review, bez customer-facing draftu.
- `existing_thread_reply`, `existing_client_request`, `same_domain_new_person` → draft tylko z kontekstem i bezpiecznym threadingiem.

## Warunki blokujące finalną ofertę PDF

- brak firmy,
- brak emaila klienta,
- brak liczby monitorowanych skrzynek,
- brak decyzji CRM,
- dowolna bramka bezpieczeństwa.

Brak źródła zapytań lub przykładowych zapytań nie blokuje PDF, jeśli pozostałe wymagania są spełnione.
