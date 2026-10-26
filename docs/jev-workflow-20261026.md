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

## Contratti e piani del coordinatore — 26 ottobre 2026

Risultato candidato: `start` conserva il mandato prima dell'espansione del
contesto; CLI e i due trasporti MCP espongono `plan` e packet offline per
singola affermazione. La preview non avvia worker. Il lancio usa un intento
persistente, ricontrolla input e revisione e non ripete un lancio interrotto.
I task dipendenti ricontrollano i materiali già disponibili; gli output dei
predecessori si risolvono alla partenza e questo limite resta esplicito.

Le ricevute collegano requisito, candidato, tentativo, ambiente e log.
Un candidato cambiato invalida la ricevuta. Il delta autore/revisore conserva
rilievi aperti e richiede requisiti, input e ambiente compatibili per il riuso.
Il riuso non accetta un nuovo commit. I job storici restano utilizzabili e
mostrano contratto mancante. Il verdetto JSON `subtask_review.verdict` resta
una dichiarazione del worker separata dallo stato del processo.

Repository `/root/subtask`, worktree `/root/wt/subtask-workflow-20261026`.
Ambiente editable vincolato, Python 3.14.7. Configurazione Jev e chiavi isolate
nelle fixture; provider finto, nessun nuovo invio a pagamento. I dati privati
dell'audit restano fuori dal repository. Nessuna policy, provider o tetto
modificati. Installazione, integrazione e controllo dei client spettano al
coordinatore dopo la revisione indipendente.

| Revisione / input | Comando nel worktree | Esito e log | Invalidazione |
|---|---|---|---|
| Candidato su `cd68bfd`, diff contratti | `build-a-turno .venv/bin/python -m pytest -q tests/test_contract_workflow.py tests/test_workflow_hooks.py tests/test_worktree_verify.py tests/test_delivery_review*.py tests/test_auto_review.py tests/test_subtask.py tests/test_context_dag.py tests/test_schema.py` | 224 passati, un errore nel nuovo controllo schema: import aggiunto durante la raccolta; `/tmp/subtask-implementation-targeted.log` | Test schema corretto |
| Stesso candidato, test schema corretto | `build-a-turno .venv/bin/python -m pytest -q tests/test_subtask.py::test_fast_server_speaks_json_rpc` | 1 passato; `/tmp/subtask-implementation-transport.log` | Cambi schema o trasporto |
| Stesso candidato, packet referenziato | `build-a-turno .venv/bin/python -m pytest -q tests/test_delivery_review.py::test_offline_claim_packets_store_complete_referenced_material` | 1 passato; `/tmp/subtask-implementation-packets.log` | Cambi packet o riferimenti |
| Candidato completo | `.venv/bin/ruff check subtask tests`; `.venv/bin/ruff format --check subtask tests`; dump schema server confrontato con `tools.json` | Verdi | Codice o schema cambiati |
| Candidato completo | `build-a-turno env -u SUBTASK_MONTHLY_CAP_USD -u SUBTASK_MAX_RUNNING .venv/bin/python -m pytest -q -p no:cacheprovider` | In corso; `/tmp/subtask-implementation-full.log` | Attendere risultato prima di integrare |

Limiti: la provenienza del coordinatore non è autenticata; materiali e ricevute
locali sono modificabili dallo stesso utente. Test e build locali non provano
rilascio pubblico né risoluzione di cause storiche. Il tempo fino alla decisione
del coordinatore resta ignoto senza una decisione osservata; i costi mancanti
restano mancanti. Non è stata misurata una riduzione dei tempi del workflow.

### Correzioni emerse dal primo gate del candidato

Il gate di `ca39eb5` ha concluso con 745 passati, quattro live esclusi e tre
regressioni: prefisso dell'errore repository, hint `files` inesistente e
conteggio rigido dei tool nel trasporto moderno. Il delta conserva il prefisso
`worktree not created`, registra gli hint mancanti senza hash né falsa lettura
e confronta il manifest completo. I `context_refs` restano letture obbligatorie.
La transizione hint mancante → presente invalida la preview.

