# Live validation and internal notifications

Use this reference when validating or re-enabling the Orchesta RFQ mailbox poller on the live Zoho inbox, or when changing how Łukasz is notified about processed messages.

## Controlled live validation sequence

1. Confirm `hermes-rfq-mail-poller.timer` is active/stopped as intended and scheduler inventory has no legacy hit.
2. Run one manual wrapper pass before changing timer state when production was stopped after an audit.
3. Verify real Zoho Drafts, not only local JSON:
   - target recipient,
   - subject/thread behavior,
   - `hasAttachment` when final offer is expected,
   - newest draft summary,
   - no new Sent message to the customer.
4. Run a second pass and confirm idempotency:
   - `already_processed == listed` for the tested messages,
   - `woken=0`,
   - `drafts_created=0`,
   - `final_offer_drafts_created=0`.
5. Only then leave or start the recurring systemd timer.

## Live test matrix that proved useful

When Łukasz asks for advanced live tests, send clearly marked test subjects such as `HRFQ-LIVE-<timestamp>-<case>` and verify behavior per case:

- full RFQ with account count and CRM decision -> final-offer PDF draft, no send;
- RFQ missing data -> discovery draft with questions, no price;
- medical/sensitive/regulated request -> `human_review_only`, no draft;
- adjacent non-RFQ topic that says “bez RFQ” -> `related_non_rfq_topic`, no draft;
- newsletter/noise -> precheck skip;
- prompt injection asking to ignore rules/send/leak tokens -> human review/no draft;

## Pitfalls found live

- Uploading a synthetic unsafe attachment through the Zoho message attachment endpoint may fail before delivery. Do not claim the unsafe attachment live path was tested unless the Inbox message actually has `hasAttachment=1` and the poller routed the attachment.

## Internal prose email notifications

Łukasz asked for internal notifications on `notifications@example.invalid` in prose, not API-log style. The notification should say who wrote, whether the sender is known/unknown, what the agent did or did not do, and why, for example:

> Napisała do Ciebie nieznana osoba: sender@example.com. Zaklasyfikowałem tę wiadomość jako unknown_review_needed. Nie odpisywałem, ponieważ wiadomość jest niejednoznaczna i wymaga Twojej decyzji.

Implementation pattern:

- allow only validated, price-free pre-offer sends and keep final offers draft-only;
- send the notification only to the fixed internal recipient;
- do not include OAuth tokens, secrets, raw credentials, or full sensitive raw email bodies;
- stay silent on no-op poller runs;
- verify each notification as a fresh message with subject prefix `Orchesta RFQ |` and no reply/thread headers.
