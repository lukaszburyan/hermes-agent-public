# Orchesta RFQ — cała logika prostym językiem

## Najkrótsza wersja

System pilnuje dwóch miejsc:

1. skrzynki e-mail,
2. leadów kampanijnych zapisanych w Google Sheets.

Gdy pojawia się nowe zgłoszenie, Hermes sprawdza:

- czy napisał człowiek, czy automat,
- czy wiadomość dotyczy Orchesta RFQ,
- czy można bezpiecznie odpowiedzieć,
- czy klient podał dane potrzebne do przygotowania oferty,
- czy ta sama sprawa nie została już wcześniej obsłużona.

Potem dzieje się jedna z czterech rzeczy:

1. Hermes ignoruje automat lub spam.
2. Hermes niczego nie wysyła i prosi Łukasza o decyzję.
3. Hermes wysyła klientowi bezpieczną wiadomość wstępną bez ceny.
4. Hermes przygotowuje finalną ofertę z PDF-em i zapisuje ją w wersjach roboczych Zoho. Finalnej oferty nigdy nie wysyła sam.

---

## 1. Jak często system sprawdza nowe zgłoszenia

Skrzynka e-mail jest sprawdzana co 2 minuty.

Google Sheets jest sprawdzany co 5 minut.

Jeżeli poprzednie sprawdzanie jeszcze trwa, system nie uruchamia drugiego równolegle. Czeka na następny termin. Dzięki temu dwie kopie Hermesa nie próbują w tym samym czasie odpowiedzieć temu samemu klientowi.

Jeżeli nie pojawiło się nic nowego, Hermes nie wysyła Łukaszowi pustych powiadomień.

---

## 2. Skąd może przyjść zgłoszenie

### Zwykły e-mail

Klient pisze bezpośrednio na skrzynkę Orchesta.

Hermes może odpowiedzieć w tym samym wątku, dzięki czemu klient widzi ciąg całej rozmowy.

### Google Sheets

Nowy lead może pojawić się w arkuszu po kampanii reklamowej.

Hermes czyta dane z wiersza, na przykład:

- imię,
- e-mail,
- firmę,
- wiadomość,
- źródło leada.

Jeżeli wiersz jest nowy albo ktoś zmienił jego treść, Hermes analizuje go ponownie.

Jeżeli nic się nie zmieniło, nie odpowiada drugi raz.

Pierwsza wiadomość do leada kampanijnego korzysta wyłącznie z informacji zapisanych w wierszu Google Sheets i nie dopowiada nieznanego źródła pozyskania.

---

## 3. Co Hermes robi jako pierwsze

Zanim przeczyta całą wiadomość, sprawdza nadawcę i temat.

### Wiadomość nie ma poprawnego numeru

Hermes jej nie obsługuje, bo nie może później pewnie rozpoznać, czy już ją przetworzył.

### Wiadomość przyszła od automatu

Dotyczy to na przykład:

- adresów typu „nie odpowiadaj”,
- potwierdzeń dostarczenia,
- autoresponderów,
- resetów haseł,
- alertów systemowych,
- oczywistych newsletterów.

Hermes nie czyta wtedy niepotrzebnie całej treści i nie odpowiada.

### Wiadomość wygląda na prawdziwy kontakt

Hermes czyta treść, sprawdza rozmowę i ewentualne załączniki.

---

## 4. Sprawdzenie bezpieczeństwa

Zanim Hermes cokolwiek wyśle klientowi, sprawdza, czy wiadomość nie jest ryzykowna.

Zatrzymuje sprawę, gdy widzi na przykład:

- prośbę o ujawnienie hasła, tokenu albo innych poufnych danych,
- polecenie zignorowania zasad bezpieczeństwa,
- próbę wymuszenia natychmiastowej wiążącej oferty,
- podejrzany link do logowania,
- plik wykonywalny,
- dokument z makrami,
- żądanie wykonania płatności lub innej wrażliwej operacji,
- niezgodność między nadawcą a wcześniejszą rozmową.

