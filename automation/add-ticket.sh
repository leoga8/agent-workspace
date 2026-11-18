#!/bin/zsh
# add-ticket.sh
# Adds a ticket to the current work session mid-run.
# Accepts a Linear URL or a bare ticket ID.
# Usage: add-ticket.sh <linear-url-or-ticket-id>
# Examples:
#   add-ticket.sh https://linear.app/<team>/issue/ABC-124/some-slug
#   add-ticket.sh ABC-124

set -e
source "${0:A:h}/lib.zsh"

INPUT="${1:?Usage: add-ticket.sh <linear-url-or-ticket-id>}"
MESSAGE="Add $INPUT to today's run"

# Extract ticket ID for display
TICKET_ID=$(echo "$INPUT" | grep -oE '[A-Z]+-[0-9]+' | head -1)
TICKET_ID="${TICKET_ID:-$INPUT}"

# Without Rex there's no way to type into the session: copy the message instead
if ! has_rex; then
  printf '%s' "$MESSAGE" | pbcopy
  echo "Rex isn't installed, so this can't reach the session. Copied to the clipboard — paste it into the work session:"
  echo "  $MESSAGE"
  exit 0
fi

SESSION=$(find_session)
if [[ -z "$SESSION" ]]; then
  echo "No active agent '$AGENT_NAME'. Run work-morning.sh first."
  exit 1
fi

case "$(session_state "$SESSION")" in
  blocked)
    # Waiting for your input — typing now could answer its question
    notify "The agent is waiting for input. Answer it first, then add $TICKET_ID." Glass
    echo "Agent is blocked. Answer it in Rex, then run this again."
    exit 1
    ;;
  working)
    echo "Agent is working. Waiting for a pause..."
    wait_until_not_working "$SESSION"
    ;;
esac

send_prompt "$SESSION" "$MESSAGE"
notify "$TICKET_ID added to today's run"
echo "Sent: $MESSAGE"
