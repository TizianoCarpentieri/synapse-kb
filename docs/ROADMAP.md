# Roadmap

## Before the first public alpha

- Confirm code ownership and the prepared MIT license.
- Review the public diff, accurate project description and Git author identity.
- Create the first commit and push; run the configured cross-platform CI.
- Publish the tested portable artifact and checksum as an explicit release.

## Next reliability work

- Streaming and incremental graph construction (memory independent of corpus
  size; rebuild only what a changed source touches). See EVOLUTION.md.
- Migration of legacy Atlas notes.
- Tests for interrupted graph generation and very large or hostile inputs;
  profiling on representative, non-generated corpora.
- Better calibrated linking, multilingual cues, scoped authority and collision cases.
- Complete English localization and actionable terminal onboarding errors.

## Evidence for adoption

- Independent edge annotations and held-out agent A/B evaluations.
- Tested onboarding on multiple agent hosts, with recorded compatibility results.
- Reproducible examples in several domains, using redistributable corpora.
- Stable versioned interfaces, conformance fixtures and an RFC process.
- MCP checks on more hosts and the 2026-07-28 revision; an optional re-ranker
  only after independent labels show where lexical ranking fails.

Becoming a broadly used standard is a long-term goal. No launch name or feature
count can guarantee it; interoperability, useful evidence and community adoption
must establish it.
