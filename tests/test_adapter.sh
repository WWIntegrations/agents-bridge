#!/usr/bin/env bash
# test_adapter - example guards run through the adapter with Codex / Gemini / OpenCode / Goose payloads.
# Self-contained: temp state dir + temp config pointing at examples/hooks.json.
R="$(cd "$(dirname "$0")/.." && pwd)"
A="$R/bridge/adapter.py"
H="$R/examples/hooks"
PY="/usr/bin/python3 -I"
T=$(mktemp -d)
export AGENTS_STATE_DIR="$T/state" AGENTS_BRIDGE_HOME="$R"
printf '{"hooks_source":"%s","approval_word":"approve"}' "$R/examples/hooks.json" > "$T/bridge.json"
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
code=$(printf '%s' "$PL" | $PY "$A" --harness codex --event PreToolUse --dispatch 2>&1 >/dev/null | sed -n 's/.*(code \([0-9A-F]\{6\}\)).*/\1/p' | head -1)
printf '{"prompt":"ok, approve %s"}' "$code" | $PY "$A" --harness codex --event UserPromptSubmit --dispatch >/dev/null
check "approve $code unlocks ask"       codex 0 "$PL"
check "approval is per call"           codex 2 "{\"cwd\":\"/tmp\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"$RM\"}}"
code2=$(printf '{"cwd":"/tmp/p","tool_name":"Bash","tool_input":{"command":"cat .env"}}' | $PY "$A" --harness codex --event PreToolUse --dispatch 2>&1 >/dev/null | sed -n 's/.*(code \([0-9A-F]\{6\}\)).*/\1/p')
printf '{"prompt":"approve %s"}' "${code2:-000000}" | $PY "$A" --harness codex --event UserPromptSubmit --dispatch >/dev/null
check "deny has no code, stays blocked"   codex 2 '{"cwd":"/tmp/p","tool_name":"Bash","tool_input":{"command":"cat .env"}}'

# hardening (security review): single-use approvals, unissued codes, crashed guards, more tool names
check "approval is single-use"          codex 2 "$PL"
printf '{"prompt":"approve ABCDEF"}' | $PY "$A" --harness codex --event UserPromptSubmit --dispatch >/dev/null
[ ! -e "$AGENTS_STATE_DIR/approvals/ABCDEF" ] && { ok=$((ok+1)); echo "OK   unissued code is ignored"; } || { bad=$((bad+1)); echo "FAIL unissued code recorded"; }
mkdir -p "$AGENTS_STATE_DIR/approvals"; c3=$(printf '%s' "$PL" | $PY "$A" --harness codex --event PreToolUse --dispatch 2>&1 >/dev/null | sed -n 's/.*(code \([0-9A-F]\{6\}\)).*/\1/p')
echo "0000" > "$AGENTS_STATE_DIR/approvals/$c3"
check "forged approval (wrong hash) fails" codex 2 "$PL"
printf '#!/bin/sh\nexit 1\n' > "$T/crash.sh"; chmod +x "$T/crash.sh"
check "crashed guard (rc=1) blocks"     codex 2 '{"cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"ls"}}' "$T/crash.sh"
check "codex write_stdin is a shell"    codex 2 "{\"cwd\":\"/tmp\",\"tool_name\":\"write_stdin\",\"tool_input\":{\"session_id\":1,\"chars\":\"$RM\\n\"}}"
check "opencode patch tool"             opencode 2 '{"cwd":"/tmp/p","tool_name":"patch","tool_input":{"patchText":"*** Begin Patch\n*** Update File: .env\n@@\n-A\n+B\n*** End Patch"}}'
mkdir -p "$T/home/.gemini"; printf '{"mcpServers":{"my_srv":{}}}' > "$T/home/.gemini/settings.json"
out=$(printf '{"cwd":"/tmp","tool_name":"mcp_my_srv_do_thing","tool_input":{}}' | HOME="$T/home" $PY "$A" --harness gemini --event PreToolUse -- /bin/sh -c 'python3 -c "import json,sys;print(json.load(sys.stdin)[\"tool_name\"],file=sys.stderr)"; exit 2' 2>&1 >/dev/null)
case "$out" in *mcp__my_srv__do_thing*) ok=$((ok+1)); echo "OK   gemini MCP server with underscore";; *) bad=$((bad+1)); echo "FAIL gemini MCP name: $out";; esac

# context events
out=$(printf '{"cwd":"/tmp","session_id":"s","source":"startup"}' | $PY "$A" --harness codex --event SessionStart --dispatch)
case "$out" in *"Today is"*) ok=$((ok+1)); echo "OK   SessionStart context (plain)";; *) bad=$((bad+1)); echo "FAIL SessionStart: $out";; esac
out=$(printf '{"cwd":"/tmp","session_id":"s","source":"startup"}' | $PY "$A" --harness gemini --event SessionStart --dispatch)
if printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert "Today is" in d["hookSpecificOutput"]["additionalContext"]' 2>/dev/null
then ok=$((ok+1)); echo "OK   SessionStart context (gemini JSON)"; else bad=$((bad+1)); echo "FAIL gemini SessionStart: $out"; fi

rm -rf "$T"
echo "-- adapter: $ok OK, $bad FAIL"
[ "$bad" = 0 ]
