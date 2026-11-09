# Verification record — 2026-11-09

This records local checks of the prepared alpha. It does not imply publication,
remote CI success, model accuracy or independent validation.

## Environment

Windows, Python 3.12.10, SQLite 3.49.1 with FTS5, pypdf 6.21.1.
The HTML viewer was exercised in Microsoft Edge headless.

## Observed results

| Check | Result |
|---|---|
| Full unittest suite | 71 tests passed; final run 33.129 s |
| Portable archive | Built; tested with Python -S, without site packages |
| Portable reproducibility | Two builds from unchanged inputs produced identical SHA-256 |
| Synthetic retrieval | 2/2 expected cross-file targets within first six candidates |
| Synthetic memory lifecycle | Automatic note visible; revocation effective |
| Demo corpus | 5 fictional sources, 15 passages, 6 candidate dependencies |
| Engine network boundary | Socket-connect-blocked update/note/export fixture passed |
| Source preservation | Fixture source bytes unchanged after update/note/export |
| Wheel | Built and installed into an isolated target directory; import/doctor/viewer asset checked |
| Source distribution | Built locally using setuptools |
| Public-file audit | Passed on the reviewed source set; heuristic, not a secret/rights guarantee |

The browser check covers graph rendering, macro/micro mode switching, selecting
a node, inspector command generation and search/empty-state handling. It observes
page JavaScript errors and external page requests. The tested page made none.
A screenshot was inspected locally; it is not a private-corpus screenshot.

The suite includes a negative regression: generic relationship words in a corpus
description must not create prerequisite links to that description. On the small
demo, filtering these generic anchors reduced candidate edges from 15 to 6 while
retaining both expected evaluation targets. This is a fixture correction, not a
general precision estimate.

## Not run / not established

- GitHub Actions on Linux, macOS or other Python versions.
- A fresh end-to-end conversation in multiple real agent hosts.
- Held-out answer-quality A/B experiments or independent edge annotation.
- New large-corpus performance/scalability measurements.
- Exhaustive adversarial-parser, concurrency or interruption testing.

The original prototype's private-corpus metrics were not reused as public
benchmark results. Follow [EVALUATION.md](EVALUATION.md) for the next study.
