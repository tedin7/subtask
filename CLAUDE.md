# subtask: istruzioni per gli agenti

- Test: lancia solo i file che tocchi, con un worker, dalla copia in cui lavori. Le copie in `/root/wt` non hanno `.venv`:
  usa quello del checkout principale con `PYTHONPATH=.`, cioe
  `PYTHONPATH=. /root/subtask/.venv/bin/python -m pytest -n 0 -q tests/test_file.py` (dettagli e marcatori in `CONTRIBUTING.md`).
  Offline, motori finti: `PYTHONPATH=. /root/subtask/.venv/bin/python -m pytest -n 0 -q -p no:cacheprovider`.
- Controlli di stile: `ruff check subtask tests` e `ruff format --check subtask tests`.
  Se cambi gli strumenti MCP, rigenera/controlla `subtask/tools.json` con
  `python -m subtask.server --dump-tools`.
- Rilascio: segui `RELEASING.md` (bump con `scripts/bump_version.py`, controlli locali, tag annotato `vX.Y.Z`;
  il tag non aspetta la CI, quindi i controlli si fanno prima). Rollback nella stessa pagina.
- Ramo predefinito: `main`. Lavora in un worktree in `/root/wt/<nome>`, non nel checkout `/root/subtask`.
- Niente segreti nel codice o nei log.

## Come leggere le mie richieste (dal 16/11/2026)

- Scrivo in fretta e impreciso. Ricostruisci l'obiettivo, non eseguire le parole.
- Prima di un lavoro grande scrivi in poche righe: cosa hai capito, cosa è ambiguo, cosa contraddice decisioni già prese.
- Se un'ambiguità cambia il risultato, fermati e chiedi. Una domanda alla volta, la più importante per prima. Se non lo cambia, scegli tu, dillo in una riga e vai.
- Non fermarti alla prima soluzione che soddisfa le parole (minimo locale). Cerca la migliore raggiungibile con il tempo macchina e gli strumenti che hai.
- Prima di chiudere chiediti: cosa resta aperto? cosa avevo proposto e non ho fatto? c'è una strada che dà di più? Elenca quello che resta, non solo quello che hai fatto.
- Ogni punto che proponi finisce fatto, in una voce con data, o in una domanda per me. Mai lasciato a metà in chat.
- I token non sono un limite: lavoro lungo, sotto-agenti (rispettando il carico della macchina), prove e riletture sono voluti quando migliorano il risultato.
- Confine: vale quando ti scrivo io in chat. Un agente lanciato dal loop, o la chat del loop, non fa domande che aspettano: scrive il dubbio nel rapporto o in una voce e va avanti con il resto.
