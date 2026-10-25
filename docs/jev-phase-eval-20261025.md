# Jev nelle fasi Subtask: confronto controllato, 25 ottobre 2026

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

Otto casi iniziali coprono quattro fasi, con un mandato conforme e uno
insufficiente per fase. Due richieste aggiungono il contesto della funzione
alla stessa patch e allo stesso criterio concreto: isolano l'effetto di quei
dati aggiuntivi. Le vecchie ricevute sul criterio generico restano una baseline
osservativa; non costituiscono un confronto causale che cambia solo il testo
del criterio. Due ulteriori inferenze del modello normale riusano esattamente
i vecchi packet e non aggiungono invii Jev.

Prima del primo nuovo invio Jev sono stati congelati anche due esempi nuovi
per ciascuna delle quattro fasi. Solo la coppia della fase selezionata viene
usata: massimo dodici richieste Jev complessive. I file privati registrano hash
dei packet, verità esterna e provenienza separati. I nomi trasmessi sono opachi.
I casi derivano dalle richieste e dai commit storici Subtask, con omissioni
controllate nei mandati e nei piani. Non sono dodici nuove consegne di codice.

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

## Budget e confini

Dodici slot da 2 USD sono stati prenotati sul ledger canonico: massimo24 USD
prudenti per l'esperimento. Le revisioni automatiche dei preparatori sono a
parte. Inferenze Go, preparazione e coordinatore non hanno una fattura completa
disponibile: non si rappresentano come un costo totale zero. Il tetto mensile
resta360, la concorrenza12 e la policy automatica delle consegne non cambia.
Gli slot prenotati ma sicuramente non inviati si chiudono a zero; un invio
ignoto mantiene l'intero accantonamento.

Il protocollo e le ricevute private sono in
`/root/.local/state/subtask-jev-phases-20261025/`. Il riferimento iniziale è
`dbc8964`; la campagna precedente e i suoi packet originali sono conservati.
I controlli di copertura usano anche vere esecuzioni di test che passano ma
non esercitano il requisito: il modello deve leggere la copertura, non soltanto
il codice di uscita. Un controllo deterministico già sufficiente va dichiarato
e non attribuito come scoperta esclusiva di Jev.
