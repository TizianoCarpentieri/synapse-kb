# Architecture

## Boundaries

`atlas_core/extract.py` extracts normalized text and declared structure.
`index.py` stores source revisions, hierarchical nodes and FTS5 search data.
`view.py` verifies sources and returns bounded text, provenance and continuations.
`graph.py` creates candidate dependencies from lexical anchors and conditional
cues; it makes no model calls. `memory.py` owns attributed observations and
derived note/manual links. `visuals.py` produces offline snapshots.
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

A manual binding records source path, full SHA-256, byte range and passage hash.
Reindexing may change node IDs. A binding is restored only to the same source
revision and passage coordinates/hash. A changed/removed/renamed source leaves a
stale binding, retained for explicit review. There is no fuzzy reassignment.

Graph rebuilding clears only derived dependencies and link tables. It never
deletes notes. Revocation disables note injection and its manual relationships.
Deleting the generated Markdown cannot silently change memory state.

## Consistency

The public CLI serializes operations per index with an OS-backed lock. Source
indexing commits its SQLite changes transactionally. Graph construction commits
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
The current algorithm matches English/Italian cue patterns, distinctive shared
phrases and headings, with bounded per-node selection. Its scores are not
calibrated confidence values. Broad or misleading headings remain a known risk.

HTML embeds escaped JSON and uses DOM text nodes for source text. It contains
no CDN, fetch or external script. Macro view groups passages by source and top
heading; micro view shows passages and notes. Exports preserve incoming/outgoing
links and disclose selection limits; the CLI remains the place for fuller reads.
