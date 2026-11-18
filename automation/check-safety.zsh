#!/bin/zsh
# check-safety.zsh
# Stops job data and secrets from reaching the workspace repo. Runs as the
# pre-commit hook (setup.zsh installs it) on the staged files, or by hand.
# Two pattern sets:
#   - generic, below: emails, home folders, API keys and tokens
#   - job terms: automation/safety-terms.txt, one extended regex per line
#     (company, customers, ticket keys, schema names). Not in git, so the
#     terms themselves never get committed.
# Usage: check-safety.zsh [--all]   --all: every tracked file, not just staged ones

cd "${0:A:h}/.." || exit 1
TERMS="automation/safety-terms.txt"
GENERIC=(
  '[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]*[a-z]{2,}'   # emails
  '/Users/[a-z][a-z0-9_-]*/'                                     # a home folder
  '(sk|pk|rk)-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}'
  '(api[_-]?key|secret|token|password)["'\'' ]*[=:] *["'\'']?[A-Za-z0-9+/_-]{16,}'
)
# Placeholders that look like the generic patterns
ALLOW='/Users/(YOU|you)/|noreply@|@example\.(com|org)|user@host|git@github\.com'

if [[ "$1" == --all ]]; then
  FILES=(${(f)"$(git ls-files)"})
else
  FILES=(${(f)"$(git diff --cached --name-only --diff-filter=ACMR)"})
fi
FILES=(${FILES:#automation/check-safety.zsh})
(( ${#FILES} )) || exit 0

found=0
check() {   # check <label> <regex>
  local hits
  if [[ "$1" == job ]]; then
    hits=$(grep -nHiE -- "$2" $FILES 2>/dev/null)
  else
    hits=$(grep -nHoE -- "$2" $FILES 2>/dev/null | grep -vE "$ALLOW")   # only the match, so a placeholder elsewhere on the line doesn't hide it
  fi
  [[ -n "$hits" ]] && { echo "$hits" | sed "s/^/  [$1] /"; found=1 }
}
for re in $GENERIC; do check generic "$re"; done
if [[ -f "$TERMS" ]]; then
  for re in ${(f)"$(grep -vE '^\s*(#|$)' "$TERMS")"}; do check job "$re"; done
fi

if (( found )); then
  echo "\ncheck-safety: the lines above look like job data or secrets. Reword them, or"
  echo "move the file to an ignored folder. (git commit --no-verify skips this check.)"
  exit 1
fi
exit 0
