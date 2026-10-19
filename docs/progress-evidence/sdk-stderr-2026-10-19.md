# Subtask: diagnostica degli avvii Claude SDK

## Risultato e limiti

Candidato locale per conservare stderr dell'SDK anche quando query fallisce con un'eccezione generica. Non risolve né attribuisce il guasto reale di avvio Sonnet. Nessuna installazione o nuova chiamata di inferenza dopo il tentativo fallito. Review indipendente del candidato ancora mancante.

Repository responsabile Subtask, canonica /root/subtask, HEAD 26dfd4dbadea182de8f83f73e03ae87d69d8d3a8. Worktree /root/wt/subtask-recupero-mcp-20261019, base precedente candidato aa24f9c. L'adattatore Claude della base coincide byte per byte con quello canonico: SHA256 1a4d712fd9f36e1837d025955c2a03f47eaea14e2685f9ac0cd2e210e7aa71f8. Conservate le modifiche di recupero precedenti, non integrate nella canonica.

## Tentativo operativo conservato

Job st-abae5430b8, review readonly K01 949ef0ebed91ab117d5126ddc5cac66eae9be5a1, motore sonnet-api, modello registrato sonnet, effort medium, max_turns=1 e limite 0,04 USD entro i 0,05 USD residui esistenti. Stato terminale failed, detail exception: ProcessError, command exit 1, «Check stderr output for details». Nessun finding, verifica non eseguita, token mancanti. Nessuna review accettata. Non è stato fatto fallback a un altro motore.

Il ledger imputa charged_total=reserved=0,04 USD, observed=null: è una riserva interamente contabilizzata per consumo sconosciuto, non un costo fatturato dal provider dimostrato. Residuo indicato dal sistema 0,01 USD. Nessun tetto o riserva modificato, nessun replay. L'avviso «claude.ai connectors disabled» non prova la causa del fallimento. Il log del job non conserva il messaggio sottostante richiesto dall'eccezione.

La lettura GET https://api.anthropic.com/v1/models?limit=1, con la credenziale e l'header workspace già usati da Subtask, risponde 200 alle 08:51:11 UTC del 19 ottobre. È accesso ai metadati, non inferenza, prova di credito disponibile o verifica del modello Sonnet. Documentazione: https://platform.claude.com/docs/en/api/models/list. Nessuna chiave stampata, cambiata o copiata nel rapporto. Diagnosi della causa reale ancora UNKNOWN.

## Correzione candidata

ClaudeAgentOptions dell'SDK installato espone il callback stderr, assente nell'adattatore corrente. _StderrCapture raccoglie al massimo gli ultimi 262.144 caratteri in memoria; al termine o nel finally della query applica il redattore comune e salva al massimo 32.768 byte UTF-8 validi in worker-stderr.log, già riconosciuto da inspect(). Non copia il testo grezzo nei riepiloghi e non aggiunge retry. La memoria resta locale al tentativo.

La redazione riusa valori noti e pattern esistenti, non riconosce ogni possibile segreto sconosciuto e non è un confine di sicurezza. Il limite al buffer può troncare un messaggio molto lungo; il file è diagnostica parziale, non traccia completa. Il salvataggio avviene al termine, quindi non promette recupero della diagnostica dopo kill/crash del processo. Se la scrittura fallisce con OSError, persist restituisce false senza sostituire l'eccezione primaria o il risultato. Un tentativo senza stderr non riutilizza il vecchio testo.

## Prove offline

Python 3.14.8, pytest 9.1.1; SDK installato claude-agent-sdk 0.2.165. Nessuna dipendenza modificata. I test sostituiscono solo il modulo SDK e la lettura di credenziali/impostazioni, eseguendo _run reale. Nessuna chiave reale o rete nei test.

