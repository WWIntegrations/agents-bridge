#!/usr/bin/env python3
"""agents-bridge doctor: is the shared layer actually working in every installed agent?
A guard that is silently off looks exactly like a guard that found nothing - so every line is a check on disk.

  python3 doctor.py           full report
  python3 doctor.py --quiet   errors only; exit 1 if anything is red (used by the `agent` launcher)
  python3 doctor.py --deep    + run the test suites
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HOME = Path.home()
HERE = Path(__file__).resolve().parent
AG = HOME / ".agents"
CFG_PATH = Path(os.environ.get("AGENTS_BRIDGE_CONFIG", AG / "bridge.json"))
res: list[tuple[str, str, str]] = []


def add(level, area, msg):
    res.append((level, area, msg))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--deep", action="store_true")
    a = ap.parse_args()

    try:
        cfg = json.loads(CFG_PATH.read_text())
        add("OK", "config", str(CFG_PATH))
    except (OSError, ValueError) as e:
        cfg = {}
        add("WARN", "config", f"{CFG_PATH} unreadable ({e}) - defaults in use")
    src = Path(os.path.expanduser(cfg.get("hooks_source", "~/.agents/hooks.json")))
    try:
        hooks = json.loads(src.read_text()).get("hooks") or {}
        n = sum(len(g.get("hooks", [])) for ev in hooks.values() for g in ev)
        add("OK" if n else "WARN", "hooks", f"{n} hook(s) in {src}")
        missing = set()
        for ev in hooks.values():
            for g in ev:
                for h in g.get("hooks", []):
                    exe = os.path.expandvars(str(h.get("command", "")).split()[0]) if h.get("command") else ""
                    exe = exe.replace("${AGENTS_BRIDGE_HOME}", str(HERE.parent)).strip('"')
                    if exe and "$" not in exe and not shutil.which(exe) and not Path(exe).exists():
                        missing.add(exe)
        if missing:
            add("ERR", "hooks", "hook commands not found: " + ", ".join(sorted(missing)[:4]))
    except (OSError, ValueError) as e:
        add("ERR", "hooks", f"hooks source {src} unreadable: {e}")
    agents_md = Path(os.path.expanduser(cfg.get("agents_md", "~/.agents/AGENTS.md")))
    add("OK" if agents_md.is_file() else "ERR", "base", f"instructions {agents_md}")
    skills = [d for d in (AG / "skills").glob("*/SKILL.md")] if (AG / "skills").is_dir() else []
    add("OK" if skills else "WARN", "skills", f"{len(skills)} skill(s) in ~/.agents/skills")
    dangling = [p.name for p in (AG / "skills").glob("*") if p.is_symlink() and not p.exists()] if skills else []
    if dangling:
        add("ERR", "skills", "dangling links: " + ", ".join(dangling[:5]))

    if shutil.which("codex"):
        cx = HOME / ".codex"
        try:
            c = tomllib.loads((cx / "config.toml").read_text())
            if c.get("features", {}).get("hooks") is False:
                add("ERR", "codex", "features.hooks = false - guards are OFF")
            st = (c.get("hooks") or {}).get("state") or {}
            trusted = sum(1 for v in st.values() if isinstance(v, dict) and v.get("trusted_hash"))
            add("OK" if trusted >= 5 else "WARN", "codex",
                f"trusted hooks: {trusted} (open `codex`, run /hooks and trust the agents-bridge entries once)")
        except (OSError, tomllib.TOMLDecodeError) as e:
            add("ERR", "codex", f"config.toml: {e}")
        hj = cx / "hooks.json"
        if hj.is_file():
            evs = sorted((json.loads(hj.read_text()).get("hooks") or {}))
            add("OK" if "PreToolUse" in evs else "ERR", "codex", "hooks.json: " + ", ".join(evs))
        add("OK" if (cx / "auth.json").is_file() else "WARN", "codex", "logged in" if (cx / "auth.json").is_file() else "not logged in: codex login")
    for name, exe, f in (("gemini", "gemini", HOME / ".gemini" / "settings.json"),
                         ("opencode", "opencode", HOME / ".config" / "opencode" / "opencode.json"),
                         ("qwen", "qwen", HOME / ".qwen" / "settings.json")):
        if not shutil.which(exe):
            continue
        try:
            d = json.loads(f.read_text())
            extra = ", hooks: " + ", ".join(sorted(d.get("hooks", {}))) if d.get("hooks") else ""
            add("OK", name, f"{f.name}: {len(d.get('mcpServers') or d.get('mcp') or {})} MCP{extra}")
        except (OSError, ValueError) as e:
            add("ERR", name, f"{f}: {e}")
    if shutil.which("opencode"):
        pl = HOME / ".config" / "opencode" / "plugins" / "agents-bridge-guard.js"
        add("OK" if pl.is_file() else "ERR", "opencode", "guard plugin " + ("present" if pl.is_file() else "MISSING"))
    if shutil.which("goose"):
        hj = AG / "plugins" / "agents-bridge-guard" / "hooks" / "hooks.json"
        add("OK" if hj.is_file() else "ERR", "goose", "guard plugin " + ("present" if hj.is_file() else "MISSING"))

    if a.deep:
        for t in sorted((HERE.parent / "tests").glob("test_*")):
            cmd = [sys.executable, str(t)] if t.suffix == ".py" else ["bash", str(t)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            add("OK" if r.returncode == 0 else "ERR", "tests", f"{t.name}: " + (r.stdout.strip().splitlines() or ["?"])[-1])

    errs = [r for r in res if r[0] == "ERR"]
    for lvl, area, msg in res:
        if not a.quiet or lvl == "ERR":
            print(f"{lvl:4} {area:8} {msg}")
    if not a.quiet:
        print(f"\n{len(errs)} error(s), {sum(1 for r in res if r[0] == 'WARN')} warning(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
