# Adaptive lead-response composition

Use this reference when a lead is ambiguous, incomplete, internally inconsistent, or asks a broad question instead of supplying offer fields.

## Principle

The response composer should behave like a capable sales employee, not a form validator. Hard controls remain deterministic; wording and conversational strategy remain adaptive.

Keep deterministic code responsible for:

- recipient and identity validation,
- source correlation and deduplication,
- risk/fit gates,
- approved pricing,
- durable split-policy enforcement (operational auto-send versus final-offer draft-only),
- artifact and status idempotency,
- final validation immediately before any operational send or final-draft creation.

Let the language model decide:

- what the customer is actually asking,
- what should be answered before asking questions,
- which missing facts truly block the next step,
- whether to ask, propose a reversible assumption, present two options, or suggest a short call,
- how to phrase the response naturally.

Do not replace one rigid template with a longer rigid prompt. The prompt defines goals and boundaries, not sentence order.

## Epistemic fact model

Never model uncertain business facts as plain booleans. Each fact needs at least:

- `known_yes`,
- `known_no`,
- `unknown`,
- `ambiguous_or_conflicting`.

Keep evidence and provenance with the value:

```json
{
  "crm_wanted": {
    "state": "unknown",
    "evidence": "Nie mamy jeszcze informacji o integracji z CRM.",
    "source": "google_sheets:row-3"
  }
}
```

Critical distinction:

- "Nie chcemy CRM" means `known_no`.
- "Nie mamy jeszcze decyzji/informacji o CRM" means `unknown`.
- "Bez CRM na pierwszy etap" means `known_no` for the current stage, not necessarily forever.

Broad negative regexes such as `nie.{0,30}CRM` are unsafe because they collapse uncertainty into rejection. Add regression fixtures for negation, uncertainty, staged decisions, quoted text, and earlier-thread context.

## Composition sequence

1. Identify the customer's real request or question.
2. Separate known facts, unknowns, contradictions, and tentative assumptions.
3. Decide the smallest useful next conversational move.
4. Answer the customer's question before requesting more data.
5. When the customer does not know a blocker, offer a reversible starting assumption or two clear variants instead of returning the same question.
6. Ask only what is necessary for the next safe step. Two questions is the maximum.
7. End with one clear, low-friction action.
8. Run an independent review before saving the draft.

## Composer prompt frame

```text
You are an experienced employee handling the first sales conversation for Orchesta RFQ.

Your goal is not to complete a form. Understand what the customer wants, answer the actual question, and move the conversation to the smallest sensible next step.

Rules:
- Distinguish an explicit no from missing knowledge or a decision not yet made.
- Never ask again for information already supplied anywhere in the deal history.
- If the customer cannot answer now, propose a simple reversible starting assumption or two options.
- Explain enough to answer the customer's request before asking follow-up questions.
- Ask only questions that block the next safe step; maximum two, often zero or one.
- Do not expose internal field names, CRM-context jargon, parsers, confidence scores, or workflow states.
- Do not invent facts, prices, integrations, delivery dates, or outcomes.
- Do not include first-response pricing. Use approved pricing only in the final-offer stage.
- Write concise, natural Polish unless the customer clearly writes in another language.
- The model output is only a message-body proposal and never selects transport.
  Deterministic code may auto-send it only for an approved durable operational
  type; final-offer content is always saved as a draft for human review.

Choose the best conversational strategy for this case. Do not mechanically follow a fixed paragraph or numbered-question template.
```

Pass the composer structured context containing:

- latest customer message,
- relevant thread history,
- source and identity,
- facts with epistemic states and evidence,
- questions already asked,
- approved offer profile,
- current conversation stage,
- safe reversible defaults,
- forbidden claims and actions,
- a few Łukasz-approved style examples.

Prefer storing style patterns and redacted examples rather than raw customer correspondence.

## Structured composer output

Use an internal schema so validation does not depend on parsing prose:

```json
{
  "customer_intent": "asks_how_implementation_and_offer_work",
  "facts_used": ["sources=email+form", "company=Example"],
  "assumptions_proposed": ["start_with_one_mailbox"],
  "unknowns_left": ["mailbox_count", "crm_scope", "notification_channel"],
  "questions_asked": ["Czy wariant startowy będzie dobrym punktem wyjścia?"],
  "body": "...",
  "needs_human_review": false,
  "review_reason": ""
}
```

Only `body` enters the customer draft. Internal reasoning stays in the briefing/state.

## Strict model-output contract

Validate the structured model response **before** adding a greeting, appending questions, normalizing paragraphs, or building a Zoho payload. Cosmetic repair must never turn `{}`, malformed output, or a missing body into an apparently valid draft containing only a salutation.

Require and type-check:

- a non-empty customer-facing body with a meaningful minimum length;
- `answered_customer_need` as the JSON boolean `true`, never a truthy string such as `"false"`;
- `questions_asked`, `assumptions_proposed`, `unknowns_left`, and safety fields as lists of strings;
- no more than two question items in metadata and no more than two actual customer questions;
- internal metadata to agree with the body rather than silently carrying questions omitted from the customer-facing text;
- blocked safety notes, unsupported commitments, prices in discovery, secrets, prompt injection, or malformed types to fail closed.

