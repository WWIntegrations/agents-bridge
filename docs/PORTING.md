# Porting a coding-agent workflow: Claude Code ⇄ Codex ⇄ everything else

This is the reference behind `bridge/sync.py` and `bridge/adapter.py`. You can also use it by hand,
or give it to an agent together with [PROMPTS.md](PROMPTS.md).

Versions verified: Claude Code 2.x, Codex CLI 0.164, Gemini CLI 0.65, OpenCode 1.19 (V1 config), Qwen Code 0.26, Goose 1.56.
These tools change quickly. Before you trust a row, check it against the agent's `--help` or its docs.

## 1. The neutral layer

Whichever agent you start from, put the shared material in one place that every agent can read:

```
~/.agents/
  AGENTS.md          global instructions (linked into each agent's global instruction file)
  skills/<name>/SKILL.md   Agent Skills standard: Codex, Gemini, OpenCode, Qwen and Goose read this natively
  commands/*.md      slash commands / prompts (markdown + optional frontmatter, $ARGUMENTS)
  memory/            optional: one-fact-per-file memory + MEMORY.md index (link to your Claude memory dir)
  bridge.json        agents-bridge config
  state/             approvals (NOT writable by the agent sandbox)
```

Hooks are not copied anywhere. Each agent's hook config calls `adapter.py --dispatch`, and the adapter reads the
hook list live from the single source file, so a guard you add once runs in every agent.

## 2. Instruction files

| | global | per project | fallback / notes |
|---|---|---|---|
| Claude Code | `~/.claude/CLAUDE.md` | `CLAUDE.md`, `.claude/CLAUDE.md` | `@path` imports |
| Codex | `~/.codex/AGENTS.md` | `AGENTS.md` (root → cwd, concatenated) | `project_doc_fallback_filenames = ["CLAUDE.md"]`, `project_doc_max_bytes` |
| Gemini CLI | `~/.gemini/GEMINI.md` | `GEMINI.md` | `context.fileName = ["AGENTS.md","CLAUDE.md","GEMINI.md"]` |
| OpenCode | `~/.config/opencode/AGENTS.md` | `AGENTS.md` | reads `CLAUDE.md` too (`OPENCODE_DISABLE_CLAUDE_CODE*` to turn off); `instructions: [...]` globs |
| Qwen Code | `~/.qwen/QWEN.md` | `QWEN.md` | `context.fileName` like Gemini |
| Goose | `~/.config/goose/AGENTS.md` (or `.goosehints`) | `AGENTS.md`, `.goosehints` | `CONTEXT_FILE_NAMES` env |

## 3. Hook events

| Claude Code | Codex | Gemini CLI | Qwen | OpenCode (plugin) | Goose (plugin hooks.json) |
|---|---|---|---|---|---|
| `SessionStart` | `SessionStart` | `SessionStart` | `SessionStart` | `session.created` event | `SessionStart` |
| `UserPromptSubmit` | `UserPromptSubmit` | `BeforeAgent` | `UserPromptSubmit` | `chat.message` | `UserPromptSubmit` |
| `PreToolUse` | `PreToolUse` | `BeforeTool` | `PreToolUse` | `tool.execute.before` (throw = block) | `PreToolUse` |
| `PostToolUse` | `PostToolUse` | `AfterTool` | `PostToolUse` | `tool.execute.after` | `PostToolUse` |
| `Stop` | `Stop` (stdout must be JSON) | `AfterAgent` | `Stop` | `session.idle` event | `Stop` |
| `PreCompact`, `Notification`, `SubagentStop` | - / partial | `PreCompress`, `Notification` | same as Claude | events | - |

Verdict semantics:

