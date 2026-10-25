# agents-bridge

**Set up your coding-agent workflow once, in Claude Code or in Codex, and run it in every other agent:**
Codex CLI, Gemini CLI, OpenCode (any model, including free and local open-source ones), Qwen Code, Goose, and back to Claude Code.

You get the same instructions, skills, slash commands, subagents, MCP servers, memory and guard hooks in every agent, and
it doesn't matter which agent you open today.

```
             ┌──────────── one source ────────────┐
             │ ~/.claude (or ~/.codex)            │
             │   hooks · skills · commands        │
             │   agents · MCP · permissions       │
             └───────────────┬────────────────────┘
                     bridge/sync.py  (idempotent, backs up, dry-run)
   ┌──────────┬──────────┬───┴──────┬──────────┬──────────┐
 Codex      Gemini    OpenCode     Qwen      Goose     Claude
   └──────────┴──── every hook → bridge/adapter.py ──────┘
                 normalizes payload → runs YOUR guard scripts → translates verdict
```

## Why

Each agent has its own config format, hook events, tool names and approval model, so moving a hardened setup to
another agent is easy to get *almost* right. "Almost" is dangerous here:

- Codex and Gemini **ignore an `ask` verdict** and let the call run. A "confirm before deleting" guard becomes a no-op.
- Codex runs no hook until you trust it in `/hooks`, and it shows no warning.
- Gemini disables hooks and MCP in folders it doesn't trust.
- A Codex `apply_patch` that deletes a file is not a shell `rm`, so a delete guard never sees it.

agents-bridge handles each of these cases and comes with tests for them.

## What it does

| | |
|---|---|
| **Hooks** | One hook source. Every agent's hook config calls `adapter.py --dispatch`. The adapter maps the payload to the Claude Code shape (`Bash`/`Edit`/`Write`/`Read`/`mcp__…`), runs the matching guards in order, and translates the verdict back for the calling agent. The first block wins. A missing script, bad input or a timeout counts as a block. |
| **Ask → approval code** | When the agent can't ask the user, the adapter blocks and shows a code. The user types `approve CODE`, and the same call then passes once within the TTL. The approval state lives outside the sandbox's writable roots, so the agent can't approve itself. |
| **Instructions** | One `~/.agents/AGENTS.md`, linked into each agent's global instruction file. Project `CLAUDE.md` files are read as fallbacks. The file includes a table that translates Claude/Codex tool names. |
| **Skills** | Agent Skills standard under `~/.agents/skills`, including skills from enabled Claude plugins. An `INDEX.md` lists them. |
| **Commands / subagents** | Converted to each agent's format: Codex prompts and `agents/*.toml`, Gemini `.toml` commands, OpenCode/Qwen markdown. |
| **MCP** | Global and per-repo `.mcp.json` servers are written in each agent's format. No secret value lands in a generated file: Codex uses a header helper, and the other agents get `$VAR` references that `bridge/agent` fills only inside the agent's process. |
| **Permissions** | `deny` → Codex `rules` (`forbidden`), Gemini policies, OpenCode `permission`. |
| **Context** | SessionStart injects your memory index and notes, using the right output format for each agent (plain stdout, or JSON for Gemini). |
| **Doctor** | `bridge/doctor.py` checks on disk that it all works: hooks trusted, guard plugins present, no dangling skill links, configs parse. |

## Install

Requirements: macOS or Linux, `python3` ≥ 3.11 for `sync.py`/`doctor.py` (the adapter itself runs on 3.9+), and the agents you want.

```bash
git clone https://github.com/WWIntegrations/agents-bridge ~/agents-bridge
mkdir -p ~/.agents && cp ~/agents-bridge/config.example.json ~/.agents/bridge.json
$EDITOR ~/.agents/bridge.json                       # source, repos, memory, secret header env names
python3 ~/agents-bridge/bridge/sync.py --dry-run    # what would change
python3 ~/agents-bridge/bridge/sync.py
python3 ~/agents-bridge/bridge/doctor.py --deep
```