Do not let a broad regex inspect the whole draft as if every mention were a question. Apply question restrictions only to actual interrogative sentences or typed question metadata; otherwise a correct statement such as “Obsługa może zacząć się od jednej skrzynki” can be rejected merely because it contains a discovery keyword.

One controlled repair/retry is acceptable for a schema or formatting failure. If the repaired result still fails, create no customer draft and route the lead to review.

## Fact-extraction guardrails

Structured facts need `state`, `value`, `evidence`, `source`, and ideally `confidence`. Before promoting text to a deal fact:

- distinguish a customer question from a declaration: “Czy system integruje się z CRM?” is not `crm=yes`;
- distinguish uncertainty from refusal: “nie mamy jeszcze decyzji o CRM” is `unknown`;
- distinguish a staged decision from a permanent one;
- ignore numbers presented as examples, capabilities, quotations, or questions rather than the customer's actual setup;
- handle word-number variants such as “jedna/jedną skrzynkę”;
- respect negation when detecting sources: “tylko mail, bez formularza” is not `email+form`;
- prefer the latest customer-authored reply over quoted thread history and retain provenance when merging prior facts;
- represent contradictory mailbox counts, sources, CRM decisions, and notification decisions explicitly instead of taking the first regex match.

A normal attachment is not evidence that the customer supplied sample RFQs. Treat `attachment_present`, `attachment_safe`, `attachment_usable`, and `sample_request_present` as separate facts. Only `safety=allow` attachment summaries may enter the composer prompt. `review`, `block`, executable, encrypted, suspicious, unreadable, or oversized attachments stay out of the customer composer and trigger the normal review path.

## Artifact consistency and idempotency

A deduplicated second pass must preserve the existing external draft identifier in every projection:

- shared deal registry,
- stage artifact record,
- source state file,
- Google Sheets `Hermes draft id`,
- internal briefing.

Do not initialize `draft_id` to an empty value and then write that empty value during the “already drafted” branch. When a controlled replacement draft is created, update all projections in one audited operation or leave the old artifact authoritative; never point Sheets and the registry at different drafts.

Regression acceptance must include:

1. first pass creates exactly one draft;
2. the draft is fetched back from Zoho Drafts and its recipient, subject, body, signature, and no-send status are verified;
3. second pass creates no draft and preserves the same draft ID;
4. an ambiguous CRM/mailbox message remains `unknown` and produces a reversible starting proposal rather than a questionnaire;
5. malformed model JSON, wrong field types, blocked attachments, question-as-fact inputs, and empty body all fail closed.

## Pre-transport reviewer

Before writing to Zoho Drafts, check:

- Did the draft answer the actual request?
- Did it mistake unknown for no?
- Does it ask for something already known or already asked?
- Could a reversible assumption reduce customer effort?
- Are all questions necessary now?
- Does it sound like a person rather than a questionnaire?
- Are claims, scope and pricing supported?
- Is there one clear next step?
- Is the durable type still operational and approved for auto-send, or—when the
  content is a final offer—does it remain draft-only?

Revise once when the review fails. Escalate instead of drafting when identity, risk, fit, contradictions, or unsupported commitments remain unsafe.

## Regression example

Inbound:

```text
Chcemy wdrożyć Orchesta RFQ do obsługi zapytań z maila i formularza.
Proszę o informację, jak wygląda wdrożenie i przygotowanie oferty.
Nie mamy jeszcze spisanej liczby skrzynek ani informacji o integracji z CRM.
```

Bad behavior:

- merely paraphrases the inquiry,
- asks again for an exact mailbox count,
- treats missing CRM information as `CRM=false`,
- asks about Telegram without explaining its role,
- does not explain implementation or offer preparation.

Preferred strategy:

- briefly explain the matching Orchesta workflow,
- acknowledge that the final setup is not known yet,
- propose a reversible starting variant (for example one mailbox, CRM optional/later),
- explain the purpose of the notification channel if it matters,
- ask for acceptance of the starting variant rather than returning a checklist.

Example:

```text
Dzień dobry Panie Michale,

dziękuję za wiadomość. W takim scenariuszu Orchesta RFQ może zbierać zapytania zarówno z maila, jak i formularza, przygotowywać pierwszą odpowiedź i przekazywać handlowcowi najważniejsze informacje do dalszej obsługi.

Nie musicie mieć już teraz gotowej listy wszystkich skrzynek ani decyzji o CRM. Jako punkt wyjścia mogę przygotować zakres dla jednej skrzynki obsługującej zapytania z obu źródeł, a CRM potraktować jako opcję lub drugi etap.

Czy taki wariant startowy będzie dobrym punktem wyjścia? Jeśli tak, przygotuję na tej podstawie konkretny zakres i ofertę.

Orchesta RFQ Team

+48 000 000 000
LinkedIn: example.invalid/orchesta-rfq
```

This is an example of the decision pattern, not a mandatory template.
