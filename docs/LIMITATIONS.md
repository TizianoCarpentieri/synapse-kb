# Known limitations

- Alpha software. Only the execution environments reported in TEST_REPORT.md
  have actually been exercised. A CI matrix being configured is not a passed run.
- No hosted chat, bundled model, automatic LLM host setup, MCP server or API service.
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
- A source change invalidates explicit bindings until reviewed. There is no
  automatic merge, rebinding UI, legacy database migration or memory JSON import.
- No OCR, image interpretation, table geometry reconstruction or spreadsheet
  recalculation. Read original artifacts when their visual structure matters.
- HTML and Obsidian graphs are bounded snapshots, not the entire database. They
  contain source information; do not upload them as public project examples.
- Related-evidence output adds a separate budget above source text. All normal
  text output is not fully localized; inherited extraction diagnostics are Italian.
- The graph builder loads passage metadata/phrases into memory. The browser
  viewer uses a small force simulation. Neither has been validated for enormous
  corpora in this release.
- PDF parsing and document archives are not sandboxed. Resource limits in the
  extractor are not a complete defense against adversarial files.
- Local indexing does not make a cloud model conversation local. Agent tools may
  send returned excerpts to their model provider.

The earlier prototype's counts and timing describe a different private corpus.
They are not included as benchmark evidence for this public package.