Then do the one-time manual steps:
- **Codex**: `codex login`, open `codex`, run `/hooks` and trust the agents-bridge entries. Do this again after any hook change.
- **MCP OAuth** servers: `codex mcp login <server>`, or the equivalent in each agent.
- Start agents with `~/agents-bridge/bridge/agent <codex|gemini|opencode|qwen|goose> [args]`. The launcher exports MCP
  header secrets only into that agent's process and runs a quick doctor first.
- An agent without hooks: run `bridge/agent-start` and paste its output as your first message.

Codex as the source instead: set `"source": "codex"`. Claude Code then becomes a target. See [docs/PORTING.md §7](docs/PORTING.md).

No Claude or Codex setup at all? Point `hooks_source` at [`examples/hooks.json`](examples/hooks.json). It has
starter guards: destructive commands need the user's yes, secret files are blocked, and changes to agent config require approval.

## Docs

- [docs/PORTING.md](docs/PORTING.md): mapping tables for instruction files, hook events and verdicts, tool names, MCP, commands,
  subagents, permissions and env, the Codex→all direction, and the list of things that silently break.
- [docs/PROMPTS.md](docs/PROMPTS.md): prompts you can paste so an agent inventories, ports or audits your setup, writes a
  cross-agent guard, or continues a project started in another agent.

## Tests

```bash
bash tests/test_adapter.sh     # payloads from Codex/Gemini/OpenCode/Goose through the example guards + approval flow
python3 tests/test_sync.py     # full sync in a throwaway $HOME: every target, no secrets, idempotent, Codex writes preserved
```

## Safety notes

- Every generated file carries an agents-bridge marker. A file you wrote yourself gets a `.pre-bridge-<timestamp>.bak` copy before the first
  overwrite. Codex `config.toml` is merged inside a marked block, and Codex's own writes are preserved.
- Guards are only as good as your scripts. The adapter's job is to make sure they actually **run**, and that they **fail closed**.
- **Approvals are as strong as the sandbox.** A code is random, bound to the full SHA-256 of the exact call and session, single-use,
  and is honored only if the adapter itself issued it. But an agent that can write to `state_dir` can forge one, and so can any
  process running as your user without a sandbox. Codex `workspace-write` keeps `state_dir` out of reach, because sync.py
  never adds it to `writable_roots`. For Gemini, OpenCode, Qwen and Goose, run them sandboxed (Gemini `--sandbox`, a container or a
  separate user) if you need approvals that the agent cannot forge. The example `ask-agent-config.sh` guard only raises the bar.
- **Secret env vars and the shell.** For agents without a header helper, `bridge/agent` exports MCP header secrets into the agent
  process, so its shell tool inherits them too. The OpenCode guard plugin scrubs them from the shell env; the other agents don't.
  Prefer an agent's own keychain/OAuth support where it has one, and keep `mcp_secret_headers` minimal.
- A repo's `.mcp.json` is controlled by the repo. sync.py bridges only the servers you approved in Claude Code
  (`enabledMcpjsonServers` / `enableAllProjectMcpServers`), or the ones listed in `project_mcp_allow`.
- A guard that crashes (exit code other than 0 or 2) **blocks** in PreToolUse (`strict_errors`, default on). Claude Code itself would let the call through.
- `bridge/mcp-headers` prints secrets on stdout, because that is how header helpers work. Never run it inside an agent session.

## Po polsku (skrót)

agents-bridge przenosi Twój workflow z Claude Code albo z Codexa na pozostałe agenty: Codex, Gemini CLI, OpenCode
(także darmowe i lokalne modele open source), Qwen Code, Goose. Przenosi instrukcje, skille, komendy, podagentów, MCP, pamięć
i hooki-strażników. Hooki nie są kopiowane: każdy agent woła `adapter.py`, który tłumaczy zdarzenia i nazwy narzędzi
i uruchamia te same skrypty. Decyzja „ask”, którą Codex i Gemini po cichu przepuszczają, staje się blokadą z kodem
zgody. Start: `config.example.json` → `~/.agents/bridge.json`, potem `sync.py --dry-run`, `sync.py`, `doctor.py --deep`.

## License

MIT
