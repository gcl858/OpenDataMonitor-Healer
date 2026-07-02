#!/bin/bash
set -euo pipefail

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
SESSION_NAME="odm-${TIMESTAMP}"
SESSION_DIR="$HOME/.omp/agent/sessions/custom"
SESSION_FILE="${SESSION_DIR}/${SESSION_NAME}.jsonl"
REPORT_FILE="odm-${TIMESTAMP}.html"

mkdir -p "$SESSION_DIR"

# 取得外部傳入的 prompt
if [ -n "${1:-}" ] && [ -f "$1" ]; then
    PROMPT=$(cat "$1")
else
    PROMPT=$(cat)
fi

if [ -z "$PROMPT" ]; then
    echo "No prompt provided" >&2
    exit 1
fi

omp \
  --session "$SESSION_FILE" \
  -p \
  "$PROMPT"

if [ ! -f "$SESSION_FILE" ]; then
    echo "No session file found: $SESSION_FILE" >&2
    exit 1
fi

omp \
  --export "$SESSION_FILE" \
  "$REPORT_FILE"

echo "Session: $SESSION_NAME"
echo "Session file: $SESSION_FILE"
echo "Report: $REPORT_FILE"