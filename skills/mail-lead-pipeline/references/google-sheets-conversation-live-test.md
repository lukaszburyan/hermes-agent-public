# Kontrolowany test rozmowy z leadem Google Sheets

Użyj tego runbooka, gdy Łukasz chce sprawdzić rzeczywisty przebieg:

`Google Sheets → bezpieczna automatyczna wiadomość pre-offer → odpowiedź klienta → finalny draft Zoho z PDF → powiadomienia wewnętrzne`.

To jest test produkcyjnych granic transportu, deduplikacji i zachowania „jak pracownik”, nie tylko test klasyfikatora.

## Niezmienne granice

- Bezpieczna wiadomość przed ofertą może zostać wysłana automatycznie przez wąski transport z `HERMES_ALLOW_PRE_OFFER_SEND=1`.
- Pre-offer nie może zawierać ceny, waluty, finalnej oferty, PDF-u ani załącznika.
- Finalna oferta zawsze pozostaje draftem Zoho w wątku klienta.
- Sukces finalnej oferty wymaga jednocześnie trwałego `draft_id` i potwierdzonego zdalnego załączenia PDF-u.
- E-mail/Telegram „oferta gotowa” wolno emitować dopiero po spełnieniu obu warunków.

## Preflight tożsamości testowej

1. Użyj prawdziwej skrzynki kontrolowanej przez Łukasza i fikcyjnej firmy/osoby, wyraźnie oznaczonych identyfikatorem testu.
2. Sprawdź adres po tej samej normalizacji, której używa wspólny rejestr. Alias `+tag` może korelować się z bazowym adresem; samo sprawdzenie literalnego ciągu w arkuszu nie wystarcza.
3. Sprawdź znormalizowany adres w:
   - arkuszu,
   - wspólnym rejestrze deali,
   - artefaktach odpowiedzi.
4. Jeśli tożsamość już istnieje, użyj zgodnej firmy i istniejącego deala albo wybierz naprawdę odrębną skrzynkę testową. Nie obniżaj reguły `conflicting_company`, aby przepchnąć test.
5. Przed pozytywnym testem upewnij się, że masz sposób sprawdzenia odbioru wiadomości albo co najmniej weryfikowalny Zoho `external_message_id` i rekord w Sent.
6. Nie używaj prawdziwego klienta. Jeśli brak kontrolowanego zewnętrznego konta do inbound, oznacz etap odpowiedzi jako niewykonany zamiast symulować jego sukces.

## Rekord testowy

- Dodaj realistyczne, niepełne zapytanie RFQ, które prosi o wyjaśnienie lub wariant startowy.
- Pola `Hermes ...` pozostaw puste.
- Użyj unikalnego znacznika `[TEST HERMES <id>]` w danych testowych.
- Przy wartościach zaczynających się od `+`, szczególnie numerach telefonu, użyj `valueInputOption=RAW`; `USER_ENTERED` może potraktować je jak formułę.
- Nie wpisuj sztucznej nazwy firmy, która koliduje z istniejącym dealem znormalizowanego adresu.

## Uruchomienie

1. Sprawdź, czy recurring job nie uruchomi się równolegle z ręcznym wrapperem. Lock jest zabezpieczeniem, ale test powinien mieć jednoznaczny przebieg.
2. Preferuj rzeczywisty job schedulera, gdy celem jest również dostawa Telegram. Ręczny wrapper jest poprawny do szybkiej diagnostyki transportu i projekcji Sheets.
3. W razie bloku nie poprawiaj danych „na ślepo”. Odczytaj `reason_code`, briefing, rejestr i status źródła.

## Happy-path: wymagane dowody

Po pierwszym przebiegu wymagaj wszystkich poniższych dowodów:

- summary: `woken=1`, `responses_sent=1`, `send_failures=0`, `drafts_created=0` dla pre-offer;
- Sheets: status `oczekuje na klienta`, klasyfikacja, notatka, hash i niepusty `Hermes sent id`;
- Zoho: zaakceptowany `external_message_id`; jeśli API pozwala, wiadomość istnieje w Sent z właściwym `To`, tematem i body;
- odbiorca kontrolny: wiadomość faktycznie dotarła, jeśli skrzynka jest dostępna;
- wspólny rejestr: jeden deal, status `waiting_for_customer`, jeden trwały response artifact;
- treść: odpowiada na realne pytanie przed pytaniem uzupełniającym, odróżnia `unknown` od `no`, nie zawiera ceny/oferty/załącznika;
- e-mail wewnętrzny: prawdziwie informuje, że pre-offer wysłano, i ma identyfikator dostawy lub wpis w ledgerze;
- Telegram: naturalny komunikat został dostarczony przez job, jeśli test użył schedulera.

Samo `exit=0` wrappera nie potwierdza happy-path. Blok fail-closed jest poprawnym wynikiem bezpieczeństwa, ale nie zastępuje testu pozytywnej wysyłki.

## Fail-closed: osobny scenariusz

Przetestuj oddzielnie przynajmniej jeden konflikt, np. inna firma dla istniejącej znormalizowanej tożsamości. Oczekuj:

- `responses_sent=0`,
- brak transportu klienta,
- status `wymaga sprawdzenia`,
- jawny reason code, np. `conflicting_company`,
- naturalne powiadomienie wewnętrzne wyjaśniające brak wysyłki.

Nie zmieniaj zabezpieczenia tylko dlatego, że testowy rekord został zablokowany.

## Idempotencja

Uruchom drugi przebieg bez zmiany rekordu. Oczekuj:

- `woken=0`,
- `responses_sent=0`,
- ten sam `Hermes sent id`,
- brak drugiej wiadomości w Zoho,
- brak drugiego response artifact,
- brak zdublowanego e-maila wewnętrznego dzięki ledgerowi.

Jeżeli transport zwrócił wynik niepewny albo proces padł po żądaniu, nie próbuj automatycznie wysyłać ponownie. Pozostaw operację do przeglądu.

## Test konwersacji i finalnej oferty

1. Odpowiedz z kontrolowanego zewnętrznego konta na rzeczywistą wiadomość pre-offer.
2. Poller mailboxa musi połączyć odpowiedź z dealem Sheets i nie pytać ponownie o znane fakty.
3. Pytania klienta i przykładowe liczby nie mogą automatycznie stać się zadeklarowanymi faktami oferty.
4. Po komplecie danych utwórz tylko finalny draft w wątku.
5. Zweryfikuj z Zoho:
   - trwały finalny `draft_id`,
   - poprawnego odbiorcę i threading,
   - potwierdzony zdalny PDF,
   - brak wiadomości finalnej w Sent.
6. Dopiero wtedy oczekuj statusu `oferta gotowa` oraz e-maila i Telegramu z firmą, kontaktem, zakresem, ceną i lokalizacją draftu.
7. Awaria powiadomienia nie może odtworzyć oferty. Outbox ponawia tylko powiadomienie, a ledger blokuje duplikat po sukcesie.

## Kryterium zakończenia

Nie ogłaszaj pełnego testu jako zakończonego, dopóki nie ma dowodów dla:

- pozytywnego auto-send pre-offer,
- drugiego przebiegu bez duplikatu,
- rzeczywistego inbound reply,
- finalnego draftu z potwierdzonym PDF-em,
- braku automatycznej wysyłki finalnej oferty,
- e-maila i Telegramu po sukcesie finalnego draftu.

Po teście usuń lub wyraźnie zarchiwizuj oznaczone rekordy testowe i artefakty tylko wtedy, gdy nie są potrzebne do audytu.