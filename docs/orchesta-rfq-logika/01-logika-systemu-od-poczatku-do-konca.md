# Orchesta RFQ — rzeczywista logika systemu od początku do końca

**Stan na:** 2026-07-29  
**Zakres:** kod Zoho Mail, Google Sheets, pre-offer, finalna oferta, PDF, draft Zoho, powiadomienia, retry i deduplikacja.

## 1. Jak czytać ten dokument

Oznaczenia:

- **[KOD]** — zachowanie bezpośrednio wynikające z aktualnego kodu produkcyjnego.
- **[KONFIG]** — zachowanie włączone przez aktualny wrapper, cron albo konfigurację runtime.
- **[POLITYKA]** — obowiązująca reguła biznesowa utrwalona w skillu/runbooku.
- **[ROZJAZD]** — dokumentacja, komentarz albo prompt nie odpowiada w pełni aktualnemu transportowi.

Najważniejsze rozróżnienie:

1. **Pre-offer / wiadomość robocza przed ofertą** może zostać automatycznie wysłana do klienta, jeśli przejdzie wszystkie bramki bezpieczeństwa.
2. **Finalna oferta z ceną i PDF-em** nigdy nie jest automatycznie wysyłana do klienta. System tworzy wyłącznie draft w Zoho z załączonym PDF-em.
3. Powiadomienia do Łukasza są wewnętrzne i nie zmieniają powyższych zasad.

---

## 2. Uruchamianie systemu

### 2.1. Zoho Mail

**[KONFIG]** Cron `Orchesta RFQ mailbox poller` działa co 2 minuty:

```text
*/2 * * * *
```

Produkcyjny wrapper:

```text
/opt/data/scripts/orchesta-rfq-mail-poller.sh
```

Wrapper włącza:

- odczyt nowych wiadomości z Zoho Inbox,
- klasyfikację,
- zunifikowany rejestr leadów,
- automatyczną wysyłkę bezpiecznych wiadomości pre-offer,
- automatyczne przygotowanie finalnej oferty jako draftu z PDF-em,
- generowanie wewnętrznego briefingu,
- powiadomienie e-mail do Łukasza,
- dostarczenie aktywności na Telegram przez wynik crona.

### 2.2. Google Sheets

**[KONFIG]** Cron `Google Sheets META ADS lead poller` działa co 5 minut:

```text
*/5 * * * *
```

Produkcyjny wrapper:

```text
/opt/data/scripts/google-sheets-lead-poller.sh
```

Poller obserwuje skonfigurowany arkusz `Arkusz1`, uzupełnia kolumny Hermesa i może wysłać bezpieczną pierwszą wiadomość pre-offer przez Zoho.

### 2.3. Ochrona przed równoległymi przebiegami

**[KOD/KONFIG]** Każdy wrapper bierze nieblokujący lock przez `flock`.

- Jeśli poprzedni przebieg nadal działa, nowy przebieg kończy się bez równoległego przetwarzania.
- Stan źródła zostaje oznaczony jako błąd `lock_busy` w monitoringu health.
- Poller nie dubluje wtedy pracy.

---

## 3. Źródła wejścia

System ma dwa aktywne adaptery wejściowe i wspólny rejestr biznesowy: zwykły e-mail klienta w Zoho oraz lead z Google Sheets.

### 3.1. Zwykły e-mail klienta do Zoho

**[KOD]** System pobiera z Inboxu metadane:

- Zoho `messageId`,
- `threadId`,
- nadawcę,
- domenę,
- temat,
- czas otrzymania,
- informację o załącznikach.

Po przejściu pre-checku pobiera treść, nagłówki RFC i — jeśli są — metadane załączników.

Odpowiedź pre-offer jest odpowiedzią w tym samym wątku i wymaga poprawnego RFC `Message-ID`.

- nie tworzy draftu ani wysyłki,
- nie generuje briefingu handlowego.

Dane kampanijne mają wejść przez Google Sheets lub uzgodniony arkusz w chmurze.

### 3.2. Google Sheets

**[KOD]** Poller czyta wiersze z danymi leada, m.in.:

- imię,
- e-mail,
- firmę,
- wiadomość,
- źródło.

Dla każdego niepustego wiersza wylicza deterministyczny hash treści.

Wiersz jest przetwarzany, gdy:

- nie ma jeszcze statusu Hermesa, albo
- ma status testowy do przetworzenia, albo
- jego treść zmieniła się od ostatniego hasha.

W przeciwnym razie jest pomijany, poza synchronizacją statusu ze wspólnego rejestru deala.

Przed walidacją i wysyłką treść przechodzi przez `ensure_campaign_origin_context()`. Dla `source_type=google_sheets` pierwsza wiadomość musi wyjaśniać kontakt po zgłoszeniu z formularza kampanii. Jeżeli generator nie zawarł takiego kontekstu, funkcja dodaje zatwierdzone zdanie `CAMPAIGN_ORIGIN_SENTENCE`.

### 3.3. Wspólny rejestr leadów

**[KOD]** Mail i Sheets rejestrują zdarzenia w jednym SQLite:

