# Controllo consultivo delle consegne con Jev

Stato del 25 ottobre 2026: **installato e verificato localmente** da
`9405d029a55741145fe954c64157549ee2d44dad`, con 419 test passati.
Jev resta consultivo e su richiesta. Codex riceve le conclusioni tramite attesa
aperta: il normale MCP non risveglia un turno già chiuso.
Il limite Subtask è impostato a 10 nei client configurati. Codex e OpenCode sono
stati ricaricati; questa sessione Codex espone ora 16 strumenti, incluso `review`.
Le sessioni Claude già aperte richiedono `/reload-plugins`, oppure la riconnessione
del solo MCP da `/mcp`. Dettagli in fondo.

## Contratto

Il coordinatore confronta una consegna terminale con 1–12 criteri scritti dal
chiamante. Il controllo usa richiesta originale, rapporto del worker, diff Git e
verifica del runner già registrata. Ogni criterio restituisce una valutazione e
un riferimento a uno di questi materiali. Non avvia shell indicate nei materiali,
non modifica il lavoro e non accetta, integra o rilascia il candidato.

Repository: `tedin7/subtask`. Il componente Fabric non è una dipendenza.
Il provider resta Jev, tramite il contratto ufficiale
<https://docs.typesafe.ai/api/system-one>. Questa fase è osservazione: anche un
responso favorevole richiede la decisione del coordinatore.

## Uso

MCP:

```python
review(id="st-…", criteria=["Il parser gestisce input vuoti", "La pagina mostra il risultato"],
       original_request="Correggi il parser e mostra il risultato nella pagina")
```

Il default prepara il materiale locale senza chiamare il provider. Per chiedere
un giudizio reale usa `submit=True`, nel budget dell'incarico autorizzato.

CLI:

```sh
subtask review st-… --criterion 'Il parser gestisce input vuoti'
subtask review st-… --criterion 'Il parser gestisce input vuoti' --request-file richiesta.txt --submit
```

La chiave viene letta da `TYPESAFE_API_KEY`, dal file delle credenziali Subtask
oppure da `SUBTASK_JEV_KEY_FILE` (default `~/.config/typesafe/api-key`, file privato
del proprietario). Non entra nel pacchetto. `SUBTASK_JEV_MODEL` conserva il modello
richiesto; il responso registra anche quello risolto dal provider.

## Risultato e limiti

`supported`, `contradicted` e `unproven` sono consigli per criterio, non stati di
accettazione. Un report del worker da solo non dimostra né il risultato né il suo contrario. Una verifica
assente, negativa, riferita a un altro commit, a un comando diverso o con stato sporco registrato impedisce di
promuovere un criterio a `supported`. Gli esiti originari restano disponibili in
`provider_choice`: il coordinatore vede anche le limitazioni deterministiche.

`scope_assessment` controlla sempre la richiesta originale completa fornita dal
coordinatore con `original_request` (CLI: `--original-request` o `--request-file`).
Senza questo parametro lo scope resta `unproven`: il testo nei metadati del job
può essere stato modificato dal worker e non ricostruisce richieste cancellate.
Un elenco di criteri che dimentica la pagina non deve eliminare quel requisito.

I metadati condividono l'utente del sistema operativo con il worker: il livello
è `same_user_mutable_records`, non una attestazione. La verifica è etichettata
`runner_recorded_unattested`. Il controllo legge solo il candidato committato,
con `working_tree_inspected=false`; non certifica le modifiche locali successive.
Git legge oggetti e diff con monitor, hook, programmi esterni e recupero remoto
disabilitati. Il controllo rifiuta file non regolari, collegamenti simbolici e
stati con retry ancora pendente. Sono rifiutati anche `SUBTASK_HOME`, `jobs`, la
cartella del job e `reviews` se sono collegamenti simbolici; gli antenati di
`SUBTASK_HOME` non sono controllati (su macOS `/tmp` è già un collegamento di sistema).
`repo`, `branch` e `base` registrati devono essere stringhe.

