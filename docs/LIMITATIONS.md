# Known limitations

- Alpha software. Only the execution environments reported in TEST_REPORT.md
  have actually been exercised. A CI matrix being configured is not a passed run.
- No hosted chat, bundled model, automatic LLM host setup or HTTP API. The MCP
  server is stdio only, without authentication, checked against one client;
  subscriptions follow protocol revisions up to 2025-06-18.
- One source root per workspace. Sources on another drive may use absolute paths;
  moving the workspace and sources independently requires configuration review.
- The generated launcher references the repository location. It is a convenience,
  not a self-contained binary; use the portable executable when transferring.
- Normalized text and bounded passages are stored locally. This is indexing and
  segmentation, even though embeddings and model-based rewrites are absent.
- Lexical search does not translate, understand all paraphrases or prove absence.
- Dependency detection is heuristic, with English/Italian cue patterns. False
  positives, missing edges and context mistakes are possible. No language-complete
  or cross-domain quality guarantee exists.
- “Self-evolving” means that attributed observations can accumulate. It does not
  mean that the agent learns verified rules or may rewrite authoritative policy.
- Author/status fields are labels, not authenticated identities or access control.
  Host and filesystem permissions are the security boundary.
- A binding survives a source change only where the annotated text is unchanged.
  Proposals for changed text rank by word overlap; the change flags are word
  lists and can miss a reversal expressed differently. Passages annotated with
  0.1 gain their stored text at the first 0.2 update, provided the source has
  not changed since. No legacy Atlas database migration.
- Relation typing is a pattern grammar over single sentences. It does not
  resolve pronouns, nested conditions or scope across sentences. A cited
  heading is resolved only when its title is unique in the corpus.
- The event journal detects accidental edits, not an attacker with write access.
- Inflection matching is suffix stripping for English and Italian, not a
  linguistic stemmer; it can match unrelated words sharing a prefix.
- No OCR, image interpretation, table geometry reconstruction or spreadsheet
  recalculation. Read original artifacts when their visual structure matters.
- HTML and Obsidian graphs are bounded snapshots, not the entire database. They
  contain source information; do not upload them as public project examples.
- Related-evidence output adds a separate budget above source text. All normal
  text output is not fully localized; inherited extraction diagnostics are Italian.
- The graph builder loads passage metadata/phrases into memory: about 2.1 GiB
  at 65,000 passages on the generated benchmark corpus, and every update
  rebuilds all edges. The browser viewer uses a small force simulation and has
  not been validated for very large corpora.
- PDF parsing and document archives are not sandboxed. Resource limits in the
  extractor are not a complete defense against adversarial files.
- Local indexing does not make a cloud model conversation local. Agent tools may
  send returned excerpts to their model provider.

The earlier prototype's counts and timing describe a different private corpus.
They are not included as benchmark evidence for this public package.
