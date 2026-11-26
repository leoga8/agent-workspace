#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["textual>=1.0"]
# ///
"""
vault.py — Read-only Textual viewer for the work vault. Previews use Rich's markdown; `g` opens a note in glow's pager.
Tabs: Today's run, Attention (what needs you), Tickets, Runs, and Search (fzf over every note).
Usage: uv run ~/Documents/code/automation/vault.py [--root ~/Downloads/code]
Settings (environment): VAULT_PREVIEW=rich|glow (default rich), VAULT_GLOW_STYLE, VAULT_GLOW_WIDTH (pager width, default 130), VAULT_EDITOR.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import textwrap
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, unquote

from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

import vault_notes as vn

GLOW       = shutil.which("glow")
FZF        = shutil.which("fzf")
GLOW_STYLE = os.environ.get("VAULT_GLOW_STYLE", "dark")
GLOW_WIDTH = os.environ.get("VAULT_GLOW_WIDTH", "130")   # `g` pager: wider margin and table cells
PREVIEW    = os.environ.get("VAULT_PREVIEW", "rich")     # "glow" renders the side preview with glow instead
EDITOR     = os.environ.get("VAULT_EDITOR", "nvim")
POLL_S     = 3   # seconds between change checks
AGENT_POLL_S = 10  # seconds between agent status checks (a few rex calls each)
SEARCH_MAX = 200   # search results shown

# (label, folder relative to the vault). None = today's run; "@…" = a view built from several notes.
ATTENTION, SEARCH = "@attention", "@search"
SECTIONS = [
    ("Attention", ATTENTION),
    ("Today",     None),
    ("Runs",      "raw/runs"),
    ("Tickets",   "raw/tickets"),
    ("Search",    SEARCH),
]
SKIP_DIRS = {"templates"}   # not searched or followed by links

ACCENT     = "#6ea8fe"   # running / current step
LOG_TIME   = "#5f875f"   # log timestamps
DRAFT_FG, DRAFT_BG, DRAFT_EDGE = "#d7d7af", "#1c1c10", "#87875f"
STATUS_STYLE = {"done": "green", "reported": "green", "running": ACCENT, "approved": ACCENT,
                "blocked": "yellow", "awaiting_approval": "yellow", "failed": "red",
                "planned": "white", "open": "white"}
AGENT_STYLE  = {"working": ACCENT, "blocked": "bold yellow", "idle": "green", "done": "green",
                "error": "red"}
TITLE_W = 28   # ticket title column; longer titles wrap onto a second line
STATUS_SHORT = {"awaiting_approval": "approval"}   # keeps the status column narrow

# ── row builders (run in worker threads) ─────────────────────────────────────

def ticket_path(vault, tid):
    return vault / "raw/tickets" / tid / f"{tid}.md"

def due_cell(due, status):
    if not due:
        return Text("")
    tag  = vn.due_tag({"due": due})
    late = due < date.today() and status not in vn.CLOSED_LOCAL
    return Text(tag, style="red" if late else "yellow" if tag == "today" else "dim")

TICKET_COLS = [(" ", 1), "ID", ("Title", TITLE_W), "Status", "Due", "Upd"]   # tier: in the preview header

def wrap_title(title, closed):
    lines = textwrap.wrap(title or "untitled", TITLE_W) or [""]
    if len(lines) > 2:
        lines = [lines[0], lines[1][:TITLE_W - 1] + "…"]
    return Text("\n".join(lines), "dim" if closed else "")

def ticket_rows(vault, tickets):
    rows = []
    for t in vn.sort_tickets(tickets):
        path   = ticket_path(vault, t["id"])
        st     = STATUS_STYLE.get(t["status"], "dim")
        closed = t["status"] in vn.CLOSED_LOCAL
        cells = [Text("●", style=st),
                 Text(t["id"].removeprefix("linear-"), "dim" if closed else "bold"),
                 wrap_title(t["title"], closed),
                 Text(STATUS_SHORT.get(t["status"], t["status"]), style=st),
                 due_cell(t["due"], t["status"]),
                 Text(vn.ago(t["updated"]), style="dim")]
        info = {"kind": "ticket", "ticket": t}
        rows.append((str(path) if path.exists() else f"missing:{t['id']}", cells,
                     f"{t['id']} {t['status']} {t['tier']} {t['title']}".lower(), info, t.get("url", "")))
    return rows

def section_files(vault, where):
    base = vault / where
    if not base.is_dir():
        return []
    if where == "raw/tickets":   # only the note named after its folder, not reports beside it
        return [d / f"{d.name}.md" for d in base.iterdir() if (d / f"{d.name}.md").exists()]
    return list(base.rglob("*.md"))

def vault_files(vault):
    """Every note in the vault except hidden folders and templates."""
    out = []
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        out += [Path(root) / f for f in files if f.endswith(".md")]
    return out

def file_of(row):
    """The note behind a row: attention drafts and search hits share their note's file."""
    return row[3].get("file") or row[0]

