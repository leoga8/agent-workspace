# worker runtime

> How planner steps run as workers on this machine: with Rex, each worker is a tab in the caller's Rex session running the same agent as you (Claude Code or OpenCode), started at the step's tier. Read by `planner/core.md` ("4. running a step") when this file exists. Personal: never synced to the shared repo.

---

## when it applies

`$REX_SESSION` is set (the session runs inside Rex). Otherwise use an in-process subagent at the tier's model and effort, as `core.md` says. In OpenCode, that's the tier's agent: `@fast`, `@standard` or `@deep`.

## deep steps that core.md runs in the main session

`core.md` runs steps with many connector calls or mid-step decisions (validate-config, review-bill) in the main session. The work-morning orchestrator runs on a light model, so when such a step's tier is `deep`, don't run it in the main session:
- With Rex: run it as a worker (below) and tell the user its tab name; they answer its decision points in that tab.
- Without Rex: run it as a `deep` subagent. At each decision point it writes the question to its result file and stops; ask the user, then resume it with a new `deep` subagent given the answer and the previous result file.

## what a worker prompt adds

Every worker prompt ends with the two lines `orchestration/tickets/prompts/tickets.md` gives: questions and approvals start with `[<ticket-id>]`, and the result file ends with a `learnings:` line. The caller reads that line and logs it in `self-improvement/` when the run closes. The worker never writes there itself.

## one worker

```sh
W=~/Documents/code/automation/rex-worker.sh
$W start <name> <tier> <root> "<worker prompt>" --<you> --result <result file>
$W wait <name> 600        # prints the state; working = timed out, wait again
```

- `--<you>`: `--claudecode` if you are Claude Code, `--opencode` if you are OpenCode, so the worker runs the same agent.
- `--result`: the result file named in the worker prompt (e.g. `<ticket folder>/step-<n>.result.md`). Always pass it. OpenCode workers report `idle` when they finish, and the file is how `wait` tells finished from not started.

- Names: `<ticket-id>-s<n>` for a step, `<ticket-id>-plan` for planning, `<ticket-id>-review` for a batch review, `daily-<source>` for a daily-note source.
- If `start` fails with "hasn't started (startup dialog?)", tell the user the tab name so they can clear the dialog, then `wait`. Never start it again blindly.
- If `wait` prints `blocked`, the worker is waiting on a permission prompt or a question. Tell the user the tab name so they can answer in Rex, then wait again.
- When it prints `done`, `idle` or `error`, read the worker's result file (not its screen), then `$W close <name>`.

## several workers in parallel

Start them all, then wait for each in turn. They run at the same time, so waiting one after another costs nothing:

```sh
$W start <name-1> <tier> <root> "<prompt 1>" --<you> --result <file 1>
$W start <name-2> <tier> <root> "<prompt 2>" --<you> --result <file 2>
$W wait <name-1> 600; $W wait <name-2> 600
```

Handle each result as above. Close every worker once its result file is read.

## giving a worker a pane

The user can watch or take over any worker by switching to its tab in Rex. Workers stay open until closed, so a `blocked` worker keeps its state until the user answers.