| | block | ask the user | add context |
|---|---|---|---|
| Claude Code | exit 2 + stderr, or `permissionDecision: deny` | `permissionDecision: ask` | stdout (SessionStart/UserPromptSubmit) or `additionalContext` |
| Codex | exit 2 + stderr / `deny` | **not supported: fails open (call proceeds)** | plain stdout; `additionalContextLimit` |
| Gemini | exit 2, or JSON `decision: deny` | not supported | JSON `hookSpecificOutput.additionalContext` only (stdout must be JSON) |
| OpenCode | throw in `tool.execute.before` | `permission: ask` in config only | `experimental.chat.system.transform` |
| Goose | exit 2 | - | stdout |

The adapter turns `ask` into a block that carries a random 8-hex code. The user replies `approve CODE`, the
UserPromptSubmit hook records that, and the identical tool call passes once for `approval_ttl` seconds. Approvals are
stored outside every sandbox's writable roots, so the agent cannot approve itself.

Trust: Codex runs a hook only after you trust it once in `/hooks`; the hash lives in `config.toml [hooks.state]`.
Gemini runs hooks and MCP only in trusted folders (`~/.gemini/trustedFolders.json`) when `hooksConfig.enabled` is set.

## 4. Tool names (what your guards will see after normalization)

| Claude | Codex | Gemini | OpenCode | Qwen | Goose |
|---|---|---|---|---|---|
| `Bash` (`command`) | `Bash` / `exec_command` (`cmd` argv) / `shell` | `run_shell_command` | `bash` | `run_shell_command` | `developer__shell` |
| `Edit` (`file_path`, `old_string`, `new_string`) | `apply_patch` (patch text in `command`), matcher `Edit\|Write` | `replace` | `edit` | `edit` | `developer__text_editor` `str_replace` |
| `Write` | `apply_patch` `*** Add File` | `write_file` | `write` | `write_file` | `text_editor` `write` |
| `Read` | shell `cat`/`sed` | `read_file` (`absolute_path`) | `read` | `read_file` | `text_editor` `view` |
| `Grep` / `Glob` | shell `rg` | `grep_search` / `glob` | `grep` / `glob` | `grep_search` / `glob` | shell |
| `WebFetch` / `WebSearch` | `web_search` | `web_fetch` / `google_web_search` | `webfetch` | `web_fetch` | extension |
| `Task` / `Agent` | `spawn_agent` (multi_agent) | subagents (`~/.gemini/agents/*.md`) | `task` | `task` | subrecipes |
| `TodoWrite` | `update_plan` | `write_todos` | `todowrite` | `todo_write` | `todo` |
| `mcp__srv__tool` | `mcp__srv__tool` | `mcp_srv_tool` | `srv_tool` | `mcp__srv__tool` | `srv__tool` |

`adapter.py` maps all of these to the Claude names, so one guard script works everywhere. A Codex `apply_patch` is split into one
`Edit`/`Write` per file. `*** Delete File` becomes `Bash rm -rf <file>`, so a "confirm deletions" guard also covers patches.

## 5. MCP servers

| | file | stdio | HTTP | secret headers |
|---|---|---|---|---|
| Claude | `~/.claude.json`, `.mcp.json` | `command`,`args`,`env` | `type: http`, `url` | literal `headers`, `headersHelper` |
| Codex | `config.toml [mcp_servers.x]` | `command`,`args`,`env`,`env_vars` | `url` | `http_headers_helper`, `env_http_headers`, `bearer_token_env_var`; per tool `approval_mode="prompt"` |
| Gemini / Qwen | `settings.json mcpServers` | `command` | `httpUrl` | `headers` with `$VAR` expansion |
| OpenCode | `opencode.json mcp` | `type: local`, `command: [..]` | `type: remote`, `url` | `{env:VAR}` |
| Goose | `config.yaml extensions` | `type: stdio` | `type: streamable_http` | `headers`, `env_keys` |

Rule: never copy a secret value into a generated file. With Codex, use a header helper (`bridge/mcp-headers`). Other agents
get `$VAR` references, and `bridge/agent` exports the values only into that agent's process.

## 6. Commands, subagents, permissions, settings

