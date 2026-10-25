#!/usr/bin/env bash
# test_adapter - example guards run through the adapter with Codex / Gemini / OpenCode / Goose payloads.
# Self-contained: temp state dir + temp config pointing at examples/hooks.json.
R="$(cd "$(dirname "$0")/.." && pwd)"
A="$R/bridge/adapter.py"
H="$R/examples/hooks"
PY="/usr/bin/python3 -I"
T=$(mktemp -d)
export AGENTS_STATE_DIR="$T/state" AGENTS_BRIDGE_HOME="$R"
printf '{"hooks_source":"%s","approval_word":"approve","ask_tools":["mcp__paid__generate-*","mcp__wholesrv","mcp__argsrv__run(x)","WebFetch","Bash(npm publish:*)"]}' "$R/examples/hooks.json" > "$T/bridge.json"
export AGENTS_BRIDGE_CONFIG="$T/bridge.json"
ok=0; bad=0
check() {  # desc harness expected_rc payload [hook...]  (no hook = --dispatch)
  local d=$1 h=$2 want=$3 pl=$4; shift 4
  local err rc
  if [ $# -eq 0 ]; then err=$(printf '%s' "$pl" | $PY "$A" --harness "$h" --event PreToolUse --dispatch 2>&1 >/dev/null); rc=$?
  else err=$(printf '%s' "$pl" | $PY "$A" --harness "$h" --event PreToolUse -- "$@" 2>&1 >/dev/null); rc=$?; fi
  if [ "$rc" = "$want" ]; then ok=$((ok+1)); echo "OK   $d"; else bad=$((bad+1)); echo "FAIL $d: rc=$rc want $want :: ${err:0:160}"; fi
}
RM='rm -rf ./build-output'
CF='git reset --hard HEAD~3'
check "codex Bash rm -rf"               codex 2 "{\"cwd\":\"/tmp\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"$RM\"}}"
check "codex exec_command argv"         codex 2 "{\"cwd\":\"/tmp\",\"tool_name\":\"exec_command\",\"tool_input\":{\"cmd\":[\"bash\",\"-lc\",\"$CF\"]}}"
check "codex ls passes"                 codex 0 '{"cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"ls -la"}}'
check "agent cannot self-confirm"       codex 2 "{\"cwd\":\"/tmp\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"$RM # confirmed-cleanup\"}}"
check "codex patch .env"                codex 2 '{"cwd":"/tmp/p","tool_name":"apply_patch","tool_input":{"command":"*** Begin Patch\n*** Update File: .env.local\n@@\n-A=1\n+A=2\n*** End Patch"}}'
check "codex patch normal file"         codex 0 '{"cwd":"/tmp/p","tool_name":"apply_patch","tool_input":{"command":"*** Begin Patch\n*** Add File: notes.txt\n+hi\n*** End Patch"}}'
check "codex patch Delete = confirm"    codex 2 '{"cwd":"/tmp/p","tool_name":"apply_patch","tool_input":{"command":"*** Begin Patch\n*** Delete File: src/app.ts\n*** End Patch"}}'
check "codex cat .env via shell"        codex 2 '{"cwd":"/tmp/p","tool_name":"Bash","tool_input":{"command":"cat .env"}}'
check "codex edit hooks.json = ask"     codex 2 '{"cwd":"/tmp","tool_name":"apply_patch","tool_input":{"command":"*** Begin Patch\n*** Update File: /Users/x/.codex/hooks.json\n@@\n-a\n+b\n*** End Patch"}}'
check "gemini run_shell_command"        gemini 2 "{\"cwd\":\"/tmp\",\"tool_name\":\"run_shell_command\",\"tool_input\":{\"command\":\"$RM\"}}"
check "gemini read_file .env"           gemini 2 '{"cwd":"/tmp/p","tool_name":"read_file","tool_input":{"absolute_path":"/tmp/p/.env"}}'
check "gemini write_file normal"        gemini 0 '{"cwd":"/tmp","tool_name":"write_file","tool_input":{"file_path":"/tmp/x.txt","content":"a"}}'
check "opencode bash"                   opencode 2 "{\"cwd\":\"/tmp\",\"tool_name\":\"bash\",\"tool_input\":{\"command\":\"$CF\"}}"
check "goose developer__shell"          goose 2 "{\"cwd\":\"/tmp\",\"tool_name\":\"developer__shell\",\"tool_input\":{\"command\":\"$RM\"}}"
check "goose text_editor write .env"    goose 2 '{"cwd":"/tmp/p","tool_name":"developer__text_editor","tool_input":{"command":"write","path":"/tmp/p/.env","file_text":"A=1"}}'
check "missing guard = fail closed"     codex 2 '{"cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"ls"}}' "$H/does-not-exist.sh"
check "bad input = fail closed"         codex 2 'not json'

# approval protocol: ask -> code -> "approve CODE" -> identical retry passes
PL='{"cwd":"/tmp","tool_name":"Edit","tool_input":{"file_path":"/Users/x/.gemini/settings.json","old_string":"a","new_string":"b"}}'
code=$(printf '%s' "$PL" | $PY "$A" --harness codex --event PreToolUse --dispatch 2>&1 >/dev/null | sed -n 's/.*(code \([0-9A-F]\{8\}\)).*/\1/p' | head -1)
printf '{"prompt":"ok, approve %s"}' "$code" | $PY "$A" --harness codex --event UserPromptSubmit --dispatch >/dev/null
check "approve $code unlocks ask"       codex 0 "$PL"
check "approval is per call"           codex 2 "{\"cwd\":\"/tmp\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"$RM\"}}"
code2=$(printf '{"cwd":"/tmp/p","tool_name":"Bash","tool_input":{"command":"cat .env"}}' | $PY "$A" --harness codex --event PreToolUse --dispatch 2>&1 >/dev/null | sed -n 's/.*(code \([0-9A-F]\{8\}\)).*/\1/p')
printf '{"prompt":"approve %s"}' "${code2:-000000}" | $PY "$A" --harness codex --event UserPromptSubmit --dispatch >/dev/null
check "deny has no code, stays blocked"   codex 2 '{"cwd":"/tmp/p","tool_name":"Bash","tool_input":{"command":"cat .env"}}'

# hardening (security review): single-use approvals, unissued codes, crashed guards, more tool names
check "approval is single-use"          codex 2 "$PL"
printf '{"prompt":"approve ABCDEF12"}' | $PY "$A" --harness codex --event UserPromptSubmit --dispatch >/dev/null
[ ! -e "$AGENTS_STATE_DIR/approvals/ABCDEF12" ] && { ok=$((ok+1)); echo "OK   unissued code is ignored"; } || { bad=$((bad+1)); echo "FAIL unissued code recorded"; }
c3=$(printf '%s' "$PL" | $PY "$A" --harness codex --event PreToolUse --dispatch 2>&1 >/dev/null | sed -n 's/.*(code \([0-9A-F]\{8\}\)).*/\1/p')
c4=$(printf '%s' "$PL" | $PY "$A" --harness codex --event PreToolUse --dispatch 2>&1 >/dev/null | sed -n 's/.*(code \([0-9A-F]\{8\}\)).*/\1/p')
[ -n "$c3" ] && [ "$c3" = "$c4" ] && { ok=$((ok+1)); echo "OK   same call reuses its code"; } || { bad=$((bad+1)); echo "FAIL code reuse $c3/$c4"; }
echo x > "$AGENTS_STATE_DIR/approvals/$c3"
check "file named by code is no approval" codex 2 "$PL"
printf '#!/bin/sh\nexit 1\n' > "$T/crash.sh"; chmod +x "$T/crash.sh"
check "crashed guard (rc=1) blocks"     codex 2 '{"cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"ls"}}' "$T/crash.sh"
check "codex write_stdin is a shell"    codex 2 "{\"cwd\":\"/tmp\",\"tool_name\":\"write_stdin\",\"tool_input\":{\"session_id\":1,\"chars\":\"$RM\\n\"}}"
check "opencode patch tool"             opencode 2 '{"cwd":"/tmp/p","tool_name":"patch","tool_input":{"patchText":"*** Begin Patch\n*** Update File: .env\n@@\n-A\n+B\n*** End Patch"}}'
printf '#!/bin/sh\ngrep -q "\\"tool_name\\": *\\"mcp__my_srv__" && { echo "BLOCKED my_srv" >&2; exit 2; }; exit 0\n' > "$T/g.sh"; chmod +x "$T/g.sh"
check "gemini ambiguous MCP name: every split checked" gemini 2 '{"cwd":"/tmp","tool_name":"mcp_my_srv_do_thing","tool_input":{}}' "$T/g.sh"
check "gemini other MCP server passes"   gemini 0 '{"cwd":"/tmp","tool_name":"mcp_other_do_thing","tool_input":{}}' "$T/g.sh"

check "ask_tools blocks paid MCP (codex _ name)" codex 2 '{"cwd":"/tmp","tool_name":"mcp__paid__generate_video","tool_input":{}}'
check "free MCP tool passes"            codex 0 '{"cwd":"/tmp","tool_name":"mcp__paid__balance","tool_input":{}}'

check "bare mcp__srv rule = every tool"  codex 2 '{"cwd":"/tmp","tool_name":"mcp__wholesrv__anything","tool_input":{}}'
check "rule with (args) still gates"     codex 2 '{"cwd":"/tmp","tool_name":"mcp__argsrv__run","tool_input":{}}'
check "goose srv__tool form gated"       goose 2 '{"cwd":"/tmp","tool_name":"paid__generate_video","tool_input":{}}'
check "opencode srv_tool form gated"     opencode 2 '{"cwd":"/tmp","tool_name":"paid_generate_image","tool_input":{}}'
check "gemini mcp_srv_tool form gated"   gemini 2 '{"cwd":"/tmp","tool_name":"mcp_wholesrv_x","tool_input":{}}'
check "unrelated builtin passes"         opencode 0 '{"cwd":"/tmp","tool_name":"read","tool_input":{"filePath":"/tmp/x"}}'

check "non-MCP rule WebFetch (gemini web_fetch)" gemini 2 '{"cwd":"/tmp","tool_name":"web_fetch","tool_input":{"url":"https://x"}}'
check "Bash(npm publish:*) gates that command"  codex 2 '{"cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"cd pkg && npm publish --access public"}}'
check "Bash rule does not gate other commands"  codex 0 '{"cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"npm test"}}'

# context events
out=$(printf '{"cwd":"/tmp","session_id":"s","source":"startup"}' | $PY "$A" --harness codex --event SessionStart --dispatch)
case "$out" in *"Today is"*) ok=$((ok+1)); echo "OK   SessionStart context (plain)";; *) bad=$((bad+1)); echo "FAIL SessionStart: $out";; esac
out=$(printf '{"cwd":"/tmp","session_id":"s","source":"startup"}' | $PY "$A" --harness gemini --event SessionStart --dispatch)
if printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert "Today is" in d["hookSpecificOutput"]["additionalContext"]' 2>/dev/null
then ok=$((ok+1)); echo "OK   SessionStart context (gemini JSON)"; else bad=$((bad+1)); echo "FAIL gemini SessionStart: $out"; fi

rm -rf "$T"
echo "-- adapter: $ok OK, $bad FAIL"
[ "$bad" = 0 ]
