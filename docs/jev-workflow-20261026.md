# Jev nel workflow reale Subtask — 26 ottobre 2026

## Risultato

Dodici casi congelati prima degli invii: Jev coincide con le annotazioni di riferimento
in 11/12, DeepSeek in 10/12, Codex nativo in 12/12. La cascata Jev→DeepSeek resta
in 11/12 e usa DeepSeek in 5 casi. Nessun modello dà un falso `supported` sui
casi annotati contrari o insufficienti. Il campione è intenzionale e correlato:
questi numeri non stimano accuratezza generale o superiorità su Codex.

| Ipotesi | Jev | DeepSeek | Codex nativo | Cascata |
|---|---:|---:|---:|---:|
| Dipendenze prima del fan-out |2/3|1/3|3/3|2/3|
| Coerenza report e ricevute |3/3|3/3|3/3|3/3|
| Controllo del prossimo feedback |3/3|3/3|3/3|3/3|
| Triage della revisione richiesta |3/3|3/3|3/3|3/3|

Jev impiega 2,941 secondi complessivi per le dodici richieste; la cascata 37,926
secondi, contro 133,820 delle componenti DeepSeek CLI di confronto. Le mediane
per singola chiamata sono 0,245 e 6,322 secondi. Sono tempi del punto di decisione:
escludono raccolta delle prove, preparazione, agenti nativi, test e coordinatore.
Il normale include avvio CLI e ragionamento `max`; l'ordine è sempre Jev prima.
Un caso DeepSeek richiede 62, 131 secondi e pesa molto sul totale. Il confronto
nativo è un batch di sola qualità, con modello ereditato senza modifiche:
nessun confronto valido di latenza per singolo caso con quel batch.

## Come usi Subtask nel campione

Finestra: ultime 60 directory job per mtime, osservate il 26 ottobre locale.
Sono 49 job non replay: 29 Codex e 20 OpenCode; 11 replay sono esclusi dalle fonti.
Stati al momento della lettura: 47 terminati, 1 fallito, 1 in corso. Non è un
campione casuale e non coincide con tutti i tuoi incarichi.

Solo 3/49 specificano `verify`; 4/49 hanno `allowed_paths` o `symbol_scope`.
Cinque job hanno almeno un reply, per sei file di reply complessivi. Questo
non prova che gli altri lavori non abbiano test: possono averli eseguiti
manualmente, senza una ricevuta indipendente legata al commit. Anche il campo
`readonly` è sempre falso nella finestra: alcuni mandati impongono comunque
sola lettura nel testo, perché i motori richiedono un worktree scrivibile.

Perciò i dati strutturati da soli non ricostruiscono tutto il tuo workflow.
Un campo mancante non è un permesso; una parola come «sicurezza» in un elenco
di esclusioni non dimostra una modifica di sicurezza.

## Dove Jev può aiutare

**Feedback prima del prossimo tentativo.** Riconosce un'istruzione pertinente,
una falsa chiusura nonostante difetti aperti, e un'estensione senza contratto
visibile. Tre risposte coerenti con il normale; candidato a un controllo
consultivo rapido prima di sprecare un giro. Non prova che il feedback corregga
il bug né misura causalmente il risparmio sul lavoro successivo.

**Triage semantico.** Distingue l'indagine in sola lettura da una migrazione di
credenziali e da un TODO non disponibile. Può segnalare la necessità di leggere
il materiale o di mantenere la revisione complessa. Non autorizza esoneri dalla
falsificazione, non cambia provider o budget e non rilascia niente.

**Coerenza delle prove.** Separa suite verde, fixture di deploy fallita con
rollback e causa storica non dimostrata. I fatti strutturati sono già adatti
al codice: per exit, commit e conteggi conviene prima usare quel controllo.
L'eventuale contributo Jev è interpretare la portata dell'affermazione.

**Dipendenze di lettura.** Jev contraddice l'indipendenza giustificata soltanto
con scritture disgiunte; DeepSeek si astiene su quel caso. È un solo esempio
su una coppia correlata, con regola esplicita nel packet: non dimostra capacità
generale di pianificare il parallelismo meglio di regole o Codex.

## Disaccordo conservato