W takiej sytuacji:

- Hermes niczego nie wysyła klientowi,
- nie przygotowuje finalnej oferty,
- informuje Łukasza, co wzbudziło podejrzenie.

Treść wiadomości i załączników jest traktowana jako informacja od klienta. Nie może zmienić zasad działania Hermesa.

---

## 5. Co dzieje się z załącznikami

Hermes najpierw filtruje plik. Sprawdza między innymi nazwę, deklarowany rodzaj, rozmiar, szyfrowanie oraz — gdy ma już pobrany plik — podstawowy podpis jego zawartości.

Blokuje pliki wykonywalne, archiwa, dokumenty z makrami, pliki zaszyfrowane, zbyt duże i część plików o niezgodnym rodzaju.

Ważne ograniczenie: to nie jest pełny skan antywirusowy. System nie powinien opisywać pliku jako „na pewno bezpieczny”. Może jedynie stwierdzić, że plik przeszedł obecne filtry i nadaje się do kontrolowanego odczytu.

### Zwykły PDF z tekstem

Hermes może go przeczytać i wykorzystać zawarte w nim informacje.

### Skan lub zdjęcie

Hermes próbuje odczytać treść ze skanu albo obrazu.

### Faktura

Hermes może wyciągnąć podstawowe dane, takie jak numer faktury, kwota i termin płatności. Nie traktuje jednak faktury jako zapytania sprzedażowego.

### Niebezpieczny plik

Hermes nie otwiera go do dalszej analizy i zatrzymuje sprawę do ręcznego sprawdzenia.

### Nie udało się przeczytać pliku

Hermes nie udaje, że zna jego zawartość. Może nadal odpowiedzieć na samą wiadomość, jeżeli jest to bezpieczne i załącznik nie był konieczny. W powiadomieniu dla Łukasza zaznacza, że pliku nie udało się odczytać.

---

## 6. Jak Hermes rozumie rodzaj wiadomości

Hermes przypisuje wiadomość do jednego z poniższych przypadków.

### Nowe zapytanie o Orchesta RFQ

Klient pyta o automatyzację obsługi zapytań ofertowych, wdrożenie Orchesta RFQ albo podobny zakres.

Jeżeli zgłoszenie jest bezpieczne, Hermes może wysłać pierwszą wiadomość bez ceny.

### Klient podał już prawie wszystko do oferty

Hermes próbuje przygotować finalną ofertę. Zanim to zrobi, jeszcze raz sprawdza wszystkie wymagane dane i zasady bezpieczeństwa.

### Ogólne pytanie o automatyzację

Wiadomość może być związana z działalnością Orchesta, ale nie wiadomo jeszcze, czy chodzi o Orchesta RFQ.

Hermes nie powinien automatycznie zakładać, że klient chce ofertę RFQ. W razie wątpliwości zostawia decyzję Łukaszowi.

### Temat podobny, ale nie dotyczy wdrożenia RFQ

Przykład: klient chce wyłącznie szkolenie lub warsztat.

Hermes nie wysyła odpowiedzi automatycznej. Informuje Łukasza.

### Słabe dopasowanie

Sprawa jest zbyt odległa od oferty Orchesta, ryzykowna albo dotyczy szczególnie wrażliwej branży.

Hermes nie odpowiada sam.

### Wiadomość wymaga człowieka

Dotyczy bezpieczeństwa, poufnych danych, skargi, płatności albo innej wrażliwej sprawy.

Hermes nie odpowiada sam.

### Wiadomość od znanego klienta

Hermes bierze pod uwagę wcześniejszą historię i może odpowiedzieć w kontekście trwającej współpracy.

Jeżeli nowa prośba zmienia wcześniej ustalony zakres, sprawa może wymagać decyzji Łukasza.

