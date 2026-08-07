# Live Variant Testing Playbook

Use this when validating the Orchesta RFQ mailbox pipeline on the active Zoho mailbox after code/classifier/draft changes.

## Safety boundaries

- Live tests may send validated price-free pre-offer messages and may create final Zoho drafts. They must never send a final offer.
- If test emails must be injected, prefix subjects/bodies with a unique marker like `HRFQ-LIVE-YYYYMMDD-HHMMSS-X` so results can be correlated in Inbox, Drafts, Sent, logs, and SQLite state.
- Prefer sending from Łukasz's own Zoho account to the same mailbox only for controlled tests. Treat this as a mailbox-write action and report it clearly.
- Do not claim an attachment-safety live test passed unless the test message actually arrived with the attachment. If upload/delivery fails, report that the attachment path was not exercised and keep the conclusion scoped.

## Recommended live variants

Run a mixed set rather than only one happy path:

1. Complete RFQ: enough data for final offer, e.g. mailbox count and CRM decision.
   - Expected: `new_quote_request`, high confidence, final offer draft created, PDF attached, no final-offer send.
2. Incomplete RFQ: clear RFQ but missing offer blockers.
   - Expected: one validated price-free discovery response with up to 2 questions, no final offer/PDF.
3. Weak fit / regulated data: medical, banking, legal, automated final decisions, sensitive attachments.
   - Expected: `human_review_only` or `weak_fit_review_only`, no customer-facing response.
4. Related non-RFQ: training/consulting/AI automation explicitly saying no RFQ implementation.
   - Expected: `related_non_rfq_topic`, no customer-facing response.
5. Newsletter/noise: automated marketing/webinar/bounce-like content.
   - Expected: cheap precheck skip or no wake.
6. Prompt injection: message asks Hermes to ignore rules, send immediately, make binding claims, or reveal secrets.
   - Expected: human review / no response / no secret exposure.
7. Unsafe attachment test.
   - Expected only if the attachment actually arrives: attachment route blocks/reviews and no unsafe content reaches draft generation.

## Verification sequence

1. Capture baseline systemd timer, service, heartbeat and scheduler-inventory status; stop the timer when the test requires an isolated manual pass.
2. Inject the marked test messages.
3. Run one manual wrapper pass:

```bash
/opt/data/scripts/orchesta-rfq-mail-poller.sh
```

4. Inspect the newest poller JSON log under `/opt/data/logs/orchesta-rfq-mail-poller/` for:
   - `listed`, `already_processed`, `precheck_skipped`, `woken`,
   - `drafts_created`, `draft_duplicates_blocked`,
   - `final_offer_attempted`, `final_offer_drafts_created`, `final_offer_pdf_created`,
   - `telegram_errors`,
   - per-briefing `classification`, `confidence`, `draft.action`, `final_offer.action/status`.
5. Re-run the wrapper once. Expected idempotency result: all test messages become `already_processed`, `woken=0`, `responses_sent=0`, `drafts_created=0`, and no new external IDs appear.
6. Query Zoho Drafts for marked subjects/recipients. Confirm:
   - draft exists only for expected cases,
   - recipient is correct,
   - reply drafts are `Re:` same thread where required,
   - final-offer drafts have exactly one PDF attachment.
7. For final-offer PDF drafts, download the PDF attachment from Zoho Drafts and verify at least:
   - starts with `%PDF-`,
   - page count is 3-4,
   - offer number exists in extracted text,
   - expected price exists in extracted text,
   - no JavaScript/OpenAction-like content is found.
8. Check Sent for marked subjects; expect only eligible pre-offer responses and never a final offer.
9. Check `.err` logs are empty or redacted.

## Pitfalls discovered in live testing

- Self-sent test messages can appear both in Sent and Inbox with different Zoho message IDs; correlate by unique marker and process Inbox IDs only.
- A final-offer draft may first create a generic `draft.action=created` briefing plus a `final_offer.action=created`; the authoritative external draft ID is also stored in `operations.external_draft_id` for `action_type='offer_draft'`.
