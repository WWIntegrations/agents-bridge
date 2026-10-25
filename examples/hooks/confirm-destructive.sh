#!/usr/bin/env bash
# confirm-destructive - destructive shell commands need the USER's yes (verdict "ask").
# Claude Code shows its permission prompt; through agents-bridge, Codex/Gemini/OpenCode/Goose get a block with an
# approval code that only the user can redeem ("approve CODE") - the agent cannot confirm for itself.
input=$(cat)
tool=$(printf '%s' "$input" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("tool_name",""))' 2>/dev/null) || {
  echo "BLOCKED: confirm-destructive could not parse input (fail closed)." >&2; exit 2; }
[ "$tool" = Bash ] || exit 0
cmd=$(printf '%s' "$input" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("tool_input",{}).get("command",""))')
if printf '%s' "$cmd" | grep -qiE 'rm[[:space:]]+-[a-z]*r[a-z]*f|rm[[:space:]]+-[a-z]*f[a-z]*r|git[[:space:]]+reset[[:space:]]+--hard|git[[:space:]]+push[^|;&]*[[:space:]]-(f|-force)|git[[:space:]]+clean[[:space:]]+-[a-z]*f|DROP[[:space:]]+TABLE|find[[:space:]].*-delete'; then
  echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"destructive command: show a dry run and a backup plan first"}}'
fi
exit 0
