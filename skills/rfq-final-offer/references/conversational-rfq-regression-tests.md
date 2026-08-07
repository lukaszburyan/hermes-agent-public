# Conversational RFQ regression tests

Use this when changing the RFQ lead pipeline, Google Sheets lead handling, mailbox thread handling, or final-offer gating.

## Durable scenario to preserve

The system must support a two-step commercial conversation, not only one-shot complete leads:

1. **Incomplete lead arrives** from either:
   - Google Sheets / Google Forms / Google Drive-backed form row, or
   - a direct Zoho mailbox thread.
2. The lead is classified as high-confidence `new_quote_request`, but final-offer data is incomplete.
3. The final-offer helper must return `status=awaiting_data`, create a short missing-data email, ask at most 2 questions, and avoid pricing/final-offer language.
4. After the customer replies with the missing data, the same flow must build final-offer input from the accumulated context and create the final offer PDF/draft artifacts.

## Regression tests added

In `/opt/data/tests/test_orchesta_rfq_audit_regressions.py`:

- `test_google_sheets_form_lead_asks_questions_before_final_offer_after_reply`
  - starts from a Google Forms/Sheets lead with incomplete scope,
  - verifies RFQ classification and `offer_status=missing_data`,
  - verifies missing questions for mailbox count + CRM,
  - simulates the customer reply and verifies `offer_draft_created` with a valid PDF.

- `test_mail_thread_asks_questions_then_creates_final_offer_after_reply`
  - starts from a direct mail lead with incomplete context,
  - verifies missing-data question flow,
  - simulates a threaded customer reply,
  - verifies thread-history extraction of company/name/scope and successful final PDF offer.

## Verification commands

Preferred when WeasyPrint/Jinja dependencies are available:

```bash
python -m pytest /opt/data/tests/test_orchesta_rfq_audit_regressions.py::test_google_sheets_form_lead_asks_questions_before_final_offer_after_reply \
  /opt/data/tests/test_orchesta_rfq_audit_regressions.py::test_mail_thread_asks_questions_then_creates_final_offer_after_reply \
  -v --tb=short -o 'addopts='
```

Full local RFQ regression file:

```bash
python -m pytest /opt/data/tests/test_orchesta_rfq_audit_regressions.py -q -o 'addopts='
```

Script fallback, useful where pytest is not installed but dependencies are present:

```bash
python /opt/data/tests/test_orchesta_rfq_audit_regressions.py
```

## Pitfalls

- Do not treat incomplete RFQ leads as failed leads. They should usually ask clarifying questions first.
- Do not generate pricing or a final PDF until pricing blockers are resolved: company, email, mailbox count, CRM decision, and safety gates.
- Google API dependencies are only needed for live Google Sheets access. Lightweight tests for pure helper functions should be able to import the module without contacting Google.
- For mail threads from free-mail senders, include explicit company text in the body/signature; otherwise domain-derived company fallback may be a placeholder.
