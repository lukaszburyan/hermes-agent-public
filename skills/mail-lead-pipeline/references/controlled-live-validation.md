# Controlled live validation after offline audit

Use this when Łukasz approves a real-mailbox validation after offline tests, but before resuming the recurring poller.

## Sequence

1. Stop `hermes-rfq-mail-poller.timer` and confirm its oneshot service is inactive.
2. Capture the exact Zoho source `message_id` and exact controlled sender address.
   Run the production wrapper once manually instead of invoking the timer, pinning
   the run to both values:

```bash
HERMES_CONTROLLED_SOURCE_MESSAGE_ID='<exact-zoho-message-id>' \
HERMES_CONTROLLED_SENDER='<controlled-sender@example.com>' \
HERMES_CONTROLLED_SEND_TELEGRAM=1 \
  /opt/data/scripts/orchesta-rfq-mail-poller.sh
```

   Both variables are mandatory together. The poller leaves every non-matching
   Inbox row untouched, including its idempotency state. Require
   `controlled_selection_enabled=true`, `controlled_selection_matched=1`, and
   confirm that `controlled_selection_skipped` accounts for the other listed
   rows. `HERMES_CONTROLLED_SEND_TELEGRAM=1` is accepted only with both
   selection values and lets the one-shot run verify the real Telegram channel
   without starting the recurring timer. A zero match is a failed test, not a
   harmless no-op.

3. Inspect the newest JSON log under `/opt/data/logs/orchesta-rfq-mail-poller/` and summarize only counters such as:

```text
listed, controlled_selection_matched, controlled_selection_skipped,
already_processed, woken, would_create, drafts_created,
final_offer_attempted, final_offer_drafts_created, final_offer_pdf_created,
telegram_sent, telegram_errors
```

4. If the run finds only already-processed messages, do **not** start the timer yet. Ask/wait for a new controlled test email, because idempotency was verified but artifact creation was not.
5. If a final-offer draft was created, verify it in Zoho Drafts before starting the timer:
   - recipient is the intended customer/test address,
   - subject is the expected reply/new-message subject,
   - message is still a draft, not sent,
   - final-offer draft has `hasAttachment=1` and the poller summary says `pdf_attached=true`,
   - final-offer manifest/status says `offer_draft_created`,
   - no `telegram_errors`, `final_offer_failed`, or `draft_duplicates_blocked` unless intentionally testing failures.
6. Only after all send/draft evidence is verified, start the systemd timer:

```bash
systemctl start hermes-rfq-mail-poller.timer
```

7. List jobs again and confirm:
   - the timer is enabled and active,
   - only the canonical service is referenced,
   - the next trigger is in the future,
   - the next tick advances the mailbox heartbeat.

## Useful Zoho read-only verification pattern

When a manual live run reports a created draft, use the existing read-only Zoho client to list the Drafts folder and verify metadata. Do not print tokens, full bodies, or sensitive attachment content.

A healthy final-offer draft verification looks like:

```text
DRAFT_FOLDER Drafts
subject: Re: <original RFQ subject>
toAddress: <intended-recipient>
fromAddress: rfq-mailbox@example.invalid
hasAttachment: 1
summary: short offer-mail opening
```

The poller briefing should show the durable details:

```json
{
  "draft": {"action": "created", "kind": "first_response", "thread_action": "reply_to_inbound"},
  "final_offer": {
    "action": "created",
    "status_name": "offer_draft_created",
    "pdf_attached": true,
    "pdf_removed": true,
    "price_net_display": "... zł"
  }
}
```

## Pitfalls

- A no-op live run proves state/idempotency, not draft creation. Do not treat it as enough to resume production polling if the agreed procedure requires checking a newly created draft.
- Do not start the recurring timer before verifying the expected Zoho Sent/draft artifacts.
- Keep reporting draft-only clearly: created Zoho draft does not mean email was sent.
