# Agent-guided onboarding

## Intended conversation

1. “Do you already have a folder of documents, want to create one, or try the demo?”
2. “Should I apply your own rules, or use the defaults?”
3. Check the runtime and describe which folder will hold the workspace.
4. Initialize, inspect coverage, search a real topic, read the result and inspect
   relevant dependencies. Explain any extraction gaps.
5. Open the new workspace in the agent host and start a fresh session when needed.

The repository AGENTS.md defines this flow. Generated workspace instructions
define everyday use. Existing rules and source files are never overwritten.
The terminal `init` wizard is an alternative, not an LLM conversation itself.

## Configuration

`atlas.toml` stores schema, source location, indexing profile and exclusions.
Paths on the same drive are relative; a source on another Windows drive uses an
absolute path and must be reconfigured when moved. `RULES.md` contains the user's
chosen rules, separate from source evidence. A generated `atlas.py` launcher
points to the original tool location; regenerate or edit that launcher if the
repository moves. The portable executable can replace the launcher.

Keep a workspace outside an existing source folder. A new empty or demo source
lives under `workspace/documents/`; its index is a sibling, not inside the sources.
Each workspace connects one source root. Use a common parent folder with exclusions
or separate workspaces for multiple roots. There is no federation in this alpha.

## User rules that are worth collecting

- Role and target audience; answer language, style and citation requirements.
- Which documents are authoritative and how to handle conflicting versions.
- Discriminating scope questions appropriate to the domain.
- Whether the chosen model may receive source excerpts.
- When the agent should propose a shared observation and who can confirm it.

The memory CLI records an author label; it does not authenticate people or enforce
roles. Filesystem permissions and the agent host remain the access boundary.

## Host compatibility

AGENTS.md is an [open instruction format](https://agents.md/). Discovery, scope,
reload behavior and shell permissions differ between agent hosts. This alpha
includes a neutral prompt and local instructions, not certified integrations.
No tool name, model name, provider credit or automatic commit trailer is required.
If discovery fails, explicitly ask the agent to read the relevant AGENTS.md and
RULES.md. Verify its first actual search/read commands, not just its promise.
