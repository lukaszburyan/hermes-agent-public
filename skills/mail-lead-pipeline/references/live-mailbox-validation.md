# Live mailbox validation checklist

Use this when validating the Orchesta RFQ mailbox pipeline on the active Zoho mailbox after code/audit changes or before leaving its systemd timer active.

## Safety boundary

- Live validation may read the active mailbox, auto-send approved operational
  messages only to controlled allowlisted test recipients, and create final
  Zoho drafts after Łukasz has approved the controlled test scope.
- Never send to a real customer during validation and never automatically send
  a final offer.
- Prefer one controlled wrapper pass first; start the recurring timer only after expected Sent/draft artifacts have been verified.
- Keep logs redacted. Do not paste OAuth tokens, raw sensitive mail bodies, or full attachment contents into reports.

## Controlled validation sequence

1. Confirm `hermes-rfq-mail-poller.timer` is stopped or intentionally active and no legacy poller cron exists.
2. Run one manual wrapper pass with the production script, not direct ad-hoc flags, so the same env, token file, state DB, lock and log path are exercised.
3. Inspect the newest poller JSON log for:
   - `listed`
   - `already_processed`
   - `woken`
   - `drafts_created`
   - `final_offer_attempted`
   - `final_offer_drafts_created`
   - `final_offer_pdf_created`
   - `telegram_errors`
4. For every approved operational message type, verify the allowlisted test
   recipient, one matching item in Zoho Sent, the external message ID in the
   outbox, and exactly zero duplicates after replay.
5. If no new eligible message exists, treat that as an idempotency/no-op pass
   only. Do not claim operational auto-send or draft creation was live-tested.
6. When a controlled final-offer test message exists, verify the newest Zoho Drafts item:
   - recipient is the real customer/test sender,
   - subject/thread is correct,
   - `hasAttachment=1` when a final offer was created,
   - body says the offer is attached, does not claim the mail was sent, and does not paste the final price into the body.
7. Download/read the draft attachment metadata/content through the Zoho read client when possible and validate:
   - PDF header is `%PDF-`,
   - page count is 3-4,
   - offer number is present,
   - price is present in the PDF,
   - obvious active-content indicators such as JavaScript/OpenAction/embedded files are absent where the PDF parser exposes them.
8. Verify state/idempotency:
   - `messages.status=done`,
   - operation row is `succeeded`,
   - `external_draft_id` points to the Zoho draft,
   - repeat run reports `already_processed == listed`, `woken=0`, `drafts_created=0`.
9. Verify recent `.err` logs are empty or redacted and non-fatal.
10. Start the recurring timer only after the send/draft and attachment checks pass.
11. Run or wait for one scheduled timer tick and verify it does not create a duplicate.

## Reporting shape

Report operationally:

- Active status: enabled/scheduled or paused.
- Last live run summary.
- Operational send verification: durable type, controlled recipient, one Sent artifact and external message ID.
- Final-draft verification: recipient, subject, attachment count/name, not sent.
- PDF verification: bytes/hash if useful, page count, offer number/price present.
- Idempotency: repeat run no duplicate.
- Errors: telegram/errors/log status.
- Any limitations, e.g. no malicious live attachment test was run.
