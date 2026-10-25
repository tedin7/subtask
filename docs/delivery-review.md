# Controllo consultivo delle consegne con Jev

## Contratto

Il coordinatore confronta una consegna terminale con 1–12 criteri scritti dal
chiamante. Il controllo usa richiesta originale, rapporto del worker, diff Git e
verifica indipendente già registrata. Ogni criterio restituisce una valutazione e
un riferimento a uno di questi materiali. Non avvia shell indicate nei materiali,
non modifica il lavoro e non accetta, integra o rilascia il candidato.

Repository: `tedin7/subtask`. Il componente Fabric non è una dipendenza.
Il provider resta Jev, tramite il contratto ufficiale
<https://docs.typesafe.ai/api/system-one>. Questa fase è osservazione: anche un
responso favorevole richiede la decisione del coordinatore.

## Uso

MCP:

```python
review(id="st-…", criteria=["Il parser gestisce input vuoti", "La pagina mostra il risultato"])
```

Il default prepara il materiale locale senza chiamare il provider. Per chiedere
un giudizio reale usa `submit=True`, nel budget dell'incarico autorizzato.

CLI:

```sh
subtask review st-… --criterion 'Il parser gestisce input vuoti'
subtask review st-… --criterion 'Il parser gestisce input vuoti' --submit
```

La chiave viene letta da `TYPESAFE_API_KEY`, dal file delle credenziali Subtask
oppure da `SUBTASK_JEV_KEY_FILE` (default `~/.config/typesafe/api-key`, file privato
del proprietario). Non entra nel pacchetto. `SUBTASK_JEV_MODEL` conserva il modello
richiesto; il responso registra anche quello risolto dal provider.

## Risultato e limiti

`supported`, `contradicted` e `unproven` sono consigli per criterio, non stati di
accettazione. Un report del worker da solo non dimostra né il risultato né il suo contrario. Una verifica
assente, negativa, riferita a un altro commit, o un candidato sporco impedisce di
promuovere un criterio a `supported`. Gli esiti originari restano disponibili in
`provider_choice`: il coordinatore vede anche le limitazioni deterministiche.

Il riferimento `evidence_ref` identifica un materiale nel pacchetto salvato; il
diff contiene file e contesto delle righe, la verifica contiene comando, commit
ed estratto di output. Questo controllo non esegue nuovi test, non visita siti e
non certifica la sicurezza o il comportamento completo del prodotto. Una risposta
probabile di Jev non è una nuova prova indipendente.

La richiesta è limitata a 96 KiB, la risposta a 64 KiB, il tempo di rete a 15
secondi. Materiale troppo grande viene rifiutato, non troncato come se fosse
completo. Lo stato e i criteri sono dati non fidati nel prompt del provider.
L'endpoint HTTPS è fisso; i redirect non vengono seguiti.

Ogni richiesta è legata al digest dei criteri e dei materiali. Un intento durevole
precede l'invio. Richieste identiche, concorrenti o riprese dopo un guasto non
producono un secondo invio. Un timeout resta `unknown`. Se cambia il candidato
durante la chiamata, la risposta non viene associata al nuovo candidato.

Il costo fatturato resta `null`: non è disponibile una tariffa verificata nel
componente. Il controllo prenota il minore tra `SUBTASK_DEFAULT_MAX_USD` e
`SUBTASK_MAX_JOB_USD` nel registro mensile esistente, e lo contabilizza per intero
in via prudenziale. `budget_charged_usd` è questa contabilizzazione, non una
fattura né un limite imposto dal provider. Non cambia il tetto mensile.

Richieste e ricevute sono in `jobs/<id>/reviews/<digest>.*.json`. Il coordinatore
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
