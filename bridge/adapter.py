#!/usr/bin/env python3
"""agents-bridge adapter: run one set of guard hooks from any coding agent.

Hook scripts written for Claude Code (stdin JSON, exit 2 = block, `permissionDecision` JSON) are the lingua franca.
This adapter lets Codex CLI, Gemini CLI, OpenCode, Qwen Code and Goose run the very same scripts:

  1. read the agent's hook payload from stdin,
  2. normalize it to the Claude Code shape (tool_name Bash/Edit/Write/Read/Grep/mcp__..., tool_input.command /
     file_path / content / new_string). Codex `apply_patch` is split per file; deleting a file through a patch is
     presented to guards as `rm -rf <file>` so a "confirm deletions" guard sees it,
  3. run every matching hook from the hooks source (sequentially - first block wins),
  4. translate the verdict back: block -> exit 2 + reason on stderr (+ JSON for Gemini).
     "ask" (Codex and Gemini would silently ALLOW it) -> block with an approval code; the user types
     "<approval_word> CODE", the UserPromptSubmit hook records it, and the identical retry passes for `approval_ttl` s.

Fail closed: unreadable input, missing guard script, timeout = block (PreToolUse only).

Usage (written into each agent's hook config by sync.py):
  adapter.py --harness codex|gemini|opencode|qwen|goose|claude --event <Event> --dispatch [--with-context]
  adapter.py --harness codex --event PreToolUse -- <single-hook-command> [args]
Config: $AGENTS_BRIDGE_CONFIG or ~/.agents/bridge.json (see config.example.json).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
CONFIG_PATH = Path(os.environ.get("AGENTS_BRIDGE_CONFIG", HOME / ".agents" / "bridge.json"))

DEFAULTS = {
    # file with Claude-shaped {"hooks": {...}}: ~/.claude/settings.json, a Codex hooks.json, or a neutral file
    "hooks_source": str(HOME / ".agents" / "hooks.json"),
    "skip": [],                      # ["<harness|*> <substring of hook command>"]
    "plugin_hooks": [],              # ["<claude-plugin> <Event>"] - Claude plugin hooks to also run elsewhere
    "context_files": [],             # files injected on SessionStart (--with-context), e.g. a memory index
    "context_limit": 24000,          # chars
    "sync_on_start": "",             # optional command run (quietly) on SessionStart in non-Claude agents
    "approval_word": "approve",      # user types "<approval_word> ABC123"
    "approval_ttl": 600,
    "state_dir": str(HOME / ".agents" / "state"),
    "lang": "en",
    "timeout": 60,
    "strict_errors": True,           # PreToolUse: a guard exiting with rc other than 0/2 blocks (Claude would pass)
}

MSG = {
    "en": {"needs": "USER APPROVAL REQUIRED (code {k}): {r}\nAsk the user. If they reply '{w} {k}', retry EXACTLY the same operation.",
           "saved": "Approval recorded for code {k} ({m} min). Retry the same operation.",
           "badin": "BLOCKED: agents-bridge could not read the hook input (fail closed).",
           "noguard": "BLOCKED: guard {n} did not run (rc={rc}): {e} - fail closed.",
           "tmo": "BLOCKED: guard {n} timed out (fail closed).",
           "err": "BLOCKED: guard {n} failed (rc={rc}): {e} - fail closed.",
           "nosrc": "BLOCKED: cannot read hooks source {p}: {e}"},
    "pl": {"needs": "WYMAGA ZGODY UZYTKOWNIKA (kod {k}): {r}\nZapytaj. Jesli odpisze '{w} {k}', ponow DOKLADNIE te sama operacje.",
           "saved": "Zgoda zapisana dla kodu {k} ({m} min). Ponow te sama operacje.",
           "badin": "BLOCKED: agents-bridge nie odczytal wejscia hooka (fail closed).",
           "noguard": "BLOCKED: bramka {n} nie uruchomila sie (rc={rc}): {e} - fail closed.",
           "tmo": "BLOCKED: bramka {n} przekroczyla czas (fail closed).",
           "err": "BLOCKED: bramka {n} zakonczyla sie bledem (rc={rc}): {e} - fail closed.",
           "nosrc": "BLOCKED: nie odczytam zrodla hookow {p}: {e}"},
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    except (OSError, ValueError):
        pass
    for k in ("hooks_source", "state_dir"):
        cfg[k] = os.path.expanduser(os.path.expandvars(cfg[k]))
    cfg["context_files"] = [os.path.expanduser(os.path.expandvars(f)) for f in cfg["context_files"]]
    return cfg


CFG = load_config()
STATE = Path(os.environ.get("AGENTS_STATE_DIR", CFG["state_dir"]))
APPROVALS = STATE / "approvals"
PENDING = STATE / "pending"
T = MSG.get(CFG["lang"], MSG["en"])

SHELL_TOOLS = {"bash", "shell", "exec_command", "local_shell", "unified_exec", "run_shell_command", "container.exec",
               "write_stdin", "shell_command"}
PATCH_TOOLS = {"apply_patch", "applypatch", "patch"}
TOOL_MAP = {  # Gemini CLI / OpenCode / Qwen / Goose tool names -> Claude Code
    "write_file": "Write", "write": "Write",
    "replace": "Edit", "edit": "Edit", "multiedit": "MultiEdit",
    "read_file": "Read", "read": "Read", "read_many_files": "Read",
    "glob": "Glob", "search_file_content": "Grep", "grep_search": "Grep", "grep": "Grep",
    "list_directory": "Glob", "ls": "Glob",
    "web_fetch": "WebFetch", "webfetch": "WebFetch", "google_web_search": "WebSearch", "websearch": "WebSearch",
    "task": "Agent", "spawn_agent": "Agent",
    "multi_edit": "MultiEdit", "notebook_edit": "NotebookEdit", "notebookedit": "NotebookEdit",
}
TOOL_EVENTS = ("PreToolUse", "PostToolUse")
EVENT_ALIAS = {"BeforeTool": "PreToolUse", "AfterTool": "PostToolUse", "BeforeAgent": "UserPromptSubmit",
               "AfterAgent": "Stop"}  # Gemini CLI event names -> Claude Code


def log(msg: str) -> None:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with (STATE / "adapter.log").open("a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg[:500] + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------------------------------------ normalize
def gemini_servers() -> list[str]:
    names: list[str] = []
    for f in (HOME / ".gemini" / "settings.json", Path(os.getcwd()) / ".gemini" / "settings.json"):
        try:
            names += list((json.loads(f.read_text()).get("mcpServers") or {}))
        except (OSError, ValueError, AttributeError):
            pass
    return names


def _abs(p: str, cwd: str) -> str:
    if not p:
        return p
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(cwd or os.getcwd(), p))


def _cmd(ti: dict) -> str:
    c = ti.get("command", ti.get("cmd", ti.get("chars", "")))
    if isinstance(c, list):
        if len(c) >= 3 and os.path.basename(str(c[0])) in ("bash", "zsh", "sh") and c[1] in ("-lc", "-c"):
            return str(c[2])
        return shlex.join(str(x) for x in c)
    return str(c or "")


def parse_patch(text: str, cwd: str) -> list[dict]:
    """Codex apply_patch envelope -> [{op: add|update|delete, path, added, removed}]."""
    ops: list[dict] = []
    cur = None
    for line in text.splitlines():
        m = re.match(r"^\*\*\* (Add|Update|Delete) File: (.+)$", line)
        if m:
            cur = {"op": m.group(1).lower(), "path": _abs(m.group(2).strip(), cwd), "added": [], "removed": []}
            ops.append(cur)
            continue
        m = re.match(r"^\*\*\* Move to: (.+)$", line)
        if m and cur:
            ops.append({"op": "add", "path": _abs(m.group(1).strip(), cwd), "added": cur["added"], "removed": []})
            ops.append({"op": "delete", "path": cur["path"], "added": [], "removed": []})
            continue
        if cur is None or line.startswith("*** "):
            continue
        if line.startswith("+"):
            cur["added"].append(line[1:])
        elif line.startswith("-"):
            cur["removed"].append(line[1:])
    return ops


def normalize(harness: str, event: str, p: dict) -> list[dict]:
    """Agent payload -> list of Claude Code payloads (a multi-file patch becomes several)."""
    cwd = p.get("cwd") or os.getcwd()
    base = {k: v for k, v in p.items() if k not in ("tool_name", "tool_input", "toolName", "toolInput", "args")}
    base.setdefault("cwd", cwd)
    base["hook_event_name"] = event
    base.setdefault("session_id", p.get("session_id") or p.get("sessionId") or os.environ.get("CODEX_THREAD_ID", "none"))
    if "transcript_path" not in base and p.get("transcriptPath"):
        base["transcript_path"] = p["transcriptPath"]
    if event not in TOOL_EVENTS:
        if "prompt" not in base:
            for k in ("user_prompt", "userPrompt", "message"):
                if isinstance(p.get(k), str):
                    base["prompt"] = p[k]
        return [base]

    name = str(p.get("tool_name") or p.get("toolName") or "")
    ti = p.get("tool_input") or p.get("toolInput") or p.get("args") or {}
    if isinstance(ti, str):
        try:
            ti = json.loads(ti)
        except ValueError:
            ti = {"command": ti}
    if harness == "goose" and "__" in name and not name.startswith("mcp__"):
        name = name.rsplit("__", 1)[1]  # developer__shell -> shell
    if name == "text_editor" and isinstance(ti, dict):  # goose: one tool, sub-command in `command`
        sub, fp = str(ti.get("command", "")), ti.get("path") or ti.get("file_path") or ""
        if sub in ("write", "create"):
            name, ti = "write_file", {"file_path": fp, "content": ti.get("file_text", ti.get("content", ""))}
        elif sub in ("str_replace", "insert", "undo_edit"):
            name, ti = "replace", {"file_path": fp, "old_string": ti.get("old_str", ""), "new_string": ti.get("new_str", "")}
        else:
            name, ti = "read_file", {"file_path": fp}
    low = name.lower()

    cmd_text = _cmd(ti) if isinstance(ti, dict) else ""
    patch_field = next((str(ti[k]) for k in ("input", "patch", "patchText", "patch_text")
                        if isinstance(ti, dict) and "*** Begin Patch" in str(ti.get(k, ""))), "")
    if low in PATCH_TOOLS or "*** Begin Patch" in cmd_text or patch_field:
        text = cmd_text if "*** Begin Patch" in cmd_text else patch_field
        out = []
        for op in parse_patch(text, cwd):
            if op["op"] == "delete":
                # deliberately "rm -rf": deleting a file must pass the same guard as any deletion
                out.append({**base, "tool_name": "Bash", "tool_input": {"command": "rm -rf " + shlex.quote(op["path"]),
                                                                        "description": "apply_patch: Delete File"}})
                continue
            body = "\n".join(op["added"])
            if op["op"] == "add":
                out.append({**base, "tool_name": "Write", "tool_input": {"file_path": op["path"], "content": body}})
            else:
                out.append({**base, "tool_name": "Edit", "tool_input": {"file_path": op["path"],
                                                                        "old_string": "\n".join(op["removed"]),
                                                                        "new_string": body}})
        return out or [{**base, "tool_name": "Edit", "tool_input": {"file_path": "", "new_string": text}}]

    if low in SHELL_TOOLS or name == "Bash":
        return [{**base, "tool_name": "Bash", "tool_input": {**ti, "command": cmd_text}}]
    if name.startswith("mcp__"):
        return [{**base, "tool_name": name, "tool_input": ti}]
    if harness == "gemini" and name.startswith("mcp_"):  # gemini: mcp_<server>_<tool>; server names may contain "_"
        rest = name[4:]
        srv = max((s for s in gemini_servers() if rest.startswith(s + "_")), key=len, default="")
        if not srv and "_" in rest:
            srv = rest.split("_", 1)[0]
        if srv:
            return [{**base, "tool_name": f"mcp__{srv}__{rest[len(srv) + 1:]}", "tool_input": ti}]

    cl = TOOL_MAP.get(low, name)
    tin = dict(ti) if isinstance(ti, dict) else {}
    for k in ("absolute_path", "filePath", "path", "file"):
        if k in tin and "file_path" not in tin and cl in ("Read", "Write", "Edit", "MultiEdit"):
            tin["file_path"] = tin[k]
    if "file_path" in tin:
        tin["file_path"] = _abs(str(tin["file_path"]), cwd)
    if cl == "Edit":
        tin.setdefault("old_string", tin.get("oldString", ""))
        tin.setdefault("new_string", tin.get("newString", ""))
    if cl in ("Grep", "Glob") and "path" in tin:
        tin["path"] = _abs(str(tin["path"]), cwd)
    return [{**base, "tool_name": cl, "tool_input": tin}]


# ------------------------------------------------------------------------------------------------ approvals
def payload_hash(payload: dict) -> str:
    key = json.dumps({"t": payload.get("tool_name"), "i": payload.get("tool_input"), "s": payload.get("session_id")},
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(key.encode()).hexdigest()


def approval_code(payload: dict) -> str:
    """Short code shown to the user; the full hash of the exact call is kept server-side in PENDING."""
    h = payload_hash(payload)
    code = h[:6].upper()
    try:
        PENDING.mkdir(parents=True, exist_ok=True)
        os.chmod(PENDING, 0o700)
        (PENDING / code).write_text(h)
    except OSError as e:
        log(f"pending write fail: {e}")
    return code


def approval_consume(payload: dict) -> bool:
    """True once per approval: the code was approved by the user within ttl AND it was issued for this exact call."""
    h = payload_hash(payload)
    f = APPROVALS / h[:6].upper()
    try:
        ok = f.is_file() and f.read_text().strip() == h and time.time() - f.stat().st_mtime < float(CFG["approval_ttl"])
        if ok:
            f.unlink()
        return ok
    except OSError:
        return False


def record_approvals(prompt: str) -> list[str]:
    word = re.escape(CFG["approval_word"])
    codes = [c.upper() for c in re.findall(rf"\b{word}[\s:]+([0-9A-F]{{6}})\b", prompt or "", flags=re.I)]
    got = []
    for c in codes:
        try:
            h = (PENDING / c).read_text().strip()  # only codes the adapter actually issued
        except OSError:
            continue
        APPROVALS.mkdir(parents=True, exist_ok=True)
        os.chmod(APPROVALS, 0o700)
        (APPROVALS / c).write_text(h)
        (PENDING / c).unlink()
        got.append(c)
        log(f"approval {c}")
    return got


# ------------------------------------------------------------------------------------------------ hooks source
def matcher_ok(matcher, tool: str) -> bool:
    if matcher in (None, "", "*"):
        return True
    try:
        return re.fullmatch(str(matcher), tool) is not None
    except re.error:
        return tool in str(matcher).split("|")


def skip_list(harness: str) -> list[str]:
    out = []
    for line in CFG["skip"]:
        who, _, frag = str(line).partition(" ")
        if who in ("*", harness) and frag.strip():
            out.append(frag.strip())
    return out


def source_cmds(event: str, tool: str, harness: str) -> list[list[str]]:
    src = json.loads(Path(CFG["hooks_source"]).read_text())
    skips = skip_list(harness)
    out = []
    for grp in (src.get("hooks") or {}).get(event, []):
        if event in TOOL_EVENTS and not matcher_ok(grp.get("matcher"), tool):
            continue
        for h in grp.get("hooks", []):
            c = h.get("command")
            if h.get("type", "command") != "command" or not c:
                continue
            if "agents-bridge" in c or "bridge/adapter.py" in c or "hooks/adapter.py" in c:
                continue  # hooks source may be the agent's own config: never call ourselves
            if not any(f in c for f in skips):
                out.append([c])
    return out


def claude_plugin_cmds(event: str, tool: str) -> list[list[str]]:
    """Hooks of enabled Claude Code plugins listed in plugin_hooks (only side-effect-free ones!)."""
    allow = {tuple(str(x).split()[:2]) for x in CFG["plugin_hooks"] if len(str(x).split()) >= 2}
    if not allow:
        return []
    try:
        s = json.loads((HOME / ".claude" / "settings.json").read_text())
        inst = json.loads((HOME / ".claude" / "plugins" / "installed_plugins.json").read_text())["plugins"]
    except (OSError, ValueError, KeyError):
        return []
    en = {k.split("@")[0] for k, v in (s.get("enabledPlugins") or {}).items() if v}
    out = []
    for key, entries in inst.items():
        name = key.split("@")[0]
        if name not in en or (name, event) not in allow or not entries:
            continue
        root = entries[0]["installPath"]
        try:
            hj = json.loads((Path(root) / "hooks" / "hooks.json").read_text())
        except (OSError, ValueError):
            continue
        for grp in (hj.get("hooks") or {}).get(event, []):
            if event in TOOL_EVENTS and not matcher_ok(grp.get("matcher"), tool):
                continue
            out += [[h["command"], "--plugin-root", root] for h in grp.get("hooks", [])
                    if h.get("type", "command") == "command" and h.get("command")]
    return out


def start_context() -> str:
    parts = []
    for f in CFG["context_files"]:
        try:
            t = Path(f).read_text().strip()
        except OSError:
            continue
        if t:
            parts.append(f"--- {f} ---\n{t}")
    txt = "\n\n".join(parts)
    lim = int(CFG["context_limit"])
    return txt if len(txt) <= lim else txt[:lim] + "\n[... truncated]"


# ------------------------------------------------------------------------------------------------ run + verdict
def run_hook(argv: list[str], payload: dict, harness: str) -> tuple[int, str, str]:
    env = dict(os.environ)
    env["AGENT_HARNESS"] = harness
    env.setdefault("AGENTS_BRIDGE_HOME", str(Path(__file__).resolve().parent.parent))
    env.setdefault("CLAUDE_PROJECT_DIR", payload.get("cwd") or os.getcwd())
    if len(argv) == 3 and argv[1] == "--plugin-root":
        env["CLAUDE_PLUGIN_ROOT"] = env["PLUGIN_ROOT"] = argv[2]
        cmd = ["/bin/bash", "-c", argv[0]]
    elif len(argv) == 1:  # hook command as written in a settings file: through a shell ($HOME, "|| true")
        cmd = ["/bin/bash", "-c", argv[0]]
    else:
        cmd = [os.path.expandvars(os.path.expanduser(a)) for a in argv]
    cwd = payload.get("cwd")
    r = subprocess.run(cmd, input=json.dumps(payload), capture_output=True, text=True, env=env,
                       timeout=float(os.environ.get("AGENTS_HOOK_TIMEOUT", CFG["timeout"])),
                       cwd=cwd if cwd and os.path.isdir(cwd) else None)
    return r.returncode, r.stdout, r.stderr


def verdict(rc: int, out: str, err: str) -> tuple[str, str, dict | None]:
    js = None
    s = out.strip()
    if s.startswith("{"):
        try:
            js = json.loads(s)
        except ValueError:
            js = None
    if rc == 2:
        return "deny", (err.strip() or out.strip() or "blocked by guard"), js
    if js:
        hso = js.get("hookSpecificOutput") or {}
        dec = hso.get("permissionDecision") or js.get("decision")
        reason = hso.get("permissionDecisionReason") or js.get("reason") or js.get("stopReason") or ""
        if dec in ("deny", "block"):
            return "deny", reason or "blocked by guard", js
        if dec == "ask":
            return "ask", reason or "needs user approval", js
    return "allow", "", js


def context_text(out: str, js: dict | None) -> str:
    if js is None:
        return out.strip()
    hso = js.get("hookSpecificOutput") or {}
    return "\n".join(x for x in (hso.get("additionalContext") or "", js.get("systemMessage") or "") if x).strip()


def block(harness: str, reason: str) -> int:
    reason = reason.strip()
    if harness == "gemini":
        print(json.dumps({"decision": "deny", "reason": reason}, ensure_ascii=False))
    sys.stderr.write(reason + "\n")
    return 2


def hook_name(cmd: list[str]) -> str:
    try:
        return Path(shlex.split(cmd[0])[0]).name
    except (ValueError, IndexError):
        return cmd[0][:40] if cmd else "?"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", required=True)
    ap.add_argument("--event", required=True)
    ap.add_argument("--dispatch", action="store_true", help="run the event's hooks from hooks_source")
    ap.add_argument("--with-context", "--with-memory", dest="with_context", action="store_true",
                    help="SessionStart: also inject context_files")
    ap.add_argument("--approvals-only", "--zgody", dest="approvals_only", action="store_true")
    ap.add_argument("script", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    script = a.script[1:] if a.script[:1] == ["--"] else a.script
    ev = EVENT_ALIAS.get(a.event, a.event)
    raw = sys.stdin.read()
    try:
        p = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return block(a.harness, T["badin"]) if ev == "PreToolUse" else 0
    try:
        payloads = normalize(a.harness, ev, p)
    except Exception as e:  # noqa: BLE001
        log(f"normalize fail {ev}: {e}")
        return block(a.harness, f"BLOCKED: agents-bridge: {e}") if ev == "PreToolUse" else 0

    texts: list[str] = []
    if ev == "UserPromptSubmit" and (a.approvals_only or a.dispatch):
        codes = record_approvals(payloads[0].get("prompt", ""))
        if codes:
            texts.append(T["saved"].format(k=", ".join(codes), m=int(CFG["approval_ttl"]) // 60))
        if a.approvals_only:
            print("\n".join(texts))
            return 0
    if ev == "SessionStart" and a.with_context:
        if CFG["sync_on_start"] and a.harness != "claude":
            try:
                subprocess.run(["/bin/bash", "-c", CFG["sync_on_start"]], capture_output=True, timeout=20)
            except (OSError, subprocess.TimeoutExpired) as e:
                log(f"sync_on_start fail: {e}")
        c = start_context()
        if c:
            texts.append(c)

    for pl in payloads:
        approved = None  # one approval covers every "ask" guard for this exact call
        if a.dispatch:
            try:
                cmds = source_cmds(ev, pl.get("tool_name", ""), a.harness)
            except (OSError, ValueError) as e:
                log(f"hooks source fail: {e}")
                if ev == "PreToolUse":
                    return block(a.harness, T["nosrc"].format(p=CFG["hooks_source"], e=e))
                cmds = []
            if a.harness != "claude":  # Claude runs its own plugin hooks
                cmds += claude_plugin_cmds(ev, pl.get("tool_name", ""))
        else:
            cmds = [script]
        for cmd in cmds:
            name = hook_name(cmd)
            try:
                rc, out, err = run_hook(cmd, pl, a.harness)
            except subprocess.TimeoutExpired:
                log(f"timeout {name} {ev}")
                if ev == "PreToolUse":
                    return block(a.harness, T["tmo"].format(n=name))
                continue
            except (OSError, ValueError) as e:
                log(f"exec fail {name}: {e}")
                if ev == "PreToolUse":
                    return block(a.harness, T["noguard"].format(n=name, rc="exec", e=e))
                continue
            dec, reason, js = verdict(rc, out, err)
            if ev == "PreToolUse":
                if rc in (126, 127):  # guard missing / not executable: never pass silently
                    log(f"missing guard {name} rc={rc}")
                    return block(a.harness, T["noguard"].format(n=name, rc=rc, e=err.strip()[:200]))
                if dec == "deny":
                    log(f"deny {name} {pl.get('tool_name')}")
                    return block(a.harness, reason)
                if rc not in (0, 2) and CFG["strict_errors"]:  # crashed guard = unknown verdict = block
                    log(f"guard error {name} rc={rc}")
                    return block(a.harness, T["err"].format(n=name, rc=rc, e=err.strip()[:200]))
                if dec == "ask":
                    if approved is None:
                        approved = approval_consume(pl)
                    if approved:
                        log(f"ask->allow (approved) {name}")
                        continue
                    code = approval_code(pl)
                    log(f"ask->deny code {code} {name}")
                    return block(a.harness, T["needs"].format(k=code, r=reason, w=CFG["approval_word"]))
                if err.strip():
                    texts.append(err.strip())  # guard warnings (exit 0 + stderr)
            else:
                t = context_text(out, js)
                if t:
                    texts.append(t)
                if ev == "PostToolUse" and rc == 2 and err.strip():
                    texts.append(err.strip())

    if ev == "Stop":
        if texts:
            print(json.dumps({"systemMessage": " ".join(texts)}, ensure_ascii=False))
        return 0
    if texts:
        if ev == "PreToolUse":
            sys.stderr.write("\n".join(texts) + "\n")
        elif a.harness == "gemini":  # Gemini: stdout must be JSON only
            print(json.dumps({"hookSpecificOutput": {"hookEventName": a.event, "additionalContext": "\n".join(texts)}},
                             ensure_ascii=False))
        else:
            print("\n".join(texts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
