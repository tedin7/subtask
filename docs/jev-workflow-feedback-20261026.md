# Feedback del coordinatore prima di un nuovo tentativo

Documento di preparazione. [Risultati delle prove dal vivo](jev-workflow-20261026.md).

## Ipotesi e metodo

Un giudizio consultivo sulla prossima istruzione del coordinatore può distinguere
una verifica pertinente, una falsa chiusura e un'istruzione priva del contratto
necessario. Questo esperimento prepara tre casi offline; non misura ancora la
qualità di Jev, il risparmio o l'effetto causale sul risultato del lavoro.

Il campione è una scansione dei 60 job locali più recenti per mtime della directory,
congelata il 26 ottobre 2026. Il criterio non è la data di creazione: nuove scritture
possono spostare job vecchi nella finestra. Sei file `reply-N.md` appartengono a
cinque job. Tre job offrono sequenze brevi e verificabili di richiesta, feedback e
risultato successivo. Sono stati letti spec, status, reply e risultati pertinenti;
nessun comando suggerito dagli esecutori è stato eseguito.

Ogni caso contiene una sola domanda C1 sulla concreta azione proposta. Le etichette
sono `supported`, `contradicted`, `unproven`. Gold, guard, identificatori dei job,
revisioni e SHA-256 degli artefatti privati restano fuori da `request`. Al modello
va inviata solo `request`. I casi hanno identificatori opachi.

| Caso | Forma | Gold | Prova disponibile |
| --- | --- | --- | --- |
| wf-83d1 | Feedback storico parafrasato, senza mutazioni sostanziali | supported | Revisione iniziale con difetto grave; feedback verso sorgente e checkout esatti; risultato successivo con 193 test e nessun difetto grave aperto |
| wf-09a7 | Variante controllata che dichiara tutto risolto | contradicted | Sei test della fixture verdi, ma revisione finale respinta con tre difetti concreti di deploy |
| wf-b721 | Variante controllata con contratto e risultato successivo omessi | unproven | Il modulo ha 87 test verdi, ma il packet non prova che aggiungere l'adattatore sia una correzione autorizzata della richiesta originale |

Il terzo caso deriva da una sequenza reale: l'adattatore fu poi richiesto
esplicitamente e il risultato dichiarò 116 test verdi. Questi dati sono esclusi
dal packet, perché usarli trasformerebbe l'astensione in una risposta suggerita.
La variante non dimostra un difetto storico del coordinatore.

## Baseline e limiti

Baseline deterministica preregistrata: rispondere sempre `unproven`, senza leggere
il packet. Ottiene 1/3 sui tre gold. Serve come controllo minimo di astensione,
non come stima della normale revisione del coordinatore. Il numero di casi non
consente conclusioni statistiche o confronti di velocità/costo.

Il caso di falsa chiusura è volutamente esplicito: un controllo deterministico
che vieta la chiusura in presenza di difetti aperti può già risolverlo. Un successo
di Jev non proverebbe un vantaggio rispetto a tale controllo.

I risultati degli esecutori sono ricevute storiche, non prove ripetute ora.
Nel primo caso i 193 test sulla sorgente sono dichiarati nel rapporto; la verifica
automatica del wrapper esegue invece 172 test sul commit del rapporto. Queste due
prove non sono equivalenti. Nel secondo caso i test di fixture non provano il
comportamento del servizio pubblico. Nessun rilascio o accesso alla produzione
è stato eseguito per questo esperimento.

Le varianti e i gold sono fissati prima di eventuali invii. Nessuna chiamata a
modelli, API o rete è stata fatta. Un eventuale invio dall'esito ignoto deve essere
riconciliato tramite identificatore stabile, senza ripeterlo. Il giudizio C1 resta
consultivo e non autorizza integrazione, rilascio o spesa.

## Verifica locale

Revisione di partenza del worktree: `709f7b5`. Ambiente: Python 3 locale,
nessuna chiamata esterna. Controllo strutturale dei tre packet: JSON valido,
identificatori unici, etichette previste, gold e provenienza separati da request,
SHA-256 uguali agli artefatti sorgente. Esito: tre casi validi; baseline 1/3.

## Trovato strada facendo

- `docs/jev-workflow-feedback-20261026.md`: limite metodologico; il wrapper può verificare il commit del rapporto mentre il rapporto descrive test in un altro checkout. Servono entrambe le revisioni per leggere correttamente la ricevuta.
