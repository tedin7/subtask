# subtask: istruzioni per gli agenti

- Test: lancia solo i file che tocchi, con un worker, dalla copia in cui lavori:
  `.venv/bin/python -m pytest -n 0 -q tests/test_file.py` (dettagli e marcatori in `CONTRIBUTING.md`).
  Offline, motori finti: `.venv/bin/python -m pytest -n 0 -q -p no:cacheprovider`.
- Controlli di stile: `ruff check subtask tests` e `ruff format --check subtask tests`.
  Se cambi gli strumenti MCP, rigenera/controlla `subtask/tools.json` con
  `python -m subtask.server --dump-tools`.
- Rilascio: segui `RELEASING.md` (bump con `scripts/bump_version.py`, controlli locali, tag annotato `vX.Y.Z`;
  il tag non aspetta la CI, quindi i controlli si fanno prima). Rollback nella stessa pagina.
- Ramo predefinito: `main`. Lavora in un worktree in `/root/wt/<nome>`, non nel checkout `/root/subtask`.
- Niente segreti nel codice o nei log.
