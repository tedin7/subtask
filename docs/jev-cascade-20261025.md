# Jev come filtro e in altre fasi Subtask

## Esito delle prove dal vivo

La cascata conserva 10/10 giudizi corretti, come il normale, usando il normale
in 1 caso anziché 10. Jev da solo dà 9 giudizi corretti e un'astensione eccessiva.
Non ci sono falsi `supported` sui casi insufficienti di questo campione,
né per la cascata né per Jev da solo. Le ricevute separano le due misure.

| Fase | Jev corretto | Normale corretto | Cascata corretta | Ritorni al normale |
|---|---:|---:|---:|---:|
| Piano dei test |4/4|4/4|4/4|0|
| Percorso presente nella consegna |1/2|2/2|2/2|1|
| Decisione dopo un errore |4/4|4/4|4/4|0|

La cascata impiega 12,864 secondi complessivi misurati sul percorso delle chiamate del banco;
le dieci inferenze normali di confronto sommano 134,356 secondi, includendo
l'avvio CLI. Le mediane sono 0,300 e 10,153 secondi rispettivamente. È un
vantaggio operativo osservato in queste condizioni, senza vantaggio di
qualità sul normale e senza prova di minor costo fatturato. Preparare i
criteri, eseguire i test, scrivere il codice e coordinare il lavoro restano
fuori da questi tempi: non sono la durata completa di un incarico. I modelli sono
Jev 1.14.0 e `opencode-go/deepseek-v4.1-flash#max`.

Il ritorno reale al normale avviene sul codice precedente di
`auto_review.details`, che non contiene il nuovo percorso richiesto. Jev
risponde `unproven`; il normale riconosce `contradicted`. La cascata usa
quest'ultimo e chiude correttamente il singolo criterio. Non è un replay.

Il punto più promettente è la copertura del piano dei test: quattro casi di
questa campagna, inclusi i due congelati prima della campagna precedente,
sono tutti corretti. Sono pochi esempi di requisiti espliciti: non dimostrano
che Jev sappia progettare test o scoprire bug meglio del normale.

I casi di recupero mostrano come valutare una proposta dopo 401 o dopo una
risposta invalida. Il 401 e la distribuzione 0,99 erano già noti al codice:
il contributo misurato è il giudizio sulla proposta, non la scoperta di quei
segnali. Per queste condizioni deterministiche il normale codice resta
preferibile a una nuova chiamata a un modello.

La conferma negativa precedente sul mandato rimane negativa. Questo nuovo
campione è un seguito esplorativo autorizzato, non una sostituzione dei casi
sfavorevoli. Il successo su criteri circoscritti non prova lo scope dell'intera
consegna automatica.

## Contratto della prova

Questa è una seconda campagna esplorativa. Non sostituisce la conferma negativa
del mandato nella [prima campagna](jev-phase-eval-20261025.md).

Prima di inviare nuove richieste sono stati congelati dieci casi e le verità
esterne, separate dai pacchetti inviati ai modelli. Due piani dei test erano
già congelati prima della prima campagna e non erano ancora stati inviati.
Gli altri casi riguardano la copertura degli eventi strumento, la presenza
del percorso richiesto nella consegna, e la decisione dopo errori di
autenticazione o una risposta Jev invalida. Derivano da codice ed eventi reali,
con proposte conformi e omissioni controllate. Non sono dieci nuovi lavori
completi eseguiti dagli agenti.

La cascata dal vivo chiama prima Jev. Una risposta interamente valida e un
giudizio C1 `supported` o `contradicted` completano quel controllo consultivo.
Un giudizio `unproven`, un formato invalido, un errore o un esito ignoto
richiedono il modello normale. Jev non viene ritentato. Il modello normale
riceve lo stesso pacchetto, senza strumenti o dati privati delle risposte.

Un'inferenza normale viene eseguita anche dopo i risultati Jev decisivi, ma
solo per il confronto. Quella chiamata non appartiene alla cascata. Quando
serve il ritorno al normale, la sua inferenza vale anche come baseline:
non sono due campagne temporali indipendenti. L'ordine è sempre Jev prima;
il percorso normale include l'avvio CLI. I tempi non misurano la sola
inferenza del modello e non provano un vantaggio economico.

La misura primaria è la correttezza del singolo criterio C1, per fase e nel
totale. La cascata deve mantenere la correttezza del normale, senza falsi
giudizi `supported` sui casi insufficienti. Il costo fatturato resta ignoto.
Dieci invii Jev hanno 20 USD di accantonamento; preparazione e revisione
automatica possono usarne altri quattro. Il massimo autorizzato è 24 USD.
Il tetto condiviso è stato allineato a 720 USD su autorizzazione esplicita.

## Applicazione alle consegne automatiche

Il filtro conserva `provider_choice` e la conclusione dopo i vincoli locali.
Un veto del wrapper richiede una revisione normale e non viene attribuito
come scoperta al modello. Il filtro non può accettare, integrare, rilasciare
o eseguire istruzioni contenute nella consegna.