Il coordinatore ha riprodotto un falso `launched` dopo un rifiuto al confine di
avvio. Ora una ricevuta interna osserva lo spawn e separa job, worker partiti,
task ancora pendenti, rifiuti ed esiti ignoti. La risposta testuale legacy resta.
Gli esiti `refused`, `partial`, `scheduled` e `unknown` non autorizzano replay.
Un file laterale conserva gli identificativi osservati anche quando manca la
scrittura conclusiva del piano. La ricezione della richiesta non prova
l'accettazione del risultato. I piani adversarial restano `unknown` quando non
è provato l'intero avvio composto; gli identificativi osservati sono conservati.

Durante il primo gate, il test preesistente della sintesi consensus ometteva
`synth_engine` e ha avviato il motore Claude reale con una chiave fixture.
Questo tentativo non era offline; il costo reale resta UNKNOWN, non zero.
Il worker `st-cdf9283cb6`, PID 3121131, figlio Claude 3121158, è stato fermato
tramite `subtask.cli stop` nel solo HOME della fixture. Entrambi i processi
sono poi risultati assenti. Il costo osservato era null; 0,5 USD nel registro
della fixture era un accantonamento prudenziale, non una fattura.
Il test ora passa esplicitamente `synth_engine="fake"`; provider, configurazione
e tetti del servizio restano invariati.

| Input | Comando / ambiente | Esito e log |
|---|---|---|
| Delta dopo `ca39eb5` | Mirati contratti, compatibilità, consensus e protocollo, `build-a-turno .venv/bin/python -m pytest -q` con selettori registrati | 27 passati, `/tmp/subtask-implementation-delta.log` |
| Delta dopo `ca39eb5` | DAG schedulato e test stop/adversarial | 8 passati, `/tmp/subtask-implementation-launch-extra.log` |
| `ca39eb5` | CLI e i due server MCP, preview stessa richiesta in HOME isolato | Stesso piano prepared, nessun worker, `/tmp/subtask-implementation-transports.log` |

Le ricevute del primo gate restano utili alla diagnosi; il gate offline definitivo
va eseguito sul delta corretto. Integrazione e disponibilità restano da verificare.

### Verdetto della continuazione e limiti delle metriche

Il gate completo del delta `5b96837` è verde: 752 passati, quattro live esclusi,
317,93 secondi, log `/tmp/subtask-implementation-full-final.log`.
La riproduzione indipendente del coordinatore dimostra anche il cambio
`launched` con zero job → `refused`, zero worker e nessun identificativo.
Ricevute private `independent-launch-state-{before,after}.json` in
`/root/.local/state/subtask-workflow-20261026-release/`.

La successiva regressione reale usa solo il motore fake: review APPROVATO
al primo tentativo, poi RESPINTO con exit 1 nella continuazione.
Il campo strutturato ora legge `result-2.md` e indica tentativo, riferimento
e binding. Il sommario legacy può conservare `result.md`; non fornisce il
verdetto dell'ultimo tentativo fallito. Se manca il rapporto corrente il
verdetto resta unknown. Solo il primo rapporto storico può usare il fallback
legacy, dichiarato non vincolato a un tentativo autenticato.

I tentativi osservati non vengono più presentati come cicli autore/revisore.
`attempts_observed` e `attempt_number` contano ciò che è registrato;
`author_reviewer_rounds` e `review_round` restano null con ragione esplicita
finché manca un ciclo collegato osservabile.

| Input | Prova | Esito |
|---|---|---|
| `5b96837`, nuove regressioni | `build-a-turno .venv/bin/python -m pytest -q tests/test_contract_followup.py` | Tre fallimenti attesi, `/tmp/subtask-implementation-followup-before.log`: verdetto precedente promosso e provenienza/misure mancanti |
| Delta verdetto e metriche | Stesso comando, stessa fixture fake | Tre passati, `/tmp/subtask-implementation-followup-after.log` |

Ruff resta verde. Il nuovo delta richiede il gate conclusivo dopo la revisione
indipendente; il risultato del gate precedente resta riferito a `5b96837`.

Il rendering testuale distingue anche l'origine dei prossimi passi: i riepiloghi
del workflow e della consegna li etichettano come wrapper deterministico.
Per ogni affermazione vengono mostrate le prove mancanti e l'azione circoscritta
già calcolate dal filtro. Questi testi non sono attribuiti a Jev e non vengono
eseguiti come comandi. I report storici senza questi campi restano leggibili.

