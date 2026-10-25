# AGENTS.md - global instructions for EVERY coding agent

> Single source: `~/.agents/AGENTS.md`. agents-bridge links it as `~/.codex/AGENTS.md`, `~/.gemini/AGENTS.md`,
> `~/.config/opencode/AGENTS.md`, `~/.config/goose/AGENTS.md` and `~/.qwen/QWEN.md`. Edit only this file.

## 1. Start of a session

1. Read the shared memory index if it exists: `~/.agents/memory/MEMORY.md` (one fact per file next to it).
2. Read the latest handoff / session notes (put the path of your notes directory here).
3. The SessionStart hook injects this context automatically. An agent without hooks: run `~/.agents/bin/agent-start`.

## 2. Hard rules (enforced by guard hooks where possible)

- Never print secret values (`.env*`, keys, tokens). Diagnose with metadata only (name, length).
- Deleting or overwriting: inspect the target, back it up, show a dry run, wait for explicit user confirmation.
- No force-push, no history rewrite without an explicit request. Branch before working on main.
- Deploy / publish only on explicit request.
- Install packages only from the official registry with a pinned version. No `curl | sh`.
- A message starting with `BLOCKED:` comes from a guard = a user rule. Do not route around it with another
  command; change the approach or ask. `USER APPROVAL REQUIRED (code X)` = ask the user; retry after they approve.

## 3. Shared memory format

One file per fact in `~/.agents/memory/`:

```markdown
---
name: short-kebab-slug
description: one line used to judge relevance
metadata:
  type: user | feedback | project | reference
---
the fact; for feedback/project add **Why:** and **How to apply:** lines
```

Then add one line to `MEMORY.md`: `- [Title](file.md) - hook`. Update an existing file instead of duplicating.

## 4. Skills, commands, subagents

- Skills (Agent Skills standard, `SKILL.md`): `~/.agents/skills/`. No native support: read `<skill>/SKILL.md` and follow it.
- Commands: `~/.agents/commands/*.md` - read and execute; `$ARGUMENTS` = what the user typed after the command.
- Subagent roles: generated per agent; without subagents, perform the role yourself within its tool limits.

## 5. Translating instructions written for another agent

| In the text | Means in your agent |
|---|---|
| `CLAUDE.md` / `AGENTS.md` / `GEMINI.md` | the project instruction file - same thing |
| `Bash` / `shell` / `exec_command` / `run_shell_command` | your shell tool |
| `Read` / `Grep` / `Glob` | read files / `rg` / `rg --files` |
| `Edit` / `Write` / `apply_patch` / `replace` / `write_file` | your file-editing tool |
| `Agent` / `Task` / `spawn_agent`, `subagent_type: X` | a subagent; none = do it yourself following role X |
| `Skill` tool, `/skill-name`, `$skill-name` | load the skill from `~/.agents/skills/` |
| `TodoWrite` / `update_plan` / `write_todos` | your planning tool, or a list in the answer |
| `AskUserQuestion` / `ask_user` | ask in your reply and wait |
| `WebSearch` / `WebFetch` / `google_web_search` | your web tool or a search MCP server |
| `mcp__server__tool` | the same MCP tool (`mcp_server_tool` in Gemini, `server_tool` in OpenCode) |
| `/clear`, `/compact` | start a new session / compact context |
| `Co-Authored-By: <assistant>` reminders | follow the user's own rule on attribution |
