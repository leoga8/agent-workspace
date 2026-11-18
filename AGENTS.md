# ~/Documents/code (agent-workspace)

- This folder is the `agent-workspace` git repo, and its `.gitignore` is an allowlist.
  - **Framework files are tracked:** `automation/`, `orchestration/`, `planner/core.md`, `planner/sources.md`, `planner/assistant/`, the `code`/`database`/`meta`/`obsidian` pipelines and workflows, and `templates/`. Other laptops get changes to them with `git pull`.
  - **Job content is never tracked:** the job planner (`planner/<PLANNER>/`), job domains, cloned work repos, vaults, every `config.md`, `automation/settings.env` and database `context/` files.
- Never put a company, customer, ticket id, internal URL or schema name in a tracked file. Use placeholders (`ABC-123`, `<team>`, `<company>`). The pre-commit hook (`automation/check-safety.zsh`) blocks them; don't bypass it.
- The job planner's name comes from `PLANNER` in `automation/settings.env` (default in `settings.example.env`). Scripts read it instead of hard-coding a planner.
- If `WORK-LAPTOP-CHANGES.md` exists here (personal laptop only, not in git), changes to **job** files the work laptop also has go in it, as one self-contained prompt per entry. Framework changes need no entry: commit them, and the work laptop pulls.
  - Give each entry the date and a one-line goal. For each file, name the section heading, the intent and the exact new text.
  - End with "Check each edit against the existing text, then report what changed". Add "then sync the shared repo" when a file that sync-shared-repo copies changed.
- Teammates get `planner/core.md` and `planner/sources.md` through a shared repo (`workflows/meta/sync-shared-repo`, where it exists).
  - In those files, only add text. Never reword, shorten or remove an existing sentence, because the sync flags any changed sentence as a removed rule.
  - Put anything that only matters on the user's machines (Rex, workers, the work-morning orchestrator) in `automation/` instead.
  - The one approved exception so far: anonymizing the incident notes in `core.md` when this repo was created (2026-11-18).
