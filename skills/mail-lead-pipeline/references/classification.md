# Mail Classification Reference

## Goal

Separate real Orchesta RFQ opportunities from noise, adjacent topics, weak-fit cases, and human-review cases before Hermes spends model tokens or creates drafts.

The legacy `classification` value remains for compatibility. Every message must also receive independent fields: `source_type` (`email`/`google_sheets`), `intent` (`rfq`, `general_business`, `admin`, `billing`, `complaint`, `security`, `legal`, `data_request`, `unknown`), `conversation_relation` (`new`, `existing_thread`, `existing_client`, `same_domain_new_person`), `risk` (`safe`, `review`, `blocked`), `fit` (`good`, `weak`, `unknown`), `offer_status` (`discovery`, `missing_data`, `ready`) and `action` (`skip`, `internal_review`, `customer_draft`, `offer_draft`). The action is derived from the set, never from one class.

For example, a known customer replying in an existing thread with complete data and a blocked attachment is still `conversation_relation: existing_thread`, `offer_status: ready`, `risk: blocked`, `action: internal_review`.

## Classes

### `new_quote_request`

Use when the sender asks about:
- quote request handling,
- RFQ,
- pricing/offer/proposal for Orchesta RFQ,
- faster response to forms or email inquiries,
- agent that reads quote requests,
- CRM update from inbound requests,
- first response in under 5 minutes.

Default action: lean research, fit check, then an auto-sent acknowledgement or discovery request only after all durable operational gates pass.

### `quote_draft_ready`

Use when the lead is RFQ-related and minimum final-offer data is present:
- company type or industry,
- source of quote requests,
- rough request volume,
- current response process and CRM/process tools,
- example request or clear request type,
- expected implementation scope.

Default action: final offer draft with validated PDF for human review, with investment if pricing is clear; never auto-send it.

### `new_general_business_inquiry`

Use when the message may concern Orchesta RFQ but lacks enough context.

Default action: short auto-sent clarification if its durable operational policy is safe; otherwise manual review and an internal notification.

### `related_non_rfq_topic`

Use for adjacent topics such as:
- AI training,
- broader automation,
- sales consulting,
- agent automation not connected to quote requests,
- other Lukasz services outside Orchesta RFQ.

Default action: Telegram only. Do not create a customer-facing draft from this skill.

### `weak_fit_review_only`

Use when the sender may be a lead but fit is weak:
- medical/financial/heavily regulated context,
- high confidentiality,
- very low quote-request volume,
- no repeatable request source,
- expectation that AI sends final technical offers without a human.

Default action: Telegram only with a short reason.

### `human_review_only`

Use for legal, banking, medical, security, account access, credential, complaint, dispute, or highly sensitive cases.

Default action: Telegram only. No draft unless Lukasz explicitly approves.

### `existing_client_request`

Use when the sender/domain is a known client or there is durable history in Obsidian CRM.

Default action: client-aware operational response only if the topic, durable type, recipient and transport policy are safe and RFQ-related; final-offer content remains draft-only.

### `existing_thread_reply`

Use when the message is part of an existing thread or clearly answers a previous Hermes/Lukasz message.

Default action: summarize what changed and auto-send only an approved operational reply when one is expected; complete commercial scope goes to the final-offer draft path.

Do not rely on the subject prefix alone. `Re:` or `Odp:` without `In-Reply-To`, `References`, Zoho thread context, or matching CRM/session context is not enough to classify as an existing thread.

### `same_domain_new_person`

Use when the sender is new but the domain is already known from CRM/history.

Default action: connect domain context and decide whether it is the same deal or a separate deal.

### `vendor_admin_billing`

Use for invoices, receipts, account notices, SaaS renewals, legal/admin, platform security alerts, or vendor messages.

Default action: briefing only unless a reply is clearly needed.

Do not let invoice words alone override a strong Orchesta RFQ signal. If the sender is asking about Orchesta RFQ, quote-request handling, offer context, or OCR as part of the RFQ workflow, classify from the RFQ intent first and treat the invoice as an attachment type.

### `newsletter_automated_spam`

Use for newsletters, noreply, autoresponders, bounces, bulk list mail, marketing automation, or obvious spam.

Default action: no model if detectable in pre-check. No draft.

### `unknown_review_needed`

Use when classification is ambiguous or context is missing.

Default action: Telegram/internal briefing. No customer draft.

## Relationship And Deal Detection

Use this order:

1. Exact sender match in Obsidian CRM/session history.
2. Domain match in Obsidian CRM/session history.
3. Thread headers, Zoho Mail thread/message ID, or explicit CRM/session thread context.
4. Similar topic, request type, and close time window.
5. Message content references to previous work, invoices, projects, or meetings.

Merge as the same deal only when domain, topic, time window, and context align. If the same domain writes about a different matter, create a separate deal in the same company note.

## Confidence

Use:
- `high` when class is obvious from sender, thread, and content,
- `medium` when one key signal is missing,
- `low` when the message is ambiguous.

Low confidence means Telegram first and no customer-facing draft.
