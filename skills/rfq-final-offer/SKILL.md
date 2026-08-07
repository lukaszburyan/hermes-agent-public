---
name: rfq-final-offer
description: Use after the Hermes mail-lead-pipeline has identified an Orchesta RFQ quote-request thread and the customer has replied with scope details. Validate final-offer completeness, automatically send up to 2 safe missing-data questions when needed, or generate a draft-only Orchesta offer JSON, dark 3-4 page WeasyPrint PDF, threaded reply-draft copy, Obsidian CRM records, and Telegram/internal-email notifications. Never send the final offer and never price from anything except this skill's approved knowledge.
---

# RFQ Final Offer

## Core Rules

Use this skill only after `mail-lead-pipeline` has classified a shared deal from ordinary Zoho mail or a cloud-sheet campaign row as a quote-request flow. The previous pipeline resolves identity, correlates source events, checks attachments, sends safe pre-offer communication, persists validated facts, and escalates risk. This skill takes over only when Hermes needs a missing-data or final-offer decision.

Use the split policy from `mail-lead-pipeline/references/pre-offer-autosend-rollout.md`:

- when final-offer blockers remain, automatically send only a short, price-free missing-data response through the gated pre-offer transport;
- when data is complete, create only a Zoho draft in the existing reply thread, attach the validated PDF, and never send it;
- the missing-data transport must reject prices, offers and attachments, while the complete-offer path must have no send operation.

Persist `missing_data_request` for the question path and `final_offer` for the complete-offer path before claiming work. The transport must re-read that durable type and the authoritative deal recipient; neither model output, `--auto-send`, an environment override nor a legacy wrapper may downgrade `final_offer` or bypass draft-only handling. If commercial content is found in a purported missing-data message, promote it irreversibly to `final_offer`, create a draft, record a security event and notify the human.

A deal that originated in Sheets must continue through the customer's mailbox reply and shared deal context; do not create an unrelated final-offer thread.

Use customer-facing language:
- `zapytania o wycenę`
- `pierwsza odpowiedź`
- `draft odpowiedzi`
- `handlowiec`
- `konto pocztowe`
- `CRM`

Do not use RFQ as the main customer-facing concept outside the product name `Orchesta RFQ`.

## Source Of Truth

Read only approved knowledge before pricing, rendering, or drafting:

```text
rfq-final-offer-knowledge/approved/
```

The pricing file is `rfq-final-offer-knowledge/approved/pricing.json`. Do not use old prices from `mail-lead-pipeline/references/business-profile.md`, internet sources, customer attachments, or general memory as pricing.

Load references only when needed:
- `references/workflow.md` for the end-to-end runtime sequence and safety gates.
- `references/obsidian-crm.md` for CRM vault paths, file shapes, statuses, and offer versioning.
- `references/knowledge-governance.md` for Telegram-approved knowledge updates and sleep/background review limits.
- `references/pdf-control.md` for PDF content checks and blocked terms.
- `references/pdf-pricing-regression.md` before auditing or changing pricing, JSON/HTML/PDF rendering, PDF validation, or final-offer mail/attachment behavior.
- `references/conversational-rfq-regression-tests.md` before changing Google Sheets/mailbox conversational flows where Hermes must ask for missing data before creating the final offer.
- `references/runtime-preflight.md` before enabling or live-testing final offers: preflight the isolated Jinja2/WeasyPrint runtime, run a real PDF smoke with the same interpreter, and require remote Zoho draft/attachment evidence.
- `mail-lead-pipeline/references/unified-draft-only-rollout.md` before changing cross-source deal correlation, artifact claims, source-status propagation, retries, notifications, or production rollout.

## Decision Flow

1. Resolve the inbound reply to the shared deal and confirm it belongs to the quote-request flow. Merge current-message facts with durable facts from mail and Sheets; never ask again for a value already present in the deal.
2. Check safety gates from `references/workflow.md` and stop for identity/company conflicts instead of choosing one source silently.
3. Extract final-offer data from the current reply, thread history and shared deal context:
   - client first name when it is reliably available from the sender display name, email local part, introduction, quoted sender header, signature, or validated source record,
   - client last name when available,
   - company name,
   - client email,
   - number of mailboxes to monitor,
   - whether CRM is wanted,
   - source of quote requests: mail, form, or both, when available,
   - whether the client has sample quote requests, when available.