Git non verifica l'hash degli oggetti che legge. Prima del diff il controllo legge
con `git cat-file --batch` i due commit, gli alberi con id diversi tra base e
candidato e i blob cambiati, e ricalcola l'id di ognuno (SHA-1 o SHA-256). Alberi
uguali sono saltati come fa `git diff`: il lavoro cresce con la consegna, non con
la storia. Un oggetto mancante, corrotto o sostituito produce `ValueError` prima di
salvare la richiesta e prima del provider: il contenuto originale non viene
ricostruito. Oltre 4096 oggetti o 16 MiB di contenuto il controllo rifiuta in modo
conservativo. Nessun comando della configurazione del candidato viene eseguito.
Il diff usa `--no-renames` e `--no-relative`, quindi non dipende da quelle impostazioni.

Git ignora anche `refs/replace`, forza la visualizzazione dei gitlink e impedisce
diff annidati dei submoduli. I contenuti binari e dei submoduli non sono giudicati:
`patch_content_not_inspected` impedisce un esito favorevole. Un diff non UTF-8 viene
rifiutato. Un puntatore Git LFS nel diff conta come contenuto non espanso.
Una verifica deve avere output registrato non vuoto, senza indicatori
espliciti di fallimento (`FAILED`, `FAILURES`, `AssertionError`, traceback Python
o riepiloghi come `1 failed` e `2 errors`). L'output è controllato sia così com'è
sia senza sequenze di colore ANSI.
Questa regola conservativa può lasciare non provati anche controlli silenziosi o
output con errori attesi. Il comando dell'oracle deve esistere e coincidere con quello richiesto.

Il riferimento `evidence_ref` identifica un materiale nel pacchetto salvato; il
diff contiene file e contesto delle righe, la verifica contiene comando, commit
ed estratto di output. Questo controllo non esegue nuovi test, non visita siti e
non certifica la sicurezza o il comportamento completo del prodotto. Una risposta
probabile di Jev non è una nuova prova indipendente.

La richiesta è limitata a 96 KiB, la risposta a 64 KiB. Il timeout di rete è 15
secondi per operazione. Materiale troppo grande viene rifiutato, non troncato come se fosse
completo. Lo stato e i criteri sono dati non fidati nel prompt del provider.
L'endpoint HTTPS è fisso; i redirect non vengono seguiti.

Ogni richiesta è legata al digest dei criteri e dei materiali prima della redazione
dei segreti (`binding_sha256`); il report include anche il digest dei byte originali.
`request_sha256` identifica invece la richiesta ripulita inviata al provider.
Un intento durevole, sincronizzato su disco,
precede l'invio. Richieste identiche, concorrenti o riprese dopo un guasto non
producono un secondo invio. Un timeout resta `unknown`. Se cambia il candidato
durante la chiamata, la risposta non viene associata al nuovo candidato.

Il costo fatturato resta `null`: non è disponibile una tariffa verificata nel
componente. Il controllo prenota il minore tra `SUBTASK_DEFAULT_MAX_USD` e
`SUBTASK_MAX_JOB_USD` nel registro mensile esistente, e lo contabilizza per intero
in via prudenziale. `budget_charged_usd` è questa contabilizzazione, non una
fattura né un limite imposto dal provider. Non cambia il tetto mensile.
Un limite del job inferiore alla prenotazione impedisce l'invio: un metadato del
worker non può ridurre l'accantonamento scelto dall'operatore.

Richieste e ricevute sono in `jobs/<id>/reviews/`: `<request_sha256>.request.json`
contiene la richiesta ripulita e `<binding_sha256>.json` la ricevuta. Il coordinatore
deve leggere la ricevuta relativa al materiale attuale; una vecchia ricevuta non
certifica una nuova consegna. La cancellazione delle ricevute non è una procedura
di retry: potrebbe causare nuovi addebiti.
Ogni controllo ripulisce anche richieste e ricevute già salvate con i segreti oggi
conosciuti. Una richiesta ripulita dopo la scrittura conserva il nome originale e
riceve `stored_copy_redacted_after_write` con `request_sha256` (digest della
richiesta originale, non dei byte conservati), `stored_sha256` (digest del
materiale conservato senza questo campo) e l'ora. Con più di 128 file salvati il
controllo rifiuta e chiede di archiviare i vecchi; ogni file è letto fino a 192 KiB.
Se un guasto avviene tra prenotazione e intento, serve riconciliazione manuale:
il componente rifiuta il secondo invio, senza presumere un esito del primo.

## Prove

