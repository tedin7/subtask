# Jev: prove delle consegne, 26 ottobre 2026

Tre packet offline pronti per valutare la lettura delle prove. Nessuna inferenza
eseguita da questo preparatore: non sono risultati di qualità di Jev.

Campione delimitato ai 60 job Subtask aggiornati più recentemente al momento
della lettura. Sono stati letti metadati, piccoli estratti e tre fonti finali.
La selezione è intenzionale, non casuale; i casi non stimano accuratezza generale.
Sono esclusi i replay a coppie e il precedente confronto 193/172 test.

| Caso opaco | Affermazione C1 | Verità osservabile | Origine |
|---|---|---|---|
| ev-7c42 | Verifica indipendente: 116 test verdi sul commit indicato | supported | Storico, senza alterazione dei risultati |
| ev-18a6 | Sei test verdi provano il successo di un secondo deploy nella fixture separata | contradicted | Sovraffermazione controllata; la fixture registra exit 1 e rollback |
| ev-90df | Il backend condiviso è la causa provata dell'incidente storico | unproven | Report storico: causa non provata, nessuna traccia causale fornita |

Il primo caso lega exit, conteggio e HEAD pulito della verifica registrata.
Il secondo separa suite verde e scenario avversario: ripristinare la vecchia
release non equivale a completare il deploy. La fixture crea repository propri;
il suo commit non viene presentato come HEAD della verifica indipendente.
Il contatore dei probe conta chiamate, non risposte HTTP 500 misurate.
Il terzo conserva l'incertezza originale: assenza di prova non significa falsità.

I controlli deterministici bastano per questi tre casi: confronto del commit e
del conteggio, confronto dell'exit, rifiuto della certificazione causale senza
traccia. Il contributo semantico eventualmente misurato riguarda la lettura del
mandato e dei diversi ambiti. Non va attribuito a Jev il rilevamento di un exit
non zero già disponibile. Nessun caso sostituisce la falsificazione del codice.

Packet, verità, baseline deterministica e hash SHA-256 delle fonti sono privati
in `/root/.local/state/subtask-jev-workflow-20261026/evidence-cases.json`.
Solo `request` va inviato al modello; etichette, note e provenienza restano fuori.
Non sono state cambiate policy, credenziali, limiti o provider. Nessun invio API,
GitHub, push, rilascio o suite completa. La fattura delle attività pregresse
non è ricostruita qui.

Verifica locale: validazione JSON, schema ternario, identità dei tre casi,
assenza di campi gold nella richiesta, hash delle fonti confrontati con i file
letti e ancora presenti. I controlli non eseguono i vecchi job.
