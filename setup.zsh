#!/bin/zsh
# setup.zsh
# Sets up this workspace on a laptop: checks the tools, creates the local files
# git doesn't carry (planner configs, settings, the safety-term list), builds a
# vault if a config points at one that doesn't exist, and installs the
# pre-commit safety check. Safe to run again: it never overwrites a file.
# Usage: setup.zsh [--check]   --check: only report, change nothing

ROOT="${0:A:h}"
CHECK=""; [[ "$1" == --check ]] && CHECK=1
source "$ROOT/automation/lib.zsh"
todo=0
ok()   { echo "  ok    $1" }
miss() { echo "  todo  $1"; todo=1 }
did()  { echo "  done  $1" }

echo "Tools"
for t in claude opencode uv jq glow rg stow; do
  (( $+commands[$t] )) && ok "$t" || miss "$t: brew install $t (see ~/.dotfiles bootstrap)"
done
has_rex && ok "Rex" || echo "  --    Rex not installed (optional: without it, work-morning.sh runs in the terminal)"

echo "\nLocal files"
for d in "$(dirname "$PENDING_FILE")"; do
  if [[ -d "$d" ]]; then ok "$d"
  elif [[ -n "$CHECK" ]]; then miss "$d"
  else mkdir -p "$d" && did "created $d"; fi
done
[[ -f "$ROOT/automation/settings.env" ]] && ok "automation/settings.env" ||
  echo "  --    automation/settings.env not there: using settings.example.env (PLANNER=$PLANNER)"
TERMS="$ROOT/automation/safety-terms.txt"
if [[ -f "$TERMS" ]]; then ok "automation/safety-terms.txt"
elif [[ -n "$CHECK" ]]; then miss "automation/safety-terms.txt (job terms the pre-commit check blocks)"
else
  printf '%s\n' "# Job terms check-safety.zsh blocks from the workspace repo, one extended regex" \
    "# per line: company, customers, ticket keys, schema names. Not in git." > "$TERMS"
  did "created automation/safety-terms.txt: add your job's terms"
fi

echo "\nPlanners"
for ex in "$ROOT"/planner/*/config.example.md(N); do
  dir="${ex:h}" name="${ex:h:t}" cfg="${ex:h}/config.md"
  if [[ -f "$cfg" ]]; then ok "planner/$name/config.md"
  elif [[ -n "$CHECK" ]]; then miss "planner/$name/config.md (copy config.example.md and fill it in)"
  else cp "$ex" "$cfg" && did "created planner/$name/config.md from the example: fill in vault_path"; fi
done
[[ -d "$ROOT/planner/$PLANNER" ]] && ok "job planner planner/$PLANNER" ||
  miss "job planner planner/$PLANNER: create it with '/assistant create a new planner' (workflows/meta/new-planner)"

echo "\nVaults"
for cfg in "$ROOT"/planner/*/config.md(N); do
  vault=$(sed -nE 's/^- \*\*vault_path:\*\* *(.*)$/\1/p' "$cfg" | head -1)
  vault="${vault/#\~/$HOME}"
  [[ -z "$vault" || "$vault" == none || "$vault" == *path/to* ]] && continue
  if [[ -d "$vault/raw" ]]; then ok "$vault"
  elif [[ -n "$CHECK" ]]; then miss "$vault doesn't exist (setup.zsh builds it)"
  else
    type=personal; [[ "${cfg:h:t}" == "$PLANNER" ]] && type=work
    bash "$ROOT/workflows/obsidian/vault-setup/tools/scaffold-vault.sh" "$vault" "$type" >/dev/null &&
      cp -n "$ROOT"/templates/vault/*.md "$vault/raw/templates/" &&
      did "built $vault ($type) with templates/vault; ask the planner for vault-setup to finish it"
  fi
done

echo "\nShims (from ~/.dotfiles)"
for name in assistant "$PLANNER"; do
  for dir in ~/.claude/commands ~/.opencode/commands; do
    [[ -f "$dir/$name.md" ]] && ok "${dir/#$HOME/~}/$name.md" ||
      miss "${dir/#$HOME/~}/$name.md: add it to ~/.dotfiles and restow (one line: @~/Documents/code/planner/$name/prompts/$name.md)"
  done
done

echo "\nGit"
HOOK="$ROOT/.git/hooks/pre-commit"
if [[ ! -d "$ROOT/.git" ]]; then miss "not a git repo (see README: adopting the repo)"
elif grep -q check-safety "$HOOK" 2>/dev/null; then ok "pre-commit safety check"
elif [[ -n "$CHECK" ]]; then miss "pre-commit safety check"
elif [[ -e "$HOOK" ]]; then miss "a pre-commit hook already exists: add 'automation/check-safety.zsh || exit 1' to it"
else
  printf '#!/bin/zsh\nexec "$(git rev-parse --show-toplevel)/automation/check-safety.zsh"\n' > "$HOOK" && chmod +x "$HOOK" &&
    did "installed the pre-commit safety check"
fi

(( todo )) && echo "\nSome items need you (todo above)." || echo "\nAll set."
