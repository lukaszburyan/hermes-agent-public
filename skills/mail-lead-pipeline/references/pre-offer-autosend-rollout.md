# Pre-offer auto-send and final-offer draft-only rollout

## Policy boundary

Customer communication is split by semantic stage, not by source adapter:

- **Before the final offer:** a high-confidence, safe response is sent automatically. This includes first responses, discovery questions, contextual replies, and missing-data questions produced while preparing the offer.
- **Final offer:** create only a Zoho draft in the existing customer thread, attach the validated PDF, and notify Łukasz by Telegram and allowlisted internal email. Never send the final offer automatically.
- Ambiguous identity, low confidence, sensitive/risky content, prompt injection, unsafe attachments, or conflicting deal facts always block customer transport and require review.

Use the durable message types `acknowledgement`, `clarification_request`, `missing_data_request`, `follow_up`, `ready_for_offer_notice`, and `final_offer`. The first five are eligible for `auto_send`; `final_offer` is permanently `draft_only`. An unknown type is `manual_review`. Store the type on the event/deal policy and outbox before rendering or transport. Model output cannot choose, rewrite or downgrade it.

Zoho Mail and Google Sheets are the only lead sources and use the same policy and shared deal registry. A Sheets first response is a new message to the validated customer address and must explain the campaign origin of the contact. A mailbox reply is sent through Zoho's reply operation for the inbound source message.

## Code-level transport separation

Keep two narrow adapters:

1. **Pre-offer sender**
   - exposes only new-message send and reply-send operations;
   - requires the deployment gates plus an exact approval phrase, but a gate only enables processing and never decides message policy;
   - accepts only allowlisted message kinds such as first response, context reply, discovery, and missing-data question;
   - rejects prices/currency, offer/final-offer kinds, attachments and PDF payloads at the transport boundary;
   - never imports final-offer pricing or PDF generation.
2. **Final-offer draft creator**
   - exposes only Zoho draft creation and attachment upload;
   - requires the separate draft gate and exact approval phrase;
   - validates generated PDF and remote attachment confirmation before recording `offer_ready`;
   - must not import or call the pre-offer send poster for a complete offer.

The final-offer orchestrator may receive both adapters, but it must route `awaiting_data` exclusively to the pre-offer sender and `offer_draft_created` exclusively to the draft+PDF adapter.

Recurring wrappers must not add `--auto-send`. Keep the legacy flag accepted only as a no-effect compatibility input so old schedulers cannot change transport policy. Immediately before provider transport, re-read the authoritative event, deal and outbox and compare recipient, normalized `email:<address>` contact identity, deal, source, thread, source message/account, canonical operation stage, durable type and allowed transport mode. Re-read once more immediately before the provider call and block if the deal version changed. Any mismatch becomes a security event and manual review.

## Durable idempotency and uncertain outcomes

Use a durable operation record before any customer transport:

1. Plan an operation keyed by inbound source event and operation type (`customer_send` or final-stage response).
2. Persist `in_progress` immediately before the HTTP request.
3. On confirmed Zoho 2xx, persist `succeeded` and an external message ID. If Zoho omits an ID, persist a deterministic `zoho-accepted:<payload-hash>` marker so a later tick cannot resend.
4. On timeout or connection loss after the request may have reached Zoho, persist `outcome_unknown`. Never automatically repeat it on restart.
5. Retry only outcomes known not to have been accepted (for example a clear 429 or selected server response). A timeout/transport-loss result is outcome-unknown, not safely retryable.
6. Before retry, reconcile the exact operation marker, Message-ID and thread against Zoho Sent. Retry only after durable proof that the original was not accepted; unresolved `outcome_unknown` remains blocked and alarmed.
7. Store sent responses as response artifacts with `external_message_id`, not as drafts with `external_draft_id`.

Use an event-specific response stage such as `response:<source-event-id>` or `missing_data:<message-id>`. Do not use one permanent `discovery` artifact per deal: a real conversation can require several safe responses. Cross-source duplicate detection still happens at deal/event registration.

Recommended deal status after sending a question or response: `waiting_for_customer`; Sheets projection: `oczekuje na klienta`.

## Google Sheets rules

- `--dry-run` must never instantiate or call the Zoho send transport.
- Store the accepted external ID in `Hermes sent id`; do not put it in the draft ID column.
- A rerun with the same row hash must reuse registry/state data and make no second send call.
- If a lead already exists through mail, correlate it before transport and do not send a duplicate first response.
- A reply from the customer continues through the mailbox thread and shared deal facts.

## Final-offer notifications

After and only after all of these are true:

- Zoho draft creation succeeded,
- a durable draft ID was returned,
- the validated PDF is attached and attachment confirmation is complete,

emit both notifications:

- Telegram;
- allowlisted internal email to Łukasz.

Include company, contact, concise scope, net price, exact Zoho Drafts/thread location, and explicit prose that nothing was sent automatically. Notification failure must not cause the final customer draft to be resent or duplicated; record and retry notification delivery separately.

## Required regression tests

1. Pre-offer payload rejects final-offer kind, currency/price and all attachments.
2. Environment gate and exact approval phrase are both required.
3. Thread replies call reply-send; Sheets first responses call new-message send and include campaign-origin context.
4. Same input run twice invokes the fake send poster once.
5. A persisted `in_progress` operation invokes the fake send poster zero times and escalates uncertain outcome.
6. Missing final-offer data sends questions and creates no draft.
7. Complete final-offer data creates a draft with validated PDF and invokes no send poster.
8. Final-offer success prepares both internal notifications.
9. Sheets dry-run makes no network/transport call.
10. Static boundary test ensures final-offer draft code cannot use send mode.

## Rollout sequence

1. Run focused transport and orchestration tests in RED/GREEN order.
2. Run the complete suite and deterministic self-tests.
3. Perform a smoke test with fake clients/posters only; inspect recorded operation and registry artifacts.
4. Inspect production wrappers and schedulers. Remove every injected `--auto-send`, retain the deployment gate, final-offer draft mode and internal notification calls, and ensure every invocation delegates to the canonical entrypoint and lock.
5. Enable the pre-offer gate only after tests and a marked controlled live validation address/thread are approved.
6. Verify Sent for the marked pre-offer test, Drafts+PDF for the final-offer test, both internal notifications, and a second-run no-duplicate result.
7. Do not claim production readiness while any source adapter is partially migrated or the full suite has not passed.
