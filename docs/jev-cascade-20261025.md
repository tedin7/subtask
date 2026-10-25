# Jev come filtro e in altre fasi Subtask

## Esito delle prove dal vivo

La cascata conserva10/10 giudizi corretti, come il normale, usando il normale
in1 caso anziché10. Jev da solo dà9 giudizi corretti e un'astensione eccessiva.
Non ci sono falsi `supported` sui casi insufficienti di questo campione.

| Fase | Jev corretto | Normale corretto | Cascata corretta | Ritorni al normale |
|---|---:|---:|---:|---:|
| Piano dei test |4/4|4/4|4/4|0|
| Percorso presente nella consegna |1/2|2/2|2/2|1|
| Decisione dopo un errore |4/4|4/4|4/4|0|

La cascata impiega12,864 secondi complessivi misurati sul percorso completo;
le dieci inferenze normali di confronto sommano134,356 secondi, includendo
l'avvio CLI. Le mediane sono0,300 e10,153 secondi rispettivamente. È un
vantaggio operativo osservato in queste condizioni, senza vantaggio di
qualità sul normale e senza prova di minor costo fatturato. I modelli sono
Jev1.14.0 e `opencode-go/deepseek-v4.1-flash#max`.

Il ritorno reale al normale avviene sul codice precedente di
`auto_review.details`, che non contiene il nuovo percorso richiesto. Jev
risponde `unproven`; il normale riconosce `contradicted`. La cascata usa
quest'ultimo e chiude correttamente il singolo criterio. Non è un replay.

Il punto più promettente è la copertura del piano dei test: quattro casi di
questa campagna, inclusi i due congelati prima della campagna precedente,
sono tutti corretti. Sono pochi esempi di requisiti espliciti: non dimostrano
che Jev sappia progettare test o scoprire bug meglio del normale.

I casi di recupero mostrano come valutare una proposta dopo401 o dopo una
risposta invalida. Il401 e la distribuzione0,99 erano già noti al codice:
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

[Pacchetti e risultati pubblicabili](live-evidence/jev-cascade-20261025.json).
L'audit offline verifica10 worktree effettivi, strumenti negati, MCP ereditati
disabilitati, richieste identiche per i due modelli e20 esiti riproducibili dai
raw completi. Eventi del normale:10 `step_start`,10 `text`,1 `step_finish`,
nessun evento strumento. L'audit non invia richieste API.

Il contratto privato, i pacchetti congelati, le ricevute di prenotazione e i
corpi grezzi sono in `/root/.local/state/subtask-jev-cascade-20261025/`.
Le inferenze normali hanno worktree Git distinti in
`/root/wt/jev-cascade-evaluation-20261025/private/normal/`.