```text
/opt/data/.tmp/hermes-rfq-state/unified-leads.sqlite3
```

Rejestr:

- normalizuje adres e-mail,
- dla Gmaila usuwa kropki i `+alias`,
- wiąże zdarzenia z jednym logicznym `deal_id`,
- scala bezpieczne fakty,
- przechowuje maksymalnie 30 ostatnich fragmentów kontekstu,
- wykrywa duplikaty między źródłami,
- przechowuje identyfikatory wysłanych wiadomości i draftów,
- propaguje status deala z powrotem do Google Sheets.

Jeśli adres e-mail jest niepoprawny albo firma konfliktuje z aktywnym dealem dla tej samej tożsamości, deal przechodzi do `review_required`.

**Ważne:** ten rejestr jest wewnętrznym rejestrem pipeline’u. Nie jest to automatyczna integracja z zewnętrznym Zoho CRM.

---

## 4. Pierwsza bramka: tani pre-check metadanych

**[KOD]** Pre-check działa przed pobraniem treści i załączników.

### Jeżeli A: brakuje `messageId`

- wiadomość nie jest przetwarzana,
- powód: `missing_message_id`,
- nie jest pobierana treść.

### Jeżeli B: nadawca ma automatyczny prefiks

Przykładowo `noreply`, `no-reply`, systemowe bounce/automaty:

- wiadomość jest pomijana,
- agent nie pobiera pełnej treści.

### Jeżeli C: temat wygląda na automatyczny

Newsletter, delivery status, reset hasła, alert systemowy i podobne:

- wiadomość jest pomijana,
- nie ma odpowiedzi ani generowania treści.

### Jeżeli D: nie ma oczywistych sygnałów automatu

- agent „budzi się”,
- pobiera treść i nagłówki,
- przechodzi do pełnej klasyfikacji.

---

## 5. Normalizacja i bezpieczeństwo treści

### 5.1. Wiadomość i wątek

**[KOD]** System buduje ujednoliconą kopertę i rozróżnia:

- nową wiadomość,
- odpowiedź w istniejącym wątku,
- nową osobę z tej samej domeny,
- znanego klienta.

Samo `Re:` w temacie nie wystarcza do uznania wiadomości za poprawną odpowiedź w wątku. Liczą się nagłówki i relacja nadawcy.

Jeśli odpowiedź deklaruje istniejący wątek, ale nadawca/domena nie pasują do oczekiwanej relacji, klasyfikacja przechodzi do `unknown_review_needed`.

### 5.2. Prompt injection i sekrety

**[KOD]** Wiadomość jest zatrzymywana do ręcznego review, gdy zawiera sygnały takie jak:

- polecenie zignorowania instrukcji,
- żądanie ujawnienia tokenów, haseł lub promptu,
- prośba o wykonanie wiążącej oferty mimo bramek,
- podejrzane linki logowania,
- dane uwierzytelniające lub operacje finansowe wymagające człowieka.

Treść klienta i załączniki są danymi, nie instrukcjami dla modelu.

### 5.3. Załączniki

**[KOD]** Załączniki najpierw przechodzą deterministyczny routing bezpieczeństwa. To filtr formatów i metadanych, nie pełny skan antywirusowy.

Warianty:

- **bezpieczny PDF tekstowy** → PyMuPDF,
- **skan/PDF bez tekstu** → ścieżka OCR/Marker,
- **bezpieczny obraz techniczny** → OCR/vision,
- **faktura** → deterministyczna ekstrakcja wybranych pól,
- **plik wykonywalny, makro lub typ ryzykowny** → blokada/review,
- **błąd pobrania/ekstrakcji** → tryb zdegradowany; nie wolno udawać, że plik został przeczytany.

Ekstrakcja jest best-effort i nie może wywrócić całej pętli. Do stanu i briefingu trafia krótki podgląd lub wybrane pola, nie pełna treść pliku.

Finalna oferta wymaga `attachments_safe = true`; załącznik oznaczony `review` lub `block` blokuje ofertę.

---

## 6. Pełna klasyfikacja

**[KOD]** Klasyfikator produkcyjny jest deterministyczny. Nie polega na swobodnej decyzji LLM.

Zwraca równolegle osie:

- typ źródła,
- intencję,
- relację konwersacji,
- ryzyko,
- fit Orchesta RFQ,
- stan gotowości oferty,
- zalecaną akcję,
- pewność: `high`, `medium` albo `low`.

### 6.1. `new_quote_request`

Nowe, realne zapytanie o Orchesta RFQ/automatyzację RFQ, ale bez pełnego kompletu do finalnej oferty.

Przy `high`:

- kwalifikuje się do automatycznej pre-offer,
- nie kwalifikuje się jeszcze automatycznie do finalnej oferty, chyba że treść zawiera równocześnie sygnał finalnej oferty i kompletny zakres.

### 6.2. `quote_draft_ready`

Treść wygląda na kompletną lub bezpośrednio prosi o ofertę z zakresem.

- omija zwykłą pierwszą odpowiedź,
- kierowana jest do generatora finalnej oferty,
- generator jeszcze raz niezależnie sprawdza komplet danych i safety.

