#!/usr/bin/env bash
# session-context - SessionStart: plain stdout becomes context in Claude Code, Codex and Qwen
# (agents-bridge wraps it in JSON for Gemini).
echo "Today is $(date '+%Y-%m-%d (%A)'). Agent: ${AGENT_HARNESS:-claude}."
latest=$(ls -1t "$HOME/notes/handoffs/"*.md 2>/dev/null | head -1)
[ -n "$latest" ] && echo "Latest handoff: $latest"
exit 0
