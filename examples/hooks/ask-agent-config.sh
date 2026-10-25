#!/usr/bin/env bash
# ask-agent-config - an agent must not silently change its own (or another agent's) guard configuration.
# Verdict "ask": Claude Code shows a prompt; through agents-bridge other agents get an approval code.
input=$(cat)
CC_HOOK_INPUT="$input" python3 -I - <<'PY'
import json, os, re, sys
d = json.loads(os.environ["CC_HOOK_INPUT"])
ti = d.get("tool_input") or {}
text = " ".join(str(ti.get(k, "")) for k in ("file_path", "command"))
PROTECTED = r"\.claude/settings[^/\s]*\.json|\.claude\.json|\.codex/(config\.toml|hooks\.json|rules)|\.gemini/(settings\.json|policies)" \
            r"|opencode/(opencode\.jsonc?|plugins?)|\.qwen/settings\.json|\.agents/(bridge\.json|hooks\.json|state|plugins)|agents-bridge/bridge/"
if re.search(PROTECTED, text) and not (d.get("tool_name") in ("Read", "Grep", "Glob")):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                              "permissionDecisionReason": "changes agent guard configuration"}}))
PY
exit 0