## Delta dopo la revisione indipendente di `ca39eb5`

Repository e worktree restano quelli del candidato. Ambiente delle prove:
Python 3.14.7, Ruff 0.17.0, installazione editable vincolata. Il coordinatore
ha inoltre verificato `94f9b15` con 755 passati, quattro live esclusi e 290,12 s;
quel gate precede questo delta e non lo certifica.

Il rapporto OpenCode è stato letto per intero, insieme ai 17 probe.
Il suo commit `ad99132` non è stato integrato: i marker xfail mascheravano i
fallimenti attesi. I probe utili sono diventati regressioni ordinarie,
senza xfail o skip. Le aspettative superate sono state corrette senza
indebolire il comportamento richiesto: un lancio interrotto si mostra unknown;
la storia viene verificata tramite riferimenti, non duplicando tutti i delta
nel contratto corrente; la provenienza dello scope resta separata dai blocchi
dei singoli criteri.

| Rilievo | Stato prima / correzione verificata nel delta |
|---|---|
| 1 | Il server MCP veloce ora accetta `contract`, come lo schema; prova anche JSON-RPC con worker fake reale. |
| 2 | Solo i criteri non-scope basati su materiale del runner possono ignorare i due limiti di identità non autenticata. Scope, richieste e claim del worker li conservano; nessuna autorizzazione deriva dal verdetto. |
| 3 | Breaker, Builder e gruppo di fallback ricevono richiesta, requisiti e ambiente del coordinatore. |
| 4 | Il requisito R1 del gruppo usa `coordinator_request`, non le note operative del task. |
| 5 | Il packet ricostruisce tutti i delta della catena `contract-N.json`, verificando digest, revisioni, riferimenti e ancoraggio al contratto iniziale. Storia mancante/manomessa produce un limite esplicito; materiale troppo grande viene bloccato senza troncamento. |
| 6 | Hash, lunghezza e riferimento del manifest identificano i byte redatti effettivamente salvati. |
| 7 | Stati falsi già corretti in `5b96837`; qui si aggiunge il preflight per adversarial senza verify e oracle non valido. |
| 8 | Gli elementi non stringa di `context_refs` producono un errore controllato, prima del worker. |
| 9 | I job Git con `cwd` condiviso osservano HEAD e albero corrente; la ricevuta non viene dichiarata stale solo perché manca un branch isolato. Nessun isolamento viene inventato. |
| 10 | Il runner CLI registra il codice di uscita realmente osservato in Outcome, tentativo e stato. API e uscite non osservabili restano null; il caso RESPINTO/exit 1 usa il processo fake reale. |
| 11 | `.txt` non implica più report-only; l'idoneità al riuso riguarda nomi/percorsi di rapporto circoscritti, con binding e invalidazioni invariati. |
| 12 | Prima delle altre correzioni, i riferimenti Git sono stati validati e fissati a SHA con `--end-of-options`; diff usa SHA e separatore opzioni. Il probe che creava PWN ora non crea alcun file. |
| 13 | I file relativi di un nuovo worktree hanno hash del blob della base Git. Eventuali byte diversi nel checkout del coordinatore sono un'osservazione separata. Assoluti e job condivisi conservano il materiale realmente letto; file assenti dalla base restano hint espliciti. |
| 14 | Il contratto vincola il contenuto effettivo della repo map. Se la seconda risoluzione di ref/map differisce dal digest preparato, start rifiuta prima dello spawn; il registro non duplica il contesto. |
| 15 | `launch=True` senza plan_id restituisce invalid; la CLI segnala `--launch needs --id`. |
| 16 | Una ricevuta malformata produce unproven esplicito anche attraverso `result(evidence=True)`. |

Il difetto del rapporto obsoleto era stato corretto in `0c057e5` per evidence;
ora `jobs.report_reference` è la selezione canonica condivisa anche dal packet.
Il packet dichiara tentativo, riferimento e binding. Una continuazione senza
rapporto corrente non eredita `result.md`; il fallback legacy resta solo al
primo tentativo e si dichiara non vincolato.

