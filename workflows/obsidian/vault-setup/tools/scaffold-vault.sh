#!/bin/bash
# scaffold-vault.sh
# Creates the full vault folder structure from scratch.
# Usage: ./scaffold-vault.sh <vault-root> [personal|work]
#        type defaults to "personal" if omitted
# Note: only creates folders — files (templates, AGENTS.md, etc.) are created by the AI workflow.

VAULT="${1:-.}"
TYPE="${2:-personal}"

if [ -z "$1" ]; then
  echo "error: vault root path is required"
  echo "usage: ./scaffold-vault.sh <vault-root> [personal|work]"
  exit 1
fi

# folders common to all vault types
COMMON_DIRS=(
  "$VAULT/add"
  "$VAULT/hubs"
  "$VAULT/maps"
  "$VAULT/raw/projects"
  "$VAULT/raw/tasks"
  "$VAULT/raw/tickets"
  "$VAULT/raw/runs"
  "$VAULT/raw/bookmarks"
  "$VAULT/raw/daily"
  "$VAULT/raw/templates"
  "$VAULT/raw/_important"
  "$VAULT/src/img"
  "$VAULT/src/pdf"
  "$VAULT/src/excalidraw"
)

# personal-only folders
PERSONAL_DIRS=(
  "$VAULT/raw/books"
  "$VAULT/raw/games"
  "$VAULT/raw/mate"
  "$VAULT/raw/bills"
  "$VAULT/raw/fitness"
)

# work-only folders
WORK_DIRS=(
)

mkdir -p "${COMMON_DIRS[@]}"

if [ "$TYPE" = "personal" ]; then
  mkdir -p "${PERSONAL_DIRS[@]}"
elif [ "$TYPE" = "work" ]; then
  [ ${#WORK_DIRS[@]} -gt 0 ] && mkdir -p "${WORK_DIRS[@]}"
else
  echo "error: type must be 'personal' or 'work'"
  exit 1
fi

echo "scaffolded: $VAULT (type: $TYPE)"
echo ""
echo "folders created:"
find "$VAULT" -type d | sort | sed "s|$VAULT/||" | grep -v "^\.$"
