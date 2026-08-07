# Mail lead pipeline — audit and regression workflow

Use this reference when auditing or changing the Orchesta RFQ mailbox pipeline.

## Safety boundary

Before any audit or regression run:

- Stop the active production mailbox systemd timer first and verify that no legacy poller cron exists.
- Do not run `--live`, OAuth/device flow, real Zoho mailbox polling, real CRM writes, or timer/service execution unless Łukasz gives a separate explicit approval for that step.
- Use `FakeZohoClient`, fixtures, synthetic messages, and local temp directories.
- Keep the production job paused after the audit unless Łukasz explicitly asks to resume it.

## Minimum offline regression set

Run these after classifier/poller/draft changes:

```bash
python3 /opt/data/execution/mail-lead-pipeline-dry-run.py --fixtures /opt/data/tests/fixtures/mail-lead-pipeline/cases.json
python3 /opt/data/execution/zoho_mail_poller.py --self-test
python3 /opt/data/execution/zoho_reply_draft.py --self-test
python3 /opt/data/tests/test_orchesta_rfq_audit_regressions.py
```

If the synthetic audit harness exists, also run:

```bash
python3 /opt/data/execution/audit_mail_lead_pipeline_synthetic.py
python3 /opt/data/tests/orchesta_rfq_full_audit.py
```

## Required audit coverage

A rigorous audit should include at least 100 synthetic cases covering:

- complete and incomplete RFQs,
- broad/general inbound automation inquiries,
- related-but-not-RFQ topics,
- weak fit: regulated domains, low volume, no repeatable source, no-human final send expectations,
- human review: credentials, security, legal, data deletion/access requests,
- existing client, existing thread, same-domain new person,
- valid/invalid website forms,
- newsletters, bounces, vendor/admin/billing,
- suspicious links,
- executable/macro/wrong-extension/large/corrupt/path-traversal attachments,
- prompt injection in body and attachment text,
- duplicate processing and retry/idempotency.

## Metrics to report

Report at minimum:

- classification accuracy,
- RFQ binary precision/recall,
- false positive and false negative counts,
- routing mismatches: draft vs Telegram,
- unnecessary escalations,
- missing escalations,
- repeat processing result: `already_processed == listed`, `woken=0`, `drafts_created=0`,
- timing for 1, 10, 50, 100, and 500 synthetic messages.

## Known durable pitfalls fixed in 2026-07 audit

- Related/non-RFQ phrasing such as “warsztat”, “sales consulting”, and explicit negations like “not quote request automation” must beat broad RFQ keywords.
- Weak-fit phrasing like “raz na kwartał”, “bez powtarzalnego źródła”, “broker ubezpieczeniowy”, and “bez udziału człowieka” must not become automatic customer drafts.
- Ambiguous “dostałem/otrzymałem ofertę” without Orchesta/RFQ/request context should be manual review, not a quote request.
- Data deletion/access requests must be `human_review_only`.
- Telegram transport failure must not abort the poll loop or leak exception text containing secret-looking strings.

## Acceptance thresholds

Required gates before controlled split-policy production testing:

- P0 count: 0.
- RFQ binary recall: 1.0 on synthetic set.
- RFQ binary precision: at least 0.98.
- Classification accuracy: at least 0.97.
- Missing escalation: at most 1%.
- Duplicate draft rate on repeat processing: 0.
- Secret-pattern hits in generated reports/logs: 0.
