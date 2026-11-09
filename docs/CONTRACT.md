# Evidence navigation contract — draft 0.1

This is a project draft for discussion, not an adopted industry standard.
The CLI implementation is the current reference; future compatibility requires
explicit versioning and conformance fixtures.

## Required distinctions

1. Source evidence, lexical hints, inferred candidates and shared observations
   must remain distinguishable.
2. A source reading must expose provenance, source revision, bounded text and
   whether a continuation is needed. Normalized coordinates must be labelled.
3. Relationship direction means “inspect this related evidence”, unless a
   separately verified relation asserts something stronger.
4. A shared observation must preserve body, creation time, attribution, review
   state, optional expiry and explicit source bindings when present.
5. An implementation must expose unread/unextracted material and omitted links;
   a bounded result is not completeness.
6. Changed sources must not silently inherit annotations intended for another
   revision. Stable equality or an explicit reviewed migration is required.
7. Source text cannot grant operational permissions to an agent.

## Current JSON interfaces

`search --json` returns `risultati`, `modalita`, query terms, pagination and a
top-level `related` block. `read --json` returns a list of bounded records with
`ref`, `fonte`, `sha256`, byte coordinates, `testo`, `continua_offset`, warnings
and a `related` block for each returned record. Compare requested/returned counts.

`related` contains `items`, `omitted` and `warnings`. Dependency items expose
source/target references, path/line, cue, anchor, kind, origin and ranking score.
Note items expose note ID, status, attribution, timestamp, expiry and full-read
command. Candidates and note excerpts do not replace full source reading.

`graph.json` uses `synapse-kb-graph-1`. `memory.json` uses `synapse-kb-memory-1`.
The [graph schema](../schemas/graph.schema.json) describes the current export.
Internal SQLite layouts and node IDs are not a cross-tool standard. Legacy Atlas
node IDs are not portable into a newly created database.

## Still needed for wider interoperability

A versioned read/search schema; independent implementations; corpus-independent
identifiers and migration rules; conformance testing across tools; semantics for
authenticated authorship and review; an agreed extension mechanism. A networked
MCP/API layer is a future adapter, not present in this release.
