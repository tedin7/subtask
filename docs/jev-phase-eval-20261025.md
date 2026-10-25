# Jev nelle fasi Subtask: confronto controllato, 25 ottobre 2026

## Esito delle dodici prove

**Non è emerso un vantaggio di qualità confermato rispetto al modello normale.**
Jev dà nove risposte corrette, due astensioni eccessive e una risposta rifiutata
per formato; DeepSeek via OpenCode dà dodici risposte corrette. Fra le nove
risposte corrette di Jev è inclusa un'astensione giustificata sul controllo
senza prove funzionali: il compito è classificare la sufficienza dell'evidenza,
non indovinare se la patch sia segretamente corretta.

Jev risponde in mediana in 0,222 secondi, OpenCode in 10,428 secondi. La qualità
non è pari sull'intero campione: questi tempi descrivono i percorsi misurati,
non costituiscono una vittoria complessiva. Il confronto usa un solo modello
normale, DeepSeek Flash con variante `max`, tramite CLI e avvio di sessione.

| Fase iniziale, due casi ciascuna | Jev corretto | Normale corretto |
|---|---:|---:|
| Copertura del mandato | 2/2 | 2/2 |
| Piano dei test | 2/2 | 2/2 |
| Patch con verifica funzionale visibile | 2/2 | 2/2 |
| Copertura della verifica eseguita | 1/2 | 2/2 |

La selezione fissata prima delle chiamate sceglie il mandato fra le fasi con
qualità pari e latenza inferiore, usando l'ordine delle fasi per lo spareggio.
La conferma su due testi nuovi **non replica la parità**: Jev 1/2, normale2/2.
La copertura del mandato è quindi3/4 contro4/4. Non si sostituisce questa
conferma sfavorevole con quella di un'altra fase nella stessa analisi.

## Domanda e metodo

Le venti patch precedenti hanno prodotto 17 astensioni e tre esiti ignoti.
Questo dimostra l'assenza di discriminazione osservata in quel percorso,
non distingue da solo carenza di dati, formulazione del criterio e capacità
del modello. L'input includeva richiesta, diff e report ma soltanto uno smoke.

