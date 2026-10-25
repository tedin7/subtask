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
