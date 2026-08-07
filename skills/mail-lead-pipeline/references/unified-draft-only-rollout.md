# Unified pre-offer send and final-draft rollout

Use this compatibility-named runbook when Zoho Mail and Google Sheets leads enter one RFQ process. Safe price-free pre-offer messages may be sent after all gates; final offers always remain human-reviewed drafts with validated PDFs.

## Safety invariant

Make final-offer sending structurally impossible and pre-offer sending narrowly gated:

- never let `--auto-send`, a wrapper or model output enable transport; only approved durable operational message types may reach the narrow sender;
- keep the final-offer writer limited to Zoho `mode: draft` with a validated PDF attachment;
- keep internal notification transport separate with a hard recipient allowlist and fresh-thread payloads;
- require artifact claims, external message IDs, conversation gates and duplicate protection before a pre-offer send;
- add static regression tests that no final-offer send entry point exists.

A disabled environment variable is not a sufficient safety boundary because it can be re-enabled accidentally.

## Safe migration sequence

1. Pause every recurring source poller before changing shared state or adapters.
2. Back up existing idempotency state and inspect active Drafts/Sent before migration.
3. Introduce the shared registry and tests without changing live source behavior.
4. Integrate one adapter at a time, preserving old source keys so processed items are not replayed.
5. Run syntax checks, module self-tests, focused tests and the full regression suite.
6. Run controlled live probes against allowlisted test identities.
7. Verify pre-offer recipient/threading, final-draft location, PDF attachment, source status and notifications.
8. Rerun each adapter to prove idempotency and inspect Sent to prove one eligible pre-offer send and zero final-offer sends.
9. Resume each poller independently only after its own live acceptance test passes.
10. After resumption, observe at least one scheduled tick per source and verify no duplicate draft.

Never resume pollers merely because partial unit tests pass. If work is interrupted between pause and acceptance, leave jobs paused and report the exact remaining gates.

## Shared registry model

Use one durable SQLite database (WAL mode and a busy timeout) for all adapters. Keep source-specific cursor/state files only for efficient polling; business idempotency belongs in the shared registry.

Minimum entities:

- `deals`: canonical email, company, contact, status, merged facts, thread, current draft, final draft, price and scope;
- `identities`: normalized identity to deal mapping;
- `events`: unique `(source_type, source_key)` plus relation (`new` or `reply`), content hash and source metadata;
- `artifacts`: unique `(deal_id, stage)` claim with state (`creating`, `created`, `retryable_failed`) and the external message or draft ID.

Use an atomic transaction when claiming an artifact. A process restart or concurrent adapter must not send a second discovery response or create a second final-offer draft.

## Identity and cross-source correlation

- Validate and lowercase email addresses.
- Canonicalize Gmail aliases by removing dots and plus-tags from the local part.
- Do not reuse an active deal from email alone; first apply hard thread/message/RFQ/offer links, then safe scoring, then controlled routing for unresolved same-address cases.
- If a new-source event for an active contact conflicts on company or identity, stop and mark `wymaga sprawdzenia` rather than guessing.
- Treat a new event from another source for an active deal as a duplicate; treat a real reply as new conversation evidence.
- Do not ask again for facts already present in the deal registry.

## Facts and stages

Persist only validated offer facts, for example:

- mailbox count,
- CRM choice,
- inquiry source,
- availability of sample requests.

Typical stages:

1. `discovery`: first pre-offer response or missing-data questions;
2. `followup:<source-event>`: later missing-data response for one reply;
3. `final_offer`: complete offer draft with validated PDF.

A final offer is complete only when the external draft ID exists and PDF upload/attachment confirmation succeeded.

## Sheet status propagation

Map shared deal state to stable human statuses:

- `new` → `nowy`
- `analysing` → `w analizie`
- `draft_ready` / waiting for customer → `oczekuje na klienta`
- `offer_ready` → `oferta gotowa`
- invalid, conflicting, risky or failed → `wymaga sprawdzenia`

The Sheets adapter must also synchronize rows whose deal progressed later through a mailbox reply.

## Retry and source isolation

Each source runs as an independent recurring job. A failure in mail must not prevent Sheets, and vice versa.

- keep failed artifact claims retryable rather than permanently consuming the stage;
- retry transient failures on later ticks with bounded counters/backoff;
- escalate after repeated failures while retaining the last error and affected source;
- never mark a source event fully processed before the required artifact or review status is durably recorded.

## Acceptance matrix

Verify at minimum:

- new direct mail → one threaded, price-free discovery response;
- new Sheet row → one new-message, price-free discovery response;
- invalid Sheet email → no send, review status, internal notifications;
- same source event in Sheet and mail → one deal and no duplicate discovery response;
- same customer with two distinct topics → two separate deals;
- customer reply → prior facts retained and only missing facts requested;
- complete reply → final draft with validated PDF;
- final notification → Telegram plus allowlisted internal email containing company, contact, scope, price and draft location;
- second run and scheduled tick → zero duplicate sends or drafts;
- Sent inspection → expected pre-offer messages only and zero final offers.
