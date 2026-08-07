# Telegram Escalation Reference

## Goal

Escalate only the cases that need Lukasz's judgment. The message must be short, concrete, and useful.

## Escalate Without Customer Draft

Block customer-facing draft and send Telegram when:
- topic is outside Orchesta RFQ,
- confidence is low,
- the lead is weak fit,
- the case involves legal, medical, financial, security, account access, credentials, disputes, or sensitive data,
- the sender demands a binding/final price without enough data,
- the message conflicts with `business-profile.md`,
- CRM context contradicts the email,
- attachment content is suspicious, encrypted, or unreadable.
- attachment route is `block_and_telegram` or `telegram_review_only`.

## Escalate With Draft Allowed

Send Telegram plus draft when:
- Mac bridge is unavailable but the draft is otherwise safe,
- Apple Calendar is unavailable but meeting slots are optional,
- CRM write failed after the draft was created,
- research stayed lean and confidence is enough for a first response.

## Message Shape

Write Telegram like a short human briefing, not a technical log.

Rules:
- 2-3 plain sentences,
- mention who wrote,
- summarize what the message is about,
- state the decision Hermes made: draft prepared in the same thread, Telegram only, or no action,
- include one concrete question only when Lukasz must decide,
- mention attachment risk or OCR route only when it changes the decision,
- avoid raw labels such as `new_quote_request` unless they are useful for debugging.

Good shape:

```text
Dostałeś zapytanie o wycenę od Jana Kowalskiego z instalacje-example.pl. Sprawdziłem wiadomość i załącznik: chodzi o system, który odpowiada na zapytania z formularza i zapisuje sprawy w CRM, więc przygotowałem draft odpowiedzi w tym samym wątku.
```

Review-only shape:

```text
Dostałeś wiadomość od identity-002@example.invalid o automatyzacji poufnych wycen medycznych. To słaby fit i temat wrażliwy, więc nie przygotowałem draftu do klienta; daj znać, czy odpisać krótko odmownie, czy zostawić bez odpowiedzi.
```

## Learning Updates

After Lukasz answers Telegram, Hermes may update `business-profile.md` automatically only when the answer clearly changes future behavior.

Rules:
- make the smallest profile change that captures the decision,
- add a dated entry to the changelog,
- do not store secrets or sensitive client data,
- do not overwrite pricing, fit, or safety rules unless the answer explicitly changes them.

## Reminder

If a draft waits without review for about 24 hours, Hermes should remind Lukasz with:
- sender/company,
- draft kind,
- risk/fit,
- next recommended action.