### Odpowiedź klienta w trwającej rozmowie

Hermes czyta nową odpowiedź razem z wcześniejszymi wiadomościami klienta.

Nie traktuje własnych pytań jako odpowiedzi klienta.

Jeżeli klient uzupełnił brakujące dane, Hermes może przejść do przygotowania finalnej oferty.

### Nowa osoba z firmy, którą już znamy

Hermes sprawdza, czy wiadomość dotyczy tej samej sprawy.

Sama domena firmy nie wystarcza, aby połączyć dwie sprawy. Jeżeli dane się nie zgadzają, Hermes prosi Łukasza o decyzję.

### Faktura lub wiadomość administracyjna

Hermes nie uruchamia rozmowy sprzedażowej. Może jedynie przygotować wewnętrzną informację.

### Newsletter lub spam

Hermes ignoruje wiadomość.

### Nie wiadomo, o co chodzi

Hermes niczego nie wysyła i prosi Łukasza o decyzję.

---

## 7. Kiedy Hermes może wysłać pierwszą wiadomość

Pierwsza wiadomość może zostać wysłana automatycznie tylko wtedy, gdy wszystkie poniższe warunki są spełnione:

1. Wiadomość rzeczywiście dotyczy Orchesta RFQ.
2. Hermes jest pewny swojego rozpoznania.
3. Nie ma zagrożenia bezpieczeństwa.
4. Adres klienta jest poprawny.
5. Hermes może bezpiecznie odpowiedzieć w istniejącej rozmowie albo utworzyć pierwszą wiadomość dla leada z arkusza.
6. Ta sama sprawa nie została już obsłużona.
7. Wiadomość nie zawiera ceny ani finalnej oferty.

Jeżeli choć jeden warunek nie jest spełniony, Hermes nie wysyła wiadomości automatycznie.

---

## 8. Jak ma wyglądać pierwsza wiadomość

Hermes ma pisać jak dobry pracownik, a nie jak formularz.

Najpierw odpowiada na to, o co klient naprawdę zapytał.

Jeżeli klient pyta, jak działa system, Hermes najpierw krótko to wyjaśnia. Nie zaczyna od listy pytań.

Jeżeli klient czegoś jeszcze nie wie, Hermes może zaproponować proste założenie na start. Na przykład:

- zaczynamy od jednej skrzynki,
- CRM może zostać dodany później,
- po sprawdzeniu pierwszych zgłoszeń dopracujemy szczegóły.

Potem pyta, czy taki kierunek jest odpowiedni.

Hermes:

- nie pyta ponownie o podane już informacje,
- nie zamienia braku wiedzy klienta w odpowiedź „nie”,
- zadaje najwyżej 2 pytania,
- zwykle powinien zadać jedno najważniejsze pytanie,
- nie podaje ceny,
- nie obiecuje finalnej oferty,
- nie używa wewnętrznych określeń technicznych,
- pisze po polsku lub po angielsku, zależnie od języka klienta,
- naturalnie odnosi się do bezpiecznego załącznika, jeżeli ma to sens.

Przed wysłaniem treść jest dodatkowo sprawdzana. Jeżeli zawiera niedozwoloną cenę, zbyt wiele pytań albo niebezpieczne sformułowanie, wiadomość nie powinna zostać wysłana.

---

## 9. O co Hermes może dopytać

Do przygotowania finalnej oferty Hermes potrzebuje:

1. nazwy firmy,
2. poprawnego adresu e-mail klienta,
3. liczby skrzynek pocztowych,
4. decyzji, czy oferta ma obejmować połączenie z CRM,
Jeżeli brakuje kilku informacji, Hermes wybiera najwyżej dwie najważniejsze, aby klient nie dostał ankiety.

Hermes nie musi znać przed przygotowaniem oferty:

- dokładnego źródła wszystkich zapytań,
- przykładowych zapytań klienta,
- miesięcznej liczby zapytań,
- imienia i nazwiska, jeżeli ma firmę i e-mail.