def all_tickets(vault):
    return [vn.read_ticket(vault, f.parent.name) for f in section_files(vault, "raw/tickets")]

def pending_drafts(tickets):
    """[(ticket, draft)] for every unapproved draft on a ticket whose drafts are still open (not reported or cancelled)."""
    return [(t, d) for t in tickets if t["status"] not in vn.DRAFTS_CLOSED for d in t["drafts"]]

ATTENTION_COLS = [(" ", 1), "ID", ("Title", TITLE_W), "Why", "Due", "Upd"]

def attention_rows(vault, tickets):
    """What needs you: failed, blocked, drafts to approve, overdue or due today, Linear out of step."""
    found = []   # (priority, id, row)
    today = date.today()
    def row(t, key, why, style, search, info):
        closed = t["status"] in vn.CLOSED_LOCAL
        cells = [Text("●", style=STATUS_STYLE.get(t["status"], "dim")),
                 Text(t["id"].removeprefix("linear-"), "dim" if closed else "bold"),
                 wrap_title(t["title"], closed), Text(why, style),
                 due_cell(t["due"], t["status"]), Text(vn.ago(t["updated"]), style="dim")]
        return (key, cells, f"{t['id']} {search} {t['title']}".lower(), info, t.get("url", ""))
    for t in tickets:
        path   = ticket_path(vault, t["id"])
        closed = t["status"] in vn.CLOSED_LOCAL
        drafts = pending_drafts([t])
        reasons = []   # (priority, text, style)
        if t["status"] == "failed":  reasons.append((0, "failed", "red"))
        if t["status"] == "blocked": reasons.append((1, "blocked", "yellow"))
        if t["status"] == "awaiting_approval" and not drafts: reasons.append((2, "approval", "yellow"))
        if t["due"] and not closed and t["due"] < today:  reasons.append((3, "overdue", "red"))
        if t["due"] and not closed and t["due"] == today: reasons.append((4, "due today", "yellow"))
        if vn.external_mismatch(t): reasons.append((5, "≠ Linear", "yellow"))
        if reasons:
            reasons.sort()
            found.append((reasons[0][0], t["id"], row(
                t, str(path), " · ".join(r[1] for r in reasons), reasons[0][2],
                " ".join(r[1] for r in reasons), {"kind": "ticket", "ticket": t})))
        for i, (_, d) in enumerate(drafts):
            found.append((2, t["id"], row(
                t, f"{path}|draft{i}", f"draft · {d['label']}"[:30], "yellow", f"draft {d['label']}",
                {"kind": "ticket", "ticket": t, "file": str(path)})))
    return [r for _, _, r in sorted(found, key=lambda x: x[:2])]

SEARCH_COLS = [("Note", 24), ("Ln", 4), ("Match", 44)]

def clip(text, n):
    return text if len(text) <= n else text[:n - 1] + "…"

def build_index(vault):
    """Lines for fzf, 'note path <tab> line no. <tab> text', plus a line 0 per note with its name."""
    out = []
    for f in vault_files(vault):
        rel = f.relative_to(vault).as_posix()
        out.append(f"{rel}\t0\t{f.stem}")
        try:
            lines = f.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            line = line.strip().replace("\t", " ")
            if line and line != "---":
                out.append(f"{rel}\t{n}\t{line[:300]}")
    return "\n".join(out)

def search_rows(vault, index, query):
    """fzf's best matches for `query` (exact terms, space = and) over the text field of the index."""
    r = subprocess.run([FZF, "--filter", query, "--exact", "--delimiter", "\t", "--nth", "3"],
                       input=index, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=10)
    rows = []
    for line in r.stdout.splitlines()[:SEARCH_MAX]:
        rel, no, text = line.split("\t", 2)
        f, n = vault / rel, int(no)
        cells = [Text(clip(f.stem, 24)), Text(str(n) if n else "·", "dim"),
                 Text(clip(text if n else "(note name)", 44), "" if n else "dim")]
        rows.append((f"{f}|{n}", cells, "", {"kind": "note", "path": f, "file": str(f),
                                            "line": n, "match": text if n else ""}, ""))
    return rows