Questa prova confronta Jev con `opencode-go/deepseek-v4.1-flash#max` su quattro
fasi: copertura del mandato, piano dei test, lettura della patch e lettura della
verifica. Ogni modello riceve gli stessi `state` e `questions`; i protocolli
nativi dei provider restano differenti. Il normale non usa strumenti, file o
web e ogni richiesta apre una sessione nuova. Un uso di strumenti invalida
la prova. La configurazione privata usa le
[permissions di OpenCode v2](https://opencode.ai/v2/docs/permissions), senza
modificare le impostazioni globali. Il primo controllo CLI senza strumenti è
riuscito nel worktree dedicato.

Otto casi iniziali coprono quattro fasi, con esempi conformi e insufficienti.
La revisione indipendente ha suggerito un confronto a tre condizioni sulla
stessa patch valida: criterio concreto con verifica funzionale (`n05`), criterio
generico con la stessa verifica (`n09`), stesso criterio concreto con soltanto
lo smoke (`n10`). Questa modifica è stata congelata nei protocolli privati
v2/v3 prima della prima chiamata Jev. Il contratto iniziale che parlava di
contesto della funzione è stato riconciliato dopo la revisione, senza
retrodatare il documento. Per i due confronti sono stati verificati identità
dei dati salvo il criterio, oppure identità del criterio salvo la verifica.

Le vecchie ricevute sul criterio generico restano una baseline osservativa:
avevano tre criteri e domande accessorie differenti. Due ulteriori inferenze
normali riusano quei packet originali e non aggiungono invii Jev. Su entrambi,
anche il normale sceglie `unproven` per C2; tutte le otto risposte per packet
passano la validazione. L'astensione precedente non è dunque una prova
sufficiente di incapacità specifica di Jev.

Prima del primo nuovo invio Jev sono stati congelati anche due esempi nuovi
per ciascuna delle quattro fasi. Solo la coppia della fase selezionata viene
usata: massimo dodici richieste Jev complessive. I file privati registrano hash
dei packet, verità esterna e provenienza separati. I nomi trasmessi sono opachi.
I casi derivano dalle richieste e dai commit storici Subtask, con omissioni
controllate nei mandati e nei piani. Non sono dodici nuove consegne di codice.
Le celle con verifica funzionale mostrano deliberatamente test ed esito:
misurano lettura delle prove, non scoperta cieca di difetti. Le verifiche erano
già state eseguite dal coordinatore sul commit; il report del worker che dice
di non averle eseguite resta vero e non annulla il successivo controllo
indipendente. I vecchi marker di replay sono identificativi opachi, uguali
nelle tre condizioni sulla patch valida, non etichette inviate al modello.

## Misure e selezione fissate prima delle chiamate

La misura primaria è la correttezza del criterio C1: corretto, errato,
astensione o risposta non validabile. I falsi via libera restano separati.
Le scelte native e gli eventuali filtri del wrapper non sono la stessa misura.
Un risultato ignoto non viene ritentato o sostituito.

Una fase è candidata se Jev risponde correttamente sui due casi iniziali,
senza falsi via libera e senza qualità inferiore al normale. Si preferisce
un vantaggio di qualità; in sua assenza si misura il tempo operativo a parità
di qualità. Gli spareggi preferiscono mandato, piano, patch, verifica, in
quest'ordine. Se nessuna fase è candidata, la coppia della verifica è soltanto
diagnostica e il rapporto deve dire che non è emerso un vantaggio.

Il tempo del normale include avvio CLI e configurazione; quello di Jev è una
richiesta HTTP. Questo confronta il percorso operativo misurato, non la latenza
intrinseca dei modelli né qualsiasi altro modello normale. Due conferme non
dimostrano superiorità generale. Il costo fatturato resta sconosciuto e non
si può dichiarare un vantaggio economico.

## Cosa spiega le astensioni

Sul medesimo codice valido e sul medesimo criterio concreto, Jev e normale
rispondono entrambi `supported` quando ricevono la verifica funzionale, ed
entrambi `unproven` con sola verifica di sintassi. In questa coppia controllata
la differenza è il contenuto della verifica. I test fornivano già l'evidenza:
non è Jev ad aver scoperto un nuovo errore.

Con i dati funzionali fissi, il criterio generico produce ancora `supported`
come scelta C1 grezza di entrambi i modelli. La risposta Jev di questa cella
non è però utilizzabile integralmente: le probabilità di `C1_evidence` sommano
a0,99 (`0 + 0,11 + 0,01 + 0,80 + 0,07`), anziché1. Il massimo è unico, quindi
non è un pareggio. Il validatore mantiene il rifiuto; la diagnostica ora
distingue `probabilities_not_normalized` da `choice_not_argmax`.
La [documentazione Choice](https://docs.typesafe.ai/primitives/choice) definisce
le probabilità come una distribuzione con somma1. I tre `ValueError` della
campagna precedente non hanno corpo grezzo e restano senza causa identificata.

## Candidato operativo, non conferma di superiorità

Un filtro Jev seguito dal normale sui risultati `unproven` o invalidi merita
una prova dedicata. Nel replay esplorativo delle dodici coppie già raccolte
produce12/12 con4 chiamate al normale anziché12. La somma dei tempi delle
componenti sarebbe87,229 secondi contro241,130. È una simulazione su dati
raccolti, proposta dopo averli osservati: **non è una cascata dal vivo né una
conferma indipendente**. Non si presenta questa analisi come vittoria del
protocollo iniziale. La qualità futura dei giudizi decisivi resta da misurare.

Per il mandato, lo stesso replay richiede il normale in1 caso su4, con
11,455 secondi stimati contro70,461. Anche questo numero non giustifica
l'uso di Jev come sostituto della verifica del codice. TypeSafe descrive Jev
come componente per decisioni strutturate dentro un agente, distinto dal
[modello che scrive il codice](https://docs.typesafe.ai/introduction/coding-agents).

## Verifiche del banco e delle condizioni reali

- Codice effettivamente usato:561bc85, derivato dal commit autore d773bed.
  L'interfaccia aggiuntiva b1f10ae del preparatore non è stata integrata.
- Unit test iniziali17 verdi; suite completa515 pass,4 live esclusi,362,08s.
  La revisione ha poi trovato due varianti di evento strumento non riconosciute:
  tre test di regressione fallivano prima e ora passano,20 unit test verdi.
- Gate finale sul codice corretto53fb8cc:518 test passati,4 live esclusi,
  362,08s; Ruff e controllo del formato verdi. Il revisore indipendente ha
  ripetuto i20 test del banco nella propria copia: nessun difetto grave aperto.
- Audit offline dopo le correzioni: tutte le12 directory effettive del normale
  sono worktree Git figlie sotto `/root/wt`, con tutti gli strumenti negati e
  tutti e tre gli MCP ereditati disabilitati prima della fine delle inferenze.
  La directory padre della campagna non è un worktree: contiene i dati privati.
- I raw completi del normale contengono12 `step_start`,12 `text`,2 `step_finish`
  e nessun evento strumento, comprese le due varianti aggiunte al rilevatore.
  Nessuno dei raw è troncato. Le richieste hanno hash uguale per i due modelli.
- Il parser corretto riproduce offline gli stessi24 esiti delle inferenze
  originali; cambia soltanto la precisione del codice diagnostico per0,99.
  Non sono state ripetute chiamate Jev ignote o invalide per correggere i numeri.

Input e risultati pubblicabili:
[corpus e ricevute](live-evidence/jev-phase-eval-20261025.json).
I corpi originali e l'audit di riconciliazione restano nella directory privata.

## Budget e confini

Dodici slot da 2 USD sono stati prenotati sul ledger canonico: massimo24 USD
prudenti per l'esperimento. Le revisioni automatiche dei preparatori sono a
parte. Inferenze Go, preparazione e coordinatore non hanno una fattura completa
disponibile: non si rappresentano come un costo totale zero. Il tetto mensile
era360 all'avvio della campagna, con concorrenza12 e policy automatica.
Questo incarico non modifica tetti, concorrenza o policy. Al controllo finale
il processo CLI e il server MCP espongono tetti differenti (720 e360): non
si assume disponibilità condivisa da quel solo numero e non si avviano altre
chiamate a pagamento. La proposta separata di due ulteriori casi sul piano
dei test resta senza approvazione e senza esecuzione.
Gli slot prenotati ma sicuramente non inviati si chiudono a zero; un invio
ignoto mantiene l'intero accantonamento.

Il protocollo e le ricevute private sono in
`/root/.local/state/subtask-jev-phases-20261025/`. Il riferimento iniziale è
`dbc8964`; la campagna precedente e i suoi packet originali sono conservati.
I controlli di copertura usano anche vere esecuzioni di test che passano ma
non esercitano il requisito: il modello deve leggere la copertura, non soltanto
il codice di uscita. Un controllo deterministico già sufficiente va dichiarato
e non attribuito come scoperta esclusiva di Jev.
