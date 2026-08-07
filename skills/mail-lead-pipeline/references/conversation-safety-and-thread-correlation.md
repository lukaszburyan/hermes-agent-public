# Conversation Safety And Thread Correlation

Use this reference before changing automatic replies, deal correlation, or final-offer handoff.

## Safety invariant

The highest-priority invariant is: **Hermes must not create or send a customer-facing artifact after a salesperson has replied manually in the same conversation.**

Enforce it per exact `(deal_id, account_id, thread_id)`, never per customer email address alone.

## Sent-folder guard

Before every customer-facing action:

1. Resolve the exact Zoho `Sent` folder by folder type/name; never fall back to Inbox or the first folder.
2. Read a bounded but complete-enough Sent window for the target thread.
3. Compare Sent message IDs with durable IDs previously recorded as Hermes automatic responses.
4. Treat every same-thread Sent message not proven to be Hermes-generated as human/unknown.
5. Persist `human_takeover` for that exact conversation and block the action.
6. If Sent cannot be resolved/read, a message lacks a usable ID, the window is saturated, or the thread ID is unavailable, fail closed. Do not send or create a customer-facing draft.
7. Run the guard once during decision-making and again inside the real creator immediately before the external Zoho operation. For final offers, also guard before attachment upload and immediately before draft creation.

Do not weaken fail-closed behavior merely to preserve an old test fixture. Update fixtures to model an accessible empty Sent folder, a real thread ID, and a current timestamp when the test is meant to exercise a later stage.

## Durable conversation control

Persist explicit states rather than inferring control from one poll run:

- `active` — automation may continue if all other gates pass;
- `paused` with reason `human_takeover` — salesperson or unknown actor replied in Sent;
- `paused` with a conversation-limit reason — bounded automation handed off to a person;
- explicit `resume` — operator action only, with actor, reason, timestamp, and audit event.

Resume must reset counters from a durable baseline so old history does not immediately trip the same limit again. It may clear `review_required` only when that status was created by the same pause reason; it must not clear unrelated review states.

Provide an operational CLI that requires exact deal/account/thread identifiers, validates the deal exists, never sends mail, and returns structured JSON. The CLI defaults to the same shared registry as both production pollers: `/opt/data/.tmp/orchesta-rfq-unified.sqlite3`.

```bash
python3 /opt/data/execution/conversation_control.py status \
  --deal-id DEAL_ID --account-id ZOHO_ACCOUNT_ID --thread-id ZOHO_THREAD_ID

python3 /opt/data/execution/conversation_control.py pause \
  --deal-id DEAL_ID --account-id ZOHO_ACCOUNT_ID --thread-id ZOHO_THREAD_ID \
  --actor lukasz --reason explicit_manual_takeover

python3 /opt/data/execution/conversation_control.py resume \
  --deal-id DEAL_ID --account-id ZOHO_ACCOUNT_ID --thread-id ZOHO_THREAD_ID \
  --actor lukasz --reason verified_manual_resume
```

Never infer missing IDs or resume every case belonging to an email address. Read the structured `status` output first, then resume only the intended tuple.

## New thread and new case policy

Thread binding is authoritative when present. A new thread from an email address that already has an active deal must not inherit the old deal merely because the address matches.

Correlation order:

1. Idempotent match on `(source_type, source_key)`.
2. Exact provider/account/thread binding.
3. Explicit reply relation only when a single unambiguous active candidate exists.
4. Proven cross-source duplicate (for example, the same campaign event arriving through Sheets and mail).
5. New case when a different thread has strong distinct-topic evidence: different subject, company, semantic scope, explicit “new project/case”, or a sufficiently long gap.
6. Otherwise create a separate review-required case with no inherited context.

Store email identity as many-to-many with deals. A legacy one-email-to-one-deal table may remain for compatibility, but it must not drive correlation.

## Long-conversation handoff

Pause automation at the first limit reached:

- 7 conversation messages after the current automation baseline;
- 5 Hermes automatic replies after the baseline; this limit blocks another pre-offer response but does not alone block a safe final-offer draft;
- 7 days or more since the baseline/first observed conversation message;
- material change in agreed scope.

Observe both inbound customer messages and outbound Sent messages durably. Use provider timestamps normalized to UTC. Preserve prior accepted facts when a material conflict appears; record the proposed change as evidence and hand off instead of silently overwriting scope.

## Migration and test discipline

Use expand-only SQLite migration:

- create new identity, thread-binding, conversation-control, observed-message, and audit-event tables with `CREATE TABLE IF NOT EXISTS`;
- copy legacy identities with `INSERT OR IGNORE`;
- keep operations transactional with `BEGIN IMMEDIATE` and rollback on error;
- retain durable external message IDs so Sent scanning can distinguish Hermes from human responses.

TDD sequence:

1. RED tests for pause persistence and explicit resume.
2. RED tests for distinct new thread, ambiguous new thread, and exact bound-thread reply.
3. RED tests for all four conversation limits.
4. Direct unit tests for Sent scope and fail-closed behavior: same-thread unknown Sent blocks, other-thread Sent is ignored, missing/unreadable/saturated Sent blocks.
5. Integration test: manual same-thread Sent message prevents sender invocation.
6. Integration test: missing Sent fails closed.
7. Integration test: paused conversation blocks both pre-offer and final-offer paths.
8. Run focused registry/mail tests, then the complete regression suite and self-tests.

Test one safety responsibility directly before testing the full poller. If an integration fixture stops before the intended branch, expose the structured decision/action/reason and identify the earlier gate; do not loosen that gate or infer the cause merely from the sender not being called. Full-pipeline fixtures must also stay inside unrelated age/message/reply limits unless those limits are the subject under test.

When old fixtures fail under the new policy, inspect the actual decision reason before changing code. Typical durable fixture requirements are: explicit Sent folder, thread ID, and a non-stale timestamp. Preserve the safety invariant and repair the fixture when it was unrealistic.