`tests/test_delivery_review.py` copre materiale incompleto, affermazioni del worker,
risposta non valida, limite mensile, invio concorrente, intento interrotto e
candidato cambiato. I test del client indipendente esercitano entrambi i server
MCP. I test del provider usano risposte simulate; una prova reale è registrata
separatamente e non sostituisce la valutazione su consegne già giudicate.

Misura successiva: tempo del coordinatore, omissioni reali trovate, obiezioni
inutili, uso token e costo di tutti i ruoli. Nessun vantaggio di qualità o costo è
dichiarato da questa implementazione.

## Registro delle verifiche del 25 ottobre 2026

Ambiente: worktree `/root/wt/subtask-jev-delivery-20261025`, venv installata
in modo editable da quel checkout con `constraints.txt`, Python 3.14.8,
MCP 1.30.0, pytest 9.1.1 e Ruff 0.17.0. Nessun modello Codex viene avviato
dai test: le prove dell'adattatore usano un CLI simulato e il client MCP ufficiale.

| Revisione o input | Comando e directory | Esito | Invalidazione |
|---|---|---|---|
| Albero candidato `e5900345a8d0b99d4698505c0a413baf94798842` | `build-a-turno .venv/bin/python -m pytest -q -p no:cacheprovider --tb=short` nel worktree | 322 passati, 4 live esclusi, 283,33 s; `combined-full.log` | Codice, schema, test o dipendenze pertinenti |
| Stesso candidato, prima delle sole modifiche di resoconto | `build-a-turno .venv/bin/ruff check subtask tests` e `ruff format --check subtask tests` | Entrambi verdi; 85 file formattati | Codice Python o configurazione del controllo |
| Candidato combinato | Test mirati di controllo Jev, protocollo, client indipendente, guida e schema | 58 passati in 51,78 s; `combined-targeted.log` | Percorsi esercitati o dipendenze |
| `server.py` da `5ad997a` confrontato con `03c1cc8` | Stesso ritardo simulato di 200 ms nel tool, chiamato attraverso FastMCP | Ciclo eventi bloccato per 201 ms prima, disponibile dopo 21 ms con il fix | Percorso del tool FastMCP |

I log e gli inventari privati sono in
`/root/.local/state/subtask-jev-20261025/`. La suite legge il working tree:
l'albero sopra corrisponde all'indice verificato prima della revisione avversaria.
La revisione ha richiesto correzioni al codice: quelle prove non certificano il
candidato successivo. Verifiche finali, prova della wheel e installazione vengono
registrate separatamente al completamento.

### Candidato corretto dopo la revisione avversaria

Codice verificato: `deb485fdafe5246827eaa9d80d0c5e4835ddb8ce`, albero
`fa6399d0f66fe3e9a0541e7a351f5081d8ad7f3b`. La prima revisione indipendente
(`st-ddcce69b47`, test in `9ce8ded`) ha individuato quattro difetti alti, tre medi
e uno basso. Le correzioni e le regressioni sono nel candidato indicato.

| Prova | Comando o ambiente | Risultato |
| --- | --- | --- |
| Regressioni Jev | `build-a-turno .venv/bin/python -m pytest -q tests/test_delivery_review.py tests/test_delivery_review_adversarial.py` | 40 passati, `review4-final-targeted.log` |
| Suite finale | `build-a-turno .venv/bin/python -m pytest -q -p no:cacheprovider` | 340 passati, 4 live esclusi, 289,82 s; `final-full.log` |
| Controlli statici | Ruff 0.17.0 check e format; schema `--dump-tools` confrontato con `tools.json` | Verdi; `review4-lint.log`, `final-format.log` |
| Wheel installata in venv separata | `python -I final_wheel_smoke.py`, client MCP ufficiale senza progressToken | 56 file del pacchetto identici al sorgente; 16 strumenti e attesa conclusa su entrambi i server; nessuna API |
| Jev reale dalla wheel | `python -I final_live_smoke.py`, fixture Git isolata, registro spese canonico | `jev-1.14.0`, 2077 token input e 292 output; parser supportato, pagina e scope non provati; `final-live-smoke-report.json` |

