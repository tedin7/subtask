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
stati con retry ancora pendente.

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
`SUBTASK_MAX_JOB_USD` e il limite del job, se presente, nel registro mensile esistente, e lo contabilizza per intero
in via prudenziale. `budget_charged_usd` è questa contabilizzazione, non una
fattura né un limite imposto dal provider. Non cambia il tetto mensile.

Richieste e ricevute sono in `jobs/<id>/reviews/`: `<request_sha256>.request.json`
contiene la richiesta ripulita e `<binding_sha256>.json` la ricevuta. Il coordinatore
deve leggere la ricevuta relativa al materiale attuale; una vecchia ricevuta non
certifica una nuova consegna. La cancellazione delle ricevute non è una procedura
di retry: potrebbe causare nuovi addebiti.

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
