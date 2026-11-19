#!/bin/zsh
# lib.zsh
# Shared helpers for the automation scripts. Source it, don't run it.
# With Rex, the work orchestrator is the "orchestrator" tab of the "work-morning"
# Rex session, and each worker is another tab in that session. Without Rex, the
# orchestrator runs in the terminal that started it. The agent is Claude Code or
# OpenCode, picked per day with --claudecode / --opencode.

AGENT_NAME="work-morning"
ORCH_LABEL="orchestrator"
WORK_DIR="$HOME/Documents/code"
# Per-laptop settings (planner name, morning sources): the defaults in
# settings.example.env, overridden by settings.env if present (not in git)
() {
  local f
  for f in "$WORK_DIR"/automation/settings{.example,}.env; do
    [[ -f "$f" ]] && source "$f"
  done
}
PLANNER="${PLANNER:-work}"
MORNING_SOURCES="${MORNING_SOURCES:-linear helpscout slack}"
PENDING_FILE="$HOME/.local/share/automation/pending-ticket-checks.txt"
# Absolute path: rex isn't on launchd's PATH
REX="$HOME/Library/Application Support/com.superlogical.rex/RexServer/Versions/Current/Rex Server.app/Contents/MacOS/rex"
REX_APP="Rex Beta"
# The Rex session the helpers act on: the orchestrator and its worker tabs.
# rex-worker.sh points it at the caller's own session instead.
WORK_SESSION="$AGENT_NAME"
# The agent picked for today (written by work-morning.sh)
TOOL_FILE="$(dirname "$PENDING_FILE")/session-tool.txt"
OPENCODE_CONFIG="$HOME/.config/opencode/opencode.json"
# Which newly assigned tickets check-tickets.sh adds to the run without asking:
#   none                 ask about every one (default)
#   due-today            add the ones due today or overdue, ask about the rest
#   due-today-or-urgent  same, plus any with Urgent priority
TICKET_AUTO_ADD="none"
# Tools a headless Claude may use for the ticket check without asking
CLAUDE_HEADLESS_TOOLS="Read,Write,mcp__claude_ai_Linear,mcp__linear"
# Per-worker files (OpenCode tier configs, result-file paths)
WORKER_DIR="$(dirname "$PENDING_FILE")/workers"
# Terminal that notify() writes to; set by work-morning.sh for the background
# ticket checks. Empty: this process's own terminal, if it has one.
NOTIFY_TTY="${NOTIFY_TTY:-}"

# has_rex: true when Rex is installed.
has_rex() {
  [[ -x "$REX" ]]
}

# parse_tool <args...>: sets TOOL to claudecode or opencode from --claudecode /
# --opencode, or leaves it empty. Returns 1 on an unknown argument.
parse_tool() {
  TOOL=""
  local a
  for a in "$@"; do
    case "$a" in
      --claudecode) TOOL=claudecode ;;
      --opencode)   TOOL=opencode ;;
      *) return 1 ;;
    esac
  done
}

# todays_tool: the agent work-morning.sh started today, or claudecode.
todays_tool() {
  local line=$(cat "$TOOL_FILE" 2>/dev/null)
  [[ "${line%% *}" == "$(date +%F)" ]] && echo "${line#* }" || echo claudecode
}

# opencode_model <tier>: the model of OpenCode's agent for that tier.
opencode_model() {
  local m=$(jq -r --arg t "$1" '.agents[$t].model // empty' "$OPENCODE_CONFIG" 2>/dev/null)
  if [[ -z "$m" ]]; then
    case "$1" in
      fast) m=claude-agent/haiku#low ;;
      deep) m=claude-agent/opus#high ;;
      *)    m=claude-agent/sonnet#medium ;;
    esac
  fi
  echo "$m"
}

# run_headless <tool> <tier> <cwd> <prompt> <max seconds> <log>: runs a one-shot,
# non-interactive agent and waits for it. Returns 124 if it ran out of time.
run_headless() {
  local model effort
  case "$2" in
    fast)     model=haiku;  effort=low ;;
    standard) model=sonnet; effort=medium ;;
    deep)     model=opus;   effort=high ;;
  esac
  (
    cd "$3" || exit 1
    if [[ "$1" == opencode ]]; then
      opencode run -m "$(opencode_model "$2")" --auto --archived "$4"
    else
      printf '%s' "$4" | claude -p --model "$model" --effort "$effort" --allowedTools="$CLAUDE_HEADLESS_TOOLS"
    fi
  ) >>"$6" 2>&1 &
  local pid=$! waited=0
  while kill -0 $pid 2>/dev/null; do
    if (( waited >= $5 )); then
      pkill -P $pid 2>/dev/null; kill $pid 2>/dev/null
      return 124
    fi
    sleep 5
    (( waited += 5 ))
  done
  wait $pid
}

# Prints the id of the Rex session labelled <label>, or nothing.
session_id() {
  "$REX" ls --json 2>/dev/null | jq -r --arg l "$1" '[.sessions[] | select(.label==$l)][0].session_id // empty' 2>/dev/null
}

# tab_block <session> <tab label>: prints that tab's block id, or nothing.
tab_block() {
  "$REX" window ls -s "$1" --json 2>/dev/null | jq -r --arg l "$2" '[.windows[] | select(.label==$l)][0].focused_block_id // empty' 2>/dev/null
}