Wheel: SHA-256 `ab6a16e5c97b888b606a06bc8c66c267ebd8c9acd8182038c6e99086282ac403`.
Il costo fatturato delle due chiamate reali dell'incarico resta sconosciuto;
il registro imputa in totale 4 USD prudenziali, senza cambiare il tetto esistente.
La prova MCP non esegue il modello Codex: il limite del client inattivo è verificato
nel sorgente ufficiale, come descritto in `progress-matrix.md`.

### Seconda revisione e correzione v5

La seconda revisione (`st-ec3610f12a`) ha respinto `deb485f`: due difetti alti,
cinque medi e uno basso. I test `25b14f71adbe69661b1a3e43fe84b9f9695565f2`
riproducono undici casi rossi. Il coordinatore ha riprodotto dieci casi prima
 dell'ultima aggiunta sui submoduli (`review5-before.log`); dopo le correzioni,
 tutti i 51 test Jev passano (`review5-after.log`). La policy diventa v5.

La correzione forza diff senza submoduli annidati o replace ref, rende visibili
le modifiche dei gitlink, limita i giudizi su contenuti non espansi e controlla
output e comando oracle. Ripulisce cache e intento prima della persistenza,
rifiuta la radice jobs simbolica, task di tipo errato e budget del job inferiore
all'accantonamento prudenziale. La fixture positiva ora registra davvero output
positivo; l'asserzione sulle verifiche incomplete controlla sia esito sia motivo.

Il test del clean filter descrive il limite dello snapshot, non dimostra da solo
l'assenza di esecuzione: il percorso avversario dei submoduli esercita quel difetto.
Resta il limite dichiarato dei metadati modificabili dallo stesso utente e della
riconciliazione manuale dopo un guasto tra prenotazione e intento.
Le prove complete e la wheel sopra appartengono a deb485f; vanno aggiornate prima
 di installare v5. La prova reale Jev precedente resta una prova API sulla policy v4.

### Terza revisione e correzione v6

La terza revisione (`st-4ccaeaa532`, test in `b5e9e40`) ha respinto `8434b44`: un
oggetto loose con contenuto diverso dal suo hash cambiava il diff senza cambiare il
commit. La policy diventa v6. La correzione verifica gli oggetti letti dal diff,
rifiuta prima del provider il materiale corrotto e limita il lavoro come descritto
sopra. Chiude anche i marker ANSI, i riepiloghi minuscoli, il collegamento simbolico
su `SUBTASK_HOME`, la pulizia delle richieste già salvate, LFS, l'oracle senza
comando e `repo` non stringa. `tests/test_delivery_review_integrity.py` copre questi
casi; il test dell'oggetto sostituito ammette il rifiuto controllato.

Limiti restanti: un processo dello stesso utente può cambiare gli oggetti tra
verifica e diff (`same_user_mutable_records`); la verifica non rileva un blob LFS
mancante, solo il puntatore; un crash tra ricevuta finale e chiusura del registro
richiede ancora riconciliazione manuale. Le prove complete e la wheel vanno
rifatte su questo candidato.

### Chiusura degli alberi ambigui e della cache Git

Il candidato Opus `aafedf3` ha superato 90 test Jev e la suite completa (390
passati, 4 live esclusi, `v6-full.log`), ma la revisione `st-f936375bcb` ha trovato
un aggiramento con entry omonime. Il coordinatore lo ha riprodotto anche a parte.
Il commit di prova `344c1d9` contiene due regressioni: albero ambiguo e cache
commit-graph alterata. Prima della correzione falliscono entrambe in 0,48 s;
dopo passano (`final-tree-before.log`, `final-tree-after.log`).

Il parser ora rifiuta nomi duplicati, percorsi o modalità illegali e ordine non
canonico, senza perdere entry prima di verificarle. Git ignora la cache con
`core.commitGraph=false`. Una prova su una directory reale verifica l'ordine
canonico di Git. I 97 test Jev passano in 9,37 s; la suite completa e la wheel
precedenti appartengono ad aafedf3, non ancora a questa correzione.


### Risultato finale installato

- Sorgente e test accettati: `3a469ba82e41fab6e9d53307962193c28d6ba9ed`,
  albero `63413df8a6d43c36249728f647ec3e0b35e0feaf`.
- Gate completo finale: `build-a-turno .venv/bin/python -m pytest -q -p no:cacheprovider`,
  **411 passati, 4 live esclusi in 288,77 s**, `release-full.log`.
