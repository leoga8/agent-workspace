# rules

> Every session in this workspace follows these rules, whatever the tool (Claude Code, OpenCode or another). Keep this file under 40 lines: it loads in every session and every worker.

- **Ticket id first.** In a ticket context, every question or approval request starts with `[<ticket-id>]`, written exactly as the folder name (`linear-ABC-123`, `20261124-slug`), so it can be found in the vault.
- **Capture learnings** in `<root>/self-improvement/` (untracked). Write one file per learning, `YYYYMMDD-HHMMSS-<slug>.md`, with frontmatter `target:` (the file it would change), `status: new` and `tracked: true|false` (from `git ls-files --error-unmatch <target>`), then 2 to 5 lines: what happened and the suggested change.
  - Log when you finish a task, but only if there is something to record. Say so in one line.
  - Log a user correction ("no, don't do X") at once.
  - Also log: nothing in scope fit the request, the same ad-hoc step was done twice, a doc contradicts the code, or `automation/vault.py` or the viewer was hard to use (`target: automation/vault.py`).
  - Writing to `self-improvement/` needs no approval. Never log secrets or job details in a `tracked: true` entry.
  - Skip this in headless one-shot runs such as `check-tickets.sh`. Workers end their result file with a `learnings:` line instead, and the orchestrator logs it when the run closes.
- **Scope.** Before editing a file, check whether it is tracked (`git ls-files --error-unmatch <file>`).
  - Tracked files are changed only on the personal laptop, committed and pushed there. The work laptop only pulls. On the work laptop, log a tracked-file learning with `tracked: true` and do not edit the file.
  - Untracked files (job planner, job workflows, configs) are edited on the laptop where they live.
  - The dotfiles repo is changed on the personal laptop only.
- **Sync.** If `workflows/meta/sync-shared-repo/` exists (the work laptop), run it after you change a file it copies, and after a `git pull` that changed one. It only stages. Never commit or push.
- **Approval.** Never change a workflow, pipeline, planner or rule without approval. `self-improvement/` is the only exception.
- **Context.** Load only the files the task needs. Read a file's `# title` and first `>` line before loading the rest.