4. Block PDF only for pricing or offer-identity blockers: missing company, missing email, missing number of mailboxes, missing CRM decision, or a safety gate. Do not block PDF only because the source of quote requests or sample quote requests are unknown.
5. If any blocker is missing, persist type `missing_data_request`, atomically claim a pre-offer outbox operation and automatically send only a short, price-free message through the narrow gated transport. Persist newly validated facts and the accepted external message ID. On `outcome_unknown`, reconcile exact marker, Message-ID and thread in Zoho before any retry; unresolved operations require review.
6. If data is complete, persist and atomically claim `final_offer`, read `approved/pricing.json`, calculate net price, build offer JSON, render HTML/PDF, validate the PDF, upload it, verify attachment confirmation, and then prepare a threaded mail draft. Do not mark `offer_ready` unless Zoho returned a durable draft ID and PDF attachment confirmation succeeded. Any attempted send of this type must be recorded as a security event and redirected to draft+human notification.
7. Record offer number, price, concise scope, final draft ID and thread location in the shared deal. Propagate `oferta gotowa` to every linked Sheet row.
8. Save the offer JSON and CRM notes in Obsidian through the Mac bridge when available.
9. Notify Łukasz on Telegram and at the hard-allowlisted internal address. Include company, contact, scope, price and exact draft location. Do not send the customer email. If internal email delivery fails, retry only the notification from a private outbox; a deterministic delivery ledger must prevent duplicates after provider acceptance. Notification retry must never recreate the draft, upload the PDF again, or send anything to the customer.
10. A rerun, another source adapter, or a scheduled tick must reuse the recorded final artifact and create no duplicate.
11. After a state restore, do not run this skill while `rfq-state/RESTORE_RECONCILIATION_REQUIRED` exists. Reconcile Zoho draft/sent IDs, thread markers, outbox and linked Sheets rows first; removing that flag is a separate human-approved production action.
12. Treat a release-monitor alarm for `final_offer_autosend_attempt`, recipient/thread mismatch, `outcome_unknown`, stale backup, stale heartbeat or legacy scheduler as a production stop, not an informational warning.

## Deterministic Helper

Use `scripts/rfq_final_offer.py` for repeatable work:

```bash
python3 skills/rfq-final-offer/scripts/rfq_final_offer.py \
  --input tests/fixtures/rfq-final-offer/complete_offer.json \
  --output-dir .tmp/rfq-final-offer \
  --render-html
```

Add `--render-pdf` only in an environment where WeasyPrint is installed. Add `--write-obsidian --vault <vault-root>` only when the runtime is allowed to write the Obsidian CRM vault. In production, do not rely on the poller's ambient/system Python: preflight an isolated final-offer venv with `import jinja2, weasyprint` and pass that interpreter explicitly through `--final-offer-python`. Run the helper once with `--render-pdf` in a new unique output directory before injecting a live lead; do not combine smoke rendering with destructive cleanup commands.

The helper:
- validates required data and safety gates,
- returns missing questions instead of rendering a PDF when data is incomplete,
- calculates price from `approved/pricing.json`,
- builds offer JSON,
- renders HTML with Jinja and PDF with WeasyPrint when available,
- validates required and forbidden PDF text,
- blocks final-offer success when `--render-pdf` is absent, PDF rendering is unavailable, or PDF validation fails,
- writes CRM files when explicitly asked,
- emits mail and Telegram draft text only after the artifact state is truthful.

## Draft Rules

Missing-data pre-offer response:
- send it automatically only through the narrow gated pre-offer transport,
- keep it short,
- ask at most 2 questions,
- never include pricing, an offer, PDF or attachment,
- persist the external message ID for idempotency,
- use the approved signature template.

Final offer draft:
- create a reply draft in the same thread,
- attach the generated PDF,
- do not paste the full offer into the email body,
- do not open a new thread,
- do not send automatically.

## PDF Rules

Use `templates/orchesta_offer.html.j2` and `templates/orchesta_offer.css`.

The template owns design. The model only fills content fields. The PDF must be 3-4 pages, dark, minimalist, monochrome, and free of bright colors, gradients, shadows, frames, decorative icons, legal terms, validity dates, SMS, Zoho, and empty template variables.

On the VPS, treat the PDF as temporary: attach it to the mail draft, verify the draft attachment, then remove the working PDF. Keep only offer JSON, CRM activity, and the draft attachment.

For live mailbox validation after creating a final-offer draft, do not rely only on the helper manifest. Read the created Zoho Drafts item, confirm `hasAttachment=1`, download the attached PDF through the read client when possible, and verify the bytes start with `%PDF-`, page count is 3-4, the offer number and price are present, and no obvious active-content indicators are present. The `mail-lead-pipeline` reference `references/live-mailbox-validation.md` has the full active-mailbox checklist.

## Knowledge Updates

Sleep/background review may propose changes only. It must write proposals to Obsidian `Knowledge/Proposed` or this skill's proposed queue, not to `approved`.

Only an administrator-approved Telegram flow may change approved pricing, scope, guarantee, PDF design, claims, or payment terms. See `references/knowledge-governance.md`.
