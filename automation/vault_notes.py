#!/usr/bin/env python3
"""
vault_notes.py — Shared, read-only parsers for the work vault (runs, tickets, notes).
Stdlib only. Used by vault.py.
Call configure(root) first when the work folder isn't ~/Documents/code.
"""
import glob
import json
import os
import re
import subprocess
import time
from datetime import date, datetime
from pathlib import Path

def read_settings(work_dir=Path.home() / "Documents/code"):
    """automation/settings.env over settings.example.env (KEY=value lines, as lib.zsh reads them)."""
    out = {"PLANNER": "work", "MORNING_SOURCES": "linear helpscout slack"}
    for name in ("settings.example.env", "settings.env"):
        try:
            lines = (Path(work_dir) / "automation" / name).read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            m = re.match(r'\s*([A-Z_]+)=(.*)', line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"\'')
    return out

WORK_DIR   = Path.home() / "Documents/code"
ORIG_DIR   = Path.home() / "Documents/code"   # root the copied config.md points at
PLANNER    = read_settings(WORK_DIR)["PLANNER"]
CONFIG     = WORK_DIR / f"planner/{PLANNER}/config.md"
AGENT_NAME = "work-morning"                    # Rex session label, as in lib.zsh
ORCH_LABEL = "orchestrator"                    # the orchestrator's tab
CHECK_TAB  = "ticket-checks"                   # not a worker
REX        = Path.home() / "Library/Application Support/com.superlogical.rex/RexServer/Versions/Current/Rex Server.app/Contents/MacOS/rex"
TOOL_FILE  = Path.home() / ".local/share/automation/session-tool.txt"
# Files beside the run notes in raw/runs that aren't runs (see check-tickets.sh)
RUN_SIDE_SUFFIXES = ("-catalog.md", ".summary.md", "-reconcile.md")

def configure(root):
    """Point the helpers at another copy of the work folder (e.g. ~/Downloads/code)."""
    global WORK_DIR, PLANNER, CONFIG
    WORK_DIR = Path(root).expanduser()
    PLANNER  = read_settings(WORK_DIR)["PLANNER"]
    CONFIG   = WORK_DIR / f"planner/{PLANNER}/config.md"

# ── helpers ──────────────────────────────────────────────────────────────────

def read_vault_path():
    try:
        text = CONFIG.read_text()
        m = re.search(r'\*\*vault_path:\*\*\s*(.+)', text)
        if m:
            p = Path(os.path.expanduser(m.group(1).strip()))
            # config.md may come from another machine: remap ~/Documents/code → WORK_DIR
            try:
                return WORK_DIR / p.relative_to(ORIG_DIR)
            except ValueError:
                return p
    except Exception:
        pass
    return None

def is_run_side_file(path):
    """Daily-source summaries, catalogs and reconcile tables written beside the runs."""
    return str(path).endswith(RUN_SIDE_SUFFIXES)

def find_run_file(vault):
    if not vault:
        return None
    today = date.today().strftime("%Y-%m-%d")
    runs  = lambda pat: [f for f in glob.glob(str(vault / "raw/runs" / pat)) if not is_run_side_file(f)]
    files = runs(f"{today}-*.md") or runs("*.md")   # stale copy: fall back to the latest run
    return Path(max(files, key=os.path.getmtime)) if files else None

def section(text, name):
    m = re.search(rf'^## {name}\s*\n(.*?)(?=\n## |\Z)', text, re.S | re.M)
    return m.group(1) if m else None

def parse_run_ids(run_file):
    if not run_file or not run_file.exists():
        return []
    text = run_file.read_text()
    sec  = section(text, "tickets")
    if sec is not None:
        ids = [x.split("|")[0].strip() for x in re.findall(r'\[\[([^\]]+)\]\]', sec)]
    else:
        ids = re.findall(r'\b([A-Z]+-\d+)\b', text)
    seen, out = set(), []
    for x in ids:
        if x not in seen:
            seen.add(x); out.append(x)
    return out

def read_ticket(vault, tid):
    path = vault / "raw/tickets" / tid / f"{tid}.md"
    base = {"id": tid, "status": "new", "title": "", "meta": "",
            "external": "", "tier": "", "url": "", "due": None,
            "created": "", "ask": "", "updated": None,
            "plan": [], "drafts": [], "outputs": [], "log": [], "results": []}
    if not path.exists():
        return base
    base["results"] = read_results(path.parent)

    text = path.read_text()

    for key, field in (("status", "status"), ("external_status", "external"),
                       ("tier", "tier"), ("url", "url")):
        m = re.search(rf'^{key}:[ \t]*(.*)$', text, re.M)
        if m and m.group(1).strip(): base[field] = m.group(1).strip()

    m = re.search(r'^due:\s*(\d{4}-\d{2}-\d{2})', text, re.M)
    if m: base["due"] = date.fromisoformat(m.group(1))

    # title: "- **Title:** …", else first plain line under ## ticket
    sec = section(text, "ticket") or ""
    m = re.search(r'\*\*Title:\*\*\s*(.+)', sec)
    if m:
        base["title"] = m.group(1).strip()[:60]
    else:
        for line in sec.splitlines():
            line = line.strip()
            if line and not line.startswith(">"):
                base["title"] = line[:60]; break
    m = re.search(r'\*\*Created:\*\*\s*(.+)', sec)
    if m: base["created"] = m.group(1).strip()
    m = re.search(r'\*\*Ask:\*\*\s*(.+)', sec)
    if m: base["ask"] = m.group(1).strip()

    # plan: "### step N — text ✅/⏸" headings, else a flat list
    sec = section(text, "plan")
    if sec:
        meta = re.findall(r'^- (confidence|needs_human):\s*(\S+)', sec, re.M)
        base["meta"] = "  ".join(f"{k}: {v}" for k, v in meta)
        steps = re.findall(r'^### step\s*(.+)\n((?:(?!### ).*\n?)*)', sec, re.M)
        if steps:
            for s, body in steps:
                done   = "✅" in s or "skipped" in s
                active = not done and "⏸" in s
                clean  = re.sub(r'^\d+\s*[✅⏸]?\s*[—:-]?\s*|[✅⏸]', '', s).strip()
                m = re.search(r'^\s*- success:\s*(.+)$', body, re.M)
                base["plan"].append({"text": clean or s, "done": done, "active": active,
                                     "success": m.group(1).strip() if m else ""})
        else:
            for line in sec.splitlines():
                line = line.strip()
                if not line or re.match(r'- (confidence|needs_human):', line): continue
                done   = bool(re.search(r'\[x\]', line, re.I))
                active = not done and '**' in line
                clean  = re.sub(r'^-\s*(\[.\]\s*)?|\*\*|^\d+\.\s*', '', line).strip()
                if clean:
                    base["plan"].append({"text": clean, "done": done, "active": active,
                                         "success": ""})
        # nothing marked active: highlight the first unfinished step while it's in flight
        if base["status"] in ("running", "blocked") and not any(p["active"] for p in base["plan"]):
            for p in base["plan"]:
                if not p["done"]:
                    p["active"] = True; break

    # drafts — unchecked "- [ ] label — body" items (+ indented continuation lines)
    sec = section(text, "drafts")
    if sec:
        for block in re.split(r'\n(?=- \[)', sec.strip()):
            m = re.match(r'- \[ \]\s*(.*)', block, re.S)
            if not m: continue
            first, _, rest = m.group(1).partition("\n")
            label, _, body = first.partition(" — ")
            body = " ".join([body] + [l.strip() for l in rest.splitlines()]).strip()
            base["drafts"].append({"label": label.strip(), "body": (body or label)[:300]})

    # outputs — last 2 top-level entries (+ their indented sub-bullets)
    sec = section(text, "outputs")
    if sec:
        entries = []
        for line in sec.splitlines():
            if line.startswith("- "):
                entries.append(line[2:].strip())
            elif line.strip() and entries:
                entries[-1] += " " + line.strip()
        base["outputs"] = [e[:300] for e in entries[-2:]]

    # log — last 6 lines; newest timestamp = last activity
    sec = section(text, "log")
    if sec:
        entries = [l.lstrip('- ').strip() for l in sec.splitlines()
                   if l.strip().startswith('-')]
        base["log"] = entries[-6:]
        stamps = re.findall(r'^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2})', sec, re.M)
        if stamps:
            base["updated"] = datetime.strptime(max(stamps), "%Y-%m-%d %H:%M")
    if not base["updated"]:   # no timestamped log: when the note last changed
        base["updated"] = datetime.fromtimestamp(path.stat().st_mtime)

    return base

def result_files(folder):
    """A ticket's worker result files (plan.result.md, step-<n>.result.md), newest first."""
    return sorted(Path(folder).glob("*.result.md"), key=os.path.getmtime, reverse=True)

def read_results(folder, keep=2, lines=15):
    """The newest result files: [{"name", "text"}], each cut to `lines` lines."""
    out = []
    for f in result_files(folder)[:keep]:
        try:
            text = f.read_text(errors="replace").strip().splitlines()[:lines]
        except OSError:
            continue
        out.append({"name": f.name.removesuffix(".result.md"), "text": "\n".join(text)})
    return out

CLOSED_LOCAL    = ("done", "reported", "cancelled")
CLOSED_EXTERNAL = ("Done", "Canceled", "closed")
DRAFTS_CLOSED   = ("reported", "cancelled")   # a `done` ticket may still wait for approval of its drafts

def external_mismatch(t):
    if not t["external"]: return False
    return (t["status"] in CLOSED_LOCAL) != (t["external"] in CLOSED_EXTERNAL)

def due_tag(t):
    if not t["due"]: return ""
    d = (t["due"] - date.today()).days
    return "today" if d == 0 else f"{d:+d}d"

def ago(dt):
    if not dt: return ""
    m = int((datetime.now() - dt).total_seconds() // 60)
    if m < 60:   return f"{max(m, 0)}m"
    if m < 1440: return f"{m // 60}h"
    return f"{m // 1440}d"

def rex_json(*args):
    """Runs rex and parses its JSON output (block calls print JSON without --json)."""
    flag = [] if args[0] == "block" else ["--json"]
    r = subprocess.run([str(REX), *args, *flag], capture_output=True, text=True, timeout=3)
    return json.loads(r.stdout)

def todays_tool():
    """The agent work-morning.sh started today (claudecode / opencode), or ""."""
    try:
        day, _, tool = TOOL_FILE.read_text().strip().partition(" ")
        return tool if day == date.today().isoformat() else ""
    except OSError:
        return ""

def agent_tabs():
    """[(tab, state)] for the work session's orchestrator and worker tabs, same
    lookups as lib.zsh. A tab with no agent in it is left out. Without Rex:
    [("no Rex", <today's agent or "not started">)]. Rex but no session: [] (offline)."""
    if not REX.exists():
        return [("no Rex", todays_tool() or "not started")]
    try:
        sessions = rex_json("ls")["sessions"]
        if not any(s.get("label") == AGENT_NAME for s in sessions):
            return []
        tabs = []
        for w in rex_json("window", "ls", "-s", AGENT_NAME)["windows"]:
            if w.get("label") == CHECK_TAB or not w.get("focused_block_id"):
                continue
            recs = rex_json("block", "call", "-s", AGENT_NAME, "-b", w["focused_block_id"], "program_status")["records"]
            root = next((r for r in recs if r.get("id") is None), None)
            if root and (root.get("app") or root.get("effective_app")) in ("claude-code", "opencode"):
                tabs.append((w.get("label") or "?", root.get("state") or "unknown"))
        # orchestrator first, then workers by name
        return sorted(tabs, key=lambda t: (t[0] != ORCH_LABEL, t[0]))
    except Exception:
        return []

# ── statuses ──────────────────────────────────────────────────────────────────

SYMS = {"done": "●", "reported": "✓", "running": "◉", "approved": "◎",
        "blocked": "◈", "failed": "✕", "awaiting_approval": "◇",
        "planned": "○", "new": "·", "cancelled": "–"}
STATUS_ORDER = ["running", "blocked", "failed", "awaiting_approval", "approved",
                "planned", "new", "done", "reported", "cancelled"]
DONE_STATUSES = ("done", "reported")

def sym(status): return SYMS.get(status, "·")

def sort_tickets(tickets):
    return sorted(tickets, key=lambda t: STATUS_ORDER.index(t["status"])
                  if t["status"] in STATUS_ORDER else 99)

# ── generic notes ────────────────────────────────────────────────────────────

def read_frontmatter(path):
    """Top-level `key: value` pairs and the tag list from a note's frontmatter (reads only the block)."""
    fm = {"tags": []}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            if f.readline().strip() != "---":
                return fm
            key = None
            for line in f:
                if line.strip() == "---":
                    break
                m = re.match(r'^([A-Za-z_]+):\s*(.*)$', line)
                if m:
                    key, val = m.group(1), m.group(2).strip().strip('"')
                    if key != "tags" and val:
                        fm[key] = val
                elif key == "tags" and line.strip().startswith("- "):
                    tag = line.strip()[2:].strip()
                    if tag: fm["tags"].append(tag)
    except OSError:
        pass
    return fm

def note_title(path, fm=None):
    """frontmatter title, else the first `# heading` / `**Title:**` line, else the filename."""
    fm = fm if fm is not None else read_frontmatter(path)
    if fm.get("title"):
        return fm["title"]
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 60: break
                m = re.match(r'^#\s+(.+)', line) or re.search(r'\*\*Title:\*\*\s*(.+)', line)
                if m: return m.group(1).strip()
    except OSError:
        pass
    return Path(path).stem

def note_meta(path):
    """Title, status and due date for any note: frontmatter first, then the first Tasks-plugin line."""
    fm   = read_frontmatter(path)
    meta = {"title": note_title(path, fm), "status": fm.get("status", ""), "due": None,
            "mtime": datetime.fromtimestamp(os.path.getmtime(path)), "url": fm.get("url") or fm.get("source", "")}
    m = re.match(r'(\d{4}-\d{2}-\d{2})', fm.get("due", "") or fm.get("date", ""))
    if m and "due" in fm:
        meta["due"] = date.fromisoformat(m.group(1))
    if not meta["status"] or not meta["due"]:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i > 40: break
                    m = re.match(r'^\s*- \[(.)\]\s.*?(?:📅\s*(\d{4}-\d{2}-\d{2}))?', line)
                    if m:
                        if not meta["status"]:
                            meta["status"] = {"x": "done", " ": "open", "-": "cancelled"}.get(m.group(1), "")
                        d = re.search(r'📅\s*(\d{4}-\d{2}-\d{2})', line)
                        if d and not meta["due"]:
                            meta["due"] = date.fromisoformat(d.group(1))
                        break
        except OSError:
            pass
    if not meta["status"] and "done" in fm["tags"]:
        meta["status"] = "done"
    return meta
