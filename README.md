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