| Claude | Codex | Gemini | OpenCode | Qwen |
|---|---|---|---|---|
| `~/.claude/commands/x.md` → `/x` | `~/.codex/prompts/x.md` → `/prompts:x` (deprecated, use skills) | `~/.gemini/commands/x.toml` (`prompt`, `{{args}}`) | `~/.config/opencode/commands/x.md` | `~/.qwen/commands/x.md` |
| `~/.claude/agents/x.md` (frontmatter `tools`) | `~/.codex/agents/x.toml` (`developer_instructions`) | `~/.gemini/agents/x.md` | `~/.config/opencode/agents/x.md` (`mode: subagent`) | `~/.qwen/agents/x.md` |
| `permissions.deny` | `rules/*.rules` `prefix_rule(decision="forbidden")` | `policies/*.toml` `decision="deny"` | `permission.bash."git push --force*" = "deny"` | `permissions.deny` |
| `permissions.ask` | `decision="prompt"` / MCP `approval_mode` | `decision="ask_user"` | `"ask"` | `permissions.ask` |
| auto mode / classifier | `approvals_reviewer = "auto_review"` | `--approval-mode auto_edit` | - | `approvalMode` |
| `env` in settings | shell env / `shell_environment_policy` | `.gemini/.env` | `shell.env` plugin hook | `.qwen/.env` |
| `CLAUDE_PROJECT_DIR` | hook `cwd` | `GEMINI_PROJECT_DIR` | `directory` | `QWEN_PROJECT_DIR` |
| session id | `CODEX_THREAD_ID` | `session_id` in payload | `sessionID` | payload |
| plugins (`enabledPlugins`) | plugins (`~/.codex/plugins`, marketplaces) | extensions | npm plugins | extensions |

Codex dropped `[profiles]` in 0.134. Use separate `CODEX_HOME` directories, or pass flags (`-c key=value`).

## 7. Codex as the source (Codex → all)

Set `"source": "codex"` in `bridge.json`. The table at the top of `bridge/sync.py` shows what is read from where. In short:
- `~/.codex/hooks.json` is the hook source (it already has the Claude shape), and the adapter dispatches from it for every other agent.
  Claude Code can call the same adapter (`--harness claude`), or point at the hooks directly.
- `~/.codex/AGENTS.md` becomes `~/.agents/AGENTS.md`, and `~/.claude/CLAUDE.md` links to it.
- `~/.codex/prompts/*.md` become commands everywhere. `~/.codex/agents/*.toml` become Claude/Gemini/OpenCode subagent markdown.
- `[mcp_servers]` become `~/.claude.json` / Gemini / OpenCode entries. `env_http_headers` becomes `$VAR` headers.
- `rules/*.rules` stay Codex-only. Translate the important `forbidden` prefixes into `permissions.deny` by hand (§6).

## 8. Things that silently break (check them with `bridge/doctor.py`)

1. **An untrusted Codex hook does not run.** Codex shows nothing. Open `/hooks` once after every change to `hooks.json`.
2. **`ask` fails open** in Codex and Gemini. Without the adapter, a guard that relies on asking simply lets the call through.
3. **Gemini in an untrusted folder** runs no MCP and no hooks.
4. **The hook interpreter is old.** macOS `/usr/bin/python3` is 3.9: no `tomllib`, no `match`. The adapter stays 3.9-safe on purpose.
5. **The sandbox can write approval state.** Keep `state_dir` outside Codex `writable_roots`, and protect it with a guard.
6. **A missing guard script** (`rc 127`) must block, not pass. The adapter treats 126/127 and timeouts as a block in PreToolUse.
7. **Plugin SessionStart hooks with side effects** (spawning `claude -p`, rewriting files) should not run inside other agents.
   Put them in `skip`, and re-implement only the read-only part in `context_files`.
8. **Codex MCP `approval_mode = "prompt"` did not stop a call in `codex exec`** (live test, 0.164). Codex also rewrites `-` to `_`
   in MCP tool names it sends to hooks. Gate paid tools in a PreToolUse hook (`ask_tools`).
9. **Codex rewrites `config.toml`** (model, trust). sync.py merges inside a marked block, and the tests cover this.
