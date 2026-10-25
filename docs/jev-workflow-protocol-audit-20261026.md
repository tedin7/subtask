# Audit del protocollo prima dell'inferenza

Dodici casi esaminati senza risultati Jev. Nessun gold rigettato; un caso resta
sospeso fino al restringimento indicato. Questo audit non valuta qualità del
modello, integrazione o disponibilità nel servizio.

| Caso | Valutazione | Limite concreto |
| --- | --- | --- |
| routing-historical-snapshot | Restringere interpretazione | Dipendenza storica dallo snapshot; nessuna attesa dell'autore se già disponibile |
| routing-disjoint-write-trap | Restringere interpretazione | File scritti distinti non provano indipendenza delle letture |
| routing-missing-candidate | Valido | Identità omessa: astensione |
| ev-7c42 | Restringere; sospeso | `dirty_after=false` prova pulizia dopo il run, non prima o durante |
| ev-18a6 | Valido | Exit 1 contraddice successo; rollback distinto |
| ev-90df | Valido | Causa storica senza traccia: non provata |
| wf-83d1 | Restringere interpretazione | Verifica proposta pertinente; nessuna accettazione |
| wf-09a7 | Valido | Difetti aperti contraddicono chiusura completa |
| wf-b721 | Valido | Contratto omesso: autorizzazione non provata |
| risk-1 | Valido | Sola indagine; futura modifica codice richiede classificazione separata |
| risk-2 | Valido | Migrazione credenziali esplicita: sicurezza |
| risk-3 | Valido | Materiali collegati assenti: categoria sconosciuta |

Prima dell'invio, restringere C1 di ev-7c42: 116 test passati con HEAD registrato;
checkout registrato pulito **dopo** l'invocazione. Conservare gold `supported`.
In alternativa serve una ricevuta esplicita della pulizia iniziale.
Le altre restrizioni riguardano soltanto l'interpretazione nel resoconto.

Le guardie uguali al gold sono annotazioni manuali di un oracolo di regole,
non risultati misurati di una baseline eseguibile. Il controllo sempre
`unproven` e quello sui soli percorsi sono deboli. Un successo non dimostra
superiorità generale alle regole. Correggere questa descrizione prima degli invii.

I 193 test di wf-83d1 sono dichiarati dal rapporto sulla sorgente rivista;
i 172 del wrapper riguardano il commit del rapporto. Le fonti locali originali
confermano la distinzione. Il gold valuta l'azione proposta, senza usare
l'esito futuro. Gold, note, provenienza e guardie devono restare fuori dalle richieste.

Verifica: lettura JSON, documenti delle quattro fasi e ricevute locali pertinenti,
su base `de4eecf`. Nessuna chiamata remota, suite o modifica dei packet.
L'audit privato conserva hash dell'input ed esatta sostituzione proposta.

## Trovato strada facendo

- `docs/jev-workflow-evidence-20261026.md:36` — medio: la baseline privata è descritta senza distinguere annotazione manuale ed esecuzione misurata.

## Chiusura prima degli invii

Il coordinatore ha ristretto ev-7c42 alla pulizia dopo il run e ha spostato
le guardie manuali nella provenienza. HOLD risolto prima della preregistrazione;
nessun gold cambiato. I risultati successivi sono nel [resoconto](jev-workflow-20261026.md).
