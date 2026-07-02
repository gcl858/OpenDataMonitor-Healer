#!/bin/bash

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

SESSION_NAME="odm-${TIMESTAMP}"
REPORT_FILE="odm-${TIMESTAMP}.html"

# 取得外部傳入的 prompt
if [ -n "$1" ] && [ -f "$1" ]; then
    PROMPT=$(cat "$1")
else
    PROMPT=$(cat)
fi

if [ -z "$PROMPT" ]; then
    echo "No prompt provided"
    exit 1
fi

omp \
  --name "$SESSION_NAME" \
  -p \
  "$PROMPT"

SESSION=$(find ~/.omp -name "*.jsonl" -type f | sort -r | head -1)

if [ -z "$SESSION" ]; then
    echo "No session found"
    exit 1
fi

omp \
  --export "$SESSION" \
  "$REPORT_FILE"

echo "Session: $SESSION_NAME"
echo "Report: $REPORT_FILE"