### 6.3. `new_general_business_inquiry`

Ogólne zapytanie biznesowe/automatyzacyjne bez jednoznacznego RFQ.

- może wymagać briefingu lub review,
- nie jest bezwarunkowo wysyłane jako pre-offer w aktualnym zestawie klas produkcyjnych.

### 6.4. `related_non_rfq_topic`

Temat powiązany, ale obok Orchesta RFQ, np. szkolenie/warsztat bez wdrożenia RFQ.

- bez odpowiedzi automatycznej,
- briefing do decyzji Łukasza.

### 6.5. `weak_fit_review_only`

Słaby fit, obszar regulowany, wrażliwy albo niezgodny z profilem rozwiązania.

- bez odpowiedzi,
- bez draftu,
- review ręczny.

### 6.6. `human_review_only`

Ryzyko bezpieczeństwa, prompt injection, credentiale, podejrzany załącznik/link, wrażliwa operacja lub niebezpieczne żądanie.

- bez odpowiedzi,
- bez automatycznego draftu,
- eskalacja do człowieka.

### 6.7. `existing_client_request`

Bezpieczne zapytanie od znanego klienta.

- może dostać automatyczną odpowiedź kontekstową,
- historia jest dopisywana do istniejącego deala,
- konflikt zakresu może zablokować proces do review.

### 6.8. `existing_thread_reply`

Poprawna odpowiedź klienta w istniejącym wątku.

- system bierze pod uwagę bieżącą wiadomość i wcześniejsze wiadomości tego samego nadawcy,
- nie traktuje własnych draftów/pytań jako odpowiedzi klienta,
- jeśli klient doprecyzował dane do oferty, wiadomość może przejść do finalnej oferty,
- wystarczy sygnał finalnej oferty **lub** zakresu, bo kontekst pochodzi również z wcześniejszych odpowiedzi.

### 6.9. `same_domain_new_person`

Nowa osoba z domeny, która jest już znana.

- domena sama nie wystarcza do przypisania do konkretnego zewnętrznego deala,
- bezpieczny kontekst może zostać połączony,
- konflikt firmy/deala prowadzi do review lub osobnego deala.

### 6.10. `vendor_admin_billing`

Faktura, wiadomość administracyjna, billing lub vendor.

- brak odpowiedzi sprzedażowej,
- brak pre-offer,
- faktura może zostać technicznie odczytana do briefingu, ale nie trafia do ścieżki RFQ.

### 6.11. `newsletter_automated_spam`

Newsletter, autoresponder, automatyczny spam/noise.

- ignorowanie,
- bez odpowiedzi,
- bez ekstrakcji załączników, jeżeli klasa jest oczywista.

### 6.12. `unknown_review_needed`

Treść niejednoznaczna albo niespójna relacja wątku/nadawcy.

- brak odpowiedzi,
- brak draftu,
- ręczna decyzja.

---

## 7. Druga bramka: decyzja o odpowiedzi pre-offer

### 7.1. Warunki konieczne dla automatycznej pre-offer

**[KOD/KONFIG]** Wszystkie muszą być prawdziwe:

1. Klasyfikator uznał, że należy przygotować odpowiedź.
2. Wiadomość nie ma `telegram_only`/review-only.
3. Pewność klasyfikacji to dokładnie `high`.
4. Rodzaj odpowiedzi to `first_response` albo `context_reply`.
5. Klasa znajduje się na produkcyjnej allowliście auto-send.
6. Dla zwykłego e-maila istnieje RFC `Message-ID`; dla Google Sheets może to być osobna nowa wiadomość.
7. Nie ma hard-blocka bezpieczeństwa.
8. Nie istnieje już obsłużony artefakt dla tego zdarzenia/deala.
9. Włączona jest flaga transportowa `HERMES_ALLOW_PRE_OFFER_SEND=1`.

Jeśli choć jeden warunek nie przejdzie, wiadomość nie jest wysyłana automatycznie.

### 7.2. Jak powstaje treść

**[KOD]** Klasyfikacja jest deterministyczna, ale treść pre-offer generuje osobny `hermes oneshot` jako inteligentny composer.

Composer ma:

- najpierw odpowiedzieć na realne pytanie klienta,
- proponować odwracalne założenie, gdy klient czegoś nie wie,
- nie mylić „brak informacji” z „nie”,
- nie pytać ponownie o znane fakty,
- zadawać maksymalnie 2 potrzebne pytania,
- nie używać ceny, kwot, `netto`, `brutto`, „inwestycja” ani „cennik”,
- nie obiecywać wiążącej/finalnej oferty,
- nie ujawniać informacji technicznych i sekretów,
- pisać po polsku lub po angielsku zgodnie z wiadomością,
- odnosić się naturalnie do bezpiecznego załącznika.

Po wygenerowaniu kod:

- waliduje JSON modelu,
- dodaje lub poprawia zwrot grzecznościowy,
- zastępuje wymyślone pytania zatwierdzoną listą,
- ogranicza pytania do 2,
- usuwa/odrzuca zabronione treści,
- dodaje stopkę programowo.

