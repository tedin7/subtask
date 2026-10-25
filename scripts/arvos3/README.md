# T-0892: installazione candidata e ritorno all'installato

Procedura preparata per il coordinatore. Non eseguire sul runtime reale senza
autorizzazione al rilascio. Nessun comando riavvia l'MCP. Prima del rilascio e del
ritorno il coordinatore deve assicurare che nessun processo usi o modifichi il
pacchetto; un processo già in memoria conserva i moduli precedenti.

I tre file `.sh`/`.py` devono rimanere nella stessa cartella. La copia consegnata
è in `/root/treniamo-calo-20261020-dati/giro5/codex/ARVOS3-prove/`.

## Installazione (comandi consegnati, non eseguiti sul runtime reale)

```sh
set -eu
prove=/root/treniamo-calo-20261020-dati/giro5/codex/ARVOS3-prove
ledger_backup=$(sh "$prove/installa-ledger.sh")
printf '%s\n' "$ledger_backup" > "$prove/ledger-backup.path"
```

Lo script verifica la wheel candidata (SHA256
`71b988983c16d46b0a63bd51eab8dcbd41ec6eb7b4cf8ba2bba92434198976d1`).
Subito prima di installarla crea una directory privata
`/root/.local/state/subtask-rollback-<dataUTC>-<suffisso>/`. Salva l'intera
cartella `subtask`, inclusi eventuali file locali e bytecode, la relativa
`dist-info` e i tre eseguibili dichiarati dal pacchetto. Mantiene contenuti e
permessi; non copia dipendenze, credenziali, configurazione o dati del ledger.
Rifiuta link simbolici e file che cambiano durante la copia.

`manifest.json` registra percorsi, SHA256 e permessi, `SHA256SUMS` contiene
l'hash del manifest, `FILES.sha256` contiene gli hash dei singoli file.
Il confronto sorgente/copia deve passare prima di chiamare `uv pip`.
`uv` usa `--offline --no-deps --reinstall --link-mode copy`.

Lo snapshot è annunciato su stderr prima dell'installazione, anche se questa
fallisce. Solo il percorso finale va su stdout, compatibile con la cattura
`ledger_backup=$(...)`. In caso di fallimento recuperare il percorso annunciato;
non ripetere alla cieca l'installazione, che salverebbe un diverso stato.

## Ritorno esatto

```sh
set -eu
prove=/root/treniamo-calo-20261020-dati/giro5/codex/ARVOS3-prove
ledger_backup=$(cat "$prove/ledger-backup.path")
sh "$prove/rollback-ledger.sh" "$ledger_backup"
```

La copia appartiene allo stesso venv: il percorso è registrato e verificato.
Prima di rimuovere file, il ritorno verifica l'hash del manifest e ogni file
salvato. Rimuove le cartelle e gli eseguibili del pacchetto candidato, copia
quelli salvati e confronta l'intero inventario, inclusi metadati e permessi.
Non usa wheel storiche e non presume quale firma avesse `ledger.reserve`.

Verifica ripetibile, senza importare il pacchetto:

```sh
/root/.local/share/uv/tools/subtask-mcp/bin/python -I -B \
  "$prove/installed_snapshot.py" verify "$ledger_backup"
(cd "$ledger_backup" && sha256sum -c SHA256SUMS && sha256sum -c FILES.sha256)
```

Il ritorno non è atomico: in caso di errore di copia mantenere fermo l'uso del
pacchetto, conservare snapshot e messaggio e completare il recupero. I controlli
confermano contenuti e permessi, non inode, proprietario o timestamp di accesso.
La directory privata e gli hash proteggono da alterazioni accidentali; non sono
una firma contro un attaccante che possa riscrivere backup e checksum.

## Prova isolata

```sh
build-a-turno /root/subtask/.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_installed_snapshot.py
```

La prova crea un venv senza dipendenze, una cache uv privata e copie senza
hardlink; aggiunge al baseline byte locali assenti dalla wheel, modifica un
eseguibile e aggiunge metadati. Installa davvero il candidato nel venv di prova,
ripristina e confronta i file, esclude residui del candidato e verifica il rifiuto
di backup alterati prima della mutazione. Gli override `RUNTIME_PYTHON`,
`ROLLBACK_PARENT` e `UV_CACHE_DIR` servono per quella prova.
