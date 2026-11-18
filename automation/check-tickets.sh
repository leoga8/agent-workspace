#!/bin/zsh
# check-tickets.sh
# Looks up newly assigned Linear tickets and brings them to the work session:
# it asks you which to add, except the ones TICKET_AUTO_ADD (lib.zsh) adds on its own.
# The lookup runs as a one-shot fast-tier agent (Claude Code or OpenCode, today's
# pick from work-morning.sh unless a flag says otherwise), so the orchestrator
# only hears about new tickets.
# - With Rex, the message is typed into the orchestrator, which asks you. If it
#   isn't running, the missed check is logged for the next work-morning.sh.
# - Without Rex, nothing can type into the session, so you get a notification
#   and the message is copied to the clipboard for you to paste.
# work-morning.sh starts it with --every 3600 for the day; one check also works
# (e.g. from launchd).
# Usage: check-tickets.sh [--claudecode | --opencode] [--every <seconds>] [--parent <pid>]
#   --every   keep checking at that interval (the first check waits one interval)
#   --parent  stop when that process ends (the agent the checks belong to)

source "${0:A:h}/lib.zsh"

STATE_DIR="$(dirname "$PENDING_FILE")"
LOG_FILE="$STATE_DIR/check-tickets.log"
EVERY="" PARENT="" TOOL_ARGS=()
while (( $# )); do
  case "$1" in
    --claudecode|--opencode) TOOL_ARGS+=("$1") ;;
    --every)  EVERY="$2"; shift ;;
    --parent) PARENT="$2"; shift ;;
    *) echo "Unknown option '$1'." >&2; exit 2 ;;
  esac
  shift
done
parse_tool "${TOOL_ARGS[@]}"
TOOL="${TOOL:-$(todays_tool)}"

mkdir -p "$STATE_DIR"

check_once() {
  local today=$(date +%F)
  local ids_file="$STATE_DIR/linear-ids-$today.txt"
  local sent_file="$STATE_DIR/sent-ids-$today.txt"
  local session=""

  if has_rex; then
    session=$(find_session)
    if [[ -z "$session" ]]; then
      echo "$(date '+%Y-%m-%d %H:%M')" >> "$PENDING_FILE"
      notify "Work session not running — missed ticket check logged. Start work-morning.sh to pick them up." Glass
      return 0
    fi
  fi

  # Ask a fast one-shot agent for the tickets only
  rm -f "$ids_file"
  echo "$(date '+%F %T') checking with $TOOL" >> "$LOG_FILE"
  run_headless "$TOOL" fast "$WORK_DIR" \
    "Using the Linear MCP tools, list the issues assigned to me whose state is Todo or In Progress. Write one line per issue to $ids_file, with four tab-separated fields: identifier (e.g. ABC-123), due date as YYYY-MM-DD or none, priority (Urgent, High, Medium, Low or None), title. Write an empty file if there are none, and nothing else in the file. If no Linear tools are available, write the single line ERROR: no Linear tools. Read-only: change nothing in Linear." \
    900 "$LOG_FILE"
  if (( $? == 124 )); then
    notify "The ticket check hasn't finished after 15 minutes — see $LOG_FILE." Glass
    return 1
  fi

  if [[ ! -f "$ids_file" ]] || grep -q '^ERROR' "$ids_file"; then
    notify "Ticket check failed: ${$(head -1 "$ids_file" 2>/dev/null):-no result}" Glass
    return 1
  fi

  # Known ids: today's run notes (if the vault is configured) and ids already
  # brought up today. The catalog, summary and reconcile files beside them don't count.
  local vault=$(sed -nE 's/^- \*\*vault_path:\*\* *(.*)$/\1/p' "$WORK_DIR/planner/$PLANNER/config.md" | head -1)
  vault="${vault/#\~/$HOME}"
  local runs=() f
  for f in "$vault"/raw/runs/$today-*.md(N); do
    [[ "$f" == *-catalog.md || "$f" == *.summary.md || "$f" == *-reconcile.md ]] || runs+=("$f")
  done
  local known=$( { cat "$sent_file" 2>/dev/null; (( ${#runs} )) && cat "${runs[@]}" } | grep -oE '[A-Z]+-[0-9]+' | sort -u)
  local new=$(grep -oE '^[A-Z]+-[0-9]+' "$ids_file" | sort -u | comm -23 - <(echo "$known"))
  [[ -z "$new" ]] && return 0

  # Split them into the ones to add now and the ones to ask about
  local auto=() ask=() id line due prio title
  for id in ${(f)new}; do
    line=$(grep -m1 "^$id"$'\t' "$ids_file")
    due=$(cut -f2 <<< "$line"); prio=$(cut -f3 <<< "$line"); title=$(cut -f4 <<< "$line")
    if [[ "$TICKET_AUTO_ADD" == due-today* && "$due" == <->-<->-<-> && ! "$due" > "$today" ]] ||
       [[ "$TICKET_AUTO_ADD" == due-today-or-urgent && "$prio" == Urgent ]]; then
      auto+=("$id")
    else
      ask+=("$id (due ${due:-none}, ${prio:-no} priority) — ${title:-untitled}")
    fi
  done
  local list="${(j:, :)${(f)new}}" message=""
  (( ${#auto} )) && message="Add these Linear tickets to today's run (skip any already in it): ${(j: :)auto}."
  (( ${#ask} )) && message="${message:+$message }These Linear tickets were newly assigned to me: ${(j:; :)ask}. Ask me which of them to add to today's run, and add only the ones I pick."

  if [[ -z "$session" ]]; then
    printf '%s' "$message" | pbcopy
    echo "$new" >> "$sent_file"
    notify "New tickets: $list — paste the copied message into the work session." Glass
    return 0
  fi

  if [[ "$(session_state "$session")" == "blocked" ]]; then
    notify "New tickets ($list) but the agent is waiting for input. Answer it, and they'll come up at the next check." Glass
    return 1
  fi
  wait_until_not_working "$session"
  send_prompt "$session" "$message"
  echo "$new" >> "$sent_file"
  notify "New tickets sent to the work session: $list"
}

parent_alive() {
  [[ -z "$PARENT" ]] || kill -0 "$PARENT" 2>/dev/null
}

if [[ -z "$EVERY" ]]; then
  check_once
  exit $?
fi

# Keep checking; wake every minute so it stops soon after the parent does
while parent_alive; do
  waited=0
  while (( waited < EVERY )) && parent_alive; do
    sleep 60
    (( waited += 60 ))
  done
  parent_alive && check_once
done
