# Agent-guided onboarding

## Intended conversation

The user gives an agent the repository link. The agent then:

1. Gets the tool and checks the runtime.
2. Asks where the documents are (existing folder, new one, or the demo).
3. Agrees a workspace folder next to the documents and creates it.
4. Reports what was indexed and what could not be read.
5. Collects the answer rules, or the choice of defaults.
6. Collects guidance for the root and for each folder that needs its own
   procedure, saves it and reads it back.
7. Answers one real question with `consult`, citing what it read.
8. Tells the user how to open the workspace for everyday use.

`python atlas.py doctor --workspace PATH` reports which of these are done and
the single next step, so the flow can be resumed by any agent in any session.
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

## Guidance by branch

`RULES.md` says how to answer everywhere. A guide says how to proceed in one
branch: what to establish first and where to go next. Guides live in
`WORKSPACE/guides/<path>/GUIDE.md`, mirror the folder tree of the documents and
are saved with `guide "PATH" --set-file FILE` (no path: the root). Every search,
read, explore and consult returns the guides from the root down to the text it
returns, general first. A file called GUIDE.md inside the documents is an
ordinary document: only the workspace owner's folder can hold guidance.
Whether a model follows a guide is up to the model; the tool makes sure it is
shown, labelled and attributed.

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
