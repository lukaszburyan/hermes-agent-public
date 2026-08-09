# Hermes RFQ message policy

Production activation evidence (2026-08-08): release `v0.18.0-rc10` exposes exactly the five operational types in `AUTO_SEND_TYPES`; `final_offer` is the sole `DRAFT_ONLY_TYPES` member. The active runtime has operational auto-send enabled, test mode disabled and no token-billed `OPENAI_API_KEY`. Production monitor and behavior manifest are green after restart.

This policy is enforced from durable state and rechecked at the transport boundary. Model output is advisory and cannot authorize transport.

| Durable type | Default transport |
|---|---|
| `acknowledgement` | auto-send |
| `clarification_request` | auto-send |
| `missing_data_request` | auto-send |
| `follow_up` | auto-send |
| `ready_for_offer_notice` | auto-send |
| `final_offer` | draft-only |
| unknown, malformed or ambiguous | manual review |

## Transport decision

Before every auto-send, the transport re-reads the durable event, deal and outbox operation and verifies:

1. source type and source key;
2. deal binding and primary recipient;
3. normalized durable `email:<address>` contact identity plus deal/thread/source-message/account binding;
4. claimed operation, canonical stage and exact message type;
5. deterministic content/attachment classifier;
6. operational auto-send deployment gate;
7. global transport kill switch;
8. controlled-recipient allowlist in test mode.
9. restore gate and persistent monitor kill-switch state;
10. exact Zoho account, source message and resolved-recipient evidence.

Any disagreement is fail-closed, recorded as a security event and routed to human review.

## Final-offer escalation

The deterministic classifier upgrades a message to `final_offer` when process/deal stage, price/cost/discount/subscription/payment/validity/acceptance language, final scope, or offer-shaped PDF/document indicates commercial finality. The guard covers Polish, English and German prices, currencies, amounts in words, discounts, installments, payment terms, validity, final-quote language, acceptance and commercial terms. Unknown or uncertain language is `manual_review`. Numbers that are clearly phones, dates, dimensions, quantities, order numbers or technical data do not alone create an offer.

An attempted automatic final-offer send is blocked at the transport boundary, recorded as `final_offer_autosend_attempt`, converted to a draft when the draft adapter succeeds, and reported to a human. The outbox records `draft_created` or `manual_review`; it can never record `sent:final_offer` through this transport.

## Reliability policy

- Zoho account and Inbox selection require exactly one configured match and never use the first API item as fallback.
- `Reply-To` is the reply recipient when present; `From`, `To` and `Cc` are saved as evidence. Automatic reply-all is disabled.
- A claim binds owner, operation, lease, content hash, type, recipient, durable contact identity, source, canonical stage, thread and marker.
- The response lifecycle is `preparing` -> `content_validated` -> `transport_starting` -> `post_started` -> one terminal or recovery outcome. `outcome_unknown` is illegal before `post_started`.
- `finalize_response_attempt()` atomically updates operation, outbox, artifact, lease, deal, review task, source disposition and response event.
- A source is done only with provider-locatable sent evidence, a verified draft, a durable manual-review task or an explicit terminal discard.
- Every review outcome creates a durable task before any best-effort notification.
- Transport intent is persisted immediately before the external call.
- A timeout after a request starts becomes `outcome_unknown`, never an automatic retry.
- Retry requires reconciliation against Zoho Sent using the exact marker/Message-ID/thread and durable bindings.
- HTTP 200/201/202 without a provider message ID or exact Sent marker is not `sent`.
- Expired claims are classified by a no-send CAS reaper: pre-POST work becomes retry/manual, post-POST work requires reconciliation.
- A restored system remains transport-paused until Zoho Sent, Zoho Drafts, Sheets and recipient bindings are reconciled and the gate is archived with reviewer evidence.
- Mail, Sheets and the no-send claim reaper have separate canonical entrypoints and locks.
- The release monitor compares the active runtime to the behavior manifest and activates a persistent transport kill switch on any critical business invariant.
