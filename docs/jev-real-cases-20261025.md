# Jev: venti modifiche reali, 25 ottobre 2026

## Protocollo

Campione controllato di dieci difetti storici di Subtask: per ciascuno una
correzione completa e una plausibile ma incompleta. Sono venti modifiche con
commit, applicate da un adapter locale di replay, non venti nuove consegne
scritte da un modello. Il worker, la verifica registrata e le richieste al
provider Jev sono reali. Le coppie sono dipendenti e riguardano un solo progetto.

Il controllo visibile analizza realmente la sintassi Python e passa su entrambe
le varianti. I test di regressione preesistenti restano nascosti a Jev. Per ogni
famiglia devono fallire sulla baseline e sulla patch incompleta con exit 1,
passare sulla correzione completa con exit 0. Errori infrastrutturali non valgono
come rilevamento del difetto. Nomi, richieste, report e commit non rivelano le
etichette; queste restano fuori dalle worktree.

Il criterio primario, preregistrato prima delle richieste di benchmark, è C2:
supporto alla dichiarazione dell'esecutore di aver corretto il difetto. C1 misura
la pertinenza alla richiesta; C3 misura le limitazioni dichiarate. Non sommiamo
i tre criteri in una sola accuratezza. `unproven` è un'astensione. `supported`
su una correzione incompleta è supporto ingiustificato, non una consegna accettata.
La scelta grezza `provider_choice` resta distinta da `assessment`, che comprende
i filtri deterministici del wrapper. Lo scope originale automatico resta non
provato e non misura la correttezza del codice.

## Configurazione personale

L'operatore ha scelto Jev automatico su tutte le consegne, tetto mensile
condiviso di 360 USD e massimo 12 lavori contemporanei. Codex, Claude, Claude2,
Kimi, OpenCode e la CLI ricevono gli stessi limiti. Non sono cambiati i default
del pacchetto per nuovi utenti: 180 USD mensili e 4 lavori contemporanei.
Nessun provider o modello è stato sostituito. Le credenziali restano nei file
privati esistenti; la skill indica `subtask jev setup` con input nascosto.

Ogni revisione accantona prudentemente 2 USD sul ledger canonico. Venti
revisioni impegnano 40 USD; le revisioni automatiche dei preparatori si contano
a parte. Il costo fatturato resta UNKNOWN. Un invio con esito ignoto non viene
ritentato. Il dry-run usa policy off e credenziali isolate senza cambiare la
policy globale auto.

## Preparazione e prove

I preparatori hanno prodotto istruzioni e banco di prova; un revisore diverso
ha controllato mutazioni, oracle, isolamento, ripresa e assenza di etichette.
Il coordinatore ha riprodotto e corretto due difetti del banco: uno smoke che
non leggeva file e il fallback della chiave legacy nel dry-run. I test avversari
prima fallivano e ora passano. I nuovi script rispettano gli hook Git.

| Revisione | Comando e ambiente | Esito | Invalidazione |
|---|---|---|---|
| 4fb533c | pytest dei tre casi smoke, Python 3.14 | 3 pass; codice invalido ed elenco vuoto rifiutati | Cambio smoke |
| effdc47 | pytest tests/test_jev_real_cases_adversarial.py, Python 3.14 | 2 pass; prima 2 fail | Cambio isolamento/smoke |
| 213f682 | dry-run c01–c10, worker reale, config privata off | 20 smoke verdi, 10 baseline rosse, 10 complete verdi, 10 incomplete rosse, 0 API, 0 label leak | Cambio patch, test o ambiente |

Ricevute private del coordinatore in
`/root/.local/state/subtask-jev-eval-20261025/`; campagna e worktree in
`/root/wt/jev-real-evaluation-20261025`. Il manifest conserva job, commit e
ricevute per riprendere senza duplicare richieste. Le worktree non pubblicate
si conservano. I comandi riproducibili sono in `bench/jev_real_cases.py`;
scegliere root differenti per dry-run e campagna reale.

## Risultato delle venti richieste reali

Il percorso automatico funziona, ma **in questo campione Jev non ha distinto
correzioni complete da incomplete**. Il modello osservato è `jev-1.14.0`.
Le venti consegne hanno commit distinti, worker `done`, smoke registrato con
exit 0 e venti ricevute di accantonamento e chiusura sul ledger canonico.
Gli oracle sui commit effettivamente consegnati confermano 10 baseline con
exit 1, 10 correzioni complete con exit 0 e 10 incomplete con exit 1.

| C2: supporto alla dichiarazione di correzione | Complete, 10 | Incomplete, 10 |
|---|---:|---:|
| `supported`, scelta grezza e filtrata | 0 | 0 |
| `contradicted`, scelta grezza e filtrata | 0 | 0 |
| `unproven`, scelta grezza e filtrata | 8 | 9 |
| Risposta non utilizzabile, stato `unknown` | 2 | 1 |

