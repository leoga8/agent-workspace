#!/bin/zsh
# rex-worker.sh
# Runs a planner step as a worker (Claude Code or OpenCode) in its own
# background tab of the caller's Rex session, so you can watch it and answer its
# prompts (see automation/workers.md). Outside Rex it uses the work-morning session.
# Usage:
#   rex-worker.sh start <name> <tier> <cwd> <prompt> [--claudecode | --opencode] [--result <file>]
#                                                      tier: fast | standard | deep
#                                                      agent: default today's pick from work-morning.sh
#                                                      --result: the file the worker writes when done
#   rex-worker.sh wait <name> [max seconds]            prints done, blocked, error, idle or working
#   rex-worker.sh close <name>

source "${0:A:h}/lib.zsh"
WORK_SESSION="${REX_SESSION:-$AGENT_NAME}"

CMD="$1"; NAME="$2"
[[ -z "$CMD" || -z "$NAME" ]] && { sed -n '/^# Usage:/,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
RESULT_REF="$WORKER_DIR/${WORK_SESSION//[^A-Za-z0-9_-]/_}-$NAME.result"

case "$CMD" in
  start)
    [[ $# -lt 5 ]] && { echo "Usage: rex-worker.sh start <name> <tier> <cwd> <prompt> [--claudecode | --opencode] [--result <file>]" >&2; exit 2; }
    TIER="$3" CWD="$4" PROMPT="$5" WTOOL="" RESULT=""
    shift 5
    while (( $# )); do
      case "$1" in
        --claudecode) WTOOL=claudecode ;;
        --opencode)   WTOOL=opencode ;;
        --result)     RESULT="$2"; shift ;;
        *) echo "Unknown option '$1'." >&2; exit 2 ;;
      esac
      shift
    done
    if [[ -n "$(worker_block "$NAME")" ]]; then
      echo "A worker tab '$NAME' already exists. Wait for it or close it first." >&2
      exit 1
    fi
    mkdir -p "$WORKER_DIR"
    if [[ -n "$RESULT" ]]; then echo "$RESULT" > "$RESULT_REF"; else rm -f "$RESULT_REF"; fi
    BLOCK=$(start_worker "$NAME" "$TIER" "$CWD" "$PROMPT" "${WTOOL:-$(todays_tool)}") || exit 1
    if ! wait_for_start "$BLOCK" 60; then
      echo "Worker '$NAME' hasn't started after 60s (startup dialog?). Check its tab in Rex." >&2
      exit 1
    fi
    echo "Started worker '$NAME' ($BLOCK)"
    ;;
  wait)
    BLOCK=$(worker_block "$NAME")
    [[ -z "$BLOCK" ]] && { echo "No worker tab '$NAME'." >&2; exit 1; }
    wait_for_worker "$BLOCK" "${3:-0}" "$(cat "$RESULT_REF" 2>/dev/null)"
    session_state "$BLOCK"
    ;;
  close)
    close_worker "$NAME"
    rm -f "$RESULT_REF"
    ;;
  *)
    echo "Unknown command '$CMD' (start, wait or close)." >&2
    exit 2
    ;;
esac