Il tail del controllo runner viene persistito in `verify-N.log`, redatto e
marcato `captured_output_tail_only`: non è un log completo. La ricevuta non
punta più a worker.log, che poteva essere vuoto. Se il salvataggio fallisce,
il limite runner_log_unavailable impedisce una ricevuta completa.

| Input / percorso | Prova e log | Risultato |
|---|---|---|
| Packet reale, storia e rapporto prima delle correzioni | `/tmp/subtask-contract-history-before.log` | Cinque fallimenti attesi |
| Storia incompleta / JSON malformato / limite dimensione | `/tmp/subtask-contract-history-complete.log`, `/tmp/subtask-contract-history-size.log`, `/tmp/subtask-contract-history-malformed-before.log` | Sette verdi dopo; JSON malformato aveva una prova rossa distinta |
| Riferimento log del runner, verify stampa un marker | `/tmp/subtask-verification-log-before.log`, `/tmp/subtask-verification-log-after.log` | Rosso: worker.log vuoto; verde: marker nel log referenziato |
| Opzione Git avversaria, fixture temporanea | `/tmp/subtask-review-git-option-before.log`, `/tmp/subtask-review-git-option-after.log` | Rosso: PWN creato; verde: nessuna scrittura e unproven controllato |
| Probe indipendenti adottati, prima delle relative correzioni | `/tmp/subtask-review-adopted-before.log` | 14 rossi, tre già verdi per correzioni precedenti / storia / protezione Git |
| Ref e repo map cambiano tra hash e prompt | `/tmp/subtask-review-boundary-before.log`, `/tmp/subtask-review-boundary-after.log` | Due rossi prima; 66 mirati verdi dopo |
| Insieme finale dei percorsi interessati | `build-a-turno .venv/bin/python -m pytest -q tests/test_review_binding_boundaries.py tests/test_review_findings_20261026.py tests/test_contract_history_packets.py tests/test_contract_followup.py tests/test_contract_workflow.py tests/test_worktree_verify.py tests/test_delivery_review*.py tests/test_schema.py tests/test_subtask.py tests/test_workflow_hooks.py` | 233 passati in 39,44 s, `/tmp/subtask-review-final-targeted.log` |

Ruff e confronto del dump schema del server con `tools.json` sono verdi.
Il coordinatore avvierà la revisione del commit cumulativo e completerà
integrazione, installazione e verifica dei client. Provider, policy e tetti
non sono cambiati; queste prove usano fixture e motori fake, senza nuovi invii.
Il revisore dichiara separatamente un tentativo auto terminato per chiave
ANTHROPIC mancante, ledger della fixture vuoto e nessuna richiesta pagata;
questa dichiarazione non è una fattura né una misura generale dei costi.

### Trovato strada facendo

- `subtask/core.py:509`, funzione `_line`: crash preesistente con verification non-dict nel risultato semplice; gravità media, fuori da questo delta. Il percorso evidence è protetto.
- `subtask/context.py:123`, funzione `repomap`: cache preesistente basata solo su HEAD; gravità bassa. Il nuovo binding descrive il contenuto effettivamente consegnato e non dichiara la cache fresca.

Copertura aggiuntiva: JSON-RPC `start` del server veloce con contratto arriva
al worker fake; il manifest con segreto identifica esattamente hash e lunghezza
dei byte su disco (due passati in `/tmp/subtask-review-transport-fallback.log`).
La prova del fallback adversarial richiedeva un wrapper di test con firma
preservata per il preflight; corretto solo il test con `functools.wraps`, il
percorso reale è verde: un passato in 1,39 s,
`/tmp/subtask-review-fallback-final.log`. Nessun provider reale è stato usato.

## Chiusura della seconda revisione del delta

Base di questo intervento: `7174386`, con guard OAuth Kimi invariato.
Il coordinatore ha verificato quella base con 793 passati e quattro live
esclusi; il risultato non certifica le correzioni successive. Nessuna modifica
a modello, profilo, policy, provider, budget o installazione.

Il rapporto `REVIEW-DELTA-20261026.md` e le cinque nuove riproduzioni del
revisore sono stati letti per intero. Le 17 regressioni precedenti restano
intatte. Le cinque nuove sono state aggiunte come test normali, senza xfail.

