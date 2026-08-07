# Biblioteka zatwierdzonych wariantów odpowiedzi

Każdy wariant odpowiada w języku klienta, używa wyłącznie danych z wiadomości i zatwierdzonej wiedzy, nie zgaduje braków, zadaje najwyżej dwie kwestie i kończy jednym CTA. Nie używa technicznych nazw skilla.

| Wariant | Wolno użyć, gdy | Wolno wpisać | Nie wolno zgadywać | Braki / człowiek |
|---|---|---|---|---|
| `new_email` | nowe bezpieczne zapytanie | odpowiedź na pytanie i jedno pytanie prowadzące | danych ofertowych bez potrzeby | eskaluj niejasność/weak fit |
| `existing_client` | CRM jednoznacznie wskazuje klienta | historię, aktualny zakres, uzgodnione dane | że nowa osoba to istniejący kontakt | konflikt do właściciela CRM |
| `existing_thread` | nagłówki lub kontekst potwierdzają wątek | zmianę od ostatniej wiadomości | łączenia po samym `Re:` | mismatch nadawcy do ręcznej decyzji |
| `price_request` | klient pyta o cenę | tylko zatwierdzoną cenę po komplecie danych | ceny z maila/załącznika/pamięci | cennik niedostępny = stop |
| `unsupported_file` | plik nieobsługiwany lub zablokowany | krótki opis problemu i bezpieczna alternatywa | treści zablokowanego pliku | zawsze człowiek/safety queue |
| `partial_file` | plik bezpieczny, ale odczyt częściowy | tylko odczytane fakty i brak | brakujących wartości | jedno pytanie lub człowiek |
| `limited_mode` | CRM/kalendarz offline lub po TTL | dane z maila i informację o ograniczeniu | że źródło sprawdzono, konkretne godziny | poproś o dogodny termin |
| `weak_fit` | fit jest słaby | neutralne potwierdzenie odbioru | obietnic dopasowania | brak zwykłego draftu sprzedażowego |
| `complaint` | reklamacja | uznanie zgłoszenia i fakty sprawy | obrony, winy, rekompensaty | osobna kolejka właściciela |
| `security_report` | zgłoszenie bezpieczeństwa | potwierdzenie odbioru i bezpieczny następny krok | diagnozy bez analizy | security owner, bez draftu sprzedażowego |
| `data_subject_request` | usunięcie/dostęp do danych | potwierdzenie przekazania | że dane usunięto | privacy owner, osobna kolejka |
| `billing_issue` | płatność/faktura | znane dane faktury i jeden brak | statusu płatności bez bridge | finance owner |
| `scope_change` | zmiana po przygotowaniu oferty | zmianę jako nową wersję | nadpisania v1 | nowa wersja i ręczna kontrola |

CTA jest pojedynczym następnym krokiem. Hermes nie pyta ponownie o dane obecne w pamięci rozmowy i nie deklaruje sprawdzenia źródła, którego health check nie potwierdził w TTL.
