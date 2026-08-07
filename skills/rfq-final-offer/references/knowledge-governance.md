# Knowledge Governance

## Approved Knowledge

Offer knowledge lives only in this skill:

```text
rfq-final-offer-knowledge/approved/
```

Use `approved` only. Do not price from `draft`, `proposed`, `rejected`, old business profiles, general memory, internet sources, or customer attachments.

## Telegram Update Flow

Approved knowledge changes require administrator approval on Telegram:

1. administrator sends a knowledge command,
2. Hermes summarizes the proposed change,
3. Hermes shows which approved file would change,
4. Hermes shows the impact on future offers,
5. administrator replies `zatwierdzam`, `odrzucam`, or `edytuj`,
6. only after `zatwierdzam` may Hermes write to `approved`.

Example response:

```text
Proponowana zmiana:
Plik: pricing.json oraz scope.md
Treść: Integracja CRM kosztuje 3000 zł netto i obejmuje podstawowe zapisanie zdarzenia jako leada w CRM.
Wpływ: przyszłe oferty z CRM pokażą tę pozycję i opis zakresu.

Odpowiedz:
zatwierdzam
odrzucam
edytuj
```

## Sleep And Background Review

Sleep may analyze:

- recent offers,
- missing data,
- frequent customer questions,
- block reasons,
- scope changes,
- frequent add-ons.

Sleep must not change:

- pricing,
- guarantee,
- product scope,
- PDF template,
- sales claims,
- payment terms.

Sleep writes proposals to `Knowledge/Proposed`. Telegram approval moves accepted changes into `Knowledge/Approved`.
