# Changelog

## 0.3.0a1 — prepared 2026-11-10, not yet published

- `consult "question"`: searches, reads the best matches as whole sections
  where they fit, and returns guidance and related evidence in the same call.
- Guidance per folder or document (`guide`), stored in `WORKSPACE/guides/`,
  returned by consult, search, read and explore from the root down; changes
  are recorded in the event journal. `--no-guide` omits it.
- `read --section`; absolute paths of original files are accepted wherever a
  reference is; `--scope`, `--neighbors`, `--all` as English option names.
- `doctor --workspace PATH` lists setup progress and the next step.
- The workspace launcher works from any directory.
- Repository and workspace AGENTS.md rewritten: step-by-step installation from
  a link, and consulting through one command.
- MCP: `consult` and `guide` tools; the root guide is part of the server
  instructions.
- The HTML graph is drawn on a canvas with a layout that stops when done.
  On a 55-source, 3,850-passage test corpus the page needed 0.24 s of browser
  main-thread time instead of 4.1 s.

The first `update` with 0.3 re-indexes every source. Workspaces made earlier
keep their old launcher and AGENTS.md; to refresh them, copy both from a new
workspace created with `init`.

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
