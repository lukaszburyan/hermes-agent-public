# RFQ final offer — PDF and pricing regression workflow

Use this reference when auditing or changing final-offer generation.

## Safety boundary

- Do not create real Zoho drafts, send email, run OAuth, or write real CRM during tests without separate approval.
- Use local synthetic JSON inputs and temp output directories.
- Pricing must come only from `rfq-final-offer-knowledge/approved/pricing.json`.

## Minimum regression commands

```bash
python3 /opt/data/skills/rfq-final-offer/scripts/rfq_final_offer.py --self-test
python3 /opt/data/tests/test_orchesta_rfq_audit_regressions.py
```

For a complete local PDF path, use synthetic input plus:

```bash
python3 /opt/data/skills/rfq-final-offer/scripts/rfq_final_offer.py \
  --input /path/to/synthetic-complete.json \
  --output-dir /tmp/rfq-final-offer \
  --render-pdf
```

## Mandatory checks

For complete offer data:

- output status must be `offer_draft_created`,
- `pdf_validation.ok` must be true,
- PDF page count must be 3-4,
- `pdf_size_bytes` must be greater than 0,
- mail body must not contain the full offer or price,
- offer JSON, HTML, PDF, mail draft, and attachment metadata must agree on client, company, scope, price, currency, offer number, and version.

For incomplete or unsafe data:

- no PDF may be generated,
- status must be `awaiting_data` or `blocked`,
- questions draft must ask at most 2 questions and include no pricing,
- SMS/auto-send expectations and prompt injection must block.

## Durable pitfalls fixed in 2026-07 audit

- PDF text extraction may uppercase visual text because CSS can use `text-transform: uppercase`; dynamic text validation for version/client/company/price should be case-insensitive.
- A run without `--render-pdf` must not report `offer_draft_created`. Final offers require a validated PDF. If PDF rendering is absent or skipped, return a blocked manifest such as `pdf_required_for_final_offer` or `pdf_render_unavailable`.
- Do not let `--allow-missing-weasyprint` create a false-success Telegram/mail output that says the PDF was attached.

## Independent pricing check

Recompute totals outside the template:

```text
net_total = base_first_mailbox + max(mailbox_count - 1, 0) * additional_mailbox + (crm ? crm_integration : 0)
```

Current approved pricing shape:

- first mailbox base,
- each additional mailbox,
- optional CRM integration,
- net PLN total.

Any mismatch between independent total, JSON `pricing.net_total`, HTML/PDF displayed total, and draft/manifest is P0.