- Ruff 0.17.0 check e format: verdi (91 file); schema MCP identico al dump.
- Ultima revisione indipendente `st-512365624d`: `severe_open=false` sul diff finale;
  i due aggiramenti sono rossi prima e verdi dopo, con 14 nuovi controlli di
  accettazione su alberi Git reali SHA-1/SHA-256. Gli altri difetti registrati
  sopra hanno regressioni distinte; restano i limiti dichiarati del controllo consultivo.
- Wheel installata: SHA-256
  `5bae513caa95c7ac29afd8344cc16be76fb6b45aa0c14461d456abc58881b40a`.
  Il confronto dell'installazione verifica 56 file identici, 16 strumenti e il
  parametro `original_request` su entrambi i server MCP. Nessuna API nello smoke.
- Prova reale sulla policy v6: `jev-1.14.0`, 2144 token input e 293 output;
  parser `supported`, pagina e scope `unproven`, `v6-live-smoke-report.json`.
- Skill Codex installata e confrontata con sorgente e tre copie dei plugin.
  Configurazione persistente `SUBTASK_MAX_RUNNING=10`; budget mensile invariato.

Ambiente: Linux, Python 3.14.8, venv editable con `constraints.txt` per i test;
venv separata e interprete `-I` per la wheel e la prova reale. Il client HTTP
installato coincide con il pacchetto verificato. Nessun modello Codex reale è
stato eseguito: il percorso adapter usa una CLI simulata, quello MCP il client
ufficiale. Il limite del turno inattivo è verificato nel sorgente Codex 0.164.0.

Contributi: OpenCode ha prodotto il percorso di consegna e le prove avversarie;
Opus ha corretto integrità e redazione (`aafedf3`); il coordinatore ha integrato,
corretto i due aggiramenti finali ed eseguito gate, wheel, prova reale e installazione.
Il registro completo dei sette ruoli è `final-accounting.json` nella directory
privata delle prove. Opus: 1,3003 USD registrati dal SDK. Tre chiamate Jev: 6 USD
prudenziali, fatturato sconosciuto. Il costo del coordinatore e la quota degli
abbonamenti non sono ripartiti; nessun risparmio economico è dimostrato.
Alle 12:47:14 UTC il registro mensile impegnava 230,7577 su 360 USD, con
129,2423 USD residui. Questa è una fotografia del registro, non una fattura.

La prima installazione era locale: main integrato, wheel installata tramite uv
e skill Codex aggiornata. Il successivo rollout CLI descritto sotto completa il
push e il reload supportato. La versione resta 0.5.0, senza un nuovo rilascio PyPI.

Ripristino pronto in `/root/.local/state/subtask-jev-20261025/rollback_install.py`:
backup di 63 file verificato prima dell'installazione, inventario dei file aggiunti
e copia della skill precedente. Il ritorno del pacchetto non cambia il limite 10
richiesto dall'operatore, i job o il registro delle spese.


### Rollout dei client CLI e correzioni CI

Il push iniziale `e4a2b7d` ha raggiunto GitHub; la CI ha trovato sei errori nelle
fixture: autore Git non configurato nei sottomoduli e tentativi di scrivere
oggetti Git di sola lettura. Il coordinatore li ha riprodotti su Linux togliendo
`DAC_OVERRIDE`/`DAC_READ_SEARCH`, senza identità globale e con `user.useConfigOnly`.
La nuova prova della pulizia processi riproduce inoltre il `PermissionError` di
`killpg` che mascherava la `ValueError` originale. Il fallback `Popen.kill()`
segnala soltanto il figlio posseduto e attende la sua uscita.

Codice corretto: `eec72e1103e980c486452db901c4a04f50b8ccf2`.
Revisore `st-b47f4c2273`: `severe_open=false`; sentinella estranea preservata,
figlio terminato, nessuna asserzione indebolita. Alla base i sei casi falliscono;
dopo il fix i sette casi direttamente modificati passano. Il comando mirato del
coordinatore include anche un controllo preesistente e conta otto passati.
I quattro file Jev interessati passano 58 test nell'ambiente con permessi limitati.