Sul caso storico di dipendenza dallo snapshot il gold fissato è `supported`;
Jev e DeepSeek rispondono `unproven`, Codex nativo `supported`. Il packet contiene
mandati e target richiesto, senza una ricevuta della disponibilità del checkout.
Il gold giudica la proposta di dipendenza, non che il checkout esista adesso:
le due letture possono spiegare il disaccordo, senza provare la causa interna
dei modelli. Il punteggio primario resta quello fissato; non si rietichetta,
non si esclude il caso sfavorevole e non si ritentano le richieste pagate.

Prima di automatizzare un dispatcher servono fatti come target presente,
HEAD effettivo, identità del checkout e insiemi di lettura/scrittura. Sono
precondizioni verificabili dal codice. Una revisione su uno snapshot già
esistente non deve aspettare inutilmente l'autore di modifiche successive.

## Protocollo e fonti

Le etichette sono preparate dagli agenti e controllate dal coordinatore usando
mandati e ricevute osservabili; non sono giudizi indipendenti di un annotatore
umano. Sui casi semantici rimane un limite di interpretazione. I preparatori
e il comparatore nativo condividono la famiglia Codex: il loro accordo può
condividere bias e non costituisce una validazione umana indipendente.

Quattro agenti nativi Codex hanno preparato ipotesi e casi in worktree separati.
Un quinto ha controllato il protocollo prima delle inferenze; un sesto è il
comparatore cieco. Nessuna delega annidata. Le richieste derivano da mandati,
reply, risultati e ricevute di job reali; includono omissioni e sovraffermazioni
controllate dichiarate. Non sono dodici nuovi lavori completi.

L'audit ha fatto restringere un criterio: `dirty_after=false` prova checkout
pulito dopo il run, senza provare lo stato precedente o durante il run. La
restrizione è avvenuta prima del congelamento, con gold invariato. Le guardie
uguali ai gold sono spostate in annotazioni manuali della provenienza: non
sono una baseline eseguibile misurata. Solo il controllo costante sempre
`unproven` è eseguito come baseline debole: 4/12. Non si attribuisce a Jev
un vantaggio generale su controlli deterministici.

Ogni Jev/DeepSeek riceve lo stesso `state` e le stesse domande C1, senza gold.
Le domande restano consultive, con astensione per dati assenti. Jev è risolto
alla revisione registrata nelle ricevute; il normale è
`opencode-go/deepseek-v4.1-flash#max`. La cascata chiama il normale soltanto su
`unproven`/errore/invalido. Per i risultati decisivi il normale è chiamato
successivamente per il solo confronto; sui ritorni la stessa inferenza vale
anche come baseline. Dodici inferenze normali complessive sono realmente
eseguite: le sette di confronto non sono un risparmio già ottenuto in produzione.

Il comparatore Codex usa gli stessi packet in un batch con ID opachi e ordine
mescolato, senza gold; dichiara sola lettura del file e nessun altro strumento.
La restrizione non ha la stessa attestazione del runner OpenCode. I casi del
batch condividono contesto e ID ordinabili; non sono dodici prove isolate.
Non si usa il suo 12/12 per una stima statistica o una superiorità generale.

Audit locale: dodici directory d'inferenza sono worktree Git distinti; tutti
gli strumenti e MCP ereditati sono negati. I raw completi contengono 12 avvii,
12 testi e 1 chiusura step, nessun evento strumento. Tutte le 24 risposte Jev/Go
sono rilette e validate offline; anche i dodici vettori del comparatore nativo
superano la validazione. Nessuna chiamata API nell'audit, nessun esito ritentato.

Accantonamenti Jev: 24 USD sul ledger canonico, entro il tetto 720 esistente.
Fattura sconosciuta. Costi e consumi completi dei sei agenti, del normale e del
coordinatore non sono disponibili: nessun vantaggio economico viene dichiarato.
La politica automatica del servizio resta invariata; nessun rilascio sui siti.

## Materiale

- [Risultati sintetici e hash](live-evidence/jev-workflow-20261026.json).
- [Preparazione dipendenze](jev-workflow-routing-20261026.md).
- [Preparazione prove](jev-workflow-evidence-20261026.md).
- [Preparazione feedback](jev-workflow-feedback-20261026.md).
- [Preparazione triage](jev-workflow-risk-20261026.md).
- [Audit preventivo](jev-workflow-protocol-audit-20261026.md).

