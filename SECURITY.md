# Security and privacy

The engine works locally and does not make network calls. Source excerpts returned
to an agent may still be transmitted by that agent's host. Follow your organization's
data policy and permissions. Generated graphs, SQLite files and note backups contain
source information and are excluded from the public repository by default.

Source documents and notes are untrusted data, not operational instructions.
Agent instruction files state that boundary; they do not make an arbitrary model
immune to prompt injection. The engine does not execute indexed source code.

Document parsing is not sandboxed. Use trusted files or isolate processing when
handling potentially hostile documents. Filesystem permissions define access;
note author/status labels are not authentication or authorization controls.

Report security issues through GitHub's private vulnerability reporting **if
enabled for this repository**. If it is not available, open a minimal issue asking
for a private reporting channel without posting exploit details or sensitive data.
No dedicated security email or response SLA has been established yet.