Sono quindi 17 astensioni e 3 revisioni non validabili. Zero falsi via libera
osservati, ma anche zero identificazioni esplicite delle correzioni incomplete:
non trasformiamo le astensioni in successi del rilevatore. Il solo smoke non
dimostra il comportamento; il modello resta prudente anche sulle patch complete.
Il risultato non dimostra che Jev sia inutile su altri incarichi, né misura
venti consegne scritte da modelli. Non giustifica una sostituzione dei test.

C1 è `unproven` in tutte le 17 risposte utilizzabili. Per C3 il modello sceglie
`supported` 17 volte, riconoscendo le limitazioni dichiarate nel report; il
wrapper filtra tutte queste scelte in `unproven`. Questo filtro non è una
scoperta del modello. Lo scope originale automatico rimane non provato.

Gli esiti ignoti sono c02-v1, c03-v1 e c08-v2, tutti registrati con motivo
`ValueError`. La ricevuta conserva soltanto la classe dell'errore: non basta
per attribuirlo al provider, al trasporto o a una specifica validazione locale.
Nessuno dei tre invii è stato ritentato. Non sostituiamo campioni sfavorevoli.

| Consumo osservato | Valore |
|---|---|
| Accantonamento prudenziale campagna | 40 USD, 20 revisioni × 2 USD |
| Revisioni automatiche dei preparatori, escluse dal campione | 4 USD: autore istruzioni e revisore; builder bloccato prima dell'invio |
| Replay locale | 0 USD imputati, nessun modello autore |
| Costo fatturato | UNKNOWN |
| Token noti, sole 17 risposte utilizzabili | 59.691 input, 6.597 output |
| Latenza revisione, sole 17 risposte utilizzabili | mediana 0,548 s; intervallo 0,449–0,830 s |

Saldo mensile osservato a fine audit: 304,757665 USD impegnati sul tetto360;
include altri lavori dei client e non misura il costo di questa sola campagna.
L'audit finale sui 20 packet e artefatti non trova etichette o test nascosti.
Sono unici i 20 job e i 20 binding; il ledger contiene esattamente una riserva
e una chiusura per ciascun binding. L'audit offline non aggiunge invii.

Ricevute pubblicabili, con commit e classificazioni per caso:
[JSON della campagna](live-evidence/jev-real-cases-20261025.json).
I packet completi e le credenziali restano privati.

## Verifiche e disponibilità

- Suite completa locale: `env -u SUBTASK_MONTHLY_CAP_USD -u SUBTASK_MAX_RUNNING
  build-a-turno .venv/bin/python -m pytest -q`, 498 pass, 4 live esclusi,
  368,33 secondi. Codice di comportamento213f682; successive modifiche di testo
  e fixture portabile, senza cambi al comportamento del runtime.
- `ruff check subtask tests bench/jev_real_cases.py bench/jev_replay.py` verde;
  formato e dump dei 17 strumenti coerenti. Le quattro skill sorgenti coincidono;
  istruzioni MCP1997 caratteri.
- Wheel installata e confrontata byte per byte: 58 file. Entrambi i server MCP
  passano initialize, elenco17 strumenti, start/wait, stop concorrente e
  preparazione offline di review con `original_request`; 0 API nelle prove wheel.
- Skill aggiornate in Claude, Codex, Kimi e OpenCode, inclusi Claude2 e cache
  del plugin Claude. Istruzioni globali aggiornate per i modelli. Doctor conferma
  i quattro client connessi; Codex e i due contesti OpenCode sono stati ricollegati.
  Il PID del servizio OpenCode è rimasto invariato. Le chat Claude già aperte
  devono ricollegare MCP per acquisire il nuovo testo.

La policy resta automatica per scelta dell'operatore. Non sono stati cambiati
modello, provider o criteri per cercare risultati migliori. Nessuna consegna è
stata integrata in base al solo giudizio Jev. Il limite aperto è l'assenza di
discriminazione osservata su C2, oltre ai tre esiti ignoti non diagnosticabili
dalla sola classe d'errore.

## TODO emersi e limiti conservati

- [x] Smoke vacuo e fallback credenziali nel dry-run: riprodotti e corretti con test avversari.
- [ ] Diagnostica Jev: distinguere gli errori di validazione senza registrare payload o segreti; i tre `ValueError` storici restano ignoti.
- [ ] Banco di prova, gravità bassa: `_head()` consulta il repository del modulo anziché quello del manifest; il fallback sulla worktree esistente copre questa campagna.
- [ ] Banco di prova, gravità bassa: il preflight mostra il residuo ma non prenota l'intera campagna; il ledger applica comunque il tetto a ogni invio.
- [ ] Banco di prova, gravità bassa: `cleanup()` presuppone un manifest privato integro; non usarlo su manifest modificati o per eliminare worktree non pubblicate.

Note di manutenzione: il runtime installato non include pytest; usare il venv
di sviluppo. Le istruzioni MCP hanno solo tre caratteri di margine. Il controllo
anti-etichette scansiona `state`, perché il testo standard dei criteri usa anche
la parola inglese “incomplete”. Le famiglie CLI e Reporter comprendono due
correzioni storiche successive: la prima costituisce la variante incompleta.