La revisione automatica non possiede sempre la richiesta originale verificata:
in quel caso il suo scope rimane `unproven`. Il filtro sull'intera consegna
deve quindi richiedere il normale. Il successo su C1 nella campagna non
permette di saltare quel limite sullo scope.

Il risultato e i dettagli della consegna indicano quando serve la revisione
normale; non lanciano automaticamente un secondo agente. La cascata viene
eseguita esplicitamente dal banco di prova. Non cambia la politica automatica
Jev scelta dall'operatore e non amplia le autorizzazioni del worker.

## Ricevute

Il filtro usato nelle chiamate dal vivo è 6412f7c, derivato dall'autore 6b67b91;
l'orchestrazione allora nel worktree è stata pubblicata nel candidato 7df73fe.
Il candidato finale del codice è a1af3a8. Un controllo offline sui risultati
grezzi riproduce tutte le dieci scelte di instradamento con i controlli più
stretti. Non è una ripetizione dal vivo del candidato finale e non sono state
ritentate le chiamate pagate.

La revisione indipendente iniziale 76ff254 ha confermato i numeri e ha trovato
una distribuzione incompleta accettabile per errore, slot senza prenotazione,
varianti di metadati strumento e veti non riconosciuti, una dicitura impropria
senza controllo e una causa di blocco nascosta dallo scope. I controesempi
sono stati trasformati in test: 17 fallivano prima, ora passano. La suite
mirata finale conta 193 test verdi; la verifica completa è eseguita in un
ambiente Python dedicato al worktree, perché il precedente ambiente editable
condiviso caricava la skill dal checkout principale nei sottoprocessi.

Gate finale su a1af3a8: 664 test passati, 4 live esclusi, 367,75 secondi.
Comando: `env -u SUBTASK_MONTHLY_CAP_USD -u SUBTASK_MAX_RUNNING build-a-turno
.venv/bin/python -m pytest -q` nel worktree dedicato. Ruff 0.17.0, formato e
schema dei 17 strumenti MCP sono verificati. Il payload wheel coincide con
tutti i 59 file sorgente distribuiti.

Il revisore indipendente ha confermato 193 test passati nella propria copia
pulita di a1af3a8 e zero difetti gravi aperti; rapporto 59c7fb1, conservato
separatamente dal codice. Il parere automatico Jev sul suo report resta
consultivo: C1 non provato, C2 contraddetto, C3 supportato, scope non provato.
La ricevuta runner verifica il ramo del report 59c7fb1, basato sul precedente
7df73fe; i 193 test sul codice finale sono un comando distinto, osservato nel
log dell'esecutore. Il coordinatore ha controllato quel comando e ha eseguito
separatamente il gate completo sul codice finale. Il parere Jev non è stato
trasformato in un pass e non viene usato come permesso di integrare.

La revisione automatica del preparatore ha consumato altri 2 USD di
accantonamento ed è rimasta `unknown/ValueError`. Il corpo grezzo non è
conservato da quel percorso: non se ne attribuisce la causa al modello.
La verifica del suo codice resta distinta dal parere automatico Jev.
La revisione automatica finale del revisore usa altri 2 USD di accantonamento:
totale Jev di questo incarico 24 USD, entro il limite autorizzato. Le fatture
restano ignote; preparazione, normale e coordinamento non sono un costo zero
dimostrato e non si includono in un confronto economico incompleto.

## Limiti aperti non bloccanti

- [ ] `subtask/jev_filter.py:136-137`: il filtro salta i sottoalberi di scelta e
  probabilità; una struttura estranea al formato dei produttori attuali può
  nascondervi metadati strumento. Il filtro non è una barriera di sicurezza.
- [ ] `subtask/jev_filter.py:518-524`: alcuni ritorni anticipati richiedono il
  normale e conservano `reason`, ma non includono il campo `blocking_reasons`.
- [ ] `subtask/core.py:854-858`: una ripresa manuale può lasciare un vecchio
  hint pendente. Nel lavoro corrente il coordinatore ha ritirato soltanto il
  proprio hint obsoleto, registrando l'evento; il comportamento generale non
  viene modificato in questo incarico.

[Pacchetti e risultati pubblicabili](live-evidence/jev-cascade-20261025.json).
L'audit offline verifica 10 worktree effettivi, strumenti negati, MCP ereditati
disabilitati, richieste identiche per i due modelli e 20 esiti riproducibili dai
raw completi. Eventi del normale: 10 `step_start`, 10 `text`, 1 `step_finish`,
nessun evento strumento. L'audit non invia richieste API.

Il contratto privato, i pacchetti congelati, le ricevute di prenotazione e i
corpi grezzi sono in `/root/.local/state/subtask-jev-cascade-20261025/`.
Le inferenze normali hanno worktree Git distinti in
`/root/wt/jev-cascade-evaluation-20261025/private/normal/`.
