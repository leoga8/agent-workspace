#!/bin/bash
# create-bill-note.sh
# Creates a bill note for a given vendor and month from the vault template.
# Usage: ./create-bill-note.sh <vault-root> <vendor-name> [YYYY-MM] [--day DD] [--yearly]
# If month is omitted, defaults to current month.
#   --day DD   set the bill date to YYYY-MM-DD instead of the 1st
#   --yearly   name the note "<vendor> - YYYY.md" instead of "<vendor> - YYYY-MM.md"
#   --property VALUE   set the note's `vendor:` property to VALUE instead of the vendor name
#                      (the filename always uses the vendor name)

VAULT="${1:-.}"
VENDOR="$2"
shift 2 2>/dev/null

MONTH="$(date +%Y-%m)"
if [[ "$1" =~ ^[0-9]{4}-[0-9]{2}$ ]]; then
  MONTH="$1"
  shift
fi

DAY="01"
YEARLY=0
PROPERTY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --day)      DAY=$(printf '%02d' "$((10#$2))"); shift 2 ;;
    --yearly)   YEARLY=1; shift ;;
    --property) PROPERTY="$2"; shift 2 ;;
    *)          echo "error: unknown option $1"; exit 1 ;;
  esac
done

if [ -z "$VENDOR" ]; then
  echo "error: vendor name is required"
  echo "usage: ./create-bill-note.sh <vault-root> <vendor> [YYYY-MM] [--day DD] [--yearly] [--property VALUE]"
  exit 1
fi

TEMPLATE="$VAULT/raw/templates/bill.md"
if [ "$YEARLY" -eq 1 ]; then
  TARGET="$VAULT/raw/bills/$VENDOR - ${MONTH%-*}.md"
else
  TARGET="$VAULT/raw/bills/$VENDOR - $MONTH.md"
fi
DATE="$MONTH-$DAY"
VENDOR_PROP="${PROPERTY:-$VENDOR}"
VENDOR_PROP_ESC=$(printf '%s' "$VENDOR_PROP" | sed -e 's/[\/&|]/\\&/g')

if [ -f "$TARGET" ]; then
  echo "error: bill already exists at $TARGET"
  exit 1
fi

if [ ! -f "$TEMPLATE" ]; then
  echo "error: template not found at $TEMPLATE"
  exit 1
fi

sed -e "s/^date:$/date: $DATE/" \
    -e "s/^vendor:$/vendor: $VENDOR_PROP_ESC/" \
    -e "s/^paid:$/paid: false/" \
    "$TEMPLATE" > "$TARGET"

echo "created: $TARGET"