| Requisito / rilievo | Correzione e percorso verificato |
|---|---|
| Base preparata = base consegnata | Anche start normale fissa `base` allo SHA del contratto. Un commit iniettato dopo preflight non cambia il worktree né il blob dichiarato. |
| Storia incompleta conserva l'ultima istruzione disponibile | Se manca la revisione immutabile corrente, il packet mantiene il delta del contratto corrente, con riferimento, digest del testo e `chain_attested=false`. Il limite storia incompleta resta; il packet non dichiara copertura totale. |
| Fallback adversarial osservabile | Il gruppo riceve una ricevuta interna; gruppi, job osservati e task pendenti confluiscono nella ricevuta del piano. Se Builder parte subito, pending=0 è corretto e il suo job deve comparire. Se resta pendente, il conteggio lo mostra. Lo stato unknown impedisce il replay. |
| Entry Git non regolari | Il preflight controlla il modo Git della entry e degli antenati. Symlink, directory usate come file e gitlink vengono rifiutati in modo controllato; non ricevono hash di una vista diversa né un falso missing_hint. |
| Mappa di un dipendente | Il piano congela `expected_repomap_sha256`; il confine start del task differito la ricontrolla. Una mappa cambiata dopo la preview rifiuta la partenza prima dello spawn. Il valore atteso non modifica il digest della metadata originaria. |
| Piani legacy senza ricevuta | Preview, lettura per ID e riuso restituiscono workers_started=null con limite esplicito, anziché dedurre zero o successo dal vecchio stato. Una ricevuta osservata con zero worker continua a riportare false. |

La nuova aspettativa del revisore sul fallback richiedeva pending positivo
anche quando `groups.create` avviava già Builder. È stata corretta solo quella
nuova aspettativa: verifica il gruppo persistito e il job Builder osservato,
oppure il conteggio pendente. Il testo «queued» non è usato come prova di spawn.

| Revisione / input | Comando / ambiente | Esito e log |
|---|---|---|
| `7174386`, cinque nuove regressioni prima del fix | `build-a-turno .venv/bin/python -m pytest -q tests/test_review_findings_20261026.py -k 'prepared_contract_base or latest_available_delta or fallback_plan or declared_base_'` | Cinque rossi, 17 precedenti esclusi; `/tmp/subtask-delta2-before.log` |
| Stessa base, sospetti limitati al rapporto | `build-a-turno .venv/bin/python -m pytest -q tests/test_review_binding_boundaries.py -k 'deferred_dag or legacy_launched'` | Due rossi; `/tmp/subtask-delta2-suspects-before.log` |
| Delta corretto, Python 3.14.7 editable vincolato | `build-a-turno .venv/bin/python -m pytest -q tests/test_review_findings_20261026.py tests/test_review_binding_boundaries.py tests/test_contract_workflow.py tests/test_contract_history_packets.py` | 66 passati in 22,40 s; `/tmp/subtask-delta2-after.log` |
| Provenienza corrente e piani legacy nei tre percorsi | Selettori dei due test interessati, stesso ambiente | Tre passati; `/tmp/subtask-delta2-final-fields.log` |
| Gruppo/Builder realmente osservati | Selettore del nuovo fallback adversarial | Un passato; `/tmp/subtask-delta2-fallback-final.log` |

Ruff e formato sono verdi. Le prove sono offline, con motore fake e
configurazione Jev isolata; nessuna nuova richiesta a provider. Il commit
cumulativo richiede il gate completo e la revisione di chiusura sullo stesso
SHA. Push, installazione e verifica del servizio restano al coordinatore.

Restano i limiti già dichiarati: identità del coordinatore non autenticata,
record locali modificabili dallo stesso utente, verifica locale distinta dal
rilascio. La cache repomap non viene resa fresca da questo intervento: si
vincola il contenuto effettivamente consegnato. Nessun nuovo audit eseguito.

## Consegna verificata dal coordinatore — 2026-10-26

Codice accettato e installato: `06345d4ba0d271423ca0ebcedcb2530dfea87a1b`.
Include il guard OAuth Kimi `7174386` e i sei gruppi del mandato. Il commit
successivo di questo rapporto modifica solo la documentazione. Il codice
proviene dal worktree completo `/root/wt/subtask-workflow-20261026`; Arvos non
è stato modificato per implementare queste funzionalità.

