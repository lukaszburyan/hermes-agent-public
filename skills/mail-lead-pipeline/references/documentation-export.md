# Documentation Export Pattern

Use when Łukasz asks for all instructions, prompts, skills, connections, or full logic of the Orchesta RFQ mailbox/offer solution in shareable files.

## Output Shape

Create a dedicated docs package under `/opt/data/docs/<solution-name>/` with Markdown files rather than one giant response.

The package must have two layers:

1. **Primary business version for Łukasz** — the complete process in simple Polish, organized around concrete "if A, then B" situations. Do not use programmer jargon, function names, internal class names, database names, protocol headers, hashes, status codes, or implementation field names.
2. **Technical evidence appendix** — source paths, internal classes, runtime details, tests, manifests and code-level discrepancies needed to prove that the business description matches production.

Recommended files:

- `README.md` — package purpose and safety note about secrets; point to the business version first.
- `01-logika-prostym-jezykiem.md` — primary end-to-end business flow, all decision variants, failures, duplicates, notifications and approval questions.
- `02-dowody-techniczne.md` — code-level flow, internal decision classes and implementation gates.
- `03-mail-lead-pipeline-skill-i-prompty.md` — `mail-lead-pipeline` SKILL.md plus relevant references/templates when a full export is requested.
- `04-rfq-final-offer-skill-i-wiedza.md` — `rfq-final-offer` SKILL.md plus approved knowledge and offer templates when requested.
- `05-polaczenia-konfiguracja-i-sekrety.md` — integrations, OAuth scopes and environment variable names only.
- `06-skrypty-cli-i-testy.md` — real deterministic test output.
- `07-runbook-operacyjny.md` — operational checks and recovery steps.
- `manifest.json` — file sizes and line counts.

The business version should answer, in order:

- where a lead can arrive,
- what Hermes checks first,
- when Hermes ignores a message,
- when Hermes answers automatically,
- what it asks and why,
- when it prepares the final offer,
- what blocks the offer,
- what happens on errors or uncertain outcomes,
- how duplicates are prevented,
- what Łukasz is told,
- which business rules still need Łukasz's approval.

Before delivery, run a jargon scan on the primary version. Keep technical detail in the appendix rather than deleting evidence.

Optionally create a `.tar.gz` archive of the directory for delivery.

## Source Paths To Include

- `/opt/data/directives/mail-lead-pipeline.md`
- `/opt/data/skills/mail-lead-pipeline/SKILL.md`
- `/opt/data/skills/mail-lead-pipeline/references/*.md`
- `/opt/data/skills/mail-lead-pipeline/templates/*`
- `/opt/data/skills/rfq-final-offer/SKILL.md`
- `/opt/data/skills/rfq-final-offer/references/*.md`
- `/opt/data/skills/rfq-final-offer/rfq-final-offer-knowledge/approved/*`
- `/opt/data/skills/rfq-final-offer/templates/*`
- `/opt/data/skills/rfq-final-offer/scripts/rfq_final_offer.py`
- `/opt/data/execution/*.py` relevant to polling, state, draft creation, attachments, monitoring, dry-runs.

## Secret Handling

Never copy secret values into docs. Safe to list variable names and OAuth scopes. Redact values matching token/secret/password/refresh_token/access_token/client_secret/api_key/Bearer patterns. Do not include `.env` contents except variable names.

## Verification

Run deterministic checks and write their real output into the documentation:

```bash
python3 /opt/data/execution/zoho_mail_poller.py --self-test
python3 /opt/data/execution/zoho_reply_draft.py --self-test
python3 /opt/data/skills/rfq-final-offer/scripts/rfq_final_offer.py --self-test
```

If a fixture path mentioned in older docs is missing, prefer the script's `--self-test` over inventing a fixture.

After generating the package, create `manifest.json`, archive it, and report archive path plus SHA256.
