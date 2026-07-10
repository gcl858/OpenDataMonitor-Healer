#!/usr/bin/env python3
"""
輪詢 Repository A 的 open issues,過濾出 label=auto-heal 的。

回傳 JSON array 字串,例如:
  [{"number": 42, "title": "...", "body": "...", "url": "..."}]

用法:
  # 在 GitHub Actions 內,由 healer.yml 呼叫,輸出會被寫入 $GITHUB_OUTPUT
  python scripts/poll_issues.py

  # 或本地手動測試(需要 $HEALER_TOKEN 或 $GITHUB_TOKEN)
  export HEALER_TOKEN=ghp_...
  python scripts/poll_issues.py | jq .
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


def list_auto_heal_issues() -> list[dict]:
    """呼叫 `gh issue list` 拉 open + label=auto-heal 的 issues。"""
    target = os.environ.get("TARGET_REPO", "gcl858/OpenDataMonitor")
    label = os.environ.get("AUTO_HEAL_LABEL", "auto-heal")
    healed_label = os.environ.get("LABEL_HEALED", "healed")
    token = os.environ.get("HEALER_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("::error::HEALER_TOKEN / GITHUB_TOKEN not set", file=sys.stderr)
        return []

    cmd = [
        "gh", "issue", "list",
        "--repo", target,
        "--state", "open",
        "--label", label,
        "--json", "number,title,body,url,createdAt,labels",
        "--limit", "10",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if p.returncode != 0:
        print(f"::error::gh issue list failed: {p.stderr}", file=sys.stderr)
        return []
    try:
        items = json.loads(p.stdout or "[]")
    except json.JSONDecodeError:
        return []

    # 防禦性過濾:已 healed 的不該再被處理
    # (理論上 close 後 state=closed 就不會被拉到了,這層只是多一道保險)
    items = [
        i for i in items
        if healed_label not in [l.get("name") for l in i.get("labels", [])]
    ]
    return items


def main() -> int:
    issues = list_auto_heal_issues()
    # 印到 stdout,GitHub Actions 會接到
    print(json.dumps(issues, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