# Prints a block's root program status as "<state> <app> <updated_at>", or nothing.
program_status() {
  "$REX" block call -s "$WORK_SESSION" -b "$1" program_status 2>/dev/null | jq -r '[.records[] | select(.id==null)][0] | select(.) | "\(.state) \(.app // .effective_app // "-") \(.updated_at // "-")"' 2>/dev/null
}

# Prints the orchestrator's block id if Claude Code or OpenCode is running in
# it, or nothing. A tab left at a plain shell doesn't count.
find_session() {
  [[ -z "$(session_id "$WORK_SESSION")" ]] && return
  local block=$(tab_block "$WORK_SESSION" "$ORCH_LABEL")
  [[ -z "$block" ]] && return
  local st=(${=$(program_status "$block")})
  [[ "${st[2]}" == (claude-code|opencode) ]] && echo "$block"
  return 0
}

# Prints a block's agent status: working, blocked, idle, done, error, or nothing.
session_state() {
  local st=$(program_status "$1")
  echo "${st%% *}"
}

# wait_for_start <block> [max seconds]: waits until the agent reports a status
# (Claude has finished starting up). Returns 1 if it never does.
wait_for_start() {
  local i
  for i in {1..${2:-60}}; do
    [[ -n "$(session_state "$1")" ]] && return 0
    sleep 1
  done
  return 1
}

# wait_until_not_working <block> [max seconds]: waits until a block stops working.
# Without a limit it waits as long as it takes.
wait_until_not_working() {
  local waited=0
  while [[ "$(session_state "$1")" == "working" ]]; do
    (( ${2:-0} > 0 && waited >= $2 )) && break
    sleep 5
    (( waited += 5 ))
  done
}

# wait_for_worker <block> [max seconds] [result file]: waits until a worker has
# finished its task (done, error) or needs you (blocked). Idle counts once the
# worker has been seen working or its result file exists: OpenCode reports idle,
# never done, and a short task can start and finish between two checks.
wait_for_worker() {
  local waited=0 seen_working="" state
  while true; do
    state=$(session_state "$1")
    case "$state" in
      done|blocked|error) break ;;
      working) seen_working=1 ;;
      idle) [[ -n "$seen_working" || ( -n "$3" && -s "$3" ) ]] && break ;;
    esac
    (( ${2:-0} > 0 && waited >= $2 )) && break
    sleep 5
    (( waited += 5 ))
  done
}

# send_prompt <block> <text>: types the text into the block and presses enter.
send_prompt() {
  "$REX" send -s "$WORK_SESSION" -b "$1" "$2" >/dev/null && "$REX" send-key -s "$WORK_SESSION" -b "$1" enter >/dev/null
}

# start_worker <name> <tier> <cwd> <prompt> [tool]: opens a background tab <name>
# in the work session running the agent (claudecode or opencode, default
# claudecode) at that tier, and prints its block id.
start_worker() {
  local model effort cmd
  case "$2" in
    fast)     model=haiku;  effort=low ;;
    standard) model=sonnet; effort=medium ;;
    deep)     model=opus;   effort=high ;;
    *) echo "Unknown tier '$2' (fast, standard or deep)" >&2; return 1 ;;
  esac
  if [[ "$5" == opencode ]]; then
    # OpenCode's TUI takes no model flag, and the shared background service
    # ignores per-process config: give the worker its own server and inline
    # config with the tier's model. Inline config beats a project opencode.json
    # (~/Documents/code has one for the orchestrator); a config file doesn't
    local cfg=$(jq -cn --arg m "$(opencode_model "$2")" '{model: $m}')
    cmd=(env "OPENCODE_CONFIG_CONTENT=$cfg" opencode --standalone --prompt "$4")
  else
    cmd=(claude --model "$model" --effort "$effort" "$4")
  fi
  "$REX" window new -s "$WORK_SESSION" "$1" --focus=false -c "$3" --json -- "${cmd[@]}" | jq -r '.block_ids[0]'
}

# worker_block <name>: prints the block id of worker tab <name>, or nothing.
worker_block() {
  tab_block "$WORK_SESSION" "$1"
}

# close_worker <name>: closes worker tab <name> and the agent in it.
close_worker() {
  local wid=$("$REX" window ls -s "$WORK_SESSION" --json 2>/dev/null | jq -r --arg l "$1" '[.windows[] | select(.label==$l)][0].window_id // empty')
  [[ -n "$wid" ]] && "$REX" window close -s "$WORK_SESSION" "$wid" >/dev/null
}

# show_session: brings Rex forward on the work session (best effort; selecting
# the session needs Remote Control turned on in Rex).
show_session() {
  local sid=$(session_id "$WORK_SESSION")
  [[ -n "$sid" ]] && "$REX" do session.select session_id="$sid" >/dev/null 2>&1
  open -a "$REX_APP"
}

# notify <body> [sound]: a system notification, "Work Agent: <body>". Sent
# through the terminal (OSC 9, which Warp and Rex both turn into a system
# notification, so clicking it opens that terminal). With no terminal, e.g.
# from launchd, a plain macOS notification instead.
notify() {
  local tty="${NOTIFY_TTY:-/dev/tty}" text="Work Agent: ${1//[$'\a\e']/}"
  if { printf '\033]9;%s\007' "$text" > "$tty" } 2>/dev/null; then
    return 0
  fi
  local sound="${2:-}"
  osascript -e "display notification \"${1//\"/\\\"}\" with title \"Work Agent\"${sound:+ sound name \"$sound\"}" >/dev/null 2>&1 || true
}
