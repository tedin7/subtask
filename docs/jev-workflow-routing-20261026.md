# Instradamento prima della delega — 26 ottobre 2026

Preparati tre casi offline da incarichi reali; nessuna chiamata Jev eseguita.
Non dimostrano ancora un vantaggio semantico rispetto a un controllo deterministico.

## Campione e fonti

Inventario limitato ai 60 job locali più recenti per modifica della directory.
Le repliche `jev-replay` non sono fonti di nuovi incarichi. Sono emersi lotti
per sito, revisioni di candidati nominati, iterazioni dopo revisioni respinte
e consegne con rilascio riservato al coordinatore. Questi sono segnali di
instradamento, non prove di dipendenze universali tra lavori dello stesso sito.

La coppia scelta riguarda Subtask stesso, senza dati dei siti:

- `st-606d99d4c1`: implementazione del filtro, con scritture consentite al
  modulo e al suo test; verifica registrata su `bd693ad7`, 116 test verdi.
- `st-c9a86ab6d4`: revisione indipendente del candidato integrato `7df73fe`,
  legge filtro, integrazione e benchmark, scrive solo il rapporto Markdown.
  La verifica registrata riguarda `59c7fb18`, con 172 test verdi.

Il candidato integrato contiene modifiche successive al modulo: non si assume
che coincida con il commit iniziale dell'esecutore. La dipendenza è dal candidato
specificato nel mandato della revisione, non dalla sola identità dell'autore.
Gli hash SHA-256 dei metadati originali e le revisioni complete sono nei casi
privati; nessun comando storico viene rieseguito dal costruttore.

## Casi e verità attesa

| Caso | Proposta valutata | Verità | Tipo |
| --- | --- | --- | --- |
| historical-snapshot | Revisione a valle della creazione del candidato, sullo snapshot richiesto | supported | Mandato storico invariato |
| disjoint-write-trap | Revisione indipendente dalla creazione del candidato perché scrive file diversi | contradicted | Proposta controfattuale controllata |
| missing-candidate | Il checkout disponibile è certamente il candidato richiesto | unproven | Omissione controllata di identità candidato e checkout |

La verità deriva dal riferimento esplicito al commit nel mandato del revisore
e dal suo insieme di lettura, diverso dall'insieme di scrittura. Il terzo caso
richiede astensione: non ricostruisce un fallimento storico osservato.
Il primo conserva il mandato storico; gli altri sono adiacenti e correlati.
Tre risposte corrette non rappresenterebbero tre esempi indipendenti.

## Confronto e limiti

Il riferimento ingenuo confronta soltanto i percorsi di scrittura consentiti:
qui li vede disgiunti e conclude indipendenza. Il conflitto è invece tra
lettura dello snapshot candidato e sua disponibilità. Una semplice estrazione
deterministica dei riferimenti ai commit potrebbe già risolvere i primi due
casi. Quindi un eventuale successo Jev dimostrerebbe soltanto un vantaggio su
quel riferimento ingenuo, non una necessità del modello né capacità generale
di scoprire conflitti tra simboli. Nessun caso forte di conflitto fra simboli
è stato aggiunto senza prove sufficienti. Servono nuove coppie indipendenti
prima di trarre conclusioni quantitative o cambiare il runtime.

## Riproduzione locale

Nel worktree della campagna:

```bash
PYTHONPATH="$PWD" /root/subtask/.venv/bin/python -m bench.workflow_routing \
  --jobs /root/.local/share/subtask/jobs \
  --output /root/.local/state/subtask-jev-workflow-20261026/routing-cases.json
PYTHONPATH="$PWD" /root/subtask/.venv/bin/python -m pytest -q tests/test_workflow_routing.py
```

Esito: due test verdi. Il costruttore separa richiesta e verità privata,
registra provenienza e rende esplicita l'omissione. Non chiama API, non modifica
configurazione, non avvia job e non misura qualità dal vivo.

## Trovato strada facendo

- `bench/workflow_routing.py:path_only_independent` — limite metodologico: le scritture disgiunte ignorano dipendenze di lettura; riferimento volutamente debole, non classificatore di produzione.
