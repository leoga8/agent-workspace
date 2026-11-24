# agent-workspace

The agent system that lives in `~/Documents/code`: planners you talk to in Claude Code or OpenCode, the workflows and pipelines they route to, ticket orchestration, and the scripts that run a work day (an orchestrator with worker tabs in Rex, hourly ticket checks, a vault viewer and a token report).

This repo holds the **framework** only. Anything tied to a job stays on that laptop, outside git:
- the job's planner
- its workflows and pipelines
- cloned work repos
- vaults
- configs and database schemas

The `.gitignore` is an allowlist, so a new folder is private unless it's added there.

Pairs with [dotfiles](https://github.com/leoga8/dotfiles), which installs the tools and the slash-command shims.

## layout

| path | what |
|------|------|
| `RULES.md` | rules every session follows, in any agent: ticket id first, capturing learnings, scope (what each laptop may edit), the shared-repo sync |
| `self-improvement/` | **not in git**: one file per learning logged by sessions and workers, waiting for a retro |
| `planner/core.md`, `planner/sources.md` | rules every planner follows; the connectors (Linear, HelpScout, Slack, manual) |
| `planner/assistant/` | the personal planner (`/assistant`) |
| `planner/<job>/` | the job planner (`/work`), **not in git**: made with `new-planner` |
| `orchestration/` | ticket runs: the state machine and the tickets orchestration |
| `pipelines/`, `workflows/` | generic domains: `code`, `database`, `meta`, `obsidian` |
| `automation/` | `work-morning.sh`, `check-tickets.sh`, `rex-worker.sh`, `vault.py`, `token-report.py`, settings |
| `viewer/ticket-viewer.html` | a read-only browser view of a tickets folder (Attention, Tickets, Search); one file, no server, no network. Open it in Chrome and choose the folder. Teammates get it through the shared repo |
| `templates/vault/` | note templates for a new vault |
| `setup.zsh` | sets up a laptop (run `--check` first) |

## a new laptop

1. Install the dotfiles (tools and shims).
2. Clone and set up:
   ```zsh
   git clone https://github.com/<you>/agent-workspace.git ~/Documents/code
   ~/Documents/code/setup.zsh --check      # what's missing
   ~/Documents/code/setup.zsh              # creates configs, builds vaults, installs the safety hook
   ```
3. Fill in each `planner/*/config.md` (mainly `vault_path`). Then create the job planner with `/assistant create a new planner`, named after `PLANNER` in `automation/settings.example.env` (`work`).
4. Put the job's terms (company, customers, ticket keys) in `automation/safety-terms.txt`, so the pre-commit check blocks them.

## adopting the repo in an existing ~/Documents/code

When the folder already has job content, you can't clone into it. Adopt the repo in place instead. Only tracked framework files change; ignored job folders are never touched.

```zsh
cd ~/Documents/code
git init && git remote add origin https://github.com/<you>/agent-workspace.git
git fetch && git reset origin/main && git branch -u origin/main   # no --hard: no files move yet
git status                              # the framework files that differ
git checkout -- .                       # take the repo's versions
./setup.zsh --check
```

After that, `git pull` keeps it current.

## settings

`automation/settings.example.env` holds the defaults. Copy it to `automation/settings.env` (not in git) to change them on one laptop:
- `PLANNER`: the job planner's folder and slash command (`work`)
- `MORNING_SOURCES`: the daily-note sections `work-morning.sh` fills

To rename a job planner on a laptop, run `automation/rename-planner.zsh <old> <new>`. It is a dry run until you add `--apply`. Then change the shim in the dotfiles.

## keeping job data out

- **The `.gitignore` allowlist:** only framework paths can be added.
- **The `automation/check-safety.zsh` pre-commit hook:** blocks emails, home folders, keys and tokens, plus every term in the local `automation/safety-terms.txt`. Run it with `--all` to check the whole repo.
- **Workflow prompts:** use placeholders (`ABC-123`, `<team>`, `<company>`) instead of real names, ids or URLs.

## how it fits together

> Moved here from the dotfiles README, so there is one copy.

The automation logic lives **outside this repo** at `~/Documents/code/`, which is its own git repo, [agent-workspace](https://github.com/leoga8/agent-workspace) (private). It tracks only the framework, and job content stays local to each machine. This repo tracks the one-line slash-command shims that point into it. To set up `~/Documents/code` on a new machine, follow the agent-workspace README.

### Layers

| layer | what it is | lives in |
|-------|-----------|----------|
| workflow | one atomic task (e.g. `test`, `query`) | `workflows/<domain>/<name>/` |
| pipeline | runs one or more workflows (menu-driven or sequential) | `pipelines/<domain>/...` |
| planner | natural-language entry point — discovers what exists and routes a request or ticket to it, picking a model tier per step | `planner/<name>/` |
| orchestration | runs a set of tickets through planners with a state machine; drafts outbound updates | `orchestration/<name>/` |
| trigger | *(future, separate repo)* external automation (e.g. n8n) that calls a planner headlessly | — |

Each layer is a **flat top-level sibling**, split by domain inside. Layers are not nested by call order: pipelines still work without a planner, planners/orchestration are cross-domain, and new layers never move existing paths.

```
~/Documents/code/
├── workflows/          # by domain: obsidian, code, database, meta (+ solutions on the work laptop)
│   └── <domain>/<name>/{prompts,context,tools}   # context/ = static reference only; no data/ folders
├── pipelines/          # by domain, prompts only: obsidian, code (+ implement, develop), database, meta/scaffold
├── planner/
│   ├── core.md         # shared rules: discovery, modes, ticket + plan formats, model tiers, drafts
│   ├── sources.md      # how to fetch / comment / set status: manual, linear, helpscout
│   ├── assistant/      # personal planner  → /assistant   (prompts/, config.md)
│   └── work/           # work planner      → /work (+ /solutions alias)   (prompts/, config.md, + shared-repo files; not in git)
└── orchestration/      # personal only — never shared
    ├── state-machine.md
    └── tickets/        # process a set of tickets
```

### Planners are the only slash commands

| shim | planner | scope |
|------|---------|-------|
| `/assistant` | `planner/assistant` | personal — domains and vault in `planner/assistant/config.md` |
| `/work` | `planner/work` | work — domains, vault and ticket sources in `planner/work/config.md` |
| `/solutions` | `planner/work` | alias of `/work`; the name teammates use in the shared repo |

Talk to them in natural language (`/assistant write tests for src/foo.py`, `/work process today's tickets`). On every call a planner globs `~/Documents/code/*/**/prompts/*.md` and reads each file's `# title`, `>` description and optional `tier`/`approval` frontmatter, so **new workflows, pipelines, planners and orchestrations need no new shims** — only their domain listed in the planner config. With no request it shows a menu of everything in scope, including creating new workflows/pipelines/planners/orchestrations (via the `meta` scaffold).

Both shims are shared on every machine. A machine without a planner's content (e.g. `/work` on the personal laptop) simply has nothing in scope for it. A new planner = new shim pair, created by the `new-planner` scaffold.

Headless use (for a future trigger): `claude -p "/work process today's tickets"`.

### Model tiers

Plan steps carry a tier that sets the model and effort of the worker that runs them:

| tier | Claude Code | OpenCode (`agents` in `opencode.json`) |
|------|-------------|------------------------------------------|
| fast | haiku, low effort | `fast` → `claude-agent/haiku#low` |
| standard | sonnet, medium effort | `standard` → `claude-agent/sonnet#medium` |
| deep | opus, high effort | `deep` → `claude-agent/opus#high` |

OpenCode reaches Claude through the **Claude Agent** login (`opencode auth login`, then Claude Agent), which uses the Anthropic subscription: no API key. It only offers the aliases (`haiku`, `sonnet`, `opus`, `fable`), which resolve to the latest models; full ids like `claude-opus-5-5` are unavailable. OpenCode and Claude Code share the subscription's usage. If it runs out, pick an OpenAI model in OpenCode by hand (needs the ChatGPT login).

The work orchestrator itself runs at low effort: on sonnet in Claude Code (`claude --model sonnet --effort low`), and on the same model in OpenCode, set by `~/Documents/code/opencode.json` (`"model": "claude-agent/sonnet#low"`, stowed from the `opencode` package). `~/Documents/code/automation/work-morning.sh [--claudecode | --opencode]` starts it.

In Rex, each step runs as its own tab, running the same agent as the orchestrator (Claude Code, or OpenCode with the tier agent's model), started by `~/Documents/code/automation/rex-worker.sh` (see `automation/workers.md`), so you can switch to a worker to watch or answer it. Elsewhere (no Rex, teammates) steps run as in-process subagents with the same model and effort.

### Tickets hold all per-case files

```
<tickets_path>/<id>/<id>.md         # ticket note + every input and output file for that ticket
<vault>/raw/runs/YYYY-MM-DD-<slug>.md
```

`tickets_path` is set per planner config — `<vault>/raw/tickets` on your machines, `<repo>/tickets` (gitignored) for teammates.

- Ids: `YYYYMMDD-<slug>` (manual) or `<source>-<external-id>` (e.g. `linear-ABC-123`)
- Frontmatter `status` follows `orchestration/state-machine.md`; sections `## ticket`, `## plan`, `## drafts`, `## outputs`, `## log`
- Replaces the old `/cases` pipeline and every `data/` / `reports/` folder — workflows read inputs from and write outputs to the ticket folder; `context/` holds only static reference
- The daily note template has a `## tickets` section; "process the tickets in today's daily note" runs them
- **Dry run:** comments and status changes for Linear/HelpScout are written to `## drafts` and only sent after explicit approval

### Shared team repo (`solutions-workflows`)

Teammates get `/solutions` only: `planner/core.md`, `planner/sources.md`, the work planner (renamed back to `solutions` by the sync), and the in-scope pipelines and workflows. Never shared: `/assistant`, `orchestration/`, configs, tickets, any future trigger. Their setup guide is `planner/work/INSTALL.md` (synced to the repo root).

To share changes: on the work laptop, `/work sync the shared repo` (runs `workflows/meta/sync-shared-repo`) → review the staged diff in the repo → `git commit` and `git push` yourself.
