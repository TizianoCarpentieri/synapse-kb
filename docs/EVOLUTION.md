# 0.2 review: diagnosis, target architecture, plan

This records why 0.2 changed what it changed, what was measured, and what is
deliberately left undone. Figures come from `scripts/benchmark.py` on a
generated corpus (4 vCPU Xeon 2.3 GHz, Linux, Python 3.13.18, SQLite 3.53.1).
They describe cost and mechanical behaviour, not answer quality.

## The rule that decides everything else

Synapse KB exists so that an agent can show which bytes of which revision it
read, and can find the exception that lives in another file. Every layer is
therefore placed on one of two sides:

- **Asserting layers** state facts about sources: text, byte range, SHA-256,
  heading path, "this note is bound here". They must be reproducible and
  checkable by equality.
- **Proposing layers** rank or suggest: search order, dependency candidates,
  where a note might belong after an edit. They may be heuristic, and later
  statistical or neural, because their output is always labelled a candidate
  and never changes what an asserting layer says.

A probabilistic component is acceptable only on the proposing side. That is the
answer to the risk of re-introducing hallucination: a model may reorder
candidates; it may not mint a reference, a quote or a binding.

## Diagnosis of 0.1

| Finding | Evidence | 0.2 |
|---|---|---|
| Any edit to a file orphaned every note bound in it, including notes on untouched passages. Bindings stored coordinates and hashes but not the text, so nothing could be recovered. | `memory.rebind`; old test `test_changed_source_binding_is_not_guessed` | Fixed: see re-anchoring |
| Saving or revoking a note re-read the whole corpus once any unanchored note existed. | 2.5 s at 13k passages, 13 s at 65k | Fixed: 4–5 ms at both sizes |
| Every command, including reads, took an exclusive non-blocking lock. A second agent failed instead of waiting. | `cli.run` wrapped `query` in `operation_lock` | Fixed: readers use WAL snapshots, writers queue |
| No record of who changed memory and when, no cursor for other processes. | — | Fixed: hash-chained journal |
| Edges had one untyped lexical origin. A document saying "see X" or linking X produced no edge towards X. | forward citations found: 0 % in the benchmark | Fixed: reference resolver, typed relations |
| An edge's cue was a trimmed string with no location; it could not be checked against the source. | `dependencies.cue` | Fixed: byte range and hash of the cue sentence |
| A query in the singular missed a passage in the plural and fell back to "any term". | FTS5 `unicode61`, no stemming | Fixed: labelled inflection tier |
| DOCX table cells became one paragraph per cell, separating a value from its row. | `extract.docx` | Fixed: one line per row |
| Memory could not move between workspaces. | roadmap item | Fixed: content-addressed import |
| CLI only; each call pays process start-up. | — | Added: MCP stdio server |
| Graph build holds every passage's phrase set in memory. | 2.0 GB peak at 65k passages | **Open**, see plan |
| Related-evidence reads re-hash each referenced source file. | `check_source(verify=True)` in `memory.related` | **Open**, by design; see plan |

## What 0.2 does

### Re-anchoring by equality (`reconcile.py`)

A binding now stores the annotated passage text and heading path. After an
update each binding gets one state:

| State | Condition | Effect |
|---|---|---|
| `exact` | same revision, coordinates and hash | active |
| `relocated` | identical characters (whitespace aside) found exactly once in the same file under the same heading path | active, automatic |
| `drifted` | best remaining candidate: changed wording, renamed heading, or the identical passage in another file | shown on the candidate passage as "written for other text"; no manual edge; a named person accepts with `notes --rebind ID --author NAME` |
| `stale` | nothing above the floor, or two places fit equally | kept for review |

**What confidence is enough to re-anchor automatically?** None below equality.
Measured on 40 notes per mutation:

| Mutation | Followed automatically | Proposed | Stale | Median similarity |
|---|---|---|---|---|
| edit elsewhere in the file | 40 | 0 | 0 | — |
| text shifted by an insertion | 40 | 0 | 0 | 1.0 |
| lines re-wrapped | 40 | 0 | 0 | 1.0 |
| 10 % of filler words replaced | 0 | 40 | 0 | 0.939 |
| 35 % of filler words replaced | 0 | 40 | 0 | 0.788 |
| "thirty days" → "fourteen days" | 0 | 40 (all flagged `number`) | 0 | 0.985 |
| "is eligible" → "is not eligible" | 0 | 40 (all flagged `negation`) | 0 | 1.0 |
| "must" → "may" | 0 | 40 (all flagged `modality`) | 0 | 0.985 |
| heading renamed ("Withdrawn …") | 0 | 40 (all flagged `context`) | 0 | 1.0 |
| section removed | 0 | 0 | 40 | 0.47 |
| section removed, a 90 % copy remains under another heading | 0 | 40 (all flagged `context`) | 0 | 0.939 |

An edit that reverses a rule leaves the passage *more* similar to the original
(0.985–1.0) than harmless rewording does (0.79–0.94). A threshold of 0.95 would
re-attach every reversed rule and no reworded one; 0.8 would re-attach every
reversed rule and half of the removed passages to their near copy. Similarity
therefore only ranks proposals and suppresses noise (floor 0.55, margin 0.10
over any other section). The `number`/`negation`/`modality`/`condition` flags
are word lists: they name a visible change, they do not prove a passage is
safe when absent.

### Typed relations and stated references (`relations.py`, `graph.py`)

