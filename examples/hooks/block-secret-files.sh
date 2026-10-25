#!/usr/bin/env bash
# block-secret-files - no agent reads or writes secret files (.env*, keys) - through file tools OR the shell.
input=$(cat)
CC_HOOK_INPUT="$input" python3 -I - <<'PY'
import json, os, re, sys
try:
    d = json.loads(os.environ["CC_HOOK_INPUT"])
except Exception:
    print("BLOCKED: block-secret-files could not parse input (fail closed).", file=sys.stderr); sys.exit(2)
SECRET = re.compile(r"(^|/)(\.env(\.[\w.-]+)?|id_rsa|id_ed25519|[\w.-]+\.pem)$")
ti = d.get("tool_input") or {}
tool = d.get("tool_name", "")
paths = [ti.get(k, "") for k in ("file_path", "path", "notebook_path") if ti.get(k)]
if tool == "Bash":
    paths += re.findall(r"[\w./~-]*(?:\.env(?:\.[\w.-]+)?|id_rsa|id_ed25519|\.pem)\b", ti.get("command", ""))
for p in paths:
    if SECRET.search(p.rstrip("/")) and not p.endswith((".example", ".sample", ".template")):
        print(f"BLOCKED: {tool} touches a secret file ({os.path.basename(p)}). Use metadata only (key names, length).",
              file=sys.stderr)
        sys.exit(2)
PY
