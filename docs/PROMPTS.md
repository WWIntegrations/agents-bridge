# Prompts: let an agent port (or audit) your workflow

Paste these into the agent that will do the work. Replace `<SOURCE>` and `<TARGETS>` with the agents you use.
Every prompt makes the agent prove its claims on disk, because a port that only looks done fails silently later.

## 1. Inventory (read-only)

```
Inventory my <SOURCE> setup without changing anything. List with counts and paths:
instruction files (global + per repo), skills (incl. plugin-provided), slash commands, subagents,
hooks per event with their matcher and script path, permission allow/deny/ask, MCP servers (name, transport,
how auth is provided - NEVER print header or env values, only their names and lengths), env vars set in settings,
memory/notes locations, and any hook that has side effects beyond printing (spawns processes, writes files, network).
Output a table. Mark anything you could not read.
```

## 2. Port with agents-bridge

```
Install agents-bridge from <repo path> for source=<SOURCE>, targets=<TARGETS>.
1. Copy config.example.json to ~/.agents/bridge.json and fill it from the inventory: hooks_source, repos, memory_dir,
   context_files, mcp_secret_headers (names of env vars only), skip (hooks with side effects that must not run in
   other agents).
2. Run `python3 bridge/sync.py --dry-run` and show me the list of files it would write. Wait for my OK.
3. Run it for real, then `python3 bridge/doctor.py --deep`. Every red line must be fixed or explained.
4. For each target, prove that a guard fires: send the adapter a payload in that agent's format
   (see tests/test_adapter.sh) that should be blocked, and one that should pass. Show both exit codes.
5. List the manual steps only I can do (logins, trusting hooks in Codex /hooks, OAuth for MCP).
Do not print secrets. Do not delete anything; sync.py makes .bak copies before overwriting.
```

## 3. Port by hand (no tool)

```
Port my <SOURCE> workflow to <TARGET> using docs/PORTING.md as the mapping reference.
Rules:
- Keep one source of truth. Link (symlink) instructions, skills and commands, don't fork copies.
- Hooks: keep the scripts where they are; register them in <TARGET>'s hook config with the event/tool-name mapping
  from PORTING.md §3-4. If <TARGET> can't "ask", the guard must block instead (it fails open otherwise).
- MCP: translate each server; secrets as env-var references or a header helper, never literal values.
- Permissions: deny-lists become <TARGET>'s rules/policies; verify each one with a test command that must be refused.
- After each step show the generated file and a command that proves it loads (e.g. `codex debug prompt-input`,
  `codex mcp list`, `gemini mcp list`, `opencode debug config`).
Stop and ask before overwriting any existing file.
```

## 4. Audit an existing port

```
Audit my multi-agent setup. For EACH installed agent (<TARGETS>), verify on disk and by running it:
1. Global instructions actually load (show the command output that contains a line from them).
2. Skills are discoverable (count) and one skill can be loaded.
3. Every PreToolUse guard from the source runs: craft one blocked + one allowed call per guard in that agent's
   payload format through the adapter; report exit codes. A guard that cannot fire = RED.
4. "ask"-type guards don't fail open (Codex/Gemini).
5. MCP servers connect (no secret values in output).
6. The agent's sandbox cannot write the approval state directory.
7. Session-start context (memory index, notes) arrives, and its size stays under the agent's injection limit.
Output a table agent × check with OK/RED and the evidence. No fixes without asking.
```

## 5. Continue a project in a different agent

```
I'm continuing work started in another agent. Before anything else:
read ~/.agents/AGENTS.md, the memory index (~/.agents/memory/MEMORY.md) and the latest handoff/notes it points to,
and the project's AGENTS.md/CLAUDE.md. Then summarize in 5 lines: current goal, last state, next step,
open risks, and which rules from the memory apply here. Instructions written for another agent use its tool
names - translate them with the table in AGENTS.md §5. If a guard blocks you, don't route around it.
```

## 6. Write a guard that works in every agent

```
Write a PreToolUse guard script in the Claude Code hook format (JSON on stdin with tool_name/tool_input,
exit 2 + message on stderr = block, or print {"hookSpecificOutput":{"hookEventName":"PreToolUse",
"permissionDecision":"ask","permissionDecisionReason":"..."}} to ask). It must:
<what to guard>.
Use only Bash + /usr/bin/python3 (3.9-compatible), no network. Handle the tool names Bash, Edit, Write, Read
(the adapter normalizes every agent to these). Add 3 blocked and 3 allowed cases to tests/test_adapter.sh and run it.
```
