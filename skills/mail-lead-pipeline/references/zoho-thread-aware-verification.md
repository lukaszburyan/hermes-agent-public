# Zoho thread-aware message verification

Use this reference when a live RFQ acceptance test must prove that a controlled message reached Inbox, was processed once, and produced the expected Draft/Sent artifacts.

## Core rule

Do not infer message absence from one `list_messages` response. Zoho can group same-subject messages into a thread, return only a representative item, or omit a specific source ID from the first page even when direct reads by ID still work.

## Verification sequence

1. Before processing, capture the exact source `message_id`, folder ID, unique body marker and intended recipient.
2. Persist the source ID in poller state/registry as part of the operation identity.
3. After processing, verify Inbox delivery directly by source ID with both content and header read endpoints. A non-empty body/header is stronger evidence than list membership.
4. If the expected ID is uncertain, recover it from durable poller state or the run log; do not guess from a visually similar draft or attachment ID.
5. Use folder listing only as supporting evidence. Search all relevant folders and handle pagination/thread grouping.
6. Verify final artifacts independently:
   - Drafts: exact `draft_id`, recipient, subject and remote attachment metadata;
   - attachment: download through read API, verify `%PDF-`, SHA-256, page count and expected offer facts;
   - Sent: assert zero matching final offers, or exact sent ID only for an approved price-free pre-offer.
7. Repeat the poller with no new input and assert no second send, draft, attachment or internal notification.

## Pitfalls

- Do not confuse a source message ID with a similarly prefixed draft ID.
- Do not rely only on subject matching when several controlled tests share the same subject; include a unique body marker and persisted source ID.
- Do not report “not in Inbox” merely because the source ID is missing from the first list response.
- Do not report success from a local PDF path or local manifest without checking the remote Zoho attachment.
