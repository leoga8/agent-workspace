#!/bin/zsh
# work-morning.sh
# Starts the work orchestrator, in Claude Code or OpenCode, and gives it the
# morning prompt. It only routes and keeps books: fetching and every real step
# run as tier-sized workers (see planner/core.md, "4. running a step").
# - With Rex: the orchestrator runs in its own Rex session, workers can run as
#   tabs (automation/workers.md), and each day gets a fresh session.
# - Without Rex (e.g. in Warp): the orchestrator runs in the current terminal,
#   and steps run as subagents.
# Either way, check-tickets.sh runs beside it for the day and adds newly
# assigned Linear tickets every hour.
# Usage: work-morning.sh [--claudecode | --opencode]
#   no flag: asks in a terminal; Claude Code when nobody is there to ask (launchd)

set -e
source "${0:A:h}/lib.zsh"

# Runs at 10:30 from launchd with nobody watching, so say when it fails
TRAPZERR() { notify "work-morning.sh failed — see /tmp/work-morning.err" Glass }

if ! parse_tool "$@"; then
  echo "Usage: work-morning.sh [--claudecode | --opencode]" >&2
  exit 2
fi
if [[ -z "$TOOL" && -t 0 && -t 1 ]]; then
  echo "Which agent today?  1) Claude Code   2) OpenCode"
  read "choice?> "
  [[ "$choice" == 2* || "$choice" == [oO]* ]] && TOOL=opencode || TOOL=claudecode
fi
TOOL="${TOOL:-claudecode}"

# Both agents have the planner's command (shims in ~/.claude/commands and
# ~/.opencode/commands); the planner and the daily-note sources come from
# settings.env
SOURCES=(${=MORNING_SOURCES})
if (( ${#SOURCES} > 1 )); then
  SOURCES_TEXT="${(j:, :)SOURCES[1,-2]} and ${SOURCES[-1]} sections"
else
  SOURCES_TEXT="${SOURCES[1]} section"
fi
MORNING_PROMPT="/$PLANNER create today's daily note; fill its $SOURCES_TEXT by running workflows/obsidian/daily-sources once per source as parallel fast workers, reading only their summary files; then start a run for all Linear tickets due today or overdue"
STARTED_FILE="$(dirname "$PENDING_FILE")/session-started.txt"
TODAY=$(date +%F)
CHECKER=("${0:A:h}/check-tickets.sh" "--$TOOL" --every 3600)

mkdir -p "$(dirname "$PENDING_FILE")"

# If there were missed ticket checks while the session wasn't running, catch up
# in the same prompt
PROMPT="$MORNING_PROMPT"
if [[ -f "$PENDING_FILE" ]]; then
  MISSED=$(wc -l < "$PENDING_FILE" | tr -d ' ')
  PROMPT="$PROMPT. Also, there were $MISSED missed ticket checks while the session wasn't running: check Linear for any tickets assigned to you that are Todo or In Progress and aren't already in today's run, and add any new ones"
fi

# The orchestrator, at low effort: Sonnet in Claude Code, GPT-6 Sol in OpenCode
# (subscription). OpenCode has no model flag for its TUI, so
# ~/Documents/code/opencode.json sets it there
if [[ "$TOOL" == opencode ]]; then
  ORCHESTRATOR=(opencode --prompt "$PROMPT")
else
  ORCHESTRATOR=(claude --model sonnet --effort low "$PROMPT")
fi

# Without Rex, run the orchestrator right here, with the ticket checks in the
# background until it exits
if ! has_rex; then
  if [[ ! -t 1 ]]; then
    notify "Rex isn't installed, so work-morning.sh can't start on its own. Run it in a terminal." Glass
    exit 1
  fi
  echo "$TODAY $TOOL" > "$TOOL_FILE"
  rm -f "$PENDING_FILE"
  NOTIFY_TTY="$TTY" "${CHECKER[@]}" --parent $$ </dev/null >/dev/null 2>&1 &!
  cd "$WORK_DIR"
  exec "${ORCHESTRATOR[@]}"
fi

OLD_SESSION=$(session_id "$AGENT_NAME")
if [[ -n "$OLD_SESSION" && "$(cat "$STARTED_FILE" 2>/dev/null)" == "$TODAY" ]]; then
  # Today's session: if the agent is running, just bring the app forward
  EXISTING=$(find_session)
  if [[ -n "$EXISTING" ]]; then
    echo "Agent '$AGENT_NAME' already running ($EXISTING). Switch to it in Rex."
    show_session || true
    exit 0
  fi
  # Today's session without an agent: leave it for you to look at
  notify "Today's '$AGENT_NAME' session is open but no agent is running in it. Close it in Rex and run work-morning.sh again." Glass
  echo "Rex session '$AGENT_NAME' exists but no agent is running in it. Close it in Rex and run this again."
  exit 1
elif [[ -n "$OLD_SESSION" ]]; then
  # A session from an earlier day: start today fresh
  "$REX" kill "$OLD_SESSION" >/dev/null
  echo "Closed the '$AGENT_NAME' session from an earlier day."
fi

# Create the session with the agent in its orchestrator tab, and the ticket
# checks in a background tab (closed with the session)
"$REX" new "$AGENT_NAME" --window "$ORCH_LABEL" -c "$WORK_DIR" --json -- "${ORCHESTRATOR[@]}" >/dev/null
echo "$TODAY" > "$STARTED_FILE"
echo "$TODAY $TOOL" > "$TOOL_FILE"
"$REX" window new -s "$AGENT_NAME" ticket-checks --focus=false -c "$WORK_DIR" --json -- "${CHECKER[@]}" >/dev/null
BLOCK=$(tab_block "$AGENT_NAME" "$ORCH_LABEL")

# Wait for the agent to finish starting up (up to 60s); it reports no status
# while a startup dialog such as "trust this folder?" is open
if wait_for_start "$BLOCK" 60; then
  rm -f "$PENDING_FILE"
else
  notify "The agent hasn't started in the '$AGENT_NAME' session (startup dialog?). Check it in Rex." Glass
  [[ -f "$PENDING_FILE" ]] && echo "The agent hasn't started yet (startup dialog?) — leaving missed ticket checks for next run."
fi

show_session || true
echo "Work session started with $TOOL. Switch to the '$AGENT_NAME' session in Rex."
