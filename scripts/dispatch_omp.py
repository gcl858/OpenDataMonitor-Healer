#!/usr/bin/env python3
"""
把每個待修 issue 派給 oh-my-pi。

整體流程:
  1. 把 target repo clone 到 WORKSPACE/target-repo
  2. 用 prompts/fix_issue.md 模板組出 prompt,寫進 target-repo/PROMPT_FOR_OMP.md
  3. 在 target-repo 裡跑 `omp -p $(cat PROMPT_FOR_OMP.md)`,等它收尾
  4. 從 log 抓 PR URL,在原 ISSUE 留言 + 關閉

用法:
  # healer.yml 內由前一個 step 把 $ISSUE_LIST 餵進來
  python scripts/dispatch_omp.py "$ISSUE_LIST"

  # 本地手動測試(需要 $HEALER_TOKEN + $ANTHROPIC_API_KEY)
  echo '[]' | python scripts/dispatch_omp.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE", "/tmp/healer-workspace"))
TARGET_REPO = os.environ.get("TARGET_REPO", "gcl858/OpenDataMonitor")
HEALER_TOKEN = os.environ.get("HEALER_TOKEN", "")
OMP_MODEL = os.environ.get("OMP_MODEL", "claude-sonnet-4-5")
MAX_TURNS = int(os.environ.get("OMP_MAX_TURNS", "25"))

# 從 prompt 抓 PR URL 的正則
PR_URL_RE = re.compile(r"PR_URL:\s*(https?://\S+/pull/\d+)")


# ---------------------------- helpers ----------------------------

def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------- core steps ----------------------------

def clone_target(branch_dir: Path) -> None:
    """把 target repo clone 到 branch_dir。"""
    branch_dir.parent.mkdir(parents=True, exist_ok=True)
    if branch_dir.exists():
        _run(["rm", "-rf", str(branch_dir)])
    url = f"https://x-access-token:{HEALER_TOKEN}@github.com/{TARGET_REPO}.git"
    p = _run(["git", "clone", "--depth", "50", url, str(branch_dir)])
    if p.returncode != 0:
        raise RuntimeError(f"clone failed: {p.stderr}")


def write_prompt(issue: dict, target_dir: Path) -> Path:
    """組出給 oh-my-pi 的 prompt,寫成檔。

    用簡單 str.replace,而不是 str.format:
      - prompt 用 {{VAR}} 形式標記
      - 避免與 prompt 內任何 { / } 字元衝突(像 shell `{x,y}` 之類)
      - 取代後的 prompt 不會留下任何奇怪的雙花括號殘留
    """
    n = issue["number"]
    title = issue["title"]
    body = issue["body"] or ""
    url = issue["url"]

    prompt_file = target_dir / "PROMPT_FOR_OMP.md"
    template_path = Path(__file__).parent.parent / "prompts" / "fix_issue.md"
    template = template_path.read_text(encoding="utf-8")

    replacements = {
        "{{target_repo}}": TARGET_REPO,
        "{{issue_number}}": str(n),
        "{{issue_title}}": title,
        "{{issue_url}}": url,
        "{{issue_body}}": body or "(empty)",
        "{{target_dir}}": str(target_dir),
    }
    content = template
    for needle, value in replacements.items():
        content = content.replace(needle, value)

    prompt_file.write_text(content, encoding="utf-8")
    return prompt_file


def run_omp(prompt_file: Path, target_dir: Path, issue_no: int) -> int:
    """叫 oh-my-pi 做事。回傳 exit code。"""
    log_path = WORKSPACE / f"omp-issue-{issue_no}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["OMP_MODEL"] = OMP_MODEL
    env["OMP_MAX_TURNS"] = str(MAX_TURNS)

    # 用 bash -lc 確保 PATH 內能找到 omp (它被裝在 ~/.local/bin)
    p = _run(
        [
            "bash", "-lc",
            f"cd {target_dir} && omp -p \"$(cat {prompt_file})\" "
            f"--max-turns {MAX_TURNS} --model {OMP_MODEL} 2>&1 | tee {log_path}"
        ],
        env=env,
    )
    return p.returncode


def find_pr_url(target_dir: Path, log_path: Path, issue_no: int) -> str | None:
    """從 omp 的 log 找 PR URL(agent 應該在最後一行 echo 出來)。"""
    if not log_path.exists():
        return None
    text = log_path.read_text(encoding="utf-8", errors="replace")
    m = PR_URL_RE.search(text)
    if m:
        return m.group(1).strip()

    # 退而求其次:用 gh pr list 查
    p = _run([
        "gh", "pr", "list",
        "--repo", TARGET_REPO,
        "--state", "open",
        "--search", f"auto-heal/issue-{issue_no} in:head",
        "--json", "url,number",
        "--limit", "1",
    ])
    if p.returncode == 0:
        try:
            items = json.loads(p.stdout or "[]")
        except json.JSONDecodeError:
            return None
        if items:
            return items[0].get("url")
    return None


def comment_on_issue(issue_no: int, message: str) -> None:
    p = _run([
        "gh", "issue", "comment", str(issue_no),
        "--repo", TARGET_REPO,
        "--body", message,
    ])
    if p.returncode != 0:
        print(f"::warning::comment failed: {p.stderr}", file=sys.stderr)


def close_issue(issue_no: int, pr_url: str | None) -> None:
    body = "🤖 已由 oh-my-pi 自動修復。"
    if pr_url:
        body += f"\n\nPR: {pr_url}"
    body += "\n\n如果還有問題,請回覆此 ISSUE,系統會自動重試。"
    p = _run([
        "gh", "issue", "close", str(issue_no),
        "--repo", TARGET_REPO,
        "--comment", body,
    ])
    if p.returncode != 0:
        print(f"::warning::close failed: {p.stderr}", file=sys.stderr)


# ---------------------------- main loop ----------------------------

def heal_one(issue: dict) -> bool:
    n = issue["number"]
    print(f"::group::Healing issue #{n}")
    target_dir = WORKSPACE / "target-repo"
    log_path = WORKSPACE / f"omp-issue-{n}.log"
    try:
        clone_target(target_dir)
    except Exception as e:
        print(f"::error::clone failed: {e}")
        comment_on_issue(n, f"⚠️ healer 連不上 target repo: `{e}`")
        return False

    prompt_file = write_prompt(issue, target_dir)
    rc = run_omp(prompt_file, target_dir, n)

    if rc != 0:
        comment_on_issue(n, f"⚠️ oh-my-pi 處理失敗(exit={rc}),請人工介入。")
        print("::endgroup::")
        return False

    pr_url = find_pr_url(target_dir, log_path, n)
    close_issue(n, pr_url)
    print("::endgroup::")
    return True


def main() -> int:
    raw = sys.argv[1] if len(sys.argv) > 1 else "[]"
    try:
        issues = json.loads(raw)
    except json.JSONDecodeError:
        print(f"::error::bad JSON: {raw}", file=sys.stderr)
        return 2

    if not issues:
        print("no issues to heal")
        return 0

    if not HEALER_TOKEN:
        print("::error::HEALER_TOKEN not set", file=sys.stderr)
        return 3

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    success = 0
    for issue in issues:
        if heal_one(issue):
            success += 1

    print(f"healed {success}/{len(issues)} issues")
    return 0 if success == len(issues) else 1


if __name__ == "__main__":
    sys.exit(main())