| Prova finale | Ambiente e comando | Esito |
| --- | --- | --- |
| Suite completa | Venv editable del worktree, Python 3.14.8; `build-a-turno .venv/bin/python -m pytest -q -p no:cacheprovider` | 412 passati, 4 live esclusi in 289,59 s; `fix-full.log` |
| Ruff e schema | Ruff 0.17.0 check/format, confronto `--dump-tools` | Verdi |
| Wheel e installazione | 56 file confrontati, client MCP ufficiale sui due backend | 16 strumenti, attese e prepare offline funzionanti |
| Reload Codex | API `config/mcpServer/reload`, configurazione Subtask con `SUBTASK_MCP_BUILD=eec72e1` | Nuovi processi con limite 10; catalogo effettivo di questa sessione da 15 a 16 strumenti |
| Tool reale nella sessione | `subtask.review` su `st-b47f4c2273`, `submit=false` | `prepared`, 0 USD registrati, nessuna chiamata al provider |
| OpenCode | Sostituzione del solo Subtask tramite API runtime in `/root` e `/root/arvos` | Connesso; PID del servizio gestito rimasto 2327792 |
| Claude e Claude2 | Marketplace aggiornato, skill del plugin e copie utente allineate; `claude mcp get plugin:subtask:subtask` su entrambi | Nuove connessioni connesse; cache delle chat già aperte da riconnettere con `/mcp` |
| Kimi | Skill e configurazione persistente, handshake MCP | Connesso, 16 strumenti; limite 10 |

Wheel corretta: SHA-256
`ab90a711e40afac83fa4a0fec8b8b2d35cb3886ca0c052d7bb44b5998592d58b`.
La correzione tocca la pulizia in errore e le fixture; la prova Jev reale precedente
resta quella registrata sul percorso di successo, senza nuove chiamate a pagamento.
La CI GitHub associata al push è il controllo degli altri interpreti Linux;
il suo esito è registrato nelle ricevute del rollout. Il job macOS
è sperimentale: i quattro errori di `test_invariants_ast.py` erano già presenti
nel run della base `1ceeeda`, prima di questa modifica.

I log e le ricevute di questo passaggio sono in
`/root/.local/state/subtask-jev-20261025/cli-rollout-20261025/`.
La wheel precedente verificata (SHA-256 `5bae513caa95c7ac29afd8344cc16be76fb6b45aa0c14461d456abc58881b40a`)
resta disponibile in `accepted-dist` per il ritorno. Modelli e tetti economici dei
client sono stati preservati; il limite di concorrenza richiesto è 10.

### Coerenza tra stop e attese concorrenti

La CI del commit `e85e4e6` ha riprodotto su Python 3.13 e 3.14 una corsa
preesistente: `wait` vedeva il worker morto e pubblicava `failed` mentre `stop`
stava ancora completando l'arresto. Il test di protocollo originale resta
invariato. Il primo candidato `86703bf` registra `stop_requested` sotto lock prima
del segnale. Le tre prove iniziali passano, ma la revisione `st-895d2d863f` lo
respinge: due nuove prove mostrano falsi `stopped` con figli vivi o PID riusati.
Il coordinatore riproduce anche un costo legacy azzerato invece che prudenziale.

Il candidato corretto `2237644` conserva l'intento, impedisce la finalizzazione
se il gruppo resta vivo e preserva `spend_possible` dei vecchi status durante
l'aggiornamento. Un decesso senza richiesta di stop conserva `failed` e il
conteggio prudenziale. Le prenotazioni restano aperte per gruppi vivi o sconosciuti.

Le tre prove di `tests/test_stop_delivery.py` falliscono sulla base `e85e4e6`
e passano sul fix. Due impongono l'interleaving morte del worker, `wait`, ritorno
di `stop`, con e senza spesa possibile. La terza conserva attivo un gruppo con
figli superstiti. I log sono `stop-delivery-before.log` e `stop-delivery-after.log`.
La prima suite conta 415 passati in 289,39 s (`stop-delivery-full.log`). I due
test del revisore falliscono sul candidato iniziale (`stop-review-before.log`),
come il nuovo test legacy (`stop-legacy-before.log`: 0 invece di 1,5 USD).
Le sei nuove prove sono verdi dopo la correzione, senza indebolire gli assert.
I 42 controlli mirati passano in 22,98 s (`stop-review-after.log`).
La suite completa sul candidato corretto conta 418 passati e 4 live esclusi in
288,27 s: `build-a-turno .venv/bin/python -m pytest -q`, log `stop-review-full.log`.
Ruff check/format resta verde su `subtask tests`; 93 file Python.