Nie ma „luźnego fallbacku”, który wysłałby cokolwiek po błędzie modelu. Błąd generacji/transportu trafia do błędu i review.

### 7.3. Pytania uzupełniające

Pytania dotyczą wyłącznie realnych blockerów oferty:

- liczba kont pocztowych,
- CRM: tak/nie,
- firma, jeśli nie da się jej ustalić.

Hermes wybiera najwyżej 2 pytania, które blokują następny bezpieczny krok.

Źródło zapytań i przykładowe zapytania są pomocne, ale nie blokują samego PDF-a.

### 7.4. Efekt wysyłki pre-offer

Jeśli Zoho potwierdzi sukces i zwróci stabilny identyfikator:

- wiadomość jest wysłana do klienta,
- identyfikator zostaje zapisany trwale,
- deal przechodzi do `waiting_for_customer`,
- status w Sheets staje się `oczekuje na klienta`,
- finalna oferta nadal nie jest wysyłana.

Jeżeli wynik transportu jest nieznany albo brak identyfikatora:

- system **nie ponawia ślepo wysyłki**, aby nie stworzyć duplikatu,
- deal przechodzi do `review_required`,
- status: `wymaga sprawdzenia`.

---

## 8. Trzecia bramka: kandydat do finalnej oferty

**[KOD]** Kandydat odpada natychmiast, gdy:

- wiadomość jest review-only/`telegram_only`, albo
- pewność klasyfikacji to `low`.

Kandydat przechodzi dalej, gdy:

- `draft_kind == final_offer`, albo
- klasa należy do klas final-offer i treść spełnia sygnały tematu/zakresu.

Dla nowego zapytania wymagany jest równocześnie:

- sygnał prośby o ofertę/cenę/finalizację,
- sygnał zakresu.

Dla `existing_thread_reply` wystarczy sygnał oferty **lub** zakresu, ponieważ brakujące fakty mogą pochodzić z wcześniejszych wiadomości klienta w tym samym wątku.

---

## 9. Ekstrakcja danych do finalnej oferty

**[KOD]** System analizuje bieżącą odpowiedź oraz wcześniejsze wiadomości tego samego klienta i próbuje ustalić:

### Dane wymagane

- firma,
- adres e-mail klienta,
- liczba kont pocztowych — liczba całkowita `>= 1`,
- CRM — jednoznaczne `tak` lub `nie`.

### Dane nieblokujące

- imię/nazwisko — przy braku używany jest neutralny zwrot,
- źródło zapytań: mail/formularz/oba — przy braku „do ustalenia operacyjnie”,
- przykładowe zapytania,
- miesięczna liczba zapytań.

### Ważna semantyka

- brak odpowiedzi nie oznacza `nie`,
- sprzeczne „tak” i „nie” są konfliktem,
- załączniki mogą potwierdzić posiadanie przykładów, ale nie zastępują innych blockerów.

---

## 10. Hard-blocki finalnej oferty

Finalna oferta jest blokowana, jeśli występuje którakolwiek sytuacja:

1. Brak kompletnego kontekstu bezpieczeństwa.
2. Niepoprawne nagłówki wątku.
3. Nadawca nie pasuje do wątku.
4. Załączniki nie są bezpieczne.
5. Wykryto prompt injection.
6. Pewność klasyfikacji jest niższa niż `medium` lub jej brak.
7. Klient oczekuje SMS jako elementu rozwiązania.
8. Klient oczekuje pełnej automatycznej wyceny technicznej bez kontroli.
9. Klient oczekuje automatycznego wysyłania finalnych ofert.
10. Cennik nie pochodzi z zatwierdzonego `approved/pricing.json`.
11. Występuje konflikt wiedzy/cennika.

Jeśli są tylko braki danych, status to `awaiting_data`.

Jeśli jest hard-block bezpieczeństwa/polityki, status to `blocked`.

---

## 11. Wariant „brakuje danych”

### Jeżeli A: brakuje danych, ale nie ma hard-blocka

- generator nie tworzy PDF-a,
- tworzy maksymalnie 2 pytania,
- poller używa transportu pre-offer i automatycznie wysyła pytania w tym samym wątku,
- zapisuje `awaiting_data_sent`,
- czeka na kolejną wiadomość klienta.

### Jeżeli B: brakuje danych i transport pre-offer jest niedostępny

- wynik pozostaje zablokowany,
- nie powstaje finalna oferta,
- Łukasz dostaje briefing.

### Jeżeli C: występuje hard-block

- pytania nie obchodzą blokady,
- nie powstaje PDF ani finalny draft,
- wymagane jest ręczne review.

---

## 12. Cennik finalnej oferty

**[KOD/POLITYKA]** Jedynym źródłem ceny jest zatwierdzony plik:

```text
rfq-final-offer-knowledge/approved/pricing.json
```

Aktualny cennik `orchesta-rfq-2026-07`:

- baza, 1 konto pocztowe: **7 200 zł netto**,
- każde kolejne konto: **3 000 zł netto**,
- integracja z CRM: **3 000 zł netto**,
- płatność: **100% z góry**,
- wdrożenie: **14 dni**,
- gwarancja: **14 dni**, zwrot całej kwoty.

