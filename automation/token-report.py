#!/usr/bin/env python3
"""
token-report.py — Tokens used by the work agent on one day, per session, for
Claude Code and OpenCode. Stdlib only, read-only.
Shows which kind of session spent them: the orchestrator, other sessions
(Rex worker tabs, your own), subagents, the hourly ticket checks and other
one-shot runs.
Usage: python3 ~/Documents/code/automation/token-report.py [YYYY-MM-DD] [--root DIR] [--all]
  date    default today
  --root  work folder (default ~/Documents/code); only sessions started under it count
  --all   every session that day, whatever folder it started in
"""
import argparse
import glob
import json
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

CLAUDE_PROJECTS = Path.home() / ".claude/projects"
OPENCODE_DB     = Path.home() / ".local/share/opencode/opencode.db"
# The first prompt tells what a session was (see work-morning.sh, check-tickets.sh);
# the orchestrator's starts with the planner's command (/solutions before the rename)
from vault_notes import read_settings
ORCH_PREFIX  = tuple(f"/{p} create today's daily note" for p in {read_settings()["PLANNER"], "solutions"})
CHECK_PREFIX = "Using the Linear MCP tools"

def local_day(ts):
    """ISO timestamp (UTC 'Z') or epoch ms → local date."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000).date()
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date()

def kind_of(first, headless=False, sub=False):
    if sub:                          return "subagent"
    if first.startswith(ORCH_PREFIX):  return "orchestrator"
    if first.startswith(CHECK_PREFIX): return "ticket check"
    return "one-shot" if headless else "session"

def text_of(content):
    if isinstance(content, str):
        return content
    for c in content or []:
        if isinstance(c, dict) and c.get("type") == "text":
            return c.get("text", "")
    return ""

def new_row(tool, kind, label):
    return {"tool": tool, "kind": kind, "label": label, "models": set(), "turns": 0,
            "input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "cost": None}

# ── Claude Code: ~/.claude/projects/<folder>/<session>.jsonl (+ <session>/subagents/*.jsonl)

def claude_rows(day, root, everything):
    prefix = str(root).replace("/", "-")
    rows = []
    for proj in CLAUDE_PROJECTS.iterdir():
        if not proj.is_dir() or (not everything and not proj.name.startswith(prefix)):
            continue
        for f in glob.glob(str(proj / "*.jsonl")) + glob.glob(str(proj / "*/subagents/*.jsonl")):
            if datetime.fromtimestamp(os.path.getmtime(f)).date() < day:
                continue   # not touched since before that day
            sub = "/subagents/" in f
            row, seen, first, headless = None, set(), "", False
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try: e = json.loads(line)
                    except ValueError: continue
                    if e.get("type") == "user" and not first:
                        first = text_of(e.get("message", {}).get("content")).strip()
                        headless = e.get("entrypoint", "").startswith("sdk")
                    if e.get("type") != "assistant" or not e.get("timestamp"):
                        continue
                    if local_day(e["timestamp"]) != day:
                        continue
                    m = e.get("message", {})
                    u = m.get("usage") or {}
                    mid = m.get("id") or e.get("requestId")
                    if not u or mid in seen:   # one API call is logged once per content block
                        continue
                    seen.add(mid)
                    if row is None:
                        row = new_row("claude", "", "")
                    row["models"].add((m.get("model") or "?").removeprefix("claude-"))
                    row["turns"]       += 1
                    row["input"]       += u.get("input_tokens", 0)
                    row["cache_write"] += u.get("cache_creation_input_tokens", 0)
                    row["cache_read"]  += u.get("cache_read_input_tokens", 0)
                    row["output"]      += u.get("output_tokens", 0)
            if row:
                row["kind"]  = kind_of(first, headless, sub)
                row["label"] = " ".join(first.split())[:60] or Path(f).stem
                rows.append(row)
    return rows

# ── OpenCode: per-session totals in its SQLite database

def opencode_rows(day, root, everything):
    if not OPENCODE_DB.exists():
        return []
    con = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    sessions = {}
    for t in ("session", "session_v2"):    # newer versions write session_v2
        if t in tables:
            for r in con.execute(f"select * from {t}"):
                sessions[r["id"]] = dict(r)
    rows = []
    for s in sessions.values():
        if local_day(s["time_updated"]) < day or local_day(s["time_created"]) > day:
            continue
        if not everything and not s["directory"].startswith(str(root)):
            continue
        total = s["tokens_input"] + s["tokens_output"] + s["tokens_cache_read"] + s["tokens_cache_write"]
        if not total:
            continue
        first = ""
        if "session_message" in tables:
            r = con.execute("""select json_extract(data,'$.text') from session_message
                               where session_id = ? and type = 'user' order by seq limit 1""", (s["id"],)).fetchone()
            first = (r[0] or "") if r else ""
        if not first and "part" in tables and "message" in tables:
            r = con.execute("""select json_extract(p.data,'$.text') from part p join message m on m.id = p.message_id
                               where p.session_id = ? and json_extract(m.data,'$.role') = 'user'
                                 and json_extract(p.data,'$.type') = 'text'
                               order by p.time_created limit 1""", (s["id"],)).fetchone()
            first = (r[0] or "") if r else ""
        try:
            m = json.loads(s["model"] or "{}")
            model = m.get("id", "?") + (f"#{m['variant']}" if m.get("variant") not in (None, "default") else "")
        except ValueError:
            model = "?"
        row = new_row("opencode", kind_of(first.strip(), sub=bool(s["parent_id"])),
                      " ".join((first or s["title"] or "").split())[:60])
        row["models"].add(model.removeprefix("claude-"))
        row["input"], row["output"] = s["tokens_input"], s["tokens_output"] + s["tokens_reasoning"]
        row["cache_read"], row["cache_write"] = s["tokens_cache_read"], s["tokens_cache_write"]
        row["cost"] = s["cost"]
        rows.append(row)
    return rows

# ── output

def k(n):
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)

KIND_ORDER = ["orchestrator", "session", "subagent", "ticket check", "one-shot"]

def main():
    ap = argparse.ArgumentParser(description="Tokens used by the work agent on one day")
    ap.add_argument("day", nargs="?", default=date.today().isoformat())
    ap.add_argument("--root", default="~/Documents/code")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    day, root = date.fromisoformat(a.day), Path(a.root).expanduser()

    rows = claude_rows(day, root, a.all) + opencode_rows(day, root, a.all)
    if not rows:
        print(f"No agent sessions on {day}" + ("" if a.all else f" under {root} (try --all)") + ".")
        return
    rows.sort(key=lambda r: (KIND_ORDER.index(r["kind"]), r["tool"], -r["cache_read"] - r["cache_write"]))

    print(f"Tokens on {day}" + ("" if a.all else f" under {root}"))
    print("in = new input · write/read = prompt cache (read costs ~10% of input) · cost = OpenCode's own figure; "
          "Claude Code and OpenAI-subscription sessions show none\n")
    hdr = f"{'kind':<13}{'tool':<9}{'model':<22}{'calls':>6}{'in':>8}{'write':>9}{'read':>9}{'out':>8}{'cost':>8}  first prompt"
    print(hdr); print("─" * len(hdr))
    totals = defaultdict(lambda: defaultdict(float))
    for r in rows:
        cost = f"${r['cost']:.2f}" if r["cost"] else ""
        print(f"{r['kind']:<13}{r['tool']:<9}{','.join(sorted(r['models']))[:21]:<22}{r['turns'] or '':>6}"
              f"{k(r['input']):>8}{k(r['cache_write']):>9}{k(r['cache_read']):>9}{k(r['output']):>8}{cost:>8}  {r['label']}")
        for key in ("input", "cache_write", "cache_read", "output", "cost"):
            totals[r["kind"]][key] += r[key] or 0
        totals[r["kind"]]["n"] += 1

    print("\nBy kind")
    for kind in KIND_ORDER:
        t = totals.get(kind)
        if not t: continue
        cost = f"   ${t['cost']:.2f}" if t["cost"] else ""
        print(f"  {kind:<13}{int(t['n']):>3} sessions   in {k(int(t['input']))}   write {k(int(t['cache_write']))}"
              f"   read {k(int(t['cache_read']))}   out {k(int(t['output']))}{cost}")

if __name__ == "__main__":
    main()