Te informacje mogą pomóc, ale nie blokują samej oferty.

---

## 10. Co dzieje się po odpowiedzi klienta

Hermes łączy nowe informacje z wcześniejszą rozmową.

Przykład:

- w pierwszym e-mailu klient podał firmę i liczbę skrzynek,
- w drugim potwierdził CRM.

Hermes powinien potraktować te trzy wiadomości jako jedną sprawę. Nie może pytać ponownie o dane, które już dostał.

Jeżeli nadal czegoś brakuje, wysyła tylko krótkie pytanie o brakującą rzecz.

Jeżeli wszystko jest kompletne, przechodzi do przygotowania finalnej oferty.

---

## 11. Kiedy można przygotować finalną ofertę

Finalna oferta może powstać tylko wtedy, gdy:

- wiadomo, dla jakiej firmy jest oferta,
- adres klienta jest poprawny,
- wiadomo, ile skrzynek ma obejmować wdrożenie,
- klient powiedział „tak” albo „nie” w sprawie CRM,
- nie ma sprzecznych informacji,
- rozmowa i załączniki są bezpieczne,
- Hermes jest wystarczająco pewny, że dobrze rozumie sprawę.

Brak odpowiedzi nie oznacza „nie”.

Jeżeli klient raz napisał, że chce CRM, a później że nie chce, Hermes nie wybiera sam jednej wersji. Prosi o wyjaśnienie albo informuje Łukasza.

---

## 12. Co blokuje finalną ofertę

Hermes nie przygotuje finalnej oferty, gdy:

- nie wiadomo, z kim rozmawia,
- nadawca nie pasuje do wcześniejszej rozmowy,
- załącznik jest niebezpieczny,
- wiadomość próbuje obejść zasady bezpieczeństwa,
- dane są sprzeczne,
- klient oczekuje obsługi przez SMS,
- klient oczekuje, że system samodzielnie wykona techniczną wycenę bez kontroli,
- klient oczekuje automatycznego wysyłania finalnych ofert,
- ceny nie można pobrać z zatwierdzonego cennika.

W takiej sytuacji Hermes nie improwizuje. Nie tworzy oferty na podstawie domysłów.

---

## 13. Co się dzieje, gdy brakuje danych

Jeżeli sprawa jest bezpieczna, ale brakuje danych:

1. Hermes nie tworzy PDF-a.
2. Wybiera najwyżej 2 naprawdę potrzebne pytania.
3. Wysyła je klientowi w tej samej rozmowie.
4. Czeka na odpowiedź.

Jeżeli brak danych łączy się z problemem bezpieczeństwa, Hermes nie wysyła pytań automatycznie. Najpierw informuje Łukasza.

---

## 14. Jak liczona jest cena

Cena pochodzi z jednego zatwierdzonego cennika.

Aktualne kwoty:

- pierwsza skrzynka pocztowa: 7 200 zł netto,
- każda kolejna skrzynka: 3 000 zł netto,
- połączenie z CRM: 3 000 zł netto.

Przykłady:

- 1 skrzynka bez CRM: 7 200 zł netto,
- 2 skrzynki bez CRM: 10 200 zł netto,
- 3 skrzynki z CRM: 16 200 zł netto.

Dodatkowe warunki zapisane w ofercie:

- płatność 100% z góry,
- wdrożenie w 14 dni,
- 14 dni gwarancji,
- możliwość zwrotu całej kwoty zgodnie z zatwierdzonym opisem oferty.

Hermes nie może sam wymyślić rabatu, dopłaty ani innej ceny.

---

## 15. Jak powstaje finalna oferta

Gdy wszystkie dane są gotowe:

1. Hermes nadaje numer oferty.
2. Wstawia dane klienta i firmy.
3. Wstawia uzgodniony zakres.
4. Oblicza cenę z zatwierdzonego cennika.
5. Przygotowuje dokument PDF.
6. Sprawdza, czy PDF zawiera właściwe dane.
7. Sprawdza, czy dokument ma 3 albo 4 strony i nie ma pustych stron.
8. Dołącza PDF do wiadomości w Zoho.
9. Zapisuje wiadomość w wersjach roboczych.
10. Powiadamia Łukasza, gdzie znajduje się oferta.

Hermes nie wysyła tej wiadomości do klienta.

Łukasz otwiera wersję roboczą w Zoho, sprawdza ją i sam podejmuje decyzję o wysłaniu.

---

## 16. Co jest sprawdzane w PDF-ie

Dokument musi zawierać:

- dane klienta,
- nazwę firmy,
- numer i wersję oferty,
- zakres skrzynek pocztowych,
- cenę,
- informację o 14-dniowej gwarancji,
- informację o Telegramie,
- podpis Łukasza.

Dokument nie może zawierać niezatwierdzonych elementów, takich jak:

- SMS,
- nieuzgodniony pilotaż,
- niezatwierdzone warunki prawne,
- niezatwierdzone przykłady realizacji,
- przypadkowe puste pola albo fragmenty szablonu.

Jeżeli kontrola dokumentu nie przejdzie, Hermes nie tworzy gotowej wersji roboczej oferty.

---

## 17. Co dzieje się w Zoho

Po przygotowaniu poprawnego PDF-a Hermes:

1. przesyła plik do Zoho,
2. sprawdza, czy Zoho przyjęło właściwy plik,
3. sprawdza nazwę i rozmiar załącznika,
4. przygotowuje odpowiedź w rozmowie z klientem,
5. zapisuje ją w folderze wersji roboczych.

Jeżeli plik nie został poprawnie dołączony, Hermes nie oznacza oferty jako gotowej.

Po sukcesie robocza kopia PDF-a na serwerze może zostać usunięta, ponieważ właściwy załącznik znajduje się już w Zoho.

---

## 18. Jak system zapobiega duplikatom

Hermes sprawdza duplikaty na kilku poziomach.

### Ta sama wiadomość

Jeżeli konkretna wiadomość została już obsłużona, Hermes nie traktuje jej ponownie jako nowej.

### Ta sama odpowiedź

Jeżeli Hermes wysłał już wiadomość wstępną, zapisuje jej numer. Nie wysyła jej drugi raz.

### Ta sama oferta

Jeżeli finalna oferta została już przygotowana, Hermes nie tworzy drugiej takiej samej oferty.

### Wersja robocza już istnieje w Zoho

Jeżeli w danej rozmowie jest już wersja robocza, Hermes nie tworzy kolejnej. Informuje o tym Łukasza.

### Ten sam lead pojawił się z dwóch miejsc

Jeżeli ta sama osoba najpierw napisała e-mail, a później pojawiła się w Google Sheets, Hermes łączy te informacje w jedną sprawę.

Drugi kanał nie wysyła kolejnej pierwszej wiadomości.

---

## 19. Co dzieje się przy błędzie

### Chwilowy problem z Zoho

Hermes próbuje ponownie kilka razy, zwiększając przerwę między próbami.

### Dostęp wygasł

Hermes próbuje odnowić dostęp i jeszcze raz wykonać operację.

### Zoho jednoznacznie odrzuciło operację

Hermes nie powtarza jej bez końca. Zapisuje błąd i informuje Łukasza.

### Nie wiadomo, czy wiadomość została wysłana

To szczególnie ważny przypadek.

Hermes nie wysyła jej drugi raz „na wszelki wypadek”, ponieważ klient mógłby dostać duplikat. Zatrzymuje sprawę i prosi Łukasza o sprawdzenie.

### Nie udało się utworzyć PDF-a albo wersji roboczej

Hermes nie oznacza oferty jako gotowej. Informuje Łukasza, na którym etapie wystąpił problem.