Wzór:

```text
7 200 zł
+ max(0, liczba_kont - 1) × 3 000 zł
+ 3 000 zł, jeśli CRM = tak
```

Przykłady potwierdzone self-testem:

- 1 konto, CRM nie → 7 200 zł netto,
- 2 konta, CRM nie → 10 200 zł netto,
- 3 konta, CRM tak → 16 200 zł netto.

---

## 13. Generowanie i walidacja PDF

Jeżeli dane i safety przejdą:

1. System nadaje numer `ORCH-RFQ-ROK-SEKWENCJA`.
2. Oblicza cenę wyłącznie z zatwierdzonego JSON-a.
3. Zapisuje wersję cennika i hashe:
   - cennika,
   - zakresu,
   - danych klienta.
4. Renderuje JSON oferty.
5. Renderuje HTML z kontrolowanego szablonu.
6. Waliduje HTML.
7. Renderuje PDF przez WeasyPrint.
8. Odczytuje PDF przez pypdf.
9. Waliduje treść i liczbę stron.
10. Wylicza SHA-256 i rozmiar PDF-a.

### PDF musi zawierać

- klienta,
- firmę,
- cenę,
- wersję,
- zakres kont pocztowych,
- gwarancję 14 dni,
- Telegram,
- stopkę Łukasza.

### PDF nie może zawierać

- SMS,
- Zoho jako elementu oferty,
- pilotażu,
- terminu ważności,
- warunków prawnych,
- case studies,
- pustych znaczników Jinja.

Wyjątek: zabronione słowo w legalnej nazwie firmy nie blokuje PDF-a.

### Kontrola stron

- wymagane: 3 albo 4 strony,
- pusta strona blokuje,
- inna liczba stron blokuje.

### Błędy

- błąd HTML → `html_validation_failed`,
- brak renderowania PDF → `pdf_render_unavailable`/błąd,
- błąd treści PDF → `pdf_validation_failed`,
- brak obowiązkowego PDF-a → `pdf_required_for_final_offer`.

W każdym z tych przypadków finalny draft nie powinien zostać utworzony.

---

## 14. Tworzenie finalnego draftu w Zoho

Po poprawnym PDF-ie:

1. PDF jest przesyłany do Zoho jako załącznik.
2. System sprawdza kod odpowiedzi uploadu.
3. Wylicza lokalny SHA-256 i rozmiar.
4. Jeśli Zoho zwróci checksum, jest porównywany z lokalnym.
5. System wymaga potwierdzenia `storeName`, `attachmentName` i `attachmentPath`.
6. Buduje odpowiedź w tym samym wątku.
7. Tworzy wiadomość w trybie **save draft**, nie `send`.
8. Po sukcesie usuwa lokalny roboczy PDF.
9. Zapisuje identyfikator draftu, numer oferty, cenę, zakres i potwierdzenie załącznika.

### Twarda zasada

**Finalna oferta nie ma ścieżki automatycznej wysyłki.**

W kodzie końcowym operacja korzysta z `create_reply_draft`, a nie z transportu pre-offer `create_pre_offer_message`.

### Błędy uploadu/draftu

- upload HTTP != 200/201 → `attachment_upload_failed`,
- rozbieżny checksum → `attachment_checksum_mismatch`,
- niepełne potwierdzenie załącznika → `attachment_confirmation_incomplete`,
- brak pliku → `pdf_missing`,
- brak treści maila → `mail_body_missing`,
- błąd tworzenia draftu → operacja błędna/retryable według stanu.

---

## 15. Deduplikacja

System ma kilka niezależnych warstw ochrony.

### 15.1. To samo `messageId`

SQLite `PipelineState` rozpoznaje wiadomości w statusach:

- `done`,
- `precheck_skipped`,
- `blocked`.

Takie wiadomości nie są przetwarzane ponownie jak nowe.

### 15.2. Ta sama operacja dla wiadomości

Operacje `customer_send`, `customer_draft` i `offer_draft` mają trwałe statusy:

- `planned`,
- `in_progress`,
- `succeeded`,
- `retryable_failed`,
- `permanent_failed`.

Operacja zakończona `succeeded` nie jest tworzona drugi raz.

### 15.3. Istniejący draft w tym samym wątku Zoho

Przed utworzeniem draftu system sprawdza folder Drafts.

Jeśli w tym samym `threadId` już istnieje draft:

- blokuje nowy draft,
- zapisuje `blocked_existing_draft_present`,
- powiadamia Łukasza.

### 15.4. Zunifikowany deal między skrzynką i Sheets

Jeśli ten sam e-mail pojawi się z innego źródła:

- zdarzenie jest wiązane z tym samym dealem,
- nowa odpowiedź z drugiego źródła jest blokowana,
- Sheets dostaje status istniejącego procesu,
- system nie wysyła drugiej pre-offer.

### 15.5. Niepewny wynik wysyłki

Jeżeli transport mógł wysłać wiadomość, ale nie zwrócił potwierdzenia:

