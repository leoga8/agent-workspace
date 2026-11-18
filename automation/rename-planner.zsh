#!/bin/zsh
# rename-planner.zsh
# Renames a planner: its folder, its prompt and its own name lines, and points
# settings.env at the new name. Job planners aren't in git, so a rename has to
# happen on each laptop; run this once there after pulling.
# If sync-shared-repo exists, the shared repo keeps the old name: only the sync's
# local paths change, and it rewrites the name back when it copies.
# The shims in ~/.claude/commands and ~/.opencode/commands come from dotfiles.
# Usage: rename-planner.zsh <old> <new> [--apply]
#   without --apply it only prints what it would change

source "${0:A:h}/lib.zsh"
WORK_DIR="${0:A:h:h}"   # the copy this script is in
OLD="$1" NEW="$2" APPLY="$3"
if [[ -z "$OLD" || -z "$NEW" || ( -n "$APPLY" && "$APPLY" != --apply ) ]]; then
  echo "Usage: rename-planner.zsh <old> <new> [--apply]" >&2
  exit 2
fi
P="$WORK_DIR/planner"
SYNC="$WORK_DIR/workflows/meta/sync-shared-repo/prompts/sync-shared-repo.md"
SETTINGS="$WORK_DIR/automation/settings.env"
[[ -d "$P/$OLD" ]] || { echo "No planner '$OLD' in $P." >&2; exit 1; }
[[ -e "$P/$NEW" ]] && { echo "$P/$NEW already exists." >&2; exit 1; }

[[ -n "$APPLY" ]] && VERB="Renaming" || VERB="Would rename"
echo "$VERB planner '$OLD' → '$NEW' in $WORK_DIR:"
echo "  move planner/$OLD → planner/$NEW, prompts/$OLD.md → prompts/$NEW.md"
echo "  rewrite in prompts/$NEW.md and config.md: '# $OLD', name: \`$OLD\`, planner/$OLD/ paths"
echo "  set PLANNER=$NEW in automation/settings.env (only if it differs from the default)"
[[ -f "$SYNC" ]] && echo "  sync-shared-repo: local paths → planner/$NEW/; the shared repo keeps '$OLD'"

if [[ -z "$APPLY" ]]; then
  echo "\nOther files that mention planner/$OLD/ (left as they are; review them):"
  rg -l --no-messages "planner/$OLD/" "$WORK_DIR"/{automation,orchestration,pipelines,planner,workflows} |
    grep -v -e "/planner/$OLD/" -e "/sync-shared-repo.md$" -e "/rename-planner.zsh$" | sed "s#^$WORK_DIR/#  #"
  echo "\nRun again with --apply to make the change."
  exit 0
fi

set -e
mv "$P/$OLD" "$P/$NEW"
[[ -f "$P/$NEW/prompts/$OLD.md" ]] && mv "$P/$NEW/prompts/$OLD.md" "$P/$NEW/prompts/$NEW.md"

python3 - "$OLD" "$NEW" "$P/$NEW" "$SYNC" <<'EOF'
import re, sys
from pathlib import Path
old, new, folder, sync = sys.argv[1], sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])

def edit(path, fn):
    if path.is_file():
        s = path.read_text()
        t = fn(s)
        if t != s:
            path.write_text(t)
            print(f"  edited {path}")

# The planner's own lines
own = lambda s: (re.sub(rf"(?m)^# {re.escape(old)}$", f"# {new}", s)
                 .replace(f"**name:** `{old}`", f"**name:** `{new}`")
                 .replace(f"planner/{old}/", f"planner/{new}/"))
edit(folder / f"prompts/{new}.md", own)
edit(folder / "config.md", lambda s: s.replace(f"planner/{old}/", f"planner/{new}/"))

# sync-shared-repo: sources move, targets keep the old name
def fix_sync(s):
    out = []
    for line in s.splitlines(keepends=True):
        if line.startswith(f"| `planner/{old}/"):
            cells = line.split("|")
            cells[1] = cells[1].replace(f"planner/{old}/", f"planner/{new}/").replace(f"/{old}.md", f"/{new}.md")
            if f"prompts/{new}.md" in cells[1]:
                cells[3] = cells[3].rstrip() + f"; name rewrite `{new}` → `{old}` "
            line = "|".join(cells)
        elif not line.startswith("|"):
            line = line.replace(f"planner/{old}/", f"planner/{new}/")
        out.append(line)
    s = "".join(out)
    rule = (f"- In `planner/{new}/prompts/{new}.md`, rewrite the local planner name back to the shared one: "
            f"the `# {new}` heading, the `name:` line, `planner/{new}/` paths and any `/{new}` command "
            f"become `{old}`. The shared repo keeps `planner/{old}/` and `/{old}`.\n")
    anchor = "- Leave `<root>`"
    if rule not in s and anchor in s:
        s = s.replace(anchor, rule + anchor, 1)
    return s
edit(sync, fix_sync)
EOF

DEFAULT=$(sed -nE 's/^PLANNER=//p' "$WORK_DIR/automation/settings.example.env" 2>/dev/null)
if [[ -f "$SETTINGS" ]] && grep -q '^PLANNER=' "$SETTINGS"; then
  sed -i '' "s/^PLANNER=.*/PLANNER=$NEW/" "$SETTINGS"
  echo "  set PLANNER=$NEW in $SETTINGS"
elif [[ "$NEW" != "$DEFAULT" ]]; then
  echo "PLANNER=$NEW" >> "$SETTINGS"
  echo "  set PLANNER=$NEW in $SETTINGS"
fi
echo "Done. Make sure your /$NEW shim points at planner/$NEW/prompts/$NEW.md (dotfiles)."
