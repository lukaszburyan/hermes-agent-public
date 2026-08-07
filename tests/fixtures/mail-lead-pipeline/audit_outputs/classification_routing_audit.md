# Audyt klasyfikacji/routingu Orchesta RFQ — syntetyczny offline

Przypadki: 144. Tryb: fixture/mock/offline, bez Zoho/OAuth/CRM/crona.
Accuracy klasyfikacji: 0.979.
RFQ-opportunity binary precision: 0.985; recall: 1.000; TP=67 FP=1 FN=0 TN=76.

## Rozkład oczekiwanych klas
- existing_client_request: 10 expected; got=10
- existing_thread_reply: 10 expected; got=10
- human_review_only: 12 expected; got=13
- new_general_business_inquiry: 10 expected; got=8
- new_quote_request: 27 expected; got=28
- newsletter_automated_spam: 10 expected; got=10
- quote_draft_ready: 12 expected; got=12
- related_non_rfq_topic: 10 expected; got=10
- same_domain_new_person: 8 expected; got=8
- unknown_review_needed: 15 expected; got=16
- vendor_admin_billing: 10 expected; got=10
- weak_fit_review_only: 10 expected; got=9

## Precision/recall per class
- existing_client_request: support=10 TP=10 FP=0 FN=0 precision=1.000 recall=1.000 f1=1.000
- existing_thread_reply: support=10 TP=10 FP=0 FN=0 precision=1.000 recall=1.000 f1=1.000
- human_review_only: support=12 TP=12 FP=1 FN=0 precision=0.923 recall=1.000 f1=0.960
- new_general_business_inquiry: support=10 TP=8 FP=0 FN=2 precision=1.000 recall=0.800 f1=0.889
- new_quote_request: support=27 TP=27 FP=1 FN=0 precision=0.964 recall=1.000 f1=0.982
- newsletter_automated_spam: support=10 TP=10 FP=0 FN=0 precision=1.000 recall=1.000 f1=1.000
- quote_draft_ready: support=12 TP=12 FP=0 FN=0 precision=1.000 recall=1.000 f1=1.000
- related_non_rfq_topic: support=10 TP=10 FP=0 FN=0 precision=1.000 recall=1.000 f1=1.000
- same_domain_new_person: support=8 TP=8 FP=0 FN=0 precision=1.000 recall=1.000 f1=1.000
- unknown_review_needed: support=15 TP=15 FP=1 FN=0 precision=0.938 recall=1.000 f1=0.968
- vendor_admin_billing: support=10 TP=10 FP=0 FN=0 precision=1.000 recall=1.000 f1=1.000
- weak_fit_review_only: support=10 TP=9 FP=0 FN=1 precision=1.000 recall=0.900 f1=0.947

## Błędne decyzje klasyfikacji
- C04_general_inbound: expected=new_general_business_inquiry got=new_quote_request; subject='Inbound'; body='Mamy leady inbound i chcemy sprawdzić, czy da się to zautomatyzować.'
- C07_general_process: expected=new_general_business_inquiry got=unknown_review_needed; subject='Proces'; body='Potrzebujemy mapowania procesu obsługi klientów i możliwej automatyzacji.'
- E07_weak_fit: expected=weak_fit_review_only got=human_review_only; subject='Automatyzacja wycen'; body='Placówka medyczna: dane medyczne w załącznikach i odpowiedzi do pacjentów.'

## Błędne decyzje routingu/draft/Telegram
- C07_general_process: class new_general_business_inquiry→unknown_review_needed; draft True→False; kind first_response→none; telegram False→True; wake True→True

## Przypadki graniczne objęte zestawem
- RFQ z fakturą jako załącznikiem-kontekstem, aby invoice nie nadpisywał intencji RFQ.
- Temat `Re:` bez nagłówków wątku — nie powinien sam tworzyć existing_thread_reply.
- Odpowiedź w wątku z inną domeną nadawcy — eskalacja unknown_review_needed.
- Valid/invalid website form: valid sparse form → high-confidence RFQ; invalid → review, bez draftu do technicznej skrzynki.
- Safe PDF/image vs exe/xlsm/short-link/login-link/prompt-injection.
- Same-domain new person i merge/separate deal zależnie od słów kluczowych.
- Weak-fit: regulowane branże, niska powtarzalność, oczekiwanie finalnej wysyłki bez człowieka.
- Newsletter/autoresponder/bounce na podstawie sender/headers/subject.
