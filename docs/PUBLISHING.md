# Publication checklist and commands

Target: https://github.com/TizianoCarpentieri/synapse-kb

The prepared folder is local. No push, remote settings change, release or package
publication is part of this preparation. GitHub credentials are not stored here.

## Review before the first commit

- Confirm that MIT is the intended license and that the reusable code can be
  released under it. Third-party license notices must remain intact.
- Review README wording and the [limitations](LIMITATIONS.md). Suggested About:
  **Local dependency graphs and shared evidence memory for LLM agents.
  Navigate document corpora through exceptions and prerequisites—without
  embeddings or full-corpus LLM rewrites.**
- Run the checks below and review all Git-visible files. Workspaces, indexes,
  excerpts, local reports and generated artifacts are ignored.
- Check the commit author. Use your own GitHub-verified or GitHub noreply email.
  No model attribution, provider credits or co-author trailers are required.

```sh
python pack.py
python -m unittest discover -v
python scripts/evaluate_demo.py
python scripts/audit_public.py
git status --short
git diff --check
git config --local user.name "Tiziano Carpentieri"
git config --local user.email "YOUR_VERIFIED_OR_NOREPLY_EMAIL"
git add .
git diff --cached --stat
git diff --cached
git commit -m "Initial Synapse KB alpha"
```

The email is a placeholder: replace it before committing. If origin is already
configured, do not add it again. Otherwise:

```sh
git remote add origin https://github.com/TizianoCarpentieri/synapse-kb.git
```

Immediately before pushing, inspect the current remote:

```sh
git ls-remote --symref origin HEAD
```

If commits appeared remotely, fetch and reconcile them first; do not force-push.
For a still-empty remote:

```sh
git push -u origin main
```

## After the first push

Inspect GitHub Actions results for Windows, Linux and macOS. The workflow was
prepared locally; remote execution cannot be claimed before a push. Configure
repository description/topics, branch protection and a private security contact.
Then create a deliberate `v0.1.0a1` prerelease, attaching `dist/atlas.pyz` and
`dist/SHA256SUMS`. Review the exact artifacts before uploading.

Do not upload `workspaces/`, `local-only/` or any generated graph from private
documents. A public `.gitignore` does not remove secrets that were already committed.
The local audit is a heuristic and cannot prove copyright ownership or absence
of all sensitive information.

PyPI publication is separate and not configured. The package/distribution name
has not been reserved or checked for availability. GitHub cloning is the initial
distribution path. Use [GitHub's Python CI guidance](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)
when maintaining the workflow.
