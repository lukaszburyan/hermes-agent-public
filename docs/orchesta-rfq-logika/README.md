# Orchesta RFQ — audyt rzeczywistej logiki

Paczka zawiera opis aktualnego działania systemu Orchesta RFQ oparty na kodzie produkcyjnym zweryfikowanym 2026-07-29.

## Pliki

- `02-logika-prostym-jezykiem.md` — główna wersja dla Łukasza: cały proces opisany prostym językiem biznesowym, sytuacje A–X, obecne ograniczenia i lista decyzji.
- `01-logika-systemu-od-poczatku-do-konca.md` — techniczna wersja pomocnicza, przeznaczona do kontroli zgodności z kodem; wyraźnie rozdziela funkcje wdrożone od braków.
- `test-results.txt` — rzeczywiste wyniki testów użytych do weryfikacji mapy.
- `manifest.json` — rozmiary i sumy kontrolne plików.

## Bezpieczeństwo

Paczka nie zawiera tokenów, haseł, kluczy, plików `.env` ani innych danych uwierzytelniających.

## Najważniejsza zasada

- bezpieczne wiadomości pre-offer mogą być automatycznie wysyłane,
- finalna oferta z ceną i PDF-em zawsze pozostaje draftem Zoho i nie jest wysyłana automatycznie.