- system nie ryzykuje automatycznego duplikatu,
- zatrzymuje deal do review.

---

## 16. Retry i odporność na błędy

### 16.1. Odczyt Zoho

Dla HTTP:

- `429`, `500`, `502`, `503`, `504` → retryable z rosnącym opóźnieniem do 60 s,
- inne `4xx` → permanent failure,
- błąd bez statusu → retryable.

Maksymalna liczba retry w core: 5.

Na `401` klient próbuje jednokrotnie odświeżyć token OAuth, a następnie ponawia żądanie.

### 16.2. Operacje klienta

- jednoznaczny błąd przed wysyłką może być zapisany jako `retryable_failed`,
- `4xx` niebędący 429 zwykle staje się `permanent_failed`,
- nieznany wynik wysyłki pre-offer jest celowo zatrzymywany do review, a nie ślepo retry’owany.

### 16.3. Powiadomienia wewnętrzne

Jeżeli e-mail wewnętrzny nie zostanie wysłany:

- JSON podsumowania trafia do katalogu pending,
- kolejne uruchomienie najpierw próbuje wysłać zaległe powiadomienia,
- po sukcesie usuwa pending.

Ledger powiadomień zapobiega ponownemu wysłaniu tego samego briefingu.

---

## 17. Powiadomienia do Łukasza

### 17.1. Telegram

**[KONFIG]** Wrapper drukuje tylko istotną aktywność. Cron ma `deliver=origin`, więc wynik jest dostarczany do bieżącego kanału Telegram.

Brak aktywności = brak wiadomości.

Briefing może powiedzieć:

- kto napisał,
- czy osoba jest znana/nieznana,
- jak sklasyfikowano wiadomość,
- co Hermes zrobił lub czego nie zrobił,
- czy wysłał pre-offer,
- czy utworzył finalny draft,
- dlaczego temat wymaga review.

### 17.2. Wewnętrzny e-mail

Powiadomienie jest wysyłane wyłącznie na allowlistowany adres Łukasza.

- nie odpowiada klientowi,
- redaguje sekrety i numery kart,
- zawiera naturalny opis decyzji,
- przy finalnej ofercie podaje firmę, kontakt, zakres, cenę i lokalizację draftu,
- ma trwałą deduplikację.

### 17.3. Kiedy nie ma powiadomienia

Jeżeli przebieg nie ma żadnego interesującego zdarzenia, wrapper pozostaje cichy.

---

## 18. Statusy biznesowe

Wspólny deal może mieć status:

- `new` → `nowy`,
- `analysing` → `w analizie`,
- `draft_ready` → `draft gotowy`,
- `waiting_for_customer` → `oczekuje na klienta`,
- `offer_ready` → `oferta gotowa`,
- `review_required` → `wymaga sprawdzenia`.

Google Sheets otrzymuje polskie odpowiedniki oraz:

- klasyfikację,
- notatkę,
- czas sprawdzenia,
- hash treści,
- ID draftu,
- ID wysłanej wiadomości.

Po powstaniu finalnego draftu status deala to `offer_ready`, a w Sheets `oferta gotowa`.

---

## 19. Kompletne warianty A–N

### Wariant A — nowy bezpieczny e-mail RFQ, brakuje danych

1. Pre-check przepuszcza.
2. Klasa `new_quote_request`, pewność `high`.
3. Composer odpowiada na realne pytanie i zadaje tylko niezbędne pytania.
4. Pre-offer jest automatycznie wysłany w tym samym wątku.
5. Deal: `waiting_for_customer`.
6. Łukasz dostaje briefing.
7. Nie ma ceny ani PDF-a.

### Wariant B — e-mail przypominający dawne powiadomienie formularza

1. Nie istnieje dla niego osobny typ źródła ani specjalny parser.
2. Wiadomość jest oceniana jak zwykły e-mail od rzeczywistego nadawcy.
3. Może przejść dalej albo zostać odrzucona wyłącznie według normalnych reguł skrzynki.
4. System nie próbuje odszukiwać zastępczego adresu klienta w treści.

### Wariant C — bezpieczny lead z Google Sheets

1. Wiersz jest nowy lub zmieniony.
2. Adres e-mail jest poprawny.
3. Klasa `new_quote_request`, `high`.
4. Nie ma sygnałów non-RFQ, injection, sekretów ani żądania wiążącej oferty.
5. System wysyła osobną pre-offer przez Zoho.
6. Zapisuje trwały sent ID.
7. Wiersz: `oczekuje na klienta`.
8. Finalna oferta nie jest wysyłana.

### Wariant D — klient odpowiada i nadal brakuje danych

1. Klasa `existing_thread_reply`.
2. System scala bieżącą odpowiedź z wcześniejszym kontekstem klienta.
3. Nie pyta ponownie o znane fakty.
4. Wysyła kolejne minimalne pytania pre-offer.
5. Nadal nie tworzy finalnej oferty.

### Wariant E — komplet danych do oferty

