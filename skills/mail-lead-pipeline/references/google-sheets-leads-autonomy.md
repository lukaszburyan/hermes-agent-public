# Google Sheets RFQ adapter

> Safe price-free pre-offer auto-send is supported. Final offers remain draft-only with a validated PDF. For the shared source/correlation/rollout contract, read `references/pre-offer-autosend-rollout.md`.

Use this adapter when campaign leads arrive in the configured Google Sheet and must enter the same Orchesta RFQ process as mailbox leads.

## Processing contract

For every non-empty new or changed row:

1. Read client/contact, company, validated email, source and request text.
2. Compute a stable hash over user-provided input columns.
3. Register the source event in the shared deal registry before any customer artifact.
4. Stop and mark `wymaga sprawdzenia` for invalid email, conflicting identity/company, risk, ambiguity or poor fit; notify Łukasz on Telegram.
5. If another source already owns the active deal/discovery artifact, update the row from shared state and do not create another response.
6. For a valid RFQ, atomically claim the discovery stage and send one validated, price-free Zoho message to the validated address.
7. Persist the external message ID before marking the source event complete.
8. Synchronize later shared-deal progress caused by mailbox replies back to the row.

## Sheet columns

Preserve all user-provided columns. Hermes-managed fields should include status, classification, notes, checked time, stable hash, external message ID and final draft ID.

Use these human statuses:

- `nowy`
- `w analizie`
- `oczekuje na klienta`
- `oferta gotowa`
- `wymaga sprawdzenia`

## Safety boundary

The Sheets wrapper must never add `--auto-send`; that legacy flag cannot enable transport. The deployment gate only enables operational processing. Customer sends require an approved durable message type, validated price-free content, an outbox claim, authoritative recipient revalidation, duplicate protection, and a durable external message ID. Final offers remain draft-only. Internal notifications are a separate allowlisted channel.

## Verification checklist

1. Pause the Sheets recurring job before code/state migration.
2. Run syntax checks and focused/full regression tests.
3. Add one controlled valid RFQ row; run the wrapper once.
4. Verify one Zoho Sent message exists with the expected recipient/subject and the row says `oczekuje na klienta`.
5. Run again; verify zero new customer messages.
6. Add an invalid-email row; verify no send, `wymaga sprawdzenia`, and internal notification output.
7. Inject the same contact through mail; verify one deal and no duplicate discovery response.
8. Complete the conversation through a mailbox reply; verify the row advances to `oferta gotowa` after the final PDF draft is created.
9. Inspect Zoho Sent and prove exactly one eligible pre-offer message was sent and no final offer was sent.
10. Resume the Sheets job only after all checks pass and observe one scheduled tick.

## Pitfalls

- A row hash prevents replay within Sheets but does not prevent duplication across mail and Sheets; use the shared registry.
- Do not mark an artifact complete when Zoho did not return a durable external message ID.
- A transient send failure must remain retryable; invalid/conflicting identity is a review stop, not a retry.
- Never write OAuth/token values into logs, skills, memory or reports.