### Gate e revisione indipendente

- Gate completo sul candidato esatto, Python 3.14.7 editable con dipendenze
  vincolate: `build-a-turno env -u SUBTASK_MONTHLY_CAP_USD -u SUBTASK_MAX_RUNNING .venv/bin/python -m pytest -q -p no:cacheprovider`.
  **801 passati, quattro live esclusi, exit 0, 298,34 secondi**.
  Log: `/tmp/subtask-workflow-delta2-full.log`. Ruff check, formato e dump dello
  schema confrontato con `tools.json` sono verdi sullo stesso candidato.
- Primo revisore OpenCode `st-935053fa7f`: 16 rilievi triagiati nel rapporto
  sopra. Il suo commit con xfail non è stato integrato. Le riproduzioni utili
  sono state adottate come test normali: 14 rosse prima, tre già verdi.
- Revisore del delta `st-3362aed5b8`: cinque nuove riproduzioni rosse, più due
  sospetti poi confermati e corretti. Nessun nuovo invio dello stesso esito
  Jev sconosciuto è stato fatto.
- Revisore di chiusura `st-26c6bb22c1`, motore OpenCode Go invariato:
  **delta idoneo**, cinque casi rossi sulla base e verdi sul candidato,
  54 test del runner verdi, controlli dei due sospetti e del guard Kimi.
  Commit del solo rapporto: `20463e0e3`; nessun codice del revisore integrato.
  Rapporto letto per intero:
  `/root/wt/subtask-workflow-closure-review-20261026/st-26c6bb22c1/review/REVIEW-CLOSURE-20261026.md`.
- Jev automatico della chiusura: un assessment supported e quattro unproven;
  scope non provato. Le scelte grezze supported filtrate dal wrapper non sono
  descritte come scoperte del modello. L'accettazione resta del coordinatore.

### Pacchetto e client osservati

Wheel 0.5.0: SHA256
`5575b65e2b8fe526a966b4b8c04cb0d6f6a370c1b5b5beb47d047a2dbb12ac8a`.
Tutti i 64 file del pacchetto coincidono byte per byte con gli oggetti Git del
candidato e con il pacchetto globale installato in
`/root/.local/share/uv/tools/subtask-mcp/lib/python3.14/site-packages/subtask`.
La revisione è distinta dalla versione nominale tramite `SUBTASK_MCP_BUILD`.
CLI plan/review e i due trasporti MCP espongono gli stessi 18 strumenti e schemi.

Sincronizzate cinque configurazioni e sei skill. Policy auto, provider,
tetto mensile di 720 USD e massimo 12 job sono invariati. Il backup attuale
conserva anche i permessi Codex `danger-full-access`/`never` richiesti
esplicitamente dall'operatore. Nessun worker preesistente era attivo al momento
dell'installazione; nessuna sessione è stata interrotta per riconnettere gli MCP.

| Client | Prova osservata | Limite |
|---|---|---|
| Codex già aperto | Ricarica accettata, connected, 18 strumenti; chiamata reale plan sul piano del collaudo e result con delivery/receipt/metrics nuovi | Non è solo una nuova connessione da shell |
| OpenCode | Subtask riconnesso per `/root/arvos` e `/root`, HTTP 204, PID del servizio invariato | Nessun nuovo turno modello lanciato per provare il client |
| Claude e Claude2 | Nuovo avvio dalla configurazione installata: initialize, tools/list e stato Jev verdi | Riconnessione delle sessioni interattive già aperte non osservata |
| Kimi | Nuovo avvio MCP configurato verde e worker reale sotto descritto | Riconnessione di un client interattivo preesistente non osservata |
| FastMCP | Connessione persistente, risposte osservate prima di chiudere stdin; schema identico al server veloce | Una prima sonda che chiudeva subito stdin è stata corretta; non era una prova negativa del servizio |

Per Claude/Kimi già aperti: riconnettere Subtask dal client oppure avviare una
nuova sessione che carichi la configurazione aggiornata. Non si dichiara una
riconnessione remota non osservata.

### Kimi e collaudo installato

