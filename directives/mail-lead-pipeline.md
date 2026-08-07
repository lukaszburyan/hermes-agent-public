---
name: mail-lead-pipeline
description: Use when designing, configuring, or operating Hermes inbound Zoho Mail and Google Sheets pipeline for Orchesta RFQ with safe pre-offer auto-send, draft-only final offers, lean research, and Telegram/internal notifications.
---

# Mail Lead Pipeline Directive

## Decisions Locked

- Primary offer: Orchesta RFQ / speed-to-lead plus Quote Draft System.
- Mail integration path: Zoho Mail account `rfq-mailbox@example.invalid` through OAuth/API or another draft-safe Zoho adapter.
- Transport policy: safe, high-confidence, price-free pre-offer communication may be sent automatically through the narrow gated adapter; every final offer remains draft-only.
- Offer artifact: versioned offer JSON plus validated PDF attachment when offer generation is enabled; otherwise a text draft or internal report.
- Calendar behavior: read-only free/busy slots only.
- Runtime split: VPS/cloud for Zoho Mail/Hermes/Telegram; Mac bridge for Obsidian CRM and Apple Calendar.
- Research mode: Lean by default, about 2 minutes maximum.
- Supported lead sources are ordinary customer email in Zoho Mail and a lead row in Google Sheets. Mailbox messages always use their actual envelope sender and the normal mailbox rules.

## Verification Plan

Test:
- classifier routes Orchesta RFQ, adjacent topics, weak fit, admin, and automated mail correctly,
- no model runs when pre-check finds only obvious automated/noise messages,
- attachment router chooses PyMuPDF, rendered-page Tesseract, OCR/vision, or Telegram-only without external calls; marker-pdf is disabled until its dependency chain is vulnerability-free,
- pre-offer email is sent only for eligible high-confidence classes and contains no price, final offer or attachment,
- every ordinary inbound pre-offer reply stays in the original thread; a Sheets first response is a separate new message,
- final offers exist only in Zoho Drafts with a remotely verified PDF and never in Sent,
- pricing appears only in final offer draft scenarios,
- Telegram blocks draft for weak fit, unknown topic, low confidence, and sensitive cases,
- CRM behavior is explicit when Mac bridge is unavailable,
- no secrets enter docs, logs, Memory Wiki, Obsidian CRM, or Git.

Done when:
- local dry-run fixtures pass,
- OAuth scopes are visible and minimal,
- a controlled incomplete lead creates exactly one pre-offer message in Sent,
- a controlled complete lead creates exactly one final draft in the proper customer thread with a validated PDF,
- the final offer does not appear in Sent and a rerun creates no duplicate,
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

Important: Zoho Mail `messages.CREATE` supports both intended transports. Code-level separation must enforce price-free pre-offer send and draft-only final offers; environment flags alone are insufficient.

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
   - resolves website-form notifications to the customer email from
     `Firmowy adres e-mail` before classification/drafting,
   - reads the thread and classifies the message before expensive attachment work,
   - routes attachments through the deterministic safety/type router,
   - runs full OCR/vision/marker/model extraction only for `new_quote_request`, `quote_draft_ready`, `existing_client_request`, `existing_thread_reply`, or `same_domain_new_person`,
   - reads only safe attachment text or summaries,
   - retrieves the inbound RFC `Message-ID` and prior `References`,
   - classifies the email,
   - runs lean research only when needed,
   - checks Obsidian CRM through Mac bridge when available,
   - reads Google Calendar freebusy and Apple Calendar busy blocks when available,
   - sends a gated, price-free pre-offer reply when deterministic gates pass,
   - creates a Zoho Mail final-offer reply draft with validated PDF when scope is complete,
   - writes a short, human Telegram/internal briefing,
   - writes short CRM notes only through Mac bridge.

Store message state and operations in SQLite. Do not rely on marking Zoho Mail messages read as the source of truth unless Lukasz approves mailbox mutations.

## V1 Runtime Scripts

The first production-leaning implementation of the two-stage design lives in:

- `execution/zoho_mail_poller.py` - two-stage Zoho poller. Side effects are disabled by default and are enabled only through separate pre-offer-send, draft-create, final-offer and notification gates. `--self-test` runs offline; `--live` polls the real mailbox.
- `execution/pipeline_state.py` - SQLite state/operation store (default `.tmp/hermes-rfq-state/state.sqlite3`, override with `HERMES_RFQ_STATE_FILE`). Operations are unique per `message_id + action_type`; a retry reconciles existing drafts before creating another one.
- `execution/zoho_pre_offer_send.py` - narrow pre-offer sender. It rejects prices, final-offer kinds, PDFs and attachments and requires `HERMES_ALLOW_PRE_OFFER_SEND=1` plus the exact approval phrase.
- `execution/zoho_reply_draft.py` - gated draft creator used by the final-offer path. It always uses `mode: draft`; final offers require a durable draft ID and remotely confirmed PDF attachment.

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

After Łukasz has approved and enabled the production split policy, do not ask for per-lead approval for safe pre-offer messages. Final offers remain draft-only and can be sent only manually by Łukasz.

## Zoho Draft Threading

Inbound replies must not become new threads.

For every final-offer reply draft:
- read the Zoho `messageId`, folder ID, RFC `Message-ID`, and existing `References`/thread header,
- save the draft with `mode: draft`,
- set `inReplyTo` to the inbound RFC `Message-ID`,
- set `refHeader` to the prior `References` plus the inbound RFC `Message-ID` when available,
- keep the subject as a reply to the inbound subject,
- do not call Zoho's send-reply endpoint from the final-offer draft path,
- if headers are unavailable or ambiguous, block the draft and send Telegram instead.

For a website-form notification, retain the source `messageId`/RFC metadata only
for correlation and audit. The first pre-offer response is a separate new message:
do not set `inReplyTo`/`refHeader`, set the recipient to the validated
`Firmowy adres e-mail`, and use the approved form subject. If that field cannot
be validated, block customer transport and notify Łukasz instead.

Reference: Zoho's Save Draft API supports `inReplyTo` and `refHeader` for final-offer reply drafts. Zoho's send/reply operation is allowed only through the narrow pre-offer adapter and must never be reachable from final-offer code.

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
- `execution/zoho_mail_poller.py` for pre-check, classification, deal correlation and transport routing,
- `execution/pipeline_state.py` for processed-message idempotency,
- `execution/zoho_pre_offer_send.py` for narrow gated pre-offer sending,
- `execution/zoho_reply_draft.py` for gated final-offer drafts,
- Zoho Mail OAuth/API tooling for mailbox and draft operations,
- Calendar freebusy only for slots.

Do not use:
- native Hermes Email auto-reply path,
- broad/native auto-reply tools that bypass `zoho_pre_offer_send.py`,
- broad mailbox mutation tools,
- public webhooks for v1.