def build_section(vault, name, where):
    """Returns (title, columns, rows); a row is (key, cells, search_text, info, url)."""
    if where is None:
        run = vn.find_run_file(vault)
        tickets = [vn.read_ticket(vault, tid) for tid in vn.parse_run_ids(run)]
        done = sum(1 for t in tickets if t["status"] in vn.DONE_STATUSES)
        title = f"{run.stem}  ·  {done}/{len(tickets)} done" if run else "no run file"
        return title, TICKET_COLS, ticket_rows(vault, tickets)

    if where == SEARCH:
        return ("press / to search every note" if FZF else "fzf not found: install it to search"), SEARCH_COLS, []

    if where == ATTENTION:
        rows = attention_rows(vault, all_tickets(vault))
        return f"{len(rows)} need you", ATTENTION_COLS, rows

    files = section_files(vault, where)
    if where == "raw/tickets":
        tickets = [vn.read_ticket(vault, f.parent.name) for f in files]
        return f"{len(files)} tickets", TICKET_COLS, ticket_rows(vault, tickets)

    # raw/runs: newest day first; each day's summaries, catalogs and reconcile
    # tables go under that day's runs
    rows = []
    side = vn.is_run_side_file
    for f in sorted(files, key=lambda f: (f.stem[:10], not side(f), f.stem), reverse=True):
        title = vn.note_title(f)
        title = title if title != f.stem else ""
        if side(f):
            cells = [Text(f.stem[:10], "dim"), Text(f"  ↳ {f.stem[11:]}", "dim")]
        else:
            cells = [f.stem[:10], title or f.stem[11:]]
        rows.append((str(f), cells, f"{f.stem} {title}".lower(), {"kind": "note", "path": f}, ""))
    runs = sum(1 for f in files if not side(f))
    return f"{runs} notes" + (f" + {len(files) - runs} side files" if len(files) > runs else ""), ["Date", "Title"], rows

def signature(vault, where):
    """Cheap change check: file count + newest mtime for the section."""
    try:
        if where is None:
            run = vn.find_run_file(vault)
            files = []
            if run:
                for t in vn.parse_run_ids(run):
                    files += [ticket_path(vault, t), *vn.result_files(ticket_path(vault, t).parent)]
                files.insert(0, run)
        elif where == SEARCH:
            files = vault_files(vault)
        elif where == ATTENTION:
            files = section_files(vault, "raw/tickets")
        else:
            files = section_files(vault, where)
        mt = [f.stat().st_mtime for f in files if f.exists()]
        return (len(mt), max(mt, default=0))
    except OSError:
        return None

OSC8 = re.compile(r"\x1b\]8;[^\x07\x1b]*(?:\x07|\x1b\\)")

