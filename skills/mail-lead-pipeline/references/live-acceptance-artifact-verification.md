# Live acceptance: wymagane artefakty i pułapki

## Definicja ukończenia

Nie uznawaj live acceptance za zakończone na podstawie samego kodu wyjścia wrappera, briefingu ani licznika `sent=1`. Zbierz niezależne dowody dla każdego oczekiwanego skutku:

1. **Inbound:** konkretna wiadomość jest widoczna w Zoho Inbox; potwierdź `message_id`, odbiorcę i testowy znacznik w treści.
2. **Pre-offer:** Zoho Sent zawiera wiadomość do wyłącznie kontrolowanego odbiorcy; potwierdź zewnętrzny message ID, a w Sheets także `Hermes sent id` i status oczekiwania na klienta.
3. **Final offer:** Zoho Drafts zawiera nowy draft; potwierdź trwały `draft_id`, właściwy adres klienta oraz zdalnie widoczny PDF. Pobierz PDF przez read API i sprawdź magic bytes `%PDF-`, SHA-256, 3–4 strony, numer oferty, firmę i cenę. Sama lokalna ścieżka PDF lub `draft_id` bez załącznika nie wystarcza.
4. **Brak wysyłki finalnej oferty:** nie ma odpowiadającej jej wiadomości w Sent.
5. **Powiadomienia:** potwierdź rzeczywisty transport na hard-allowlisted internal email i Telegram; nie utożsamiaj tekstu briefingu z dostarczeniem.
6. **Idempotencja:** drugi przebieg i przebieg po restarcie nie tworzą kolejnego send/draft/notification.
7. **Źródło:** sprawdź projekcję statusu w Sheets lub trwały stan wiadomości/dealu.

Jeśli użytkownik oczekuje normalnej pracy w skrzynce, test musi pozostawić rzeczywisty, oznaczony artefakt w Inbox/Drafts/Sent. Fake transport i lokalny manifest są tylko testami pomocniczymi, nie dowodem akceptacyjnym.

## Kontrolowane tożsamości

- Nigdy nie wysyłaj do realnych klientów.
- Przed insertem lub wysyłką sprawdź znormalizowaną tożsamość w shared registry.
- Gmail normalizuje local-part przez usunięcie kropek i `+tag`. Kolejne plus-addressy tego samego Gmaila są jednym dealem i mogą prawidłowo wywołać `conflicting_company` przy różnych firmach.
- Dla wielu niezależnych scenariuszy użyj różnych kontrolowanych bazowych skrzynek albo jednej spójnej firmy/dealu. Nie osłabiaj deduplikacji tylko po to, by test przeszedł.
- Wysłanie z Zoho na własny plus-alias może potwierdzić Sent, ale nie musi stworzyć nowego artefaktu w Inbox. Pozytywny test Inbox potwierdzaj osobno przez rzeczywistą wiadomość dostarczoną do `rfq-mailbox@example.invalid`.

## Ekstrakcja faktów z naturalnego języka

- Fakty wymagające deklaracji wyciągaj tylko ze zdań oznajmujących, nie z pytań, przykładów ani cytowanej historii.
- Decyzje dotyczące różnych pól muszą być rozpatrywane lokalnie. Negacja CRM w zdaniu `Nie potrzebujemy CRM` nie może zmienić innych, niezależnych danych zakresu.
- Dla pól boolowskich, takich jak CRM, najpierw wydziel zdanie lub wiersz zawierający słowo kluczowe, potem oceniaj negację i zgodę.
- Dodawaj test regresyjny z dokładną naturalną frazą ujawnioną live, zachowując test przeciwnej decyzji.

## Bezpieczny przebieg

1. Zatrzymaj oba kanoniczne timery systemd, potwierdź brak legacy cronów/procesów i zapisz bazowy stan folderów.
2. Uruchom preflight zależności final-offer/PDF przed pierwszym inboundem.
3. Użyj unikalnego test ID i kontrolowanego odbiorcy.
   Ręczny przebieg pollera przypnij równocześnie do dokładnego Zoho
   `message_id` i dokładnego adresu nadawcy przez
   `HERMES_CONTROLLED_SOURCE_MESSAGE_ID` oraz `HERMES_CONTROLLED_SENDER`.
   Brak jednego z parametrów albo brak dokładnego dopasowania ma zatrzymać test;
   inne wiadomości muszą pozostać nieprzetworzone.
4. Najpierw pozytywny happy-path, potem osobny fail-closed.
5. Po każdej wysyłce polluj po test ID w treści, nie tylko po powtarzalnym temacie.
6. Używaj nowego unikalnego katalogu output dla smoke testu. Nie łącz renderowania z `rm -rf`; sprzątanie jest osobnym, jawnie zatwierdzonym krokiem.
7. Uruchom timery dopiero po pełnym dowodzie happy-path, idempotencji i braku finalnej oferty w Sent; potem sprawdź kolejny tick, heartbeat i raport monitora.
