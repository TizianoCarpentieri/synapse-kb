# Architecture

## Boundaries

`atlas_core/extract.py` extracts normalized text and declared structure.
`index.py` stores source revisions, hierarchical nodes and FTS5 search data.
`view.py` verifies sources and returns bounded text, provenance and continuations.
`graph.py` creates candidate dependencies from lexical anchors and conditional
cues; it makes no model calls. `memory.py` owns attributed observations and
derived note/manual links. `reconcile.py` decides where a binding points after
a source changed. `relations.py` types cue sentences and recognises stated
references. `events.py` is the append-only journal. `mcp_server.py` exposes the
CLI operations over MCP stdio. `visuals.py` produces offline snapshots.
`workspace.py` and `cli.py` implement setup, configuration and command routing.

The extraction/navigation engine was adapted from the author's earlier Atlas
prototype. The experimental graph algorithm was extracted into ordinary source
modules. The portable bundle is now built from those modules; runtime source-code
patching and embedded alternative CLI implementations have been removed.

## Persistence

| Data | Authority / lifecycle |
|---|---|
| Original documents | Evidence; never rewritten by this tool |
| atlas.toml / RULES.md | User configuration and agent policy |
| sources / nodes / search | Derived index with hashes and provenance |
| dependencies | Rebuildable heuristic candidates |
| shared_notes | Durable observations with body, author, status, date and expiry |
| manual_edges / note_links | Derived from durable notes and validated bindings |
| memory.json / appunti_condivisi.md | Generated backups/views; not independently authoritative |
| visuals/ | Bounded snapshots containing source excerpts; private by default |

A manual binding records source path, full SHA-256, byte range, passage hash,
the passage text and its heading path. Reindexing may change node IDs. After a
source changes, the binding is restored automatically only when the identical
text (whitespace aside) occurs exactly once in the same file under the same
heading path. Changed wording, a renamed heading or a moved file produce a
proposal with a similarity figure and named changes; a person accepts it and
the earlier binding is kept in the note's history. Otherwise the binding stays
stale. Similarity never activates a binding: see EVOLUTION.md for the measurement.

Every index build, graph build and memory mutation appends a row to `events`
in the same transaction. Each row hashes its predecessor.

Graph rebuilding clears only derived dependencies and link tables. It never
deletes notes. Revocation disables note injection and its manual relationships.
Deleting the generated Markdown cannot silently change memory state.

## Consistency

The database uses WAL. Reading commands take no lock and see one committed
snapshot. Mutations hold an OS-backed lock per index: note operations wait up
to 15 seconds, builds fail at once if another writer is active. Derived text
of a superseded revision is deleted immediately, so a reader that started
before a concurrent update may be told to retry; it never receives other text.
Source indexing commits its SQLite changes transactionally. Graph construction commits
separately and records the index edition. If these phases are interrupted,
edition mismatch prevents stale graph injection. Export is a separate, retryable
operation; no claim is made that the complete filesystem update is atomic.

Direct low-level Python APIs are intended for single-process use; they do not
all acquire the public CLI lock. Sources must remain unchanged during building.
Related-evidence reads validate referenced source hashes; whole-corpus consistency
is checked during update/graph export. Hashes detect changed bytes, not factual truth.

## Graph direction and ranking

An edge A → B means “while reading A, inspect candidate B”. The destination may
contain an exception or prerequisite. It is not a proof that B governs A.
Two resolvers feed it. The lexical one matches English/Italian cue patterns,
distinctive shared phrases and headings (`origin: heuristic`). The reference
one follows what a document states: a Markdown link, a cited file name, or a
cited heading whose title is unique in the corpus (`origin: reference`).
Selection is bounded per node. Its scores are not
calibrated confidence values. Broad or misleading headings remain a known risk.

HTML embeds escaped JSON and uses DOM text nodes for source text. It contains
no CDN, fetch or external script. Macro view groups passages by source and top
heading; micro view shows passages and notes. Both add folder and document
nodes joined by `struttura` edges: these say where a text is stored and are
not dependency candidates, so they are excluded from every relationship count.
The Obsidian vault repeats the source folder tree, names notes after the
document and heading, and never overwrites a file it did not generate. Exports preserve incoming/outgoing
links and disclose selection limits; the CLI remains the place for fuller reads.