def render_markdown(path, width):
    """Rich's own Markdown (default), or glow's output as Rich Text when VAULT_PREVIEW=glow and glow is installed."""
    if GLOW and PREVIEW == "glow":
        # stdin must not be inherited: glow reads a non-tty stdin instead of the file
        r = subprocess.run([GLOW, "-s", GLOW_STYLE, "-w", str(width), str(path)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            # glow turns links into terminal hyperlinks (OSC 8); Rich can't read them and would print their text
            return Text.from_ansi(OSC8.sub("", r.stdout))
    text = Path(path).read_text(errors="replace")
    if text.startswith("---\n") and "\n---" in text[3:]:
        text = text[3:].split("\n---", 1)[1]   # drop the frontmatter only
    return Markdown(text)

def two_sides(left, right):
    """A full-width line with `left` on the left and `right` on the right."""
    grid = Table.grid(expand=True, padding=(0, 0, 0, 2))
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis"); grid.add_column(justify="right", no_wrap=True)
    grid.add_row(left, right)
    return grid

def info_line(info, path, vault):
    """The header above the preview: for a ticket, its id, title and status, then its details."""
    if info.get("kind") == "ticket":
        t = info["ticket"]
        head = two_sides(Text(f"{t['id']} — {t['title'] or 'untitled'}", "bold"),
                         Text(t["status"].replace("_", " ").upper(), f"bold {STATUS_STYLE.get(t['status'], 'dim')}"))
        out = Text()
        out.append("Linear: ", "dim")
        mis = vn.external_mismatch(t)
        out.append(t["external"] or "—", "bold yellow" if mis else "")
        if mis: out.append(" (≠ local)", "yellow")
        out.append("   tier: ", "dim"); out.append(t["tier"] or "—")
        if t["due"]:
            out.append("   due: ", "dim"); out.append(f"{t['due']:%b %d} ({vn.due_tag(t)})")
        if t["updated"]:
            out.append("   updated: ", "dim"); out.append(f"{vn.ago(t['updated'])} ago")
        if t["url"]:
            out.append("\n" + t["url"], "dim")
        return Group(head, out)
    rel = os.path.relpath(path, vault) if path else ""
    m = info.get("meta")
    out = Text(rel, "dim")
    if info.get("line"):
        out.append(f":{info['line']}", "dim")
    if m and m["url"]:
        out.append("\n" + m["url"], "dim")
    return out

LOG_STAMP = re.compile(r'^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})\s*[—–-]?\s*')

def log_line(entry):
    """A log entry with its time in its own colour: HH:MM today, "Nov 03 HH:MM" before."""
    out = Text()
    m = LOG_STAMP.match(entry)
    if m:
        d = date.fromisoformat(m.group(1))
        out.append(m.group(2) if d == date.today() else f"{d:%b %d} {m.group(2)}", LOG_TIME)
        out.append("  " + entry[m.end():], "dim")
    else:
        out.append(entry, "dim")
    return out

def ticket_summary(t):
    """A digest of a ticket: ask, plan, pending drafts, latest results and output, log."""
    parts = []
    def label(name):
        parts.append(Text(("\n" if parts else "") + name, "bold dim"))
    if t["ask"] or t["created"]:
        label("ASK")
        body = Text()
        if t["ask"]:     body.append(f"{t['ask']}\n")
        if t["created"]: body.append(f"created {t['created']}", "dim")
        body.rstrip(); parts.append(body)
    if t["plan"] or t["meta"]:
        label("PLAN")
        body = Text()
        if t["meta"]: body.append(f"{t['meta']}\n", "dim")
        for j, step in enumerate(t["plan"], 1):
            if step["done"]:
                body.append(f"{j}. {step['text']}\n", "green")
            elif step["active"]:
                body.append(f"{j}. ", f"bold {ACCENT}"); body.append(f"{step['text']}\n", "bold")
                if step.get("success"):
                    body.append(f"   success: {step['success']}\n", "dim")
            else:
                body.append(f"{j}. ", "dim"); body.append(f"{step['text']}\n")
        body.rstrip(); parts.append(body)
    if t["drafts"]:
        label("DRAFT PENDING")
        for d in t["drafts"]:
            parts.append(Panel(Text(d["body"], DRAFT_FG),
                               title=Text(f" {d['label'].upper()} · AWAITING APPROVAL ", "yellow"),
                               title_align="left", border_style=DRAFT_EDGE,
                               style=f"on {DRAFT_BG}", padding=(0, 1)))
    if t["results"]:
        label("LATEST RESULTS")
        body = Text()
        for r in t["results"]:
            body.append(f"{r['name']}\n", ACCENT)
            for line in r["text"].splitlines():
                body.append(f"  {line}\n", "dim")
        body.rstrip(); parts.append(body)
    if t["outputs"]:
        label("LATEST OUTPUT")
        parts.append(Text("\n".join(t["outputs"])))
    if t["log"]:
        label("LOG")
        parts.extend(log_line(e) for e in t["log"])
    return Group(*parts) if parts else Text("nothing to summarise", "dim")

# ── wikilinks ────────────────────────────────────────────────────────────────

WIKILINK = re.compile(r'!?\[\[([^\]\n]+)\]\]')

def note_links(text):
    """The [[targets]] in a note, in order, without |aliases or #headings."""
    out = []
    for raw in WIKILINK.findall(text):
        target = raw.split("|", 1)[0].split("#", 1)[0].strip()
        if target and target not in out:
            out.append(target)
    return out

MDLINK = re.compile(r'(?<!\!)\[([^\]\n]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)')

def md_links(text):
    """[(label, target)] for [label](target) links, in order, without anchors-only targets."""
    out = []
    for label, target in MDLINK.findall(text):
        if target.startswith("#") or (label, target) in out:
            continue
        out.append((label.strip() or target, target))
    return out

def link_index(vault):
    """{lowercase note name: [paths]} for resolving links."""
    names = {}
    for f in vault_files(vault):
        names.setdefault(f.stem.lower(), []).append(f)
    return names

def resolve_link(names, vault, target):
    """Notes a [[target]] could mean: matched by name, narrowed by folders when the link has them."""
    name = target.removesuffix(".md").lower()
    hits = names.get(name.rsplit("/", 1)[-1], [])
    if "/" in name:
        hits = [f for f in hits if f.relative_to(vault).as_posix().lower().removesuffix(".md").endswith(name)] or hits
    return hits

class LinkPicker(ModalScreen):
    """The links in the current note; Enter opens one, Esc closes."""
    BINDINGS = [Binding("escape", "dismiss", "close"), Binding("q", "dismiss", "close", show=False)]
    CSS = """
    LinkPicker { align: center middle; }
    #links { width: 70; max-height: 20; border: round $accent; background: $surface; }
    """

    def __init__(self, items):
        super().__init__()
        self.items = items   # [(label, path or None)]

    def compose(self) -> ComposeResult:
        yield OptionList(*[Option(label, id=str(i), disabled=path is None)
                           for i, (label, path) in enumerate(self.items)], id="links")

    def on_mount(self):
        links = self.query_one("#links", OptionList)
        links.border_title = "links"
        links.focus()

    def on_option_list_option_selected(self, event):
        self.dismiss(self.items[int(event.option_id)][1])

# ── app ──────────────────────────────────────────────────────────────────────

class VaultApp(App):
    TITLE = "vault"
    CSS = """
    #topbar   { height: 1; padding: 0 1; background: $panel; }
    #sections { width: 15; border: round $panel; }
    #middle   { width: 1fr; }
    #right    { width: 1fr; }
    #notes    { height: 1fr; border: round $panel; }
    #filter   { display: none; }
    #filter.visible { display: block; }
    #info     { height: auto; padding: 0 1 1 1; color: $text-muted; }
    #preview-scroll { border: round $panel; }
    #preview  { padding: 0 1; }
    #status   { height: 1; padding: 0 1; background: $panel; }
    .zoom #sections, .zoom #middle { display: none; }
    *:focus   { border: round $accent; }
    #sections, #notes, #preview-scroll { border-title-color: $text-muted; border-subtitle-color: $text-muted; }
    #notes > .datatable--header { color: $text-muted; text-style: bold; background: $surface; }
    #notes > .datatable--cursor { background: #1f3a2a; text-style: bold; }
    """
    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("slash", "filter", "filter"),
        Binding("escape", "clear_filter", "clear", show=False),
        Binding("j", "down", "", show=False),
        Binding("k", "up", "", show=False),
        Binding("g", "glow", "glow"),
        Binding("e", "edit", "nvim"),
        Binding("o", "open_url", "open url"),
        Binding("O", "open_obsidian", "obsidian"),
        Binding("l", "links", "links"),
        Binding("backspace", "back", "back"),
        Binding("s", "summary", "summary/full"),
        Binding("z", "zoom", "zoom"),
        Binding("r", "refresh", "refresh"),
    ]

    def __init__(self, vault):
        super().__init__()
        self.vault    = vault
        self.sections = [(n, w) for n, w in SECTIONS
                         if w is None or w.startswith("@") or (vault / w).is_dir()]
        self.where    = None
        self.rows     = []      # all rows of the current section
        self.by_key   = {}
        self.sig      = None
        self.current  = None    # previewed row key
        self.cache    = {}      # (path, mtime, width) -> renderable
        self.summary  = True    # tickets: digest instead of the full note (s toggles)
        self.index, self.index_sig = None, None   # search: fzf lines for every note, and their file signature
        self.search_timer = None
        self.detached = {}      # key -> row, for notes opened through a link (not in the table)
        self.history  = []      # keys to go back to (backspace)
        self.want_jump = False  # scroll the next render to the search match

    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        with Horizontal():
            yield OptionList(*[n for n, _ in self.sections], id="sections")
            with Vertical(id="middle"):
                yield Input(placeholder="filter…", id="filter")
                yield DataTable(id="notes", cursor_type="row", zebra_stripes=False)
            with Vertical(id="right"):
                yield Static(id="info")
                with VerticalScroll(id="preview-scroll"):
                    yield Static(id="preview")
        yield Static(id="status")
        yield Footer()

    def on_mount(self):
        self.query_one("#sections").border_title = "vault"
        self.query_one("#preview-scroll").border_title = "preview"
        self.query_one("#notes").focus()
        self.select_section(0)
        self.set_interval(POLL_S, self.poll)
        self.set_interval(AGENT_POLL_S, self.poll_agents)
        self.set_interval(30, self.update_clock)
        self.update_clock()
        self.poll()
        self.poll_agents()

    def update_clock(self):
        path = str(self.vault).replace(str(Path.home()), "~", 1)
        self.query_one("#topbar", Static).update(two_sides(
            Text.assemble(("vault", "bold"), (f"  {path}", "dim")),
            Text(f"{datetime.now():%Y-%m-%d %H:%M}", "dim")))

    # sections ──
    def select_section(self, idx):
        name, where = self.sections[idx]
        if (where == SEARCH) != (self.where == SEARCH):   # the box is the query here, a row filter elsewhere
            f = self.query_one("#filter", Input)
            f.value = ""
            f.placeholder = "search notes (fzf)…" if where == SEARCH else "filter…"
            f.set_class(where == SEARCH, "visible")
        self.where = where
        self.query_one("#notes").border_title = "TODAY'S RUN" if where is None else name.upper()
        self.load_section(name, where)

    def on_option_list_option_highlighted(self, event):
        if event.option_list.id == "sections":   # not the link picker's list
            self.select_section(event.option_index)

    @work(thread=True, exclusive=True, group="table")
    def load_section(self, name, where):
        title, cols, rows = build_section(self.vault, name, where)
        sig = signature(self.vault, where)
        if not get_current_worker().is_cancelled:
            self.call_from_thread(self.fill_table, where, title, cols, rows, sig)

    def fill_table(self, where, title, cols, rows, sig):
        if where != self.where:
            return
        table = self.query_one("#notes", DataTable)
        keep  = self.current
        self.rows, self.sig = rows, sig
        self.by_key = {r[0]: r for r in rows}
        table.clear(columns=True)
        for c in cols:   # a column is a label, or (label, width)
            label, width = c if isinstance(c, tuple) else (c, None)
            table.add_column(label, width=width)
        table.border_subtitle = title
        self.apply_filter(keep)
        if where == SEARCH and self.query_one("#filter", Input).value.strip():
            self.search_now()

    def apply_filter(self, keep=None):
        table = self.query_one("#notes", DataTable)
        q     = self.query_one("#filter", Input).value.strip().lower()
        if self.where == SEARCH:   # the rows are already fzf's matches for the query
            q = ""
        table.clear()
        for key, cells, search, _, _ in self.rows:
            if not q or all(w in search for w in q.split()):
                height = max((c.plain if isinstance(c, Text) else str(c)).count("\n") + 1 for c in cells)
                table.add_row(*cells, key=key, height=height)
        if keep and keep in self.by_key:
            try: table.move_cursor(row=table.get_row_index(keep))
            except Exception: pass
        if table.row_count == 0:
            self.show_preview(None)

    # preview ──
    def on_data_table_row_highlighted(self, event):
        key = event.row_key.value if event.row_key else None
        if self.current in self.detached and self.focused is not self.query_one("#notes"):
            return   # reading a linked note: a table refresh shouldn't pull the preview away
        if key != self.current:
            self.history.clear(); self.detached.clear()
        self.show_preview(key)

    def on_data_table_row_selected(self, event):
        self.query_one("#preview-scroll").focus()

    def row_of(self, key):
        return self.by_key.get(key) or self.detached.get(key)

    def cur_file(self):
        """The note file behind the previewed row, if any."""
        row = self.row_of(self.current) if self.current else None
        return file_of(row) if row and not self.current.startswith("missing:") else None

    def show_preview(self, key, force=False):
        changed = key != self.current
        self.current = key
        info, preview = self.query_one("#info", Static), self.query_one("#preview", Static)
        if not key:
            info.update(""); preview.update(Text("nothing here", "dim")); return
        row = self.row_of(key)
        if key.startswith("missing:"):
            info.update(info_line(row[3], None, self.vault) if row else "")
            preview.update(Text(f"no ticket note for {key[8:]}", "dim")); return
        path = file_of(row) if row else key
        info.update(info_line(row[3], path, self.vault) if row else "")
        scroll = self.query_one("#preview-scroll")
        is_ticket = bool(row) and row[3].get("kind") == "ticket"
        scroll.border_title = "summary" if self.summary and is_ticket else "preview"
        if self.summary and is_ticket:
            preview.update(ticket_summary(row[3]["ticket"]))
            if changed: scroll.scroll_home(animate=False)
            return
        width = max(scroll.size.width - 4, 30)
        try: mtime = os.path.getmtime(path)
        except OSError: mtime = 0
        ck = (path, mtime, width)
        self.want_jump = changed
        if ck in self.cache and not force:
            preview.update(self.cache[ck])
            if changed: scroll.scroll_home(animate=False)
            self.place(self.cache[ck], row)
        else:
            self.render_preview(path, width, ck)
            if changed: scroll.scroll_home(animate=False)

    @work(thread=True, exclusive=True, group="preview")
    def render_preview(self, path, width, ck):
        try:
            r = render_markdown(path, width)
        except Exception as e:
            r = Text(f"could not render: {e}", "red")
        if not get_current_worker().is_cancelled:
            self.call_from_thread(self.set_preview, path, ck, r)

    def set_preview(self, path, ck, r):
        self.cache[ck] = r
        if path == self.cur_file():
            self.query_one("#preview", Static).update(r)
            self.place(r, self.row_of(self.current))

    def place(self, r, row):
        """After a search hit renders, scroll to the matching line (best effort: glow rewraps text)."""
        jump, self.want_jump = self.want_jump, False
        needle = re.sub(r'\[\[|\]\]|[*_`#>|]', '', (row[3].get("match") or "") if row else "").strip()[:25]
        if not (jump and needle and isinstance(r, Text)):
            return
        for y, line in enumerate(r.plain.splitlines()):
            if needle in line:
                scroll = self.query_one("#preview-scroll")
                self.call_after_refresh(scroll.scroll_to, y=max(y - 2, 0), animate=False)
                return

    def on_resize(self, event):
        if self.current:
            self.set_timer(0.3, lambda: self.show_preview(self.current))

    # polling ──
    @work(thread=True, exclusive=True, group="poll")
    def poll(self):
        sig = signature(self.vault, self.where)
        self.call_from_thread(self.after_poll, self.where, sig)

    @work(thread=True, exclusive=True, group="agents")
    def poll_agents(self):
        tabs = vn.agent_tabs()
        run  = vn.find_run_file(self.vault)
        tickets = [vn.read_ticket(self.vault, tid) for tid in vn.parse_run_ids(run)] if run else []
        stats = {"run": run.stem[:10] if run else "",
                 "done": sum(1 for t in tickets if t["status"] in vn.DONE_STATUSES),
                 "total": len(tickets),
                 "drafts": len(pending_drafts(all_tickets(self.vault)))}   # across all tickets, not just today's run
        self.call_from_thread(self.show_agents, tabs, stats)

    def show_agents(self, tabs, stats):
        """● orchestrator idle · ● ABC-12-s2 working   run 2026-11-03 · 2/5 done   1 draft pending"""
        status = Text()
        if not tabs:
            status.append("○ ", "dim"); status.append(f"{vn.AGENT_NAME} ", "dim"); status.append("offline", "dim")
        for i, (name, state) in enumerate(tabs):
            if i: status.append("  ·  ", "dim")
            style = AGENT_STYLE.get(state, "dim")
            status.append("● ", style); status.append(f"{name} "); status.append(state, style)
        if stats["run"]:
            status.append(f"     run {stats['run']}", "dim")
            status.append(f"  ·  {stats['done']}/{stats['total']} done", "dim")
        if stats["drafts"]:
            n = stats["drafts"]
            status.append("     ")
            status.append(f" {n} draft{'s' if n > 1 else ''} pending · approval needed ",
                          f"bold {DRAFT_BG} on #c8c87a")
        self.query_one("#status", Static).update(status)

    def after_poll(self, where, sig):
        if where == self.where and self.sig is not None and sig != self.sig:
            if where == SEARCH:   # keep the typed query; rerun it on the changed notes
                self.sig = sig
                self.search_now()
                return
            idx = [w for _, w in self.sections].index(where)
            self.load_section(self.sections[idx][0], where)
            if self.current:
                self.show_preview(self.current)

    # search ──
    def search_now(self):
        self.run_search(self.query_one("#filter", Input).value)

    @work(thread=True, exclusive=True, group="search")
    def run_search(self, query):
        if not FZF:
            self.call_from_thread(self.notify, "fzf not found: install it to search", severity="warning")
            return
        sig = signature(self.vault, SEARCH)
        if self.index is None or sig != self.index_sig:   # re-read the notes only when they changed
            self.index = build_index(self.vault)
            self.index_sig = sig
        rows = search_rows(self.vault, self.index, query) if query.strip() else []
        if not get_current_worker().is_cancelled:
            self.call_from_thread(self.show_results, rows)

    def show_results(self, rows):
        if self.where != SEARCH:
            return
        self.rows, self.by_key = rows, {r[0]: r for r in rows}
        n = len(rows)
        self.query_one("#notes").border_subtitle = (f"{n}{'+' if n == SEARCH_MAX else ''} matches"
                                                    if rows else "press / to search every note")
        self.apply_filter()

    # actions ──
    def action_filter(self):
        f = self.query_one("#filter", Input)
        f.add_class("visible"); f.focus()

    def action_clear_filter(self):
        f = self.query_one("#filter", Input)
        f.value = ""; f.remove_class("visible")
        self.apply_filter(self.current)
        self.query_one("#notes").focus()

    def on_input_changed(self, event):
        if self.where == SEARCH:   # debounce: wait for a pause in typing
            if self.search_timer:
                self.search_timer.stop()
            self.search_timer = self.set_timer(0.25, self.search_now)
        else:
            self.apply_filter(self.current)

    def on_input_submitted(self, event):
        self.query_one("#notes").focus()

    def action_down(self):
        w = self.focused
        (getattr(w, "action_cursor_down", None) or getattr(w, "action_scroll_down", lambda: None))()

    def action_up(self):
        w = self.focused
        (getattr(w, "action_cursor_up", None) or getattr(w, "action_scroll_up", lambda: None))()

    def current_file(self):
        path = self.cur_file()
        if not path:
            self.notify("no note selected", severity="warning")
        return path

    def action_glow(self):
        path = self.current_file()
        if path and GLOW:
            with self.suspend():
                subprocess.run([GLOW, "-p", "-s", GLOW_STYLE, "-w", GLOW_WIDTH, path])

    def action_edit(self):
        path = self.current_file()
        if path:
            with self.suspend():
                subprocess.run([EDITOR, path])
            self.show_preview(self.current, force=True)

    def action_open_url(self):
        row = self.row_of(self.current or "")
        if row and row[4]:
            subprocess.Popen(["open", row[4]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            self.notify("no url on this note", severity="warning")

    def action_open_obsidian(self):
        path = self.current_file()
        if path:
            subprocess.Popen(["open", f"obsidian://open?path={quote(str(path))}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # wikilinks ──
    def action_links(self):
        path = self.current_file()
        if not path:
            return
        note = Path(path)
        text = note.read_text(errors="replace")
        names, items, seen = link_index(self.vault), [], set()   # items: (label, ("note"|"open", Path|str) or None)
        def rel(p):
            try:    return p.relative_to(self.vault)
            except ValueError: return p
        for t in note_links(text):                                # [[wikilinks]]
            hits = resolve_link(names, self.vault, t)
            if not hits:
                items.append((f"{t}  (not found)", None))
            else:
                more = f"  (+{len(hits) - 1} more)" if len(hits) > 1 else ""
                items.append((f"{t}  → {rel(hits[0])}{more}", ("note", hits[0])))
                seen.add(hits[0])
        for label, target in md_links(text):                      # [label](path or url)
            if re.match(r"[a-z][a-z0-9+.-]*://", target, re.I) and not target.lower().startswith("file://"):
                items.append((f"{label}  → {target}", ("open", target)))
                continue
            p = Path(unquote(target.removeprefix("file://")))
            p = p if p.is_absolute() else note.parent / p
            if not p.is_file():
                items.append((f"{label}  (not found: {target})", None))
            elif p.suffix == ".md":
                items.append((f"{label}  → {rel(p)}", ("note", p))); seen.add(p)
            else:
                items.append((f"{label}  → {rel(p)}  (opens outside)", ("open", str(p))))
        if note.name == f"{note.parent.name}.md":                 # a ticket note: the other notes in its folder
            for p in sorted(note.parent.glob("*.md")):
                if p != note and p not in seen:
                    items.append((f"{p.name}  (in this folder)", ("note", p)))
        if not items:
            self.notify(f"no links in {rel(note)}"); return
        self.push_screen(LinkPicker(items), self.open_link)

    def open_link(self, target):
        if not target:
            return
        kind, value = target
        if kind == "note":
            self.open_note(value)
        else:
            subprocess.Popen(["open", value], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def open_note(self, path):
        """Show a linked note in the preview, keeping the way back (backspace)."""
        key = str(path)
        if key == self.current:
            return
        if self.current:
            self.history.append(self.current)
        self.detached[key] = self.note_row(path)
        self.show_preview(key)
        self.query_one("#preview-scroll").focus()

    def note_row(self, path):
        """A row for a note that isn't in the table; tickets get their summary."""
        key = str(path)
        if path.name == f"{path.parent.name}.md" and path.parent.parent == self.vault / "raw/tickets":
            t = vn.read_ticket(self.vault, path.parent.name)
            return (key, [], "", {"kind": "ticket", "ticket": t}, t["url"])
        m = vn.note_meta(path)
        return (key, [], "", {"kind": "note", "path": path, "meta": m}, m["url"])

    def action_back(self):
        if self.history:
            self.show_preview(self.history.pop())
        elif self.current in self.detached:   # back to the row the table is on
            self.detached.clear()
            table = self.query_one("#notes", DataTable)
            try:
                key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            except Exception:
                key = None
            self.show_preview(key)
            table.focus()

    def action_summary(self):
        self.summary = not self.summary
        row = self.row_of(self.current or "")
        if self.summary and not (row and row[3].get("kind") == "ticket"):
            self.notify("summary shows on tickets (Today / Tickets)")
        self.show_preview(self.current, force=True)

    def action_zoom(self):
        self.screen.toggle_class("zoom")
        self.set_timer(0.1, lambda: self.show_preview(self.current))

    def action_refresh(self):
        self.cache.clear()
        idx = [w for _, w in self.sections].index(self.where)
        self.load_section(self.sections[idx][0], self.where)
        self.show_preview(self.current, force=True)


def main():
    ap = argparse.ArgumentParser(description="Read-only viewer for the work vault")
    ap.add_argument("--root", default="~/Documents/code", help="work folder (default ~/Documents/code)")
    args = ap.parse_args()
    vn.configure(args.root)
    vault = vn.read_vault_path()
    if not vault or not vault.is_dir():
        sys.exit(f"vault not found (vault_path in {vn.CONFIG} → {vault})")
    VaultApp(vault).run()

if __name__ == "__main__":
    main()
