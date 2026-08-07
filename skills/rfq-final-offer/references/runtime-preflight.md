# Final-offer runtime preflight

## Cel

Kompletny lead nie powinien być pierwszym testem dostępności renderera PDF. Produkcyjny wrapper ma sprawdzić runtime finalnej oferty przed pobraniem lub oznaczeniem wiadomości.

## Zalecany układ

- Utrzymuj zależności final-offer w izolowanym venv, np. `/opt/data/rfq-runtime/.venv`.
- Minimalne biblioteki renderera i walidatora: Jinja2, WeasyPrint i pypdf oraz wymagane biblioteki systemowe.
- Poller może działać w swoim interpreterze, ale do generatora przekaż jawnie `--final-offer-python <venv>/bin/python`.
- Wrapper powinien zakończyć się błędem przed przetwarzaniem Inbox, gdy interpreter nie istnieje lub `import jinja2, weasyprint, pypdf` nie przechodzi.
- Zależności zapisuj w wersjonowanym `execution/requirements-rfq-final-offer.txt`; nie polegaj na przypadkowym systemowym Pythonie.

## Smoke przed live

1. Zbuduj kompletne wejście final-offer z kontrolowanej fixture lub znormalizowanej wiadomości testowej.
2. Utwórz nowy, unikalny katalog output — bez kasowania poprzednich katalogów w tej samej komendzie.
3. Uruchom helper tym samym interpreterem, który dostanie wrapper, z `--render-pdf`.
4. Wymagaj statusu `ready`, pliku zaczynającego się od `%PDF-`, prawidłowej liczby stron oraz przejścia walidacji treści/ceny.
5. Dopiero potem wysyłaj kontrolowany inbound prowadzący do Zoho Drafts.

## Kryterium live

Lokalny smoke nie kończy acceptance. Po utworzeniu draftu odczytaj go ponownie z Zoho, potwierdź trwały `draft_id`, zdalny załącznik PDF i brak odpowiadającej finalnej wiadomości w Sent. Powiadomienia można uznać za gotowe dopiero po tym potwierdzeniu.
