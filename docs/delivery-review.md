# Controllo consultivo delle consegne con Jev

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