1. Kandydat final-offer przechodzi.
2. Wszystkie dane wymagane i safety są poprawne.
3. Cena jest obliczana z zatwierdzonego JSON-a.
4. Powstają JSON, HTML i PDF.
5. PDF przechodzi walidację 3–4 stron i treści.
6. PDF zostaje przesłany do Zoho.
7. Powstaje finalny draft w wątku.
8. Klient niczego jeszcze nie otrzymuje.
9. Łukasz dostaje e-mail i Telegram z ceną/lokalizacją draftu.

### Wariant F — prośba o ofertę, ale brakuje blockerów

1. Generator finalnej oferty zwraca `awaiting_data`.
2. Nie tworzy PDF-a.
3. System automatycznie wysyła maksymalnie 2 pytania pre-offer.
4. Czeka na odpowiedź.

### Wariant G — wiadomość niejednoznaczna lub słaby fit

1. Klasa `unknown_review_needed`, `related_non_rfq_topic` albo `weak_fit_review_only`.
2. Brak automatycznej odpowiedzi.
3. Brak finalnego draftu.
4. Briefing do Łukasza.

### Wariant I — ryzyko bezpieczeństwa

1. Klasa `human_review_only` lub hard-block safety.
2. Nie ma pre-offer.
3. Nie ma PDF-a/draftu finalnej oferty.
4. Ręczne review.

### Wariant J — newsletter/faktura/admin

- newsletter/spam → ignorowanie,
- faktura/admin/billing → klasyfikacja administracyjna, bez ścieżki sprzedażowej,
- ewentualna ekstrakcja faktury trafia tylko do bezpiecznego briefingu.

### Wariant K — ten sam lead w mailu i Sheets

1. Rejestr rozpoznaje ten sam e-mail/deal.
2. Drugi adapter nie wysyła duplikatu.
3. Status w Sheets synchronizuje się z istniejącym dealem.

### Wariant L — draft już istnieje w Zoho

1. Poller znajduje draft w tym samym wątku.
2. Blokuje tworzenie kolejnego.
3. Łukasz dostaje informację o istniejącym drafcie.

### Wariant M — błąd API/PDF/draftu

1. Brak udawania sukcesu.
2. Operacja dostaje status retryable/permanent albo review — zależnie od rodzaju błędu.
3. Finalna oferta nie jest oznaczana jako gotowa bez potwierdzenia draftu i załącznika.
4. Łukasz dostaje błąd/briefing.

### Wariant N — ponowne uruchomienie tego samego zgłoszenia

1. Stan wiadomości, operacji, artefaktu i deala jest sprawdzany.
2. Sukces nie jest wykonywany drugi raz.
3. Kontrolowany test potwierdził jeden draft i jeden PDF-attachment po ponownym przebiegu.

---

## 20. Rozjazdy i rzeczy wymagające świadomej decyzji

### Rozjazd 1 — stara dokumentacja Google Sheets

**[ROZJAZD]** Starszy plik `references/google-sheets-leads.md` opisywał tryb auto-draft/no-send.

**Aktualny kod:** wysyła zatwierdzone typy operacyjne automatycznie po trwałej walidacji typu, odbiorcy i deala. Wrapper nie dodaje globalnego `--auto-send`; finalna oferta pozostaje draftem.

Źródłem prawdy jest aktualny kod i produkcyjny wrapper.

### Rozjazd 2 — komentarz `note_for` w Sheets

**[ROZJAZD]** Funkcja `note_for` nadal zawiera historyczny tekst „w trybie startowym nie tworzyłem jeszcze draftu Zoho”. W normalnej ścieżce produkcyjnej notatka po sukcesie jest później nadpisywana informacją o wysłaniu pre-offer, ale stary tekst jest mylący i powinien zostać usunięty.

### Rozjazd 3 — prompt composera mówi „wersja robocza do sprawdzenia”

**[ROZJAZD TECHNICZNY]** Composer sam nie wysyła wiadomości i generuje tekst roboczy, ale w ścieżce pre-offer jego zwalidowany wynik jest następnie automatycznie wysyłany przez osobny, bramkowany transport.

To nie zmienia bezpieczeństwa transportu, ale opis w promptcie może sugerować dawny model draft-only.

### Rozjazd 4 — termin „CRM”

Kod potrafi policzyć opcję integracji CRM i utrzymuje wewnętrzny rejestr leadów, ale bieżący pipeline nie wykonuje automatycznych zapisów do zewnętrznego Zoho CRM. Nie należy utożsamiać SQLite `UnifiedLeadRegistry` z gotową integracją CRM klienta.

### Rozjazd 5 — `auto_draft`

Kod zachowuje kompatybilność z trybem auto-draft, ale produkcyjny wrapper działa w trybie auto-send pre-offer i final-draft-only. Flaga Sheets `--auto-draft` jest oznaczona jako legacy.

### Brak 6 — kontrola folderu `Sent` i przejęcie przez człowieka

**[BRAK]** Przed automatyczną wysyłką poller sprawdza własny stan oraz istniejące drafty w wątku, ale nie sprawdza, czy człowiek wysłał już nowszą odpowiedź z folderu `Sent`. Nie ma też trwałego stanu `human_takeover`/`paused_by_human`. Możliwa jest równoległa odpowiedź Hermesa i handlowca.

