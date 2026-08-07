# Lean Research And CRM Reference

## Goal

Gather only enough context to route the lead, create a safe draft, and update CRM. Research must be lean, factual, and useful. It must not exhaust tokens or browse broadly by default.

## Runtime Split

VPS/cloud handles:
- Zoho Mail pre-check for `rfq-mailbox@example.invalid`,
- message/thread loading,
- safe attachment text extraction,
- classification and reasoning,
- Zoho Mail draft creation,
- Telegram notification.

Mac-local bridge handles:
- Obsidian CRM lookup and write,
- Apple Calendar busy-block export,
- local `business-profile.md` updates after Telegram learning.

If the Mac bridge is offline, continue in degraded mode:
- Zoho Mail draft may still be created when safe,
- Telegram briefing must say CRM/Apple Calendar were unavailable,
- do not write CRM data on VPS,
- do not claim CRM or Apple Calendar were checked.

## Research Budget

Default mode: Lean.

Order:
1. Email body, headers, subject, sender domain, and safe attachment text.
2. Obsidian CRM lookup through Mac bridge.
3. Sender domain/company website.
4. LinkedIn, KRS, registers, or broader search only when fit cannot be judged.

Stop as soon as routing and next step are clear. Target research time is about 2 minutes.

Do not use deep OSINT for every lead. Do not browse news/social sources unless the lead is strong and the missing information matters.

## Extract From Email

Extract locally before web research:
- sender email,
- sender domain,
- name,
- company,
- role if present,
- phone if present,
- website if present,
- subject,
- request type,
- attachment filenames and safe summaries,
- attachment route/extractor from `attachment-processing.md`,
- urgency,
- requested next step.

## CRM Lookup

Lookup order:
1. exact sender email,
2. sender domain,
3. company name from subject/body/signature,
4. known deal keywords in the same company note.

CRM path:

```text
/Users/lukaszburyan/Library/Mobile Documents/iCloud~md~obsidian/Documents/Orchesta RFQ Team Obsidian/07 CRM/
```

The CRM is the source of truth for relationship and deal history. `business-profile.md` is the source of truth for the offer.

## Deal Grouping

Merge into an existing deal only when all signals point to the same matter:
- same company domain,
- similar topic/request type,
- close time window,
- similar project context or reply chain.

If the same domain sends different matters, create separate deals in the same company note.

Default first status for a sensible lead: `discovery`.

## CRM Write Rules

Hermes may automatically write short CRM notes through the Mac bridge.

Write:
- short company context,
- contact details,
- request summary,
- fit verdict,
- deal status,
- next action,
- short research summary,
- chronology entry.

Do not write:
- full raw email bodies,
- full sensitive attachments,
- full raw OCR/vision output unless explicitly approved,
- tokens, passwords, API keys, private keys,
- confidential data that is not needed for sales follow-up.

Use the template in `templates/crm-inbound-note.md` for new company notes.

## Fit Verdict

Use `orchesta_fit`:
- `green` - strong fit for Orchesta RFQ,
- `yellow` - possible fit, needs clarification,
- `red` - weak fit; Telegram only.

Good fit signals:
- 10-100 employees,
- recurring quote requests,
- technical or service offer that needs clarification,
- manual response process,
- CRM or sales process exists,
- delay in first response creates lost opportunities.

Weak fit signals:
- heavily regulated medical/financial/legal exposure,
- high confidentiality,
- very low lead volume,
- no repeatable quote source,
- expectation that AI sends final technical offers without a human.

## Briefing Research Fields

Use this shape:

```yaml
research:
  mode: lean
  time_budget: "about 2 minutes"
  sources_used:
    - email
    - crm
    - company_website
  summary: "..."
  confidence: medium
crm:
  mode: mac_bridge
  action: append_history
  note_path: "07 CRM/Example Sp. z o.o..md"
  status: discovery
```
