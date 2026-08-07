# Obsidian CRM

## Vault Layout

Use the configured Obsidian vault root. Inside it, create:

```text
CRM/
  Companies/
  People/
  Deals/
  Offers/
  Activity/
  Knowledge/
    Approved/
    Proposed/
    Rejected/
  Logs/
```

Do not write full raw email bodies, sensitive attachments, secrets, tokens, passwords, or private keys.

## Companies

Path:

```text
CRM/Companies/ACME.md
```

Include company name, website when known, industry when known, quote-request source, contact people, open deals, closed deals, and short notes.

## People

Path:

```text
CRM/People/Tomasz Nowak.md
```

Include first name, last name, email, phone if known, company, role if known, mail threads, notes, and later consent status if needed.

## Deals

Path:

```text
CRM/Deals/ORCH-RFQ-2026-0001 ACME.md
```

Allowed statuses:

- `new_inquiry` - nowe zapytanie
- `awaiting_data` - brakuje danych do oferty
- `ready_for_offer` - dane kompletne
- `offer_draft_created` - PDF dodany do draftu
- `sent_manually` - Lukasz wysłał ofertę ręcznie
- `revision_requested` - klient chce zmianę zakresu
- `won` - klient zaakceptował
- `lost` - klient odmówił
- `stale` - brak odpowiedzi

Include client, company, email, quote-request source, mailbox count, CRM yes/no, net price, status, last contact date, latest draft id, offer number, offer JSON path, and short summary.

## Offers

Path:

```text
CRM/Offers/ORCH-RFQ-2026-0001.json
```

Store immutable versions in one JSON file. A scope, price, company, mailbox
count, CRM choice or customer-data change requires a new version; never replace
the older object. The JSON and PDF carry the same `version`, `pricing_hash`,
`scope_hash` and `customer_data_hash`.

```json
{
  "offer_number": "ORCH-RFQ-2026-0001",
  "versions": [
    {
      "version": "v1",
      "status": "offer_draft_created"
    }
  ]
}
```

When scope changes, append `v2`, `v3`, and so on. Do not overwrite history.