A cue sentence is classified as `exception`, `precondition`, `condition`,
`scope` or `supersedes` (English and Italian patterns) and split into a
condition and a consequence. This is a pattern grammar over one sentence, not a
syntactic parse. A second resolver creates edges a document states itself: a
Markdown link, a file name after a reference cue, or a cited heading whose
title is unique in the corpus. When the citing sentence is itself an exception
the cited rule gets a typed edge back to it. Each edge carries
`origin` (`heuristic`, `reference`, `explicit`), `relation`, and the byte range
and SHA-256 of the cue sentence.

On the generated corpus the expected target is in the first six candidates for
100 % of titled exceptions and citing exceptions in both releases, and for
100 % of forward citations in 0.2 against 0 % in 0.1. The corpus was written
with the engine; independent labelling is still required before any accuracy
claim.

### Journal, concurrency, federation (`events.py`, `index.py`, `memory.py`)

- `events` is an append-only table; each row hashes the previous one. Index
  builds, graph builds and every memory mutation are recorded with the actor,
  in the same transaction as the change. `events --verify` recomputes the
  chain. It detects accidental or naive edits; it is not a signature.
- The database runs in WAL mode. Reads take no lock and keep a consistent
  snapshot; short mutations wait up to 15 s for the writer lock; builds still
  fail fast if another writer is active. Six parallel `annotate` processes all
  commit in the regression suite.
- A note has a content address (`uid`). `notes --import memory.json` is a set
  union: idempotent, order-independent, revocation always wins, a remote
  `confirmed` arrives as `proposed`. Bindings are resolved by the same
  re-anchoring rules, so a note imported into a workspace whose document
  differs goes to review instead of attaching.

A CRDT library or an event-sourced store was considered and not adopted: notes
are immutable and their status only moves forward, so union plus "revoked
wins" already converges. Binding acceptance is the one non-commutative
operation and stays local.

### MCP server (`mcp_server.py`)

`python atlas.py serve --workspace PATH` speaks MCP over stdio with the
standard library only. Tools map one-to-one to CLI commands and return the same
JSON as `structuredContent`. Three resources can be subscribed to; a journal
change sends `notifications/resources/updated`, including changes made by
another process.

Checked: raw JSON-RPC regression tests, and a session with the official Python
SDK client 2.3.0 (handshake, tool listing, calls, error results, resource read).
That client asked for revision 2026-07-28 and accepted 2025-06-18. **Not
checked:** Claude Desktop, IDE hosts, other SDKs. The SDK reports that
`resources/subscribe` is removed in revision 2026-07-28; its replacement is not
implemented here.

## Measured cost

| | 0.1, 13k passages | 0.2, 13k | 0.1, 65k passages | 0.2, 65k |
|---|---|---|---|---|
| index build | 5.7 s | 5.7 s | 24.7 s | 25.9 s |
| graph build | 7.0 s | 8.9 s | 39.9 s | 50.6 s |
| peak memory | 437 MiB | 471 MiB | 1 992 MiB | 2 151 MiB |
| database | 36.8 MiB | 49.8 MiB | 184.8 MiB | 249.1 MiB |
| search p50 / p95 | 1.2 / 1.3 ms | 1.4 / 1.5 ms | 1.7 / 1.9 ms | 1.8 / 2.0 ms |
| read p50 | 0.7 ms | 0.8 ms | 0.7 ms | 0.8 ms |
| related evidence p50 | 1.0 ms | 1.2 ms | 1.0 ms | 1.2 ms |
| save a note | 2 503 ms | 5 ms | 13 014 ms | 4 ms |

0.2 pays about 27 % more graph-build time and 35 % more database for typed
relations, reference edges and per-edge evidence.

## Questions the mandate asked

**Cost of local models versus FTS5.** Not measured: the build environment could
not download any model. What is measured is the baseline a model must justify
itself against: a full search in under 2 ms and a graph build of 0.8 ms per
passage. The integration point is fixed by the rule above: a re-ranker receives
the candidate list produced by FTS5 and returns an order; the response records
the model file's SHA-256; references, quotes and hashes still come from the
index. Dense retrieval that adds candidates FTS5 did not find is the same
contract with a second candidate source.

**Minimum confidence for re-anchoring.** Equality; see the table above.

**Multi-host interoperability.** One independent client verified, the rest
unverified; see MCP above.

**Does a probabilistic layer erode determinism?** Only if it is allowed to
assert. The contract now says so explicitly (CONTRACT.md, points 6 and 8).

**Do very large context windows make this redundant?** Not settled by anything
measured here. Two properties do not depend on window size: a citation that
can be checked against a hash, and an exception surfaced from a file the
question never mentioned. EVALUATION.md describes the A/B study that would
settle it, with "whole corpus in context" as a fourth condition.

## Plan

Done in 0.2: everything in the "What 0.2 does" section, 36 new regression
tests, the benchmark script.

Next, in order of value:

1. **Streaming graph build.** Move phrase document-frequency and the anchor
   inverted index into temporary SQLite tables and process passages in
   batches. Target: peak memory independent of corpus size. This is the
   current ceiling: about 33 KiB of Python objects per passage.
2. **Incremental graph.** Rebuild edges only for changed sources and for
   passages sharing an anchor with them. Today every update rebuilds all edges.
3. **Verification cache for long-lived servers.** Inside the MCP process, keep
   a source's verified hash keyed by size, mtime and inode so related-evidence
   reads stop re-hashing large files.
4. **Independent labels.** Edge precision/recall on a redistributable corpus
   not written by the authors; the A/B study in EVALUATION.md.
5. **Optional re-ranker**, only after 4 shows where lexical ranking fails.
6. **Extraction plug-ins** for OCR and column-aware PDF, outside the core and
   declared per source in coverage, so the zero-dependency core stays intact.
7. **MCP revision 2026-07-28** change notifications; HTTP transport only if a
   host needs it.
