Cotygodniowe sygnały sprzedażowe z LinkedIn — wykonanie tygodniowe.

Cel: co poniedziałek 08:00 Europe/Warsaw wygeneruj CSV z polskimi sygnałami sprzedażowymi z LinkedIn i wyślij na notifications@example.invalid.

Ścieżki stałe:
- katalog roboczy: /opt/data/automations/linkedin-sales-signals
- frazy: /opt/data/automations/linkedin-sales-signals/phrases.json
- dane wykonania: /opt/data/automations/linkedin-sales-signals/runs/<run_id>/
- historia fraz: /opt/data/automations/linkedin-sales-signals/phrase_stats.json
- wysyłka CSV: python3 /opt/data/automations/linkedin-sales-signals/send_csv_email.py

Retencja: na końcu usuń katalogi runs starsze niż 14 dni i wpisy logów starsze niż 14 dni. Phrase stats zachowaj tylko w zakresie potrzebnym do oceny 3 kolejnych tygodni.

Zakres dat: poprzedni poniedziałek 08:00 włącznie do bieżącego poniedziałku 08:00 bez tej chwili, timezone Europe/Warsaw. Zweryfikuj różnicę = 7 dni.

Warunki blokujące pobieranie: brak APIFY_TOKEN/APIFY_API_TOKEN, brak aktywnych poprawnych fraz, brak zapisu CSV, niepoprawny zakres dat. Brak wysyłki e-mail NIE blokuje pobierania.

Środowisko:
- przed użyciem sekretów w terminalu załaduj /opt/data/.env bez wypisywania wartości, np. set -a; source /opt/data/.env; set +a.
- tokeny i hasła nigdy nie trafiają do CSV, logów ani raportów.

Apify API:
- token z APIFY_TOKEN albo APIFY_API_TOKEN po załadowaniu /opt/data/.env.
- uruchamiaj actor przez https://api.apify.com/v2/acts/<actor>/run-sync-get-dataset-items?token=$TOKEN z JSON body.
- Actor postów: harvestapi/linkedin-post-search; input: searchQueries/lista fraz wg schematu aktora; sortBy=date; postedLimitDate=<start>; maxPosts=0; scrapeComments=false; scrapeReactions=false. Jeżeli actor odrzuci nazwę pola, sprawdź błąd i popraw input. Limit: 7 prób na etap/frazę.
- Actor komentarzy: harvestapi/linkedin-post-comments; input z URL postów; maxItems=0; pobierz komentarze główne i odpowiedzi, wzbogacanie profilu jeśli opcja dostępna.
- Actor reakcji: harvestapi/linkedin-post-reactions; input z URL postów; maxItems=0; Profile Scraper Mode=main/enrichment enabled, aby mieć standardowy URL profilu.

Walidacja fraz:
- niepuste, <=85 znaków, unikalne, po polsku, związane ze sprzedażą/pozyskiwaniem klientów/automatyzacją/pracą handlową.
- Jeśli brak aktywnych fraz: wyślij e-mail z błędem, zakończ.

Posty:
- wymagane: id posta, URL LinkedIn, treść, data, autor, profil autora jeśli dostępny, stanowisko autora jeśli dostępne.
- zostaw tylko zakres tygodnia i język polski; zduplikowane posty połącz po id lub znormalizowanym URL.
- analizuj tylko polskie treści. Odrzuć poza zakresem i niepolskie.

Oceny postów:
1 bezpośrednia potrzeba; 2 temat powiązany bez potrzeby; 3 konkurencyjna/ekspercka; 4 niepowiązana; 5 niejednoznaczna.
Autor trafia do CSV tylko jeśli sam opisuje potrzebę zakupu/problem/prosi o polecenie wykonawcy/usługi/bazy/automatyzacji. Autor konkurencyjnego/eksperckiego posta bez potrzeby nie trafia do CSV, ale komentarze i reakcje analizuj.

