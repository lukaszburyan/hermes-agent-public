# Google Sheets campaign leads for Orchesta RFQ

Use when Google Sheets is a campaign-lead source feeding the Orchesta RFQ pipeline.

## Architecture

Current production shape:

```text
Google Sheets lead row
  -> deterministic RFQ classifier
  -> unified lead/deal registry and cross-source deduplication
  -> gated Zoho pre-offer send for safe high-confidence RFQ leads
  -> update Hermes status/hash/sent-id columns in the sheet
  -> internal prose notification to Łukasz
```

Keep this as a separate poller/wrapper from the Zoho mailbox poller. Reuse the same classifier, composer, unified registry, transport safety gates, and internal-notification style so mailbox and sheet leads behave consistently.

## Sheet columns

Expected base columns:

- `Data`
- `Imię`
- `Email`
- `Telefon`
- `Firma`
- `Wiadomość`
- `Źródło`

Hermes-managed columns:

- `Hermes status`
- `Hermes klasyfikacja`
- `Hermes uwagi`
- `Hermes sprawdzono`
- `Hermes hash`
- `Hermes draft id`
- `Hermes sent id`

Do not require users to maintain the Hermes columns manually; the poller can add/maintain them.

## Idempotency

Use both visible sheet state and local state:

- process rows with empty `Hermes status`,
- compute a hash from the base lead columns,
- write `Hermes hash` after processing,
- skip rows whose status/hash already match,
- on changed rows, reprocess only if the hash changed or an explicit test/reprocess flag is used.

This prevents duplicate notifications and duplicate Zoho drafts.

## Current production auto-send mode

The deployment explicitly enables operational processing:

```bash
HERMES_OPERATIONAL_AUTOSEND_ENABLED=1
HERMES_ALLOW_PRE_OFFER_SEND=1
```

The wrapper never adds `--auto-send`. The poller persists the message type, and the transport re-checks type, recipient, deal and outbox immediately before sending.

A customer-facing message is sent only when all of these are true:

- classification is `new_quote_request`,
- confidence is `high`,
- customer email is valid,
- the row does not contain explicit non-RFQ, prompt-injection, secret, or binding-offer signals,
- the unified registry does not mark the identity as conflicting or the event as a cross-source duplicate,
- the response artifact can be atomically claimed for this exact row/hash.

The sent message is a separate Zoho pre-offer message, not a draft and not a final offer. It contains no prices or attachments and uses only source facts actually stored in the Sheet row. After Zoho returns a stable message ID, write `Hermes status=oczekuje na klienta` and persist `Hermes sent id` locally and in the sheet.

Final offers are never sent from the Sheets adapter. They continue through the shared mail/thread pipeline and remain Zoho drafts with validated PDFs.

If the send outcome is unknown or lacks a stable external message ID, do not retry blindly. Mark the deal and row `wymaga sprawdzenia` to prevent a duplicate customer message.

`--auto-draft` remains only as a legacy compatibility flag and does not represent the current production behavior.

## Internal notifications

Use the same prose style as mailbox RFQ notifications, but mention the source as Google Sheets / campaign lead rather than mailbox. State explicitly whether the safe pre-offer was sent, blocked as a duplicate, or held for review. Never imply that a final offer was sent.

## Validation checklist

Before enabling or changing recurring polling:

1. Confirm Google OAuth/token can read and edit the sheet.
2. Write/read a test row and verify all Hermes columns, including `Hermes sent id`.
3. Run classification variants: safe RFQ, incomplete RFQ, safety/human-review, adjacent non-RFQ, and invalid email.
4. Test with transport disabled first, then enable `HERMES_OPERATIONAL_AUTOSEND_ENABLED=1` and `HERMES_ALLOW_PRE_OFFER_SEND=1` for one controlled, allowlisted row.
5. Verify the message in Zoho Sent, the stable external message ID in the sheet/registry, and `Hermes status=oczekuje na klienta`.
6. Rerun unchanged input and confirm no duplicate message is sent.
7. Create the same lead through another source and confirm cross-source deduplication blocks the second response.
8. Confirm final offers still remain Zoho drafts with validated PDF attachments.
