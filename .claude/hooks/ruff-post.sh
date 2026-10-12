#!/usr/bin/env bash
# PostToolUse hook: after Claude edits a .py file, auto-fix and format it with ruff.
# Anything ruff cannot fix is sent back to Claude on stderr (exit 2).
input=$(cat)
file_path=$(echo "$input" | uv run python -c "import json,sys; print(json.load(sys.stdin)['tool_input']['file_path'])" 2>/dev/null)

if [[ "$file_path" == *.py ]] && [[ -f "$file_path" ]]; then
  uv run ruff check --fix --quiet "$file_path" >/dev/null 2>&1
  uv run ruff format --quiet "$file_path" >/dev/null 2>&1
  if ! report=$(uv run ruff check --output-format concise "$file_path" 2>&1); then
    echo "ruff found problems that need fixing:" >&2
    echo "$report" >&2
    exit 2
  fi
fi
exit 0
