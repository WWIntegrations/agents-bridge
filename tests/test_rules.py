#!/usr/bin/env python3
"""test_rules - Bash(...) approval-rule matcher: every bypass found in review must stay a hit."""
import os
from pathlib import Path
import importlib.util, sys
sp = importlib.util.spec_from_file_location("ad", str(Path(__file__).resolve().parent.parent / "bridge" / "adapter.py")); m = importlib.util.module_from_spec(sp)
os.environ.setdefault("AGENTS_BRIDGE_CONFIG", "/nonexistent"); sys.argv = ["x"]; sp.loader.exec_module(m)
F = "--" + "force"
N = "npm publish:*"
cases = [(N, "npm publish", 1), (N, "npm publish --tag x", 1), (N, "cd a && npm publish", 1), (N, "cd a & npm publish", 1),
         (N, "echo\nnpm publish", 1), (N, "sudo npm publish", 1), (N, "FOO=1 npm publish", 1), (N, "(npm publish)", 1),
         (N, "bash -c 'npm publish'", 1), (N, "echo $(npm publish)", 1), (N, "\"npm\" publish", 1), (N, "n\\pm publish", 1),
         (N, "npm pub\\\nlish", 1), (N, "/usr/local/bin/npm publish", 1), (N, "'/usr/bin/npm' 'publish'", 1),
         (N, "x=npm; $x publish", 1), (N, "n${x}pm publish", 1), (N, "np? publish", 1), (N, "{npm,x} publish", 1),
         (N, "xargs npm publish < list", 1), (N, "find . -exec npm publish \\;", 1), (N, "npm  publish", 1),
         ("/usr/bin/npm publish:*", "npm publish", 1), ("/usr/bin/npm publish:*", "/opt/npm publish", 1),
         (N, "<in npm publish", 1), (N, "npm>log publish", 1), (N, ">x npm publish", 1), (N, "2>&1 npm publish", 1), (N, "npm test", 0), (N, "npm publisher", 0), (N, "ls -la", 0), (N, "cat package.json | grep version", 0),
         ("git push*origin", "git push " + F + " origin", 1), ("git push*origin", "git status", 0)]
bad = 0
for a, c, w in cases:
    got = int(m.bash_rule_hit(a, c))
    if got != w:
        bad += 1
        print("ZLE", repr(a), repr(c), got, w)
print("-- rules:", len(cases) - bad, "/", len(cases))
sys.exit(1 if bad else 0)
