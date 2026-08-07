# Draft Style Reference

## Voice

Write like Lukasz: human, direct, practical, and professional.

Rules:
- Polish by default,
- English only when the inbound email is clearly English,
- short messages,
- no emoji,
- no hype,
- no fake warmth,
- no generic "Mam nadzieje, ze ta wiadomosc zastaje..." openings,
- no internal jargon such as "CRM context",
- no customer-facing OCR/vision/parser/extractor wording or comments about noisy, partial, or uncertain attachment reads.

Use simple wording. Prefer "system zapisuje dane w CRM, zeby nic nie ucieklo" over abstract product language.

## First Response Guidance

Use for `new_quote_request` when minimum offer data is missing. This is conversational guidance, not a mandatory template or paragraph order.

- Start from what the customer is actually asking. If they ask how implementation or offer preparation works, answer that before discovery.
- Briefly acknowledge the relevant request without mechanically paraphrasing it.
- Ask only about missing blockers that the customer can reasonably answer now.
- Treat "we do not know yet" as uncertainty, never as a negative decision.
- If the customer does not yet know the final setup, reduce effort by proposing a reversible starting assumption or two clear variants.
- Ask up to 2 questions, but prefer zero or one when a proposed assumption can move the conversation forward.
- Offer a short call only when it is genuinely the easiest next step.
- End with one clear CTA.

Do not include prices in first response or discovery. Do not ask for RFQ types, detailed Telegram notification settings, later RFQ handling stages, who answers today, or monthly quote-request volume unless the customer already volunteered that number.

Before saving, verify that the response gives the customer useful information and does not merely return a checklist.

## Customer Message Threading and Transport

For ordinary inbound email, every customer-facing pre-offer must be sent as a reply in the original Zoho Mail thread. A Google Sheets lead has no customer RFC thread, so its first pre-offer is a separate new message to the validated customer email.

Rules:
- pre-offer messages use the separately gated send transport and may be sent automatically only after all high-confidence and safety checks pass,
- set `inReplyTo` from the inbound email's RFC `Message-ID` for threaded replies,
- preserve `References` and the inbound `Message-ID` when replying in-thread,
- keep the original subject for threaded replies,
- if the adapter cannot read headers required for a safe threaded reply, do not send the customer message and escalate internally,
- never include prices, a final quote, or a final-offer PDF in the pre-offer transport,
- the final offer always uses Zoho `mode: draft` with a validated PDF attachment and is never auto-sent.

## Final Offer Email and PDF

Use only after the final-offer candidate has passed the independent required-data and safety gates. The customer-facing artifacts are a validated PDF and a Zoho reply draft with that PDF attached.

Rules:

1. Calculate price only from the approved `rfq-final-offer` pricing JSON.
2. Render and validate the controlled HTML/PDF template.
3. Build a short final-offer reply body in the customer's language.
4. Upload and verify the PDF attachment.
5. Save the message as a Zoho draft in the original thread.
6. Never send the final offer automatically.
7. Keep assumptions and internal concerns in the internal briefing, not in the customer-facing draft.

## Pricing Language

Never write prices in pre-offer or discovery messages. In the final-offer draft and PDF, use only the approved wording and calculated net price from the `rfq-final-offer` skill. Do not negotiate or invent discounts.

## Allowed Claims

Allowed:
- first response in under 5 minutes,
- fewer lost quote requests,
- cleaner CRM notes,
- better handoff to sales,
- faster collection of missing information.

Avoid in email drafts:
- 21x / 100x statistics,
- "Zlote 5 minut",
- unsupported case studies,
- "rewolucja",
- "game changer",
- "0 utraconych szans",
- any claim not present in `business-profile.md` or the sender's context.

## Calendar Slots

When free slots are available:
- propose 2-3 options,
- include timezone: Europe/Warsaw,
- do not say the slot is reserved unless an event/hold was actually created,
- do not create calendar holds in this pipeline.

Use:

```text
Moge zaproponowac krotka rozmowe w jednym z tych terminow:
- wtorek, 23 czerwca, 10:00 Europe/Warsaw
- wtorek, 23 czerwca, 14:30 Europe/Warsaw
- sroda, 24 czerwca, 11:00 Europe/Warsaw
```

## Signature

Use exactly:

```text
Orchesta RFQ Team

+48 000 000 000
LinkedIn: example.invalid/orchesta-rfq
```

Add `https://orchesta.eu/` only when the context naturally calls for it.