---

## 20. Jakie powiadomienia dostaje Łukasz

Hermes przekazuje krótkie informacje przez Telegram i e-mail wewnętrzny. Przy ważnej aktywności oba kanały są uruchamiane w tym samym przebiegu, ale nie dokładnie w tej samej chwili: e-mail jest wysyłany przed zakończeniem pracy skryptu, a Telegram dostaje końcowy wynik zadania. Awaria jednego kanału nie jest dowodem, że drugi również zawiódł.

Powiadomienie ma mówić prostym językiem:

- kto napisał,
- czy osoba jest znana,
- czego dotyczy wiadomość,
- co Hermes zrobił,
- czego nie zrobił,
- dlaczego sprawa wymaga uwagi.

Przykłady:

- „Napisał nowy klient. Wysłałem mu krótką odpowiedź bez ceny i czekam na informację o liczbie skrzynek.”
- „Klient uzupełnił wszystkie dane. Przygotowałem ofertę z PDF-em w wersjach roboczych Zoho. Nic nie zostało wysłane.”
- „Wiadomość zawiera podejrzany załącznik. Nie odpowiedziałem i proszę o ręczne sprawdzenie.”
- „Ten sam lead był już obsłużony z e-maila. Nie wysłałem drugiej wiadomości z Google Sheets.”

Jeżeli nie wydarzyło się nic ważnego, Hermes milczy.

---

## 21. Co Hermes zapisuje w Google Sheets

W arkuszu zapisuje:

- bieżący stan sprawy,
- rodzaj wiadomości,
- krótką notatkę,
- czas ostatniego sprawdzenia,
- informację pozwalającą rozpoznać zmianę treści,
- numer przygotowanej wersji roboczej, jeśli istnieje,
- numer wysłanej wiadomości wstępnej, jeśli została wysłana.

Widoczne stany to:

- nowy,
- w analizie,
- oczekuje na klienta,
- oferta gotowa,
- wymaga sprawdzenia.

---

## 22. Wszystkie najważniejsze sytuacje „jeżeli A, to B”

### A. Nowy klient pyta o Orchesta RFQ, ale nie podał zakresu

Hermes odpowiada na pytanie klienta, proponuje prosty kierunek i pyta o brakujące dane. Nie podaje ceny.

### B. W arkuszu pojawia się lead z kampanii

Hermes analizuje dane z wiersza. Jeżeli może bezpiecznie napisać, korzysta wyłącznie z danych zapisanych w arkuszu.

### C. W Google Sheets pojawił się bezpieczny lead RFQ

Hermes wysyła pierwszą wiadomość przez Zoho, zapisuje jej numer w arkuszu i czeka na klienta.

### D. Ten sam lead był już wcześniej w skrzynce

Hermes nie wysyła drugiej pierwszej wiadomości. Łączy sprawę i aktualizuje stan w arkuszu.

### F. Klient odpowiada tylko na część pytań

Hermes zachowuje podane dane i pyta wyłącznie o to, czego nadal brakuje.

### G. Klient podał wszystko

Hermes przygotowuje PDF, zapisuje finalną ofertę w wersjach roboczych Zoho i informuje Łukasza. Niczego nie wysyła klientowi.

### H. Klient nie chce CRM

To poprawna decyzja. Hermes liczy ofertę bez dopłaty za CRM.

### I. Klient nie odpowiedział w sprawie CRM

Hermes nie uznaje tego za „nie”. Pyta lub proponuje CRM jako późniejszą opcję.

### J. Klient podał sprzeczne informacje

Hermes nie wybiera sam jednej wersji. Prosi o wyjaśnienie lub informuje Łukasza.

### K. Wiadomość dotyczy tylko szkolenia

Hermes nie wysyła oferty RFQ. Przekazuje temat Łukaszowi.

### L. Wiadomość wygląda na spam lub newsletter

Hermes ją ignoruje.