La wheel del candidato `2237644` ha SHA-256
`d35a5655b870e85f20fee7f640503a0a6665b80e6f5ece2f058b04c91ec4cf9f`.
Il confronto verifica 56 file del pacchetto. Il client MCP ufficiale esercita
entrambi i backend: 16 strumenti, attesa del risultato, preparazione Jev offline
e `wait`/`stop` simultanei, senza chiamate a provider. Il backup della wheel
precedente installata (`ab90a711...`) è in `cli-rollout-20261025/pre-stop-dist/`.

Restano due rischi preesistenti fuori dal delta: morte prima della registrazione
dell'intento e riuso del PID tra verifica e segnale al gruppo. Il rapporto del
revisore li distingue dalle regressioni corrette; non sono dichiarati risolti.

### Installazione finale e compatibilità con il ledger aggiornato

Il revisore finale `st-c7eb52eb03` approva `2237644` con `severe_open=false`:
42 test mirati passati in 23,02 s e quattro probe indipendenti verdi su figli
superstiti, identità sconosciuta, PID riusato e chiusura unica della prenotazione.
Il rapporto completo distingue i rischi preesistenti dal delta accettato.

Durante l'integrazione, un'altra sessione aveva già pubblicato `577ca9c`, che
aggiunge il motore alle righe del ledger. Il merge `9405d02` conserva entrambe
le modifiche; `subtask/jobs.py` è identico byte per byte al commit approvato.
La suite sul merge passa con 419 test e 4 live esclusi in 287,68 s, stesso comando
completo; `combined-final-full.log`. Ruff check/format e schema MCP sono verdi.

Wheel effettivamente installata: SHA-256
`420f8e17523d2cc7194fc7a6c17a7ba0dec6177b9ec16e1648e34363312d2357`.
I 56 file del pacchetto corrispondono al sorgente e all'installazione globale.
Entrambi i backend espongono 16 strumenti e passano attesa, prepare offline e
stop concorrente via client MCP ufficiale. Nessuna chiamata aggiuntiva a Jev.

Codex è stato ricaricato di nuovo con `SUBTASK_MCP_BUILD=9405d02`: nuovi processi
con limite 10, catalogo effettivo di questa sessione con 16 strumenti e `review`
provato offline su `st-c7eb52eb03` (`prepared`, nessun invio al provider).
OpenCode ha creato due nuovi processi Subtask, connessi in `/root/arvos` e `/root`,
con servizio gestito invariato. Il controllo attende lo stato `connected` dopo
il primo stato transitorio `pending`; la sola risposta HTTP 204 non basta.

Le nuove connessioni dei CLI usano il pacchetto aggiornato. Le chat Claude e
Claude2 già aperte conservano la propria connessione: richiedono `/reload-plugins`,
oppure la riconnessione del solo server da `/mcp`. Non sono state comandate
tramite la loro interfaccia interattiva. Il limite persistente è 10 nei client
configurati; i budget, i modelli e le credenziali restano quelli precedenti.

### Pulizia portabile della prova macOS

Il run GitHub `40213701814` passa su Linux 3.11–3.14 e nel pacchetto. Nel job
macOS sperimentale la nuova prova del PID passa le asserzioni, ma la sua pulizia
fallisce con `PermissionError` sul probe `killpg(..., 0)` mentre il figlio termina.
Il test `test_group_cleanup_survives_a_permission_race` riproduce lo stesso errore
su Linux con un figlio reale: rosso prima, verde dopo. Il helper ora raccoglie
il proprio figlio prima del probe e ritenta il controllo se la fine del gruppo
produce un errore transitorio di permessi. Le asserzioni avversarie restano intatte.

Le sette prove dei due file stop passano dopo la correzione. Questa modifica
riguarda solo la pulizia dei test: codice distribuito, wheel e installazione
restano quelli verificati sopra. La matrice CI del push successivo verifica
il test anche su macOS. Il run precedente mostrava inoltre un fallimento del
test hint preesistente, senza modifiche al suo percorso: non è dichiarato risolto.
