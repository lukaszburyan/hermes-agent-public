# Hermes Mail Lead Pipeline PRD

Status: production-candidate split transport policy
Last updated: 2026-08-06 Europe/Warsaw

## Goal

Hermes monitors the configured Zoho Mail RFQ mailbox and Google Sheets lead
source, classifies and correlates requests, automatically sends only approved
operational communication, prepares final offers as Zoho drafts with validated
PDFs, updates durable state, and notifies Łukasz when human judgment is needed.

Hermes must never automatically send a final offer from this pipeline.

## Locked Decisions

- Offer: Orchesta RFQ / speed-to-lead plus Quote Draft System.
- Mail access: Zoho Mail account `rfq-mailbox@example.invalid` through OAuth/API or another draft-safe Zoho adapter.
- Zoho scope set: `ZohoMail.accounts.READ`, `ZohoMail.folders.READ`, `ZohoMail.messages.READ`, `ZohoMail.messages.CREATE`, `ZohoMail.tags.READ`.
- Calendar: free/busy read-only; propose slots, do not create holds.
- Output: operational Zoho message when approved, final-offer Zoho draft with
  validated PDF, internal briefing, and Telegram/internal notification as needed.
- CRM: Obsidian `07 CRM/` through Mac bridge.
- Runtime: VPS/cloud for Zoho Mail/Hermes/Telegram; Mac bridge for Obsidian CRM and Apple Calendar.
- Research: Lean by default, about 2 minutes maximum.
- Safety: durable message-type policy; approved operational messages auto-send,
  final offers are draft-only, and unknown or inconsistent cases require manual
  review.
- Attachments: safety/type router first; PyMuPDF for text PDFs, rendered-page Tesseract for scanned/complex PDFs, OCR/vision only when needed. Marker-pdf is disabled until its dependency chain is vulnerability-free.

## Non-Goals

- No automatic final-offer or commercial-terms sending.
- No public webhook in v1.
- No calendar event creation in v1.
- No Zoho Mail settings/filter/forwarding changes in v1.
- No automatic customer delivery of generated offer documents.
- No long-term storage of secrets or raw sensitive attachments.
- No final binding quote without human review.

## Selected Skill Files

- `skills/mail-lead-pipeline/SKILL.md` - workflow entrypoint.
- `skills/mail-lead-pipeline/references/business-profile.md` - source of truth for offer, pricing, fit, style, and claims.
- `skills/mail-lead-pipeline/references/research-and-crm.md` - lean research, Mac bridge, Obsidian CRM, deal grouping.
- `skills/mail-lead-pipeline/references/telegram-escalation.md` - Telegram gates and learning updates.
- `skills/mail-lead-pipeline/references/draft-style.md` - draft structure and wording.
- `skills/mail-lead-pipeline/references/classification.md` - mail classes and routing.
- `skills/mail-lead-pipeline/references/calendar-availability.md` - freebusy policy.
- `skills/mail-lead-pipeline/references/attachment-processing.md` - attachment safety, OCR/vision, PyMuPDF, Tesseract, and invoice routing.
- `execution/attachment_router.py` - deterministic local helper for attachment route decisions.

## Mail Classes

Hermes must classify each message as one of:

- `new_quote_request`
- `quote_draft_ready`
- `new_general_business_inquiry`
- `related_non_rfq_topic`
- `weak_fit_review_only`
- `human_review_only`
- `existing_client_request`
- `existing_thread_reply`
- `same_domain_new_person`
- `vendor_admin_billing`
- `newsletter_automated_spam`
- `unknown_review_needed`

## Business Logic

For Orchesta RFQ lead-like classes, Hermes should:

1. Identify sender, company, domain, request type, and relationship.
2. Check Obsidian CRM through Mac bridge when available.
3. Route attachments through the local safety/type router and analyze safe attachment text or summaries only.
4. Run lean research only when needed.
5. Score `orchesta_fit` as green/yellow/red.
6. Identify missing information needed for first response or final offer draft.
7. Read Google Calendar freebusy and Apple Calendar busy blocks when available.
8. Auto-send only an approved operational message after transport revalidates
   the durable event, deal, recipient, thread, type and policy.
9. Create a final-offer Zoho draft with validated PDF only when warranted.
10. Produce a short, human briefing for Lukasz.
11. Write short CRM notes through Mac bridge when available.

For weak fit, unrelated, sensitive, or low-confidence classes, Hermes should send Telegram only and avoid customer-facing drafts.

## Message Transport Rule

The configured Zoho account follows a durable split policy:

- `acknowledgement`, `clarification_request`, `missing_data_request`,
  `follow_up` and `ready_for_offer_notice` may auto-send after every gate;
- `final_offer` always creates a draft and is never sent automatically;
- unknown types, ambiguous content and recipient/deal mismatches go to manual review;
- the model may propose wording but cannot select or downgrade message type;
- the transport re-reads the durable event, deal and outbox immediately before send;
- the global kill switch blocks customer transport while processing and draft/outbox persistence continue;
- create inbound drafts as replies in the original thread, not as new conversations,
- never promise a meeting is reserved,
- never quote price in first response/discovery,
- answer normal pricing questions without enough data by asking concise discovery questions,
- use prices only in final offer draft and call them "inwestycja",
- never modify Calendar events,
- never claim Apple Calendar or Obsidian CRM was checked if the Mac bridge is unavailable.
- never run OCR/vision/model extraction before the attachment safety gate passes.

## Business Inputs Filled

- Offer name: Orchesta RFQ.
- Positioning: system that reacts to quote requests from email/forms in under 5 minutes, asks follow-up questions, updates CRM, and prepares handoff or offer draft.
- Ideal customer: 10-100 employees, recurring quote requests, technical/service B2B or B2B-like, manual response process.
- Weak fit: medical, financial, heavily regulated, highly confidential, very low request volume.
- Pricing: 7200 PLN net mailbox/form, about 15000 PLN net CRM connection, 3999 PLN net each additional mailbox/salesperson, 1499 PLN net monthly maintenance.
- Language: Polish default, English when the sender writes clearly in English.
- Meeting duration: 30 minutes, Monday-Friday, 09:00-17:00 Europe/Warsaw.
- CRM destination: Obsidian `07 CRM/` through Mac bridge.
- Offer artifact: Zoho Mail draft with a validated final-offer PDF.

## OAuth Note

Zoho Mail `messages.CREATE` is required for drafts but can be close to send-capable APIs. Therefore the technical implementation must include application-level and test-level controls that prohibit send operations.

For inbound reply drafts, the adapter must fetch the inbound RFC `Message-ID` and prior `References`, then save a draft with `mode: draft`, `inReplyTo`, and `refHeader`. If those headers cannot be fetched, Hermes should not create a customer-facing draft and should notify Lukasz on Telegram.

Recommended Google Calendar scope for the chosen behavior is `calendar.freebusy`.

MacBook Calendar note: local Apple Calendar access may require macOS privacy permission or a local helper. If unavailable, Hermes must fall back to Google Calendar and disclose the limitation in the briefing.

Sources:

- https://orchesta.eu/
- https://www.zoho.com/mail/help/api/post-save-draft-template.html
- https://developers.google.com/workspace/calendar/api/auth
