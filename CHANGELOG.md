# Changelog

## 0.2.0a1 — prepared 2026-11-09, not yet published

- Note bindings store the annotated text. After a source changes a binding is
  `exact`, `relocated` (identical text, same file and heading path: automatic),
  `drifted` (flagged proposal; `notes --rebind ID --author NAME`) or `stale`.
  `notes --review` lists what needs a person.
- Dependency candidates carry a typed relation, an origin and the byte range
  and hash of the cue sentence. New relations: scope, supersedes. New origin
  `reference`: Markdown links, cited file names and uniquely titled headings.
- Hash-chained event journal (`events`, `events --verify`).
- Readers no longer take the operation lock (SQLite WAL); writers queue.
- `notes --import` merges another workspace's `memory.json` by content address.
- MCP stdio server: `serve`, with resource subscriptions.
- Search accepts inflected forms as a labelled tier before partial matches.
- DOCX table rows are kept on one line.
- Saving a note no longer scans the corpus.
- `scripts/benchmark.py`; results in docs/EVOLUTION.md.
- Obsidian vault mirrors the source folders: readable file names, one hub note
  per document, folder tags, and graph colours per folder unless the vault
  already has its own graph settings. Files from the earlier flat layout
  (`section-….md`) are removed on the next export; notes you added stay.
- HTML graph: folders and documents are nodes, colours and a legend follow the
  source folder, relationships are drawn clearly, the view fits the window,
  and "Only linked" hides everything without a relationship.

Changed behaviour: a note on an untouched passage of an edited file stays
bound (0.1 marked it stale). `memory.json` uses `synapse-kb-memory-2`. The
first `update` with 0.2 re-indexes every source.

## 0.1.0a1 — prepared 2026-11-09, not yet published

- Agent-guided onboarding, separate workspaces, external sources and local rules.
- Atlas structural extraction, source verification, FTS5 search and bounded reads.
- Ordinary source modules for graph generation; no runtime patching.
- Shared observations with attribution, proposed/confirmed state, expiry and revocation.
- Persistent manual links, exact-revision rebinding and stale-binding review data.
- Related evidence prepended in text and included in JSON.
- Generic offline HTML/Obsidian exports with explicit selection bounds.
- Synthetic demo, regression suite, portable build, package configuration and CI.

The public alpha has no inherited release history. Earlier private experiments
are not public accuracy or performance benchmarks.