Cinque regressioni: avvio fallito con messaggio specifico e token sintetico; successo con usage e costo invariati; errore del disco che non maschera l'eccezione; buffer/file limitati e UTF-8 valido con token spezzato tra callback; log vuoto che non presenta un vecchio tentativo. Prima: 5 fallimenti, tra callback assente e nuovo helper assente. Dopo: 5 passati in 0,17 s. Il messaggio «unknown option --fixture» è esclusivamente sintetico, non la causa reale del job.

Comandi nel worktree:

- /root/subtask/.venv/bin/python -m pytest -q tests/test_claude_stderr.py
- build-a-turno /root/subtask/.venv/bin/python -m pytest -q
- ruff check subtask tests
- ruff format --check subtask tests
- /root/subtask/.venv/bin/python -m subtask.server --dump-tools, confronto con subtask/tools.json

xdist non installato: nessun -n disponibile. Suite con addopts -m 'not live', nessuna chiamata live implicita. I log privati, l'esito di accesso ai metadati e l'envelope del tentativo sono in /root/.local/state/todo-subtask-stderr-20261019/. La CI multipiattaforma/Python 3.11–3.14 e il pacchetto installato non sono qualificati da questa prova locale.

Invalidano la prova cambiamenti ad adattatore, redattore, contratto SDK, fixture o ambiente. Restano review indipendente, integrazione, installazione e prova di avvio reale separatamente autorizzata e coperta da quota. Non dichiarare la review K01 sbloccata dal verde di questi test.

## Esito finale del candidato locale

Suite completa: 292 passati, 4 live deselezionati in 277,96 s. Comprende i cinque nuovi casi; non sommare le successive cinque ripetizioni mirate come test distinti. La sola pulizia finale del test sostituisce lambda: {} con dict, senza cambiare il comportamento; i cinque casi sono stati ricontrollati (0,15 s). Codice dell'adattatore invariato rispetto alla suite completa.

Formato: 89 file già formattati. Schema MCP generato identico a subtask/tools.json. Lint dei due file modificati passato. Lint globale con il ruff corrente NON passato: DTZ007, subtask/engines/codex.py:112, datetime.strptime senza timezone esplicito. Nessuna modifica in quel file: SHA256 d657927a2cad2f7ddf76e604c0032cd4df3119de78ad5a1c4ea20d05b4f36d7d coincide con aa24f9c. Quindi il controllo corrente rileva un problema preesistente, non una regressione del candidato stderr; il precedente resoconto verde non sostituisce questo risultato. Non corretto fuori perimetro e non nascosto con una soppressione.

| Input/revisione | Comando e directory | Ambiente | Esito/log | Invalidazione |
|---|---|---|---|---|
| Base aa24f9c, nuovo test | python -m pytest -q tests/test_claude_stderr.py nel worktree | SDK finto, nessuna rete | 5 falliti, before.log | Cambiamento requisito/fixture |
| Candidato stderr | stesso comando | Stesso interprete | 5 passati; dopo pulizia 5 passati | Modifiche di comportamento, SDK o redattore |
| Candidato stderr | build-a-turno /root/subtask/.venv/bin/python -m pytest -q | Python 3.14.8, xdist assente | 292 passati, 4 deselezionati; full.log | Codice/ambiente rilevante cambiati |
| File modificati | ruff check subtask/engines/claude_api.py tests/test_claude_stderr.py | Ruff host | Passato | File/regole Ruff cambiati |
| Progetto | ruff check subtask tests | Ruff host | Un DTZ007 preesistente in codex.py | File/regole Ruff cambiati |
| Progetto | ruff format --check subtask tests; dump-tools e diff | Ambiente locale | Passati | File/schema/ambiente rilevante cambiati |

La base installata resta 26dfd4d: nessun worker o MCP è stato ricaricato. Il nuovo commit è un candidato supplementare sopra aa24f9c, non la revisione accettata dal coordinatore indipendente. La review necessaria resta aperta e l'avvio Sonnet reale resta da diagnosticare. Non usare questi test simulati come prova del consumo, dell'autenticazione per inferenza o dell'avvenuta revisione K01.