Packet originali, annotazioni, preregistrazione, raw e inventario sono privati in
`/root/.local/state/subtask-jev-workflow-20261026/`. I risultati delle campagne
precedenti restano conservati, compresa la conferma negativa sul mandato.

## Verifiche finali

Codice verificato: `2ab0d65`. Suite completa nel worktree con ambiente editable:
`env -u SUBTASK_MONTHLY_CAP_USD -u SUBTASK_MAX_RUNNING build-a-turno .venv/bin/python -m pytest -q`.
Esito: **666 passati, 4 live esclusi**, 373,11 secondi. Ruff 0.17.0 e controllo
formato verdi. Log conservato nel percorso privato della campagna.

Il diff riguarda benchmark, test e documentazione; nessun cambiamento al
runtime `subtask/`. Non occorrono installazione del pacchetto o riavvio MCP.

## Attivazione nel servizio

Contratto 26 ottobre: pubblicare i quattro pareri consultivi nel percorso MCP e
CLI condiviso. Repository canonica `/root/subtask`; sviluppo e verifiche in
`/root/wt/subtask-jev-prod-20261026`. Routing in `start_many`, feedback in
`reply`, prove e rischio nello stesso controllo finale già esistente.

Vincoli: policy automatica e tetto condiviso invariati; nessuna accettazione,
integrazione, esecuzione di comandi suggeriti o cambio provider automatici.
Le nuove richieste routing/feedback usano il cap esistente; gli invii ignoti
non vengono ripetuti. Prova: test di regressione degli output reali MCP/CLI,
caso minimo live, suite completa, CI finale e handshake client installati.

### Implementazione e gate di attivazione

Codice `99be022`: `start_many` restituisce il parere sulle dipendenze dopo aver
avviato i lavori; il parere non modifica il grafo né annulla partenze riuscite.
`reply` confronta l'istruzione con mandato, rapporto e ricevute precedenti prima
di riprendere il worker, senza riscrivere il messaggio. Entrambi i pareri
restano nel risultato testuale e nell'envelope JSON.

La consegna automatica aggiunge C4 (qualità delle prove) e C5 (triage del
rischio) ai tre criteri precedenti, nello stesso invio. `wait` e `result`
espongono fase, scelta grezza, assessment, riferimento e prossimo passo.
L'origine non autenticata del mandato continua a lasciare lo scope non provato.

Ogni nuovo batch o feedback valutabile può richiedere un invio aggiuntivo da
2 USD prudenziali sul cap attuale. La consegna mantiene il suo unico invio.
Policy manual/off, chiavi, cap e provider restano sotto il controllo operatore.
Gli intent persistenti precedono l'HTTP: un invio ignoto non viene ripetuto.

| Revisione | Comando / ambiente | Esito |
|---|---|---|
| `99be022` | `env -u SUBTASK_MONTHLY_CAP_USD -u SUBTASK_MAX_RUNNING build-a-turno .venv/bin/python -m pytest -q`, worktree completo, ambiente editable | 693 passati, 4 live esclusi, 377,66 s |
| `99be022` | Ruff 0.17.0, check e format; schema MCP rigenerato dal server | verde |
| `99be022` | Regressione output con arricchimento batch disabilitato nel solo processo di prova | fallisce come previsto; implementazione corretta verde |
| `99be022` | Revisore indipendente del runtime, inclusa conservazione del packet redatto | nessun blocco residuo |
| `99be022` | Un invio Jev reale sulle dipendenze dei due incarichi di implementazione | provider=contradicted, assessment=contradicted, 0,246 s |

Il caso minimo segnala la dipendenza di lettura dell'interfaccia: scritture
su file distinti non dimostrano indipendenza. Questa prova non misura
l'accuratezza generale. Packet, ricevuta, hash del wheel, log del gate e
backup per il ritorno sono in
`/root/.local/state/subtask-jev-prod-20261026/`.

Il rilascio aggiorna il pacchetto locale e le skill distribuite. Le nuove
connessioni usano il nuovo codice; Codex e OpenCode supportano ricarica remota.
Per sessioni Claude interattive già aperte il client può richiedere una
riconnessione dal menu MCP: una nuova connessione verificata non prova
la sostituzione di tutte quelle vecchie.
