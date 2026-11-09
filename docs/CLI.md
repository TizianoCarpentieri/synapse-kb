# Commands

Run `python atlas.py COMMAND --help` for complete options. `python atlas.pyz`
and the installed `synapse-kb` entry point use the same parser.

From the repository, pass `--workspace workspaces/main`. From the initialized
workspace, use its `python atlas.py` launcher. For advanced existing Atlas layouts,
`--indice PATH` / `--index PATH` selects an explicit index.

| English | Italian | Purpose |
|---|---|---|
| init | inizia | New empty workspace, external source folder or demo |
| doctor | doctor | Python, FTS5, optional pypdf availability |
| build | prepara | Index a source directory, optionally with --output |
| update | aggiorna | Reuse workspace configuration and rebuild graph |
| explore | esplora | Structural navigation, H1–H6 and branch references |
| search | cerca | Full-text candidate search |
| read | leggi | Source reading, context, continuations and related evidence |
| annotate | annota | Save an attributed observation, auto or explicit links |
| link | collega | Save an explicit relationship, backed by a durable note |
| dependencies | dipendenze | Inspect candidate links, warnings and omitted count |
| notes | appunti | Full note bodies, binding review, rebind, revocation, import |
| events | eventi | Journal of index and memory changes; chain verification |
| serve | servi | MCP server on stdin/stdout |
| graph | grafo | Export offline HTML, JSON and Obsidian snapshots |
| status | stato | Extraction coverage and memory review state |
| export | esporta | Regenerate the structural Markdown preview |

## Common operations

```sh
python atlas.py search "topic" --workspace workspaces/main --json
python atlas.py read REFERENCE --workspace workspaces/main --vicini 1 --verifica
python atlas.py dependencies REFERENCE --workspace workspaces/main --limit 1000
python atlas.py annotate "Observation" --title "Topic" --collega REFERENCE --author "reviewer" --workspace workspaces/main
python atlas.py link FROM_REFERENCE TO_REFERENCE --note "Why this matters" --workspace workspaces/main
python atlas.py notes 1 --workspace workspaces/main
python atlas.py notes --revoke 1 --workspace workspaces/main
python atlas.py notes --review --workspace workspaces/main
python atlas.py notes --rebind 1 --author "Reviewer name" --workspace workspaces/main
python atlas.py notes --import other/_atlas/memory.json --workspace workspaces/main
python atlas.py events --since 0 --workspace workspaces/main
python atlas.py events --verify --workspace workspaces/main
python atlas.py serve --workspace workspaces/main
python atlas.py update --workspace workspaces/main --verifica
python atlas.py graph --workspace workspaces/main --rebuild
```

References above are placeholders. Use full hash-suffixed passage references
returned by the current index. Manual notes reject invalid references atomically.
`--collega` and `--verso` accept multiple references; a target requires an explicit
source. A document or folder reference is not a valid memory anchor.

Notes default to `--status proposed`. Use `--status confirmed` only for a
deliberately confirmed observation. Confirmation is a label, not proof or
authentication. `--expires YYYY-MM-DD` expires at the end of that date in UTC.
Automatic links can be absent or wrong; inspect linked passages before relying
on them. There is no automatic promotion from proposed to confirmed.

## Pagination and budgets

`read --max-chars` budgets the source-reading output. Related evidence has a
separate bounded selection (`--related-limit`, default 6, maximum 50), prepended
to protect it from accidental tail truncation. It is not part of that character
budget. Never truncate CLI output with head/Select-Object and then claim full
reading. Follow `continua_offset` / `--offset`, preserving `--vicini` when used.
Batch JSON reading can return fewer records than requested; compare counts.

`search --tutti` enumerates lexical matches with count, edition and continuation.
Search is not semantic completeness. `--no-graph` is for controlled A/B tests,
not the normal source consultation workflow.

## Recovery

Run `update --ricostruisci --verifica` to regenerate derived text and references.
After any update a note stays bound where its annotated text is found again
unchanged, once, in the same file under the same headings. `notes --review`
lists the others: a `drifted` binding has a proposed passage, a similarity and
the kind of words that changed (`number`, `negation`, `modality`, `condition`,
`context`, `path`); a `stale` one has none. `--rebind` records that the named
person accepted the proposals of one note. An agent must not accept on its own. Do not delete `_atlas/`: it contains durable notes, not
only disposable caches. Back up the whole workspace. `memory.json` is a human
readable backup/export. `notes --import FILE` merges such a file from another
workspace: the same note is never duplicated, a revocation always wins, and a
remote `confirmed` label arrives as `proposed` unless `--keep-status` is given.

## Several agents

Reading commands never wait. Note operations queue for up to 15 seconds behind
another writer; `build`/`update` stop at once if a writer is active. `events
--since N` tells a process what changed after the last sequence number it saw.

## MCP

`serve` reads JSON-RPC from stdin and writes to stdout. Example host entry:

```json
{"mcpServers": {"synapse-kb": {"command": "python",
  "args": ["/absolute/path/to/atlas.py", "serve", "--workspace", "/absolute/path/to/workspaces/main"]}}}
```

Tools: search, read, explore, dependencies, status, notes, review_bindings,
events, annotate, link, revoke_note, accept_rebind, update. Results carry the
same JSON as `--json`. Resources `synapse://status`, `synapse://notes` and
`synapse://events` can be subscribed to. Host configuration formats differ;
only the Python SDK client has been exercised.

Editing/deleting `appunti_condivisi.md` does not alter the database. Use
`notes --revoke ID`. The generated HTML/Obsidian files are snapshots. CLI note
operations refresh them; if export fails after a successful note mutation, the
response reports that the note was saved/revoked and that export needs retrying.
