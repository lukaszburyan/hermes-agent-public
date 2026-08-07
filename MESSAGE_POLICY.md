# Hermes RFQ message policy

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

Any disagreement is fail-closed, recorded as a security event and routed to human review.

## Final-offer escalation

The deterministic classifier upgrades a message to `final_offer` when process/deal stage, price/cost/discount/subscription/payment/validity/acceptance language, final scope, or offer-shaped PDF/document indicates commercial finality. Numbers that are clearly phones, dates, dimensions, quantities, order numbers or technical data do not alone create an offer.

An attempted automatic final-offer send is blocked at the transport boundary, recorded as `final_offer_autosend_attempt`, converted to a draft when the draft adapter succeeds, and reported to a human. The outbox records `draft_created` or `manual_review`; it can never record `sent:final_offer` through this transport.

## Reliability policy

- A claim binds owner, operation, lease, content hash, type, recipient, durable contact identity, source, canonical stage, thread and marker.
- Transport intent is persisted before the external call.
- A timeout after a request starts becomes `outcome_unknown`, never an automatic retry.
- Retry requires reconciliation against Zoho Sent using the exact marker/Message-ID/thread and durable bindings.
- A restored system remains transport-paused until Zoho Sent, Zoho Drafts, Sheets and recipient bindings are reconciled and the gate is archived with reviewer evidence.
- Mail and Sheets have separate canonical entrypoints and locks.
