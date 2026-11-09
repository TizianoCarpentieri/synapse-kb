# Contributing

Start with a small issue or reproducible example. Never attach private source
documents or generated indexes. Synthetic examples are preferred.

Set up Python 3.11+, install `requirements-dev.txt`, then run `python pack.py`,
`python -m unittest discover -v` and `python scripts/evaluate_demo.py`.
Use the public audit before a release. Keep source paths relative in fixtures.

Explain the concrete behavior before/after a change, relevant tests and known
limits. Preserve source fidelity, continuations, JSON/text parity and note
provenance. Do not claim improved model accuracy from retrieval tests alone.

Protocol changes should include a proposal in an issue: problem, data contract,
compatibility impact, alternatives and evaluation evidence. The maintainer will
review before a release. Be respectful and address technical disagreements with
examples. No contribution is entitled to acceptance.
