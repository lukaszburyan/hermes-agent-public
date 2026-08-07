# Orchesta RFQ Business Profile

## Purpose

This file is the source of truth for how Hermes talks about the Orchesta RFQ offer in operational Zoho Mail messages, final-offer drafts, Telegram briefings, and CRM notes.

Use only this profile for offer positioning, pricing rules, fit rules, forbidden claims, discovery questions, and draft style. CRM history lives in Obsidian; this file describes the offer and response rules.

## Offer In One Sentence

Orchesta RFQ is a system that reacts to quote requests from email or forms in under 5 minutes, asks useful follow-up questions, fills CRM with the important context, and prepares the salesperson to answer faster with a draft or next step.

## Naming Rules

Allowed:
- Orchesta
- Orchesta RFQ
- speed-to-lead
- system
- agent
- automation
- virtual worker

Do not use:
- "Zlote 5 minut" as an offer name,
- inflated claims,
- internal jargon such as "CRM context" in customer-facing drafts.

"<5 minut" may describe first-response speed only. It must never imply that Orchesta creates a complete quote or final offer in 5 minutes.

## What The System Does

Orchesta RFQ:
- watches the mailbox where form and email quote requests arrive,
- reads the message and safe attachment text,
- recognizes what the quote request is about,
- checks the sender and company with lean research,
- automatically sends the first human-sounding response when its durable operational type, recipient, content and transport policy pass all gates,
- asks at most 2 follow-up questions when data needed for the next safe step is missing,
- writes the important facts to CRM,
- tells the salesperson what happened and what the next step is,
- prepares the final offer as a Zoho draft with a validated PDF when enough information exists.

The production policy is split: approved operational messages are auto-sent,
while final offers, final prices and commercial terms are always draft-only and
can be sent only by a human. Unknown or inconsistent cases require manual
review. The global transport kill switch may stop all sending without stopping
lead processing or durable draft/outbox creation.

## What The System Does Not Do

Orchesta RFQ does not:
- send final offers without human review,
- negotiate price,
- sign contracts,
- create binding quotes,
- replace the whole sales process,
- build a second CRM,
- add another panel for the sales team,
- pretend to understand private technical details that were not provided.

## Main Modules

- First response: confirm the request and show that the case is moving.
- Follow-up questions: ask for missing details needed for a reliable quote.
- Company check: gather only the facts needed to judge fit and context.
- CRM update: write a short, clean summary so nothing is lost.
- Salesperson handoff: give the human the sender, company, topic, fit, risk, and next step.
- Quote Draft System: create a text offer draft for review when the minimum information exists.

## Ideal Customer

Best fit:
- 10-100 employees,
- small or medium company,
- regular quote requests from email, forms, referrals, trade fairs, or direct contact,
- sales or customer service team that responds manually,
- offers that require clarification before pricing,
- speed matters because customers ask several vendors.

Good industries:
- industrial automation,
- HVAC and technical installations,
- machinery and devices,
- parts and technical components,
- service companies for industry,
- furniture/interior projects,
- other B2B or B2B-like services where quotes take time to prepare.

Weak fit:
- medical, financial, or heavily regulated services,
- highly confidential matters,
- very low request volume,
- companies with no repeatable source of quote requests,
- companies expecting AI to prepare final technical quotes without a human.

Weak fit means Telegram only. Do not create a customer-facing draft unless Lukasz explicitly approves.

## Internal Pricing Rules

Use prices only in final offer drafts/PDF when the minimum offer data is available. Do not reveal prices in first response or discovery emails. For final-offer pricing, use only `skills/rfq-final-offer/rfq-final-offer-knowledge/approved/pricing.json`.

When prices are used, call them "inwestycja".

Current approved final-offer prices:
- base Orchesta RFQ implementation for 1 mailbox: 7200 PLN net,
- each additional mailbox: 3000 PLN net,
- CRM integration: 3000 PLN net.

If data is incomplete or the scope is unclear, do not guess the final investment. Ask discovery questions or escalate to Telegram.

## Minimum Data Before Final Offer Draft

Before creating a final offer draft/PDF with investment, Hermes needs:
- company name,
- client email,
- number of mailboxes to monitor,
- whether CRM is wanted.

If any of these are missing, create a first response or discovery draft instead of a final offer draft.

## Discovery Questions

Ask 2 questions maximum. Choose only the blockers that are missing for this lead. If the customer explicitly says they do not yet know a blocker, do not repeat the question mechanically: propose a reversible starting variant or two options and ask for one simple decision. Distinguish "nie chcemy CRM" from "nie mamy jeszcze informacji/decyzji o CRM"; the latter remains unknown.

Good questions:
- Ile kont pocztowych ma sledzic system?
- Czy uwzglednic integracje z CRM w ofercie?
- Na jaka firme mam przygotowac oferte?

Do not ask for monthly quote-request volume unless the customer already provided it. Source of quote requests and sample requests are useful context, but they must not block a final PDF by themselves.

Never ask:
- Jakie typy zapytan RFQ pojawiaja sie najczesciej?
- Jakie powiadomienia maja trafiac na Telegram?
- Czy system ma wspierac dalsze etapy obslugi RFQ?
- Kto dzis odpowiada na pierwsza wiadomosc?
- Ile zapytan o wycene pojawia sie miesiecznie?

## Draft Style

Customer-facing drafts:
- short,
- simple Polish by default,
- English only when the sender clearly writes in English,
- human and professional,
- no emoji,
- no hype,
- no 21x/100x statistics,
- no "hope you are well" style filler,
- no OCR/vision/parser/extractor wording or comments about noisy/partial attachment reads,
- one clear next step.

Allowed effects:
- faster first response,
- fewer lost quote requests,
- cleaner CRM data,
- better context for the salesperson,
- fewer manual follow-ups,
- faster handoff to a human.

Avoid unsupported promises. If a claim is not in this file or in the message context, do not use it.

## Signature

Use this signature:

```text
Orchesta RFQ Team

+48 000 000 000
LinkedIn: example.invalid/orchesta-rfq
```

Add `https://orchesta.eu/` only when the context naturally calls for it.

## Telegram Learning Changelog

Hermes may update this file after a Telegram answer from Lukasz when the answer clearly changes future behavior. Each update must:
- be small,
- preserve existing rules unless explicitly corrected,
- add a dated changelog entry,
- avoid secrets, private keys, tokens, passwords, and sensitive client data.

### 2026-06-23

- Business profile narrowed to Orchesta RFQ / speed-to-lead plus Quote Draft System.
- Offer artifact is a versioned offer JSON plus a validated PDF attachment when
  offer generation is enabled; otherwise Hermes prepares a text draft or an
  internal report. There is no Google Slides artifact.
- Prices are internal and used only in final offer drafts with enough data.
- Weak-fit leads go to Telegram only.
