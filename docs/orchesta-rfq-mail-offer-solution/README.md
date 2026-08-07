# Orchesta RFQ — dokumentacja rozwiązania mailbox → draft odpowiedzi → gotowa oferta

> Archiwalny eksport sprzed audytu produkcyjnego. Nie używać jako instrukcji
> uruchomienia. Aktualna polityka znajduje się w `MESSAGE_POLICY.md`,
> `DEPLOYMENT_RUNBOOK.md` oraz `skills/mail-lead-pipeline/SKILL.md`.

Wygenerowano: `2026-07-27T14:57:06`  
Katalog źródłowy: `/opt/data`  
Katalog dokumentacji: `/opt/data/docs/orchesta-rfq-mail-offer-solution`

Ten pakiet opisuje rozwiązanie, które obserwuje pocztę Zoho Mail, klasyfikuje zapytania, przygotowuje drafty odpowiedzi, obsługuje bezpieczne załączniki, eskaluje do Telegrama, a po zebraniu danych generuje gotową prezentację/ofertę Orchesta RFQ jako JSON/HTML/PDF.

## Najważniejsza zasada

Hermes automatycznie wysyła wyłącznie zatwierdzone typy operacyjne po trwałej
walidacji. Finalna oferta, cena i warunki handlowe zawsze pozostają draftem do
ręcznej wysyłki. Nieznany lub niejednoznaczny przypadek trafia do oceny człowieka.

## Pliki w tym pakiecie

- `00-index.md` — mapa całego rozwiązania.
- `01-architektura-i-logika.md` — logika end-to-end, stany, bramki bezpieczeństwa, przepływy.
- `02-mail-lead-pipeline-skill-i-prompty.md` — pełny skill i referencje pipeline’u pocztowego.
- `03-rfq-final-offer-skill-i-wiedza.md` — pełny skill finalnej oferty, approved knowledge, szablony.
- `04-polaczenia-konfiguracja-i-sekrety.md` — integracje, zmienne środowiskowe bez wartości sekretów, OAuth, runtime.
- `05-skrypty-cli-i-testy.md` — skrypty wykonawcze, interfejsy CLI, realne wyniki testów/self-testów.
- `06-runbook-operacyjny.md` — jak uruchamiać, sprawdzać, wdrażać i diagnozować.

## Źródła użyte do wygenerowania

- `directives/mail-lead-pipeline.md`
- `skills/mail-lead-pipeline/SKILL.md` + `references/` + `templates/`
- `skills/rfq-final-offer/SKILL.md` + `references/` + `rfq-final-offer-knowledge/approved/` + `templates/`
- `execution` — skrypty runtime.

Sekrety nie zostały przepisane do dokumentacji; pokazuję tylko nazwy zmiennych i zakresy dostępu.
