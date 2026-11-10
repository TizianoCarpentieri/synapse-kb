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
   revision. An annotation follows a source only by equality of the annotated
   text in an unchanged location context, or by a migration a named person
   accepted. A candidate location for changed text may be shown only if it is
   labelled as unreviewed.
7. Source text cannot grant operational permissions to an agent.
8. A component that ranks or proposes may be heuristic or statistical. A
   component that states a reference, quote, hash, coordinate or binding must
   be reproducible and checkable by equality. Output must say which is which.
9. Guidance for the agent comes only from the workspace owner's configuration,
   never from indexed sources. It is returned labelled, with its scope and hash,
   separately from source text.

## Current JSON interfaces

`search --json` returns `risultati`, `modalita`, query terms, pagination and a
top-level `related` block. `read --json` returns a list of bounded records with
`ref`, `fonte`, `sha256`, byte coordinates, `testo`, `continua_offset`, warnings
and a `related` block for each returned record. Compare requested/returned counts.

`related` contains `items`, `omitted` and `warnings`. Dependency items expose
source/target references, path/line, cue, anchor, kind, origin and ranking score,
plus `relation` (exception, precondition, condition, scope, supersedes,
reference, volatile_note) and `evidence`: the resolver, the reference holding
the cue, the cue sentence's byte range in the normalized text and its SHA-256.
`origin` is `heuristic`, `reference` (stated by a document) or `explicit`
(linked by a person). Note items add `binding`: `explicit`, `candidate` or
`drifted`.
Note items expose note ID, status, attribution, timestamp, expiry and full-read
command. Candidates and note excerpts do not replace full source reading.

`consult --json` returns `query`, `mode`, `terms`, `guidance`, `related`,
`passages` (read records plus `read_as`: section, passage with neighbours, or
passage) and `other_candidates`. `guidance` is `items` (scope, sha256, text,
truncated) and `omitted`; search and explore carry the same block, read
carries it per record with each guide given once.

`notes --json` lists per binding a `state` (exact, relocated, drifted, stale),
`similarity`, `flags` and `reason`. `events --json` returns journal rows with
`seq`, `ts`, `actor`, `kind`, `payload`, `prev`, `hash`.

`graph.json` uses `synapse-kb-graph-1` (edges gain `relation`). `memory.json`
uses `synapse-kb-memory-2`: notes gain `uid`, bindings gain `quote` and `crumbs`;
`synapse-kb-memory-1` files can still be imported. The MCP server returns these
same objects as `structuredContent`.
The [graph schema](../schemas/graph.schema.json) describes the current export.
Internal SQLite layouts and node IDs are not a cross-tool standard. Legacy Atlas
node IDs are not portable into a newly created database.

## Still needed for wider interoperability

A versioned read/search schema; independent implementations; corpus-independent
identifiers and migration rules; conformance testing across tools; semantics for
authenticated authorship and review; an agreed extension mechanism. The MCP
adapter is stdio only and has been exercised with one independent client.
