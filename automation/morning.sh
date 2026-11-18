#!/bin/zsh
# morning.sh
# Creates today's personal daily note via the assistant planner.
# One-shot — runs CC on the fast tier (haiku, low effort), creates the note, done.
# Usage: morning.sh

cd "$HOME/Documents/code"
exec claude --model haiku --effort low "/assistant create today's daily note"