### N. Wiadomość dotyczy faktury

Hermes nie uruchamia rozmowy sprzedażowej. Może przygotować wewnętrzną informację.

### O. Wiadomość zawiera próbę wyłudzenia danych

Hermes niczego nie wysyła i ostrzega Łukasza.

### P. Załącznik jest niebezpieczny

Hermes nie tworzy oferty i prosi o ręczne sprawdzenie.

### Q. Załącznik jest bezpieczny, ale nieczytelny

Hermes nie udaje, że go przeczytał. Odpowiada tylko wtedy, gdy sama treść wiadomości wystarcza.

### R. W Zoho jest już wersja robocza

Hermes nie tworzy drugiej.

### S. Nie wiadomo, czy pierwsza wiadomość została wysłana

Hermes nie ponawia jej automatycznie. Prosi Łukasza o sprawdzenie.

### T. PDF nie przeszedł kontroli

Hermes nie zapisuje finalnej oferty jako gotowej.

### U. Wszystko się udało, a system uruchomił się ponownie

Hermes rozpoznaje zakończoną sprawę i nie powtarza wysyłki ani przygotowania oferty.

### V. Handlowiec odpowiada klientowi ręcznie

Hermes sprawdza folder „Wysłane”. Wiadomość bez trwałego artefaktu Hermesa oznacza `human_takeover`, zatrzymuje automatykę i wymaga jawnego wznowienia.

### W. Ta sama osoba zaczyna nowy wątek

Sam adres e-mail nie łączy spraw. Hermes najpierw używa twardych identyfikatorów, potem bezpiecznego scoringu, a nierozstrzygnięte przypadki kieruje do kontrolowanego routingu lub ręcznej oceny.

### X. Rozmowa jest bardzo długa

Hermes zatrzymuje automatykę po 7 wiadomościach w rozmowie albo po 7 dniach od rozpoczęcia aktywnej rozmowy. Licznik jest zerowany dopiero przez jawne wznowienie.

---

## 23. Zasady, które obecnie obowiązują

1. Wiadomości wstępne mogą być wysyłane automatycznie, ale tylko bez ceny i po przejściu obecnych kontroli.
2. Finalna oferta zawsze czeka na Łukasza w wersjach roboczych Zoho.
3. Finalna oferta zawsze ma PDF.
4. Hermes nie wysyła finalnej oferty sam.
5. Hermes nie zgaduje brakujących decyzji klienta.
6. Hermes nie ponawia niepewnej wysyłki, jeżeli grozi to duplikatem.
7. Hermes łączy tę samą sprawę między e-mailem i Google Sheets.
8. Wiadomość w Zoho jest zwykłym e-mailem od rzeczywistego nadawcy, a drugi obsługiwany kanał wejścia to Google Sheets.
9. Ważna aktywność jest zgłaszana przez Telegram i osobny e-mail wewnętrzny.

---

## 24. Co powinno zostać usprawnione

### Priorytet 1 — mocniejsza kontrola załączników

Obecne filtry powinny zostać uzupełnione o skan antywirusowy pobranych plików. Do czasu wdrożenia nie należy opisywać załącznika jako całkowicie bezpiecznego.

### Priorytet 2 — cenniki dla większych klientów

Obecny mechanizm korzysta z jednego zatwierdzonego cennika i prostych reguł. Duży klient z wieloma cennikami potrzebuje osobnych, wersjonowanych zasad, dat obowiązywania, kolejności reguł, kontroli konfliktów i testowych przykładów. Bez tego Hermes nie powinien sam wybierać ceny.

## 25. Decyzje biznesowe do ustalenia

1. Czy przed szerszym wdrożeniem wymagamy skanera antywirusowego dla wszystkich pobieranych załączników?
2. Czy złożone cenniki mają być dobierane według klienta, rodzaju usługi i daty obowiązywania, czy zawsze zatwierdzane ręcznie?
