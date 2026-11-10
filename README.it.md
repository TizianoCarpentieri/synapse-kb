# Synapse KB

**Trova la regola. Segui l'eccezione. Conserva la prova.**

Una cartella di documenti diventa consultabile da un agente LLM attraverso
struttura, ricerca locale, collegamenti candidati e appunti condivisi.
L'agente ti guida nella configurazione: documenti già esistenti o nuova raccolta,
regole personalizzate oppure comportamento predefinito.

Versione **alpha 0.3.0a1**. Il progetto funziona come strumento locale; il vantaggio
sulla qualità delle risposte richiede ancora un confronto indipendente.

## Avvio con l'agente

Basta dare il link a un agente che può eseguire comandi sul tuo computer:

> Configura Synapse KB per i miei documenti:
> https://github.com/TizianoCarpentieri/synapse-kb
> Segui il suo AGENTS.md un passo alla volta.

L'agente scarica lo strumento, ti chiede dove sono i documenti, crea accanto una
cartella di lavoro separata, raccoglie le tue regole e le istruzioni per ciascun
ramo della documentazione (cosa stabilire prima di rispondere, quale cartella
serve quale caso) e verifica tutto con una domanda reale.

Serve un agente con accesso a file e terminale. La repo non installa un modello
e non avvia una chat. Il caricamento automatico di AGENTS.md dipende dall'host;
il prompt esplicito resta il percorso di riserva.

## Prova immediata

```sh
python atlas.py doctor
python atlas.py init --demo --workspace workspaces/demo
python atlas.py cerca "Orion return window" --workspace workspaces/demo
```

Copia un riferimento completo del risultato e leggilo con
`python atlas.py leggi RIFERIMENTO --workspace workspaces/demo --vicini 1`.
Apri `workspaces/demo/_atlas/visuals/graph.html` per vedere il grafo offline.

## I tuoi documenti

```sh
python atlas.py init --source "D:\Documentazione" --workspace workspaces/main
```

Puoi aggiungere `--rules-file "D:\Regole.md"` o usare `--empty` per una raccolta
vuota. I documenti restano dove sono; il workspace contiene configurazione,
indice e memoria. Apri quel workspace nelle sessioni successive.

Il motore usa Python 3.11+ e SQLite FTS5. Per i PDF testuali:
`python -m pip install "pypdf==6.21.1"`. Scansioni e geometria delle tabelle non
sono interpretate automaticamente.

## Promesse verificabili

Nessuna chiamata LLM durante l'indicizzazione; nessun embedding richiesto;
nessuna riscrittura dell'intero corpus tramite modello. Il motore crea comunque
testi normalizzati e passaggi: **“zero-ingest” e “senza chunking” non descrivono
correttamente questa implementazione**.

Il grafo suggerisce collegamenti; la prova resta la fonte letta. Gli appunti
hanno autore, stato e data, possono scadere o essere revocati e non diventano
automaticamente regole autorevoli. Il modello scelto può ricevere i testi letti
dai tool: indicizzazione locale e conversazione interamente locale sono cose diverse.

Per i dettagli: [comandi](docs/CLI.md), [limiti](docs/LIMITATIONS.md),
[verifiche](docs/EVALUATION.md), [passi prima del push](docs/PUBLISHING.md).