CLI Kimi 2.3.1: risposta minima e scrittura di un file osservate con exit 0.
La sonda quota aveva ricevuto 401 leggendo un token scaduto; il comando nativo
ha rinnovato OAuth e la quota è tornata leggibile. Il nuovo guard non manda
il token scaduto alla sonda e non modifica le credenziali. Il rinnovo resta
alla CLI ufficiale. L'assenza di una quota corrente resta sconosciuta, non zero.

Nessun aggiornamento di modello è stato necessario. La
[documentazione ufficiale dei modelli](https://www.kimi.com/code/docs/en/kimi-code/models.html)
include ancora K3-256k. Nel wire del collaudo sono osservate **11 richieste**,
tutte con `model=k3-256k`, alias `kimi-code/k3-256k` e ragionamento `low`,
compresa la continuazione. Il default interattivo globale non è stato cambiato.

Piano `plan-c3a5c70a7f862470e64e88f666625731a0f11d9669da7ee8b26ced3b2859966b`,
un solo job **`st-73153d3c6a`**, tre tentativi, nessun avvio duplicato:

1. Il test sintetico fallisce prima. Preview senza worker, poi un worker reale;
   il riuso del lancio restituisce la stessa ricevuta. Kimi corregge alpha.py,
   commit `566167e8b`; processo e test a exit 0.
2. Primo result: correttamente unproven perché unittest genera cache Python non
   tracciata. La continuazione aggiunge la docstring, commit `1266f569d`;
   anche il secondo result mantiene onestamente il limite della fixture.
3. Il tentativo con PYTHONDONTWRITEBYTECODE non raggiunge il verificatore,
   che usa un ambiente minimo. Si corregge solo `info/exclude` del repository
   sintetico per la cache generata. Nessun test viene ignorato o modificato.
4. Seconda continuazione, solo verifica: stesso commit, contratto revisione 3
   e nuovo digest; processo e runner exit 0, **receipt_validation=valid**,
   albero pulito. Il log referenziato contiene il controllo realmente eseguito.
   Il result dal client Codex già aperto conferma i nuovi campi.

Il test controlla alpha.py; test_beta.py contiene un difetto preesistente
estraneo alla fixture assegnata. Non si dichiara verde l'intera fixture.
Il collaudo prova il pacchetto locale e il workflow; non prova un rilascio
pubblico, una causa storica di produzione o l'autenticità del coordinatore.

### Consumi, disponibilità e ricevute

Nove prenotazioni Jev attribuite tramite job e hash delle richieste:
**18 USD addebitati prudenzialmente al tetto**, fattura **UNKNOWN**.
Costo di abbonamenti, agenti nativi e totale di tutti i ruoli: **UNKNOWN**,
non zero. Ultima osservazione globale: 167,24 USD residui su 720; include
altri incarichi e non viene usata per stimare il costo di questo lavoro.
Kimi mostrava circa 99% di quota libera dopo il collaudo.

Ricevute private: `/root/.local/state/subtask-workflow-20261026-release/`:
`installed-wheel-receipt.json`, `live-client-receipt.json`,
`fresh-clients-receipt.json`, `installed-workflow-smoke.json`,
`installed-workflow-final-evidence.json`, `kimi-installed-profile.json`,
`cost-receipt.json` e `rollback-current.json`. Stato di push, GitHub sullo SHA
pubblicato e pulizia dei worktree sono registrati in `STATUS.json` nella stessa
cartella. Il gate locale non viene presentato come esito GitHub.

### Trovato strada facendo

- `subtask/core.py:524`, `_line`: crash preesistente con verification non-dict nel risultato semplice, gravità media; evidence è protetto, percorso semplice fuori incarico.
- `subtask/context.py:123`, repomap: cache preesistente basata solo su HEAD, gravità bassa; il binding identifica il contenuto consegnato, non garantisce freschezza.
- `subtask/planning.py`, ritorno di eccezione del lancio: workers_started=false può accompagnare state=unknown; la ricevuta conta solo worker osservati. Nessuna prova di assenza di effetti oltre quei dati e nessun replay automatico.
- Ulteriori limiti del revisore: record di piano corrotti, provenienza identica spec/status, gitlink e scadenza zero/millisecondi non collaudati direttamente. Non sono promossi a difetti riprodotti né a prove di copertura completa.
