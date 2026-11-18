#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["textual>=1.0"]
# ///
"""
vault.py — Read-only Textual viewer for the work vault, previews rendered with glow.
Usage: uv run ~/Documents/code/automation/vault.py [--root ~/Downloads/code]
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

from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Input, OptionList, Static
from textual.worker import get_current_worker

import vault_notes as vn

GLOW       = shutil.which("glow")
GLOW_STYLE = os.environ.get("VAULT_GLOW_STYLE", "dark")
EDITOR     = os.environ.get("VAULT_EDITOR", "nvim")
POLL_S     = 3   # seconds between change checks
AGENT_POLL_S = 10  # seconds between agent status checks (a few rex calls each)

# (label, folder relative to the vault). None = today's run; a list = fixed files.
SECTIONS = [
    ("Today",     None),
    ("Tickets",   "raw/tickets"),
    ("Runs",      "raw/runs"),
    ("Tasks",     "raw/tasks"),
    ("Projects",  "raw/projects"),
    ("Cases",     "raw/cases"),
    ("Daily",     "raw/daily"),
    ("Bookmarks", "raw/bookmarks"),
    ("Important", "raw/_important"),
    ("Hubs",      "hubs"),
    ("Home",      ["home.md", "me.md"]),
]
SKIP_DIRS = {"templates"}

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
    if isinstance(where, list):
        return [vault / f for f in where if (vault / f).exists()]
    base = vault / where
    if where == "raw/tickets":   # only the note named after its folder, not reports beside it
        return [d / f"{d.name}.md" for d in base.iterdir() if (d / f"{d.name}.md").exists()]
    return list(base.rglob("*.md"))

def build_section(vault, name, where):
    """Returns (title, columns, rows); a row is (key, cells, search_text, info, url)."""
    if where is None:
        run = vn.find_run_file(vault)
        tickets = [vn.read_ticket(vault, tid) for tid in vn.parse_run_ids(run)]
        done = sum(1 for t in tickets if t["status"] in vn.DONE_STATUSES)
        title = f"{run.stem}  ·  {done}/{len(tickets)} done" if run else "no run file"
        return title, TICKET_COLS, ticket_rows(vault, tickets)

    files = section_files(vault, where)
    if where == "raw/tickets":
        tickets = [vn.read_ticket(vault, f.parent.name) for f in files]
        return f"{len(files)} tickets", TICKET_COLS, ticket_rows(vault, tickets)

    rows = []
    if where in ("raw/runs", "raw/daily"):
        # newest day first; in Runs, each day's summaries, catalogs and reconcile
        # tables go under that day's runs
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

    metas = [(f, vn.note_meta(f)) for f in files]
    for f, m in sorted(metas, key=lambda x: x[1]["mtime"], reverse=True):
        st = m["status"]
        rows.append((str(f), [m["title"], Text(st, style=STATUS_STYLE.get(st, "dim")),
                              due_cell(m["due"], st), Text(f"{m['mtime']:%Y-%m-%d}", style="dim")],
                     f"{m['title']} {st} {f.stem}".lower(), {"kind": "note", "path": f, "meta": m}, m["url"]))
    return f"{len(files)} notes", ["Title", "Status", "Due", "Modified"], rows

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
        else:
            files = section_files(vault, where)
        mt = [f.stat().st_mtime for f in files if f.exists()]
        return (len(mt), max(mt, default=0))
    except OSError:
        return None

def render_markdown(path, width):
    """glow output as Rich Text, or Rich's own Markdown if glow isn't installed."""
    if GLOW:
        # stdin must not be inherited: glow reads a non-tty stdin instead of the file
        r = subprocess.run([GLOW, "-s", GLOW_STYLE, "-w", str(width), str(path)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return Text.from_ansi(r.stdout)
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
        Binding("s", "summary", "summary/full"),
        Binding("z", "zoom", "zoom"),
        Binding("r", "refresh", "refresh"),
    ]

    def __init__(self, vault):
        super().__init__()
        self.vault    = vault
        self.sections = [(n, w) for n, w in SECTIONS
                         if w is None or isinstance(w, list) or (vault / w).is_dir()]
        known = {w for _, w in self.sections if isinstance(w, str)}
        for d in sorted((vault / "raw").iterdir()):   # folders added later still show up
            rel = f"raw/{d.name}"
            if d.is_dir() and rel not in known and d.name not in SKIP_DIRS:
                self.sections.append((d.name.strip("_").title(), rel))
        self.where    = None
        self.rows     = []      # all rows of the current section
        self.by_key   = {}
        self.sig      = None
        self.current  = None    # previewed row key
        self.cache    = {}      # (path, mtime, width) -> renderable
        self.summary  = True    # tickets: digest instead of the full note (s toggles)

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
        self.where = where
        self.query_one("#notes").border_title = "TODAY'S RUN" if where is None else name.upper()
        self.load_section(name, where)

    def on_option_list_option_highlighted(self, event):
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

    def apply_filter(self, keep=None):
        table = self.query_one("#notes", DataTable)
        q     = self.query_one("#filter", Input).value.strip().lower()
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
        self.show_preview(event.row_key.value if event.row_key else None)

    def on_data_table_row_selected(self, event):
        self.query_one("#preview-scroll").focus()

    def show_preview(self, key, force=False):
        changed = key != self.current
        self.current = key
        info, preview = self.query_one("#info", Static), self.query_one("#preview", Static)
        if not key:
            info.update(""); preview.update(Text("no notes", "dim")); return
        row = self.by_key.get(key)
        if key.startswith("missing:"):
            info.update(info_line(row[3], None, self.vault) if row else "")
            preview.update(Text(f"no ticket note for {key[8:]}", "dim")); return
        info.update(info_line(row[3], key, self.vault) if row else "")
        scroll = self.query_one("#preview-scroll")
        is_ticket = bool(row) and row[3].get("kind") == "ticket"
        scroll.border_title = "summary" if self.summary and is_ticket else "preview"
        if self.summary and is_ticket:
            preview.update(ticket_summary(row[3]["ticket"]))
            if changed: scroll.scroll_home(animate=False)
            return
        width = max(scroll.size.width - 4, 30)
        try: mtime = os.path.getmtime(key)
        except OSError: mtime = 0
        ck = (key, mtime, width)
        if ck in self.cache and not force:
            preview.update(self.cache[ck])
        else:
            self.render_preview(key, width, ck)
        if changed:
            self.query_one("#preview-scroll").scroll_home(animate=False)

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
        if path == self.current:
            self.query_one("#preview", Static).update(r)

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
                 "drafts": sum(len(t["drafts"]) for t in tickets)}
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
            idx = [w for _, w in self.sections].index(where)
            self.load_section(self.sections[idx][0], where)
            if self.current:
                self.show_preview(self.current)

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
        if self.current and not self.current.startswith("missing:"):
            return self.current
        self.notify("no note selected", severity="warning")
        return None

    def action_glow(self):
        path = self.current_file()
        if path and GLOW:
            with self.suspend():
                subprocess.run([GLOW, "-p", "-s", GLOW_STYLE, path])

    def action_edit(self):
        path = self.current_file()
        if path:
            with self.suspend():
                subprocess.run([EDITOR, path])
            self.show_preview(path, force=True)

    def action_open_url(self):
        row = self.by_key.get(self.current or "")
        if row and row[4]:
            subprocess.Popen(["open", row[4]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            self.notify("no url on this note", severity="warning")

    def action_summary(self):
        self.summary = not self.summary
        row = self.by_key.get(self.current or "")
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
