# Evaluation protocol

## What automated tests establish

The regression suite checks document coverage, hierarchy, UTF-8 continuation,
source changes, invalid references, package portability, notes, revocation,
manual-link persistence and related evidence in text/JSON. The synthetic demo
checks that two expected cross-file targets appear in the first six candidates
and that an automatic observation can be found and revoked.

```sh
python pack.py
python -m unittest discover -v
python scripts/evaluate_demo.py
```

These checks establish mechanical behavior on fixtures. They do **not** establish
that an agent answers better, that all graph edges are correct, or that the
heuristics generalize. The demo was designed alongside the implementation.

## A meaningful next experiment

1. Freeze a legally shareable corpus, its hashes, questions and gold source
   passages. Separate development examples from a held-out evaluation set.
2. Include ordinary rules, cross-file exceptions, prerequisites, scope ambiguity,
   conflicting versions, missing evidence, irrelevant notes and misleading titles.
3. Compare the same model/host/configuration under four conditions: ordinary
   filesystem tools; Atlas structure/search with `--no-graph`; full graph and
   notes; and, where the corpus fits, the whole corpus placed in the context
   window with no tool. The last one tests whether large windows make
   navigation unnecessary or make missed exceptions more frequent.
4. Use fresh sessions, randomized condition order and the same tool/output budget.
   Avoid carrying notes from one condition into another. Record model/version,
   prompt, corpus revision, random settings if available and actual tool traces.
5. Have reviewers score answer correctness, applicable exceptions recovered,
   appropriate clarification questions, unsupported claims and citation validity.
   Also measure tool calls, returned characters, elapsed time and cost.
6. Report per-question results, failures, paired differences and uncertainty.
   Repeat stochastic runs. Do not choose only successful examples.

Before an accuracy claim, independently label a sample of suggested edges for
precision and a sample of known dependencies for recall. A larger edge count
is not necessarily an improvement. Confirmed notes must not leak gold answers
into a supposedly unseen comparison.

## Fresh-agent acceptance test

Use a new workspace with the demo. Ask: “Can I return my Orion equipment after
twenty days?” A successful workflow reads the policy, inspects the candidate
exception/prerequisite, asks about engraving/defect/account scope when relevant,
and cites the sources actually read. This is an observable acceptance scenario,
not a claim that any particular host has already passed it.

Test memory in a separate clean session: record an observation without explicit
references, inspect where it linked, then ask a second agent a relevant question.
Success requires actual note retrieval and appropriate handling as an observation.
Revoke it and verify it disappears from injected evidence. Preserve the traces.