### Brak 7 — nowy wątek od tej samej osoby

**[BRAK]** Rejestr aktywnego deala koreluje przede wszystkim po znormalizowanym adresie e-mail. Nowy temat od tej samej osoby może zostać połączony z aktywną sprawą. Brakuje jawnej polityki nowy wątek kontra nowa sprawa.

### Brak 8 — długa rozmowa

**[BRAK]** Helper historii pobiera maksymalnie 8 wiadomości tego samego nadawcy z Inboxu, a rejestr zachowuje do 30 fragmentów kontekstu. Nie ma limitu liczby etapów rozmowy ani automatycznego handoffu po przekroczeniu długości/czasu/zmian zakresu. Folder `Sent` nie jest częścią rekonstrukcji historii.

### Brak 9 — pełny skan antywirusowy

**[BRAK]** Routing blokuje wskazane rozszerzenia, typy MIME, szyfrowanie, zbyt duże pliki i wybrane niespójności magic bytes. Nie uruchamia skanera antywirusowego lub sandboxa malware. Wynik `allow` oznacza przejście filtrów, nie gwarancję braku złośliwego kodu.

### Brak 10 — cenniki złożone

**[OGRANICZENIE]** Finalna oferta czyta jeden zatwierdzony `pricing.json` i realizuje proste reguły skrzynek/CRM. Nie ma wyboru cennika według klienta, daty, segmentu, waluty, produktu ani priorytetu reguł. Złożone wdrożenie wymaga wersjonowanego pakietu wiedzy i osobnych testów konfliktów.

---

## 21. Weryfikacja wykonana na realnym kodzie

Uruchomione testy:

```text
uv run --with pytest --with weasyprint --with jinja2 --with pypdf python -m pytest -q
51 passed in 6.09s

mail-lead-pipeline dry-run: All 37 fixture cases passed.
zoho_mail_poller self-test: ok
google_sheets_lead_poller self-test: ok
pipeline_state self-test: ok
rfq_final_offer self-test: ok
```

Pierwsza próba pełnego `pytest` w środowisku bez WeasyPrint miała 48 zaliczonych i 3 błędy środowiskowe związane wyłącznie z brakiem biblioteki PDF. Powtórzenie z kompletem zależności PDF zaliczyło wszystkie 51 testów.

Dodatkowo wcześniejszy kontrolowany przebieg Zoho potwierdził:

- powstał jeden finalny draft,
- powstał jeden PDF 4-stronicowy,
- PDF był załączony,
- wiadomość nie trafiła do Sent,
- ponowny przebieg nie utworzył duplikatu.

---

## 22. Lista decyzji do zatwierdzenia przez Łukasza

Proszę potwierdzić lub poprawić poniższe punkty:

1. Czy **każda bezpieczna pre-offer** ma być automatycznie wysyłana bez review?
2. Czy automatyczna pre-offer ma obejmować tylko `new_quote_request`, czy także bezpieczne `existing_client_request`, `existing_thread_reply` i `same_domain_new_person`?
3. Czy firma, liczba skrzynek i decyzja CRM to właściwy minimalny komplet do PDF-a?
4. Czy źródło zapytań i przykładowe zapytania mają pozostać nieblokujące?
5. Czy finalna oferta ma zawsze pozostać wyłącznie draftem Zoho z PDF-em?
6. Czy przy nieznanym wyniku wysyłki system ma nadal zatrzymywać retry i wymagać review?
7. Czy wiadomości `related_non_rfq_topic` mają zawsze pozostać bez automatycznej odpowiedzi?
8. Czy identyfikacja deala ma zawsze opierać się najpierw na twardych identyfikatorach, a nie samym e-mailu?
9. Czy Łukasz chce powiadomienie o każdym wysłanym pre-offer, czy tylko o finalnych draftach, review i błędach?
10. Czy wewnętrzny e-mail ma nadal iść równolegle z Telegramem?
11. Czy aktualne ceny 7 200 / 3 000 / 3 000 zł netto są nadal obowiązujące?
12. Czy płatność 100% z góry, 14 dni wdrożenia i 14 dni gwarancji są nadal obowiązujące?
13. Czy system ma później zapisywać deal także do zewnętrznego CRM, czy wewnętrzny rejestr SQLite jest wystarczający?
15. Czy każda ręczna odpowiedź znaleziona w `Sent` ma automatycznie ustawiać przejęcie sprawy przez człowieka?
16. Jak długo ma obowiązywać przejęcie: do ręcznego wznowienia, do nowego wątku czy przez określony czas?
17. Po ilu wiadomościach, dniach lub zmianach zakresu długa rozmowa ma trafić do człowieka?
18. Jak rozpoznawać nową sprawę od tej samej osoby: po nowym `threadId`, temacie, czasie i analizie sensu wiadomości?
19. Czy pełny skan antywirusowy ma być warunkiem szerszej automatycznej obsługi załączników?
20. Czy złożone cenniki mają być wybierane automatycznie według klienta i daty, czy każda nietypowa wycena ma wymagać ręcznej decyzji?
