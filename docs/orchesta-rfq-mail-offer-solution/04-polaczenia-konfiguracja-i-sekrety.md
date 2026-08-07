# 04 — Połączenia, konfiguracja, sekrety — archived export

> Historyczny spis. Audytowane wydanie nie przekazuje `OPENAI_API_KEY` do
> kontenera i wymusza oba zewnętrzne przełączniki modeli na `0`.

## Integracje

- Zoho Mail: odczyt konta/folderów/wiadomości/tagów + tworzenie draftów.
- Telegram: briefing i eskalacje do Łukasza.
- Obsidian CRM: przez Mac bridge; VPS nie zapisuje pełnego CRM jako substytutu.
- Apple/Google Calendar: tylko free/busy/read-only, bez tworzenia wydarzeń.
- Lokalny runtime VPS: `/opt/data`, skrypty w `/opt/data/execution`, skills w `/opt/data/skills`.

## OAuth / zakresy

Konserwatywny zestaw Zoho:

```text
ZohoMail.accounts.READ,ZohoMail.folders.READ,ZohoMail.messages.READ,ZohoMail.messages.CREATE,ZohoMail.tags.READ
```

Uwaga: `ZohoMail.messages.CREATE` jest potrzebne do transportu. Kod, prompty i testy muszą dopuszczać tylko zwalidowane wiadomości przedofertowe bez ceny oraz wymuszać `draft-only` dla finalnej oferty.

Kalendarz: preferowane tylko `https://www.googleapis.com/auth/calendar.freebusy`.

## Zmienne środowiskowe — nazwy bez wartości

Poniżej są nazwy zmiennych wykryte w `.env` lub kodzie. Wartości sekretów celowo nie są dokumentowane.

```text
APIFY_API_TOKEN
GENERAL_RFQ_PATTERNS
GITHUB_REPO_NAME
GITHUB_REPO_URL
GITHUB_TOKEN
GITHUB_USERNAME
HERMES
HERMES_ALLOW_DRAFT_CREATE
HERMES_ATTACHMENT_EXTRACTION_ENABLED
HERMES_ATTACHMENT_MAX_FILES
HERMES_ATTACHMENT_MAX_FILE_BYTES
HERMES_ATTACHMENT_MAX_IMAGE_PIXELS
HERMES_ATTACHMENT_MAX_MEMORY_BYTES
HERMES_ATTACHMENT_MAX_PDF_PAGES
HERMES_ATTACHMENT_MAX_PROCESSING_SECONDS
HERMES_ATTACHMENT_MAX_TOTAL_BYTES
HERMES_AUTO_DRAFT_CLASSES
HERMES_CRM_WRITE_ENABLED
HERMES_DRAFTS_ENABLED
HERMES_DRAFT_GENERATOR
HERMES_DRAFT_LLM_TIMEOUT_SECONDS
HERMES_DRAFT_MODEL
HERMES_DRAFT_NOTIFY_ONLY
HERMES_OBSIDIAN_VAULT
HERMES_OFFER_GENERATION_ENABLED
HERMES_ONESHOT_COMMAND
HERMES_ONESHOT_CWD
HERMES_ONESHOT_TOOLSETS
HERMES_RFQ_MARKER_TIMEOUT_SECONDS
HERMES_RFQ_MARKER_VENV_DIR
HERMES_RFQ_MARKER_ENABLED=0
HERMES_RFQ_STATE_FILE
HERMES_RFQ_VENV_PYTHON
HERMES_SHADOW_MODE
HERMES_TIMEOUT_
HERMES_TIMEOUT_ATTACHMENT_SECONDS
HERMES_TIMEZONE
HRFQ
MARKER_CMD
OBSIDIAN_VAULT_PATH
ORCHESTA_RFQ_ALLOW_EXTERNAL_VISION
ORCHESTA_RFQ_ALLOW_SCHEMA_MODEL
ORCHESTA_RFQ_ATTACHMENT_EXTRACT_CLASSES
ORCHESTA_RFQ_SCHEMA_MODEL
ORCHESTA_RFQ_TESSERACT_LANG
ORCHESTA_RFQ_VISION_DETAIL
ORCHESTA_RFQ_VISION_MODEL
RELATED_NON_RFQ_PATTERNS
RFQ_PATTERNS
TELEGRAM_ALLOWED_USERS
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
TELEGRAM_HOME_CHANNEL
TELEGRAM_USER_ID
TZ
ZOHO_MAIL_ACCOUNTS_BASE_URL
ZOHO_MAIL_ACCOUNT_EMAIL
ZOHO_MAIL_API_BASE_URL
ZOHO_MAIL_CLIENT_ID
ZOHO_MAIL_CLIENT_SECRET
ZOHO_MAIL_REDIRECT_URI
ZOHO_MAIL_REFRESH_TOKEN
ZOHO_MAIL_SCOPE
```

## Bramki zgody

Wymagane świeże `OK` przed:

- OAuth/device flow,
- zapisem/rotacją tokenów,
- włączeniem crona przeciw realnej skrzynce,
- utworzeniem pierwszego realnego draftu Zoho,
- zmianą filtrów/labeli/settings mailboxa,
- tworzeniem eventów/holdów w kalendarzu,
- publicznym webhookiem.

Nie ma zgody na automatyczne wysyłanie emaili — to jest poza zakresem pipeline’u.

## Sekrety

Nie wolno zapisywać w docs, memory, repo, CRM ani raportach:

- tokenów OAuth/API,
- haseł,
- refresh tokenów,
- client secret,
- kluczy SSH,
- pełnych treści poufnych załączników.
