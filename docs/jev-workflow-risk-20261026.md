# Jev: triage consultivo all'ingresso, 2026-10-26

Sono preparati tre casi privati di incarichi Subtask reali. Nessuna chiamata
Jev è stata eseguita da questo lavoro. I verdetti attesi sono annotazioni umane,
non risultati del modello. Nessun caso autorizza integrazione o rilascio.

Il risultato cercato è distinguere una proposta di triage sostenuta, una
contraddetta e una non dimostrabile. Il vincolo conserva mandato originale,
proprietario del lavoro, provider, budget e politica. La prova locale controlla
struttura, separazione delle etichette e completezza dei tre input.

## Fonti e metodo

La selezione ha esaminato i mandati delle 60 directory job più recenti per
mtime. Questa finestra include replay: i tre casi scelti non sono replay.
Sono stati letti tre stati, due risultati brevi e un estratto di diff/test.
Inventario, mandati esatti, hash dei file e revisioni restano nello stato
privato del coordinatore. La finestra mtime non è un campione casuale e può
cambiare mentre altri job lavorano.

Le regole provengono da `docs/CLAUDE.md` di Arvos: la falsificazione indipendente
è obbligatoria per codice complesso, inclusi sicurezza, concorrenza, dati
persistenti, migrazioni e logica condivisa. L'ordine corrente dei provider resta
un vincolo esterno; questi casi non lo selezionano e non risolvono istruzioni
storiche discordanti sui modelli dei revisori.

| Caso | Proposta giudicata | Atteso | Fondamento |
|---|---|---|---|
| Indagine del consenso | Nessuna falsificazione del codice obbligatoria per la sola indagine corrente; futura correzione da classificare separatamente | supported | Mandato di misurazione e proposta, senza implementazione |
| Configurazione credenziali | Omettere falsificazione prima di attivare la migrazione | contradicted | Spostamento esplicito delle credenziali e permessi privati: sicurezza |
| Incarico con rinvio a TODO | Classificare il fix come semplice senza leggere il materiale collegato | unproven | Contenuto della voce assente dall'input |

Le proposte sono ipotesi di triage create per la prova, non decisioni storiche
attribuite agli esecutori. Il testo operatore è conservato; gli involucri runtime generati e i percorsi identificativi restano soltanto nella provenienza privata. Nel terzo
caso i riferimenti a briefing e TODO sono deliberatamente irrisolti: non si
inventa la categoria reale del lavoro nascosto. Risultati e diff servono solo
alla provenienza privata; non entrano nell'input di intake.

## Baseline e limiti

Una baseline deterministica può cercare parole e percorsi relativi a sicurezza,
migrazioni o codice condiviso. Nel secondo caso la regola è già esplicita:
un verdetto corretto del modello non scopre un nuovo rischio. Nel primo caso
le parole sul consenso non provano una modifica di sicurezza. Nel terzo caso
le esclusioni generiche su credenziali o pagamenti non descrivono il fix.
Questi due casi distinguono l'ambito semantico dai semplici match lessicali.

La baseline non è stata eseguita: non si dichiarano accuratezza o vantaggi di
Jev. Tre casi preparati non misurano calibrazione, causalità o utilità in
produzione. Il verdetto `unproven` richiede materiale aggiuntivo e non equivale
a un permesso. Anche `supported` riguarda soltanto la proposta consultiva.

## Verifica locale

Nel worktree dedicato, Python 3 ha validato tre richieste JSON con domanda C1,
modello `jev-latest`, identificativo opaco, campi di provenienza e distribuzione
attesa uno/uno/uno. Ha verificato che verità, ID storici e guardie restino fuori
dall'oggetto richiesta. Nessuna suite completa, chiamata remota o modifica del
servizio. Il commit della consegna identifica la revisione documentale.

## Trovato strada facendo

- `docs/CLAUDE.md:247` (Arvos), bassa: regole storiche Sonnet e regole più recenti OpenCode coesistono; il triage non deve scegliere autonomamente il provider.