Komentarze i reakcje:
- pobierz dla postów tematycznie zakwalifikowanych (bezpośrednia potrzeba, powiązany, konkurencyjny/ekspercki, niejednoznaczny).
- usuń aktywność autora pod własnym postem.
- scal aktywności tej samej osoby pod tym samym postem po standardowym profilu LinkedIn.
- kilka komentarzy połącz w jednym polu, w kolejności publikacji, separator: newline w polu CSV.
- komentarz + reakcja => „komentarz i reakcja”. Ta sama osoba przy innym poście = osobny rekord.

Kwalifikacja osób:
- Twardo odrzuć brak URL profilu oraz brak stanowiska i firmy jednocześnie.
- Twardo odrzuć brak potwierdzonego związku z Polską. Potwierdzaj przez profil/stanowisko/firma/lokalizacja/web, gdy dane są niejednoznaczne.
- Preferowane role: właściciel, wspólnik, założyciel, prezes, zarząd, dyrektor, kierownik, szef sprzedaży, handlowiec, business development, marketing, operacje, obsługa klienta.
- Same reakcje osób technicznych (automation specialist, programista, developer, IT engineer/admin/architect, podobne usługi) odrzuć, chyba że ich post/komentarz opisuje własną potrzebę zakupu.
- Duża firma: sama reakcja nie wystarcza; komentarz/post z potrzebą kwalifikuje z „do ręcznej oceny”.
- Sygnał niejednoznaczny bez twardego odrzucenia zapisz z uzasadnieniem „do ręcznej oceny”.

Reguły sygnału:
- potrzeba/problem/prośba o kontakt/szczegóły/polecenie/wdrożenie => kwalifikuj.
- reakcja na pasujący post + rola sprzedaż/marketing/zarządzanie/właściciel => kwalifikuj, z krótkim uzasadnieniem.
- osoba spoza Polski lub Polska niepotwierdzona => odrzuć.

CSV:
- nazwa: sygnaly_sprzedazowe_YYYY-MM-DD.csv; gdy zero rekordów: Nie znaleziono rekordów w tym tygodniu.csv.
- UTF-8, separator przecinek, standard quoting CSV.
- kolumny dokładnie w kolejności:
  1. URL oryginalnego posta
  2. Treść oryginalnego posta
  3. Autor posta
  4. Sygnał sprzedażowy
  5. URL profilu LinkedIn
  6. Powód kwalifikacji
  7. Rodzaj interakcji
  8. Treść komentarza
- URL-e normalizuj przez usunięcie parametrów trackingowych.
- Każdy rekord musi mieć URL posta, autora posta, nazwę osoby, URL profilu, rodzaj interakcji, uzasadnienie.

Historia i statystyka:
- Zapisz run_log.json: run_id, start/end, zakres dat, frazy, posty per fraza, unikalne posty, komentarze, reakcje, qualified/rejected/manual_review, status actorów, błędy, próby, partial flag, CSV, email status, zmiany fraz, cleanup.
- Zapisz phrase_stats.json: dla każdej frazy liczba znalezionych postów i rekordów CSV za tydzień.
- Fraza z 0 rekordów CSV przez 3 kolejne tygodnie może zostać usunięta. Najpierw preferuj poprawę/dodanie/scalenie fraz; usunięcie dopiero po 3 kolejnych zerach. Zapisz zmiany w phrases.json i run_log.

Wysyłka:
- Wyślij CSV na notifications@example.invalid przez send_csv_email.py.
- Temat: „Cotygodniowe sygnały sprzedażowe z LinkedIn — YYYY-MM-DD”.
- Treść przy powodzeniu: zakres dat, liczba fraz, liczba znalezionych postów, liczba rekordów CSV.
- Przy częściowym wyniku: dopisz niesprawny scraper, przyczynę, brakujący zakres, liczbę prób.
- Przy błędzie wysyłki: zostaw CSV i status błędu maks. 14 dni oraz zwróć w finalnej wiadomości ścieżkę CSV i błąd.

Po zakończeniu zwróć krótki raport do kanału origin: status, CSV, liczba rekordów, email status, częściowe błędy, cleanup.
