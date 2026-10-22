# Kimi: quota e ragionamento per incarico — 22 ottobre 2026

## Risultato e perimetro

Il vecchio adapter restituiva sempre quota sconosciuta per Kimi. Cercava
l’autenticazione nella vecchia directory `.kimi` e ignorava `effort` nel comando.

La correzione legge il provider gestito dalla configurazione attuale sotto
`KIMI_CODE_HOME`, oppure `~/.kimi-code`. L’endpoint autenticato `/usages`
restituisce finestre indipendenti: il valore usato dal selettore è il minimo
residuo fra tutte le finestre disponibili, comprese quelle mensili.
Il lettore conserva anche i reset e distingue una misura mancante da zero.
Non aggiorna credenziali e non aggira errori di autenticazione.

I nuovi job senza modello esplicito usano `kimi-code/k3-256k`.
L’effort richiesto dal job viene passato con `KIMI_MODEL_THINKING_EFFORT`
solo al nuovo processo. Per questo incarico il valore autorizzato è `low`.
Il comando di ripresa non aggiunge un nuovo modello o un override di effort.
La configurazione globale e le sessioni già avviate restano invariate.

Il token è ammesso solo verso gli endpoint gestiti HTTPS di Kimi.
I redirect sono rifiutati. Errori HTTP, risposte non valide o troppo grandi
producono quota sconosciuta, senza includere token o corpo della risposta.

## Prove

- Base: `26dfd4d`, repository `tedin7/subtask`, Python 3.14.8.
- Tre regressioni falliscono sulla base originale: quota, autenticazione
  corrente e passaggio di `low`. I test mirati sul candidato passano.
- Risposta reale del 22/10: residuo settimanale 89,7%, finestra di cinque ore
  oltre 99%. Il dato è temporaneo, non una quota fissa o configurata.
- Revisore indipendente Kimi, job `st-1e1b960372`: nessun difetto grave,
  21 test offline superati. Non ha modificato file.
- Nel registro nativo della revisione, tutte le tre richieste `llm.request`
  riportano modello `k3-256k` e `thinkingEffort: low`.
- Il coordinatore ha aggiunto alla fixture offline `KIMI_CODE_HOME` temporaneo:
  i test simulati non devono leggere l’account reale. La prima suite è stata
  interrotta dal coordinatore dopo 178 passaggi per applicare questo isolamento.
- Ruff 0.17.0, fissato in `constraints.txt`: lint completo e formattazione
  completa superati. Ruff globale 0.16.9 segnalava un problema già presente
  nell’adapter Codex; non è stato usato per certificare il progetto.

Comandi finali:

```sh
build-a-turno uvx --from ruff==0.17.0 ruff check subtask tests
build-a-turno uvx --from ruff==0.17.0 ruff format --check subtask tests
build-a-turno /root/subtask/.venv/bin/python -m pytest -q -p no:cacheprovider
```

Suite completa sul candidato quota: 293 passati, un fallimento in
`test_foreach_refuses_too_many_or_missing_items`, 4 prove live escluse.
Il difetto è una corsa tra i due snapshot del padre e l’espansione foreach;
la correzione isolata è affidata al job Kimi `st-a56d167a12`.
Il gate combinato resta obbligatorio prima dell’integrazione su main.

Le prove private della sessione sono sotto
`/tmp/arvos-errori-20261022-audit-01`; nessun token è incluso nel commit.
Una nuova release del CLI, un cambio di schema della quota, di provider,
di autenticazione o del contratto delle variabili di ambiente richiede
di ripetere i controlli pertinenti. Non sono stati ampliati budget API.
