#!/usr/bin/env python3
"""
把每個待修 issue 派給 oh-my-pi。

整體流程:
  1. 把 target repo clone 到 WORKSPACE/target-repo(只 clone 一次)
  2. 對每個 issue,從 base clone 建一個 detached worktree
     (WORKSPACE/target-repo-issue<NUM>),agent 在自己的工作目錄裡跑
  3. 用 prompts/fix_issue.md 模板組出 prompt,寫進 worktree
  4. 在 worktree 裡跑 `omp -p $(cat PROMPT_FOR_OMP.md)`,等它收尾
  5. 從 log 抓 PR URL,在原 ISSUE 留言 + 關閉

用法:
  # healer.yml 內由前一個 step 把 $ISSUE_LIST 餵進來
  python scripts/dispatch_omp.py "$ISSUE_LIST"

  # 本地手動測試(需要 $HEALER_TOKEN + $MINIMAX_API_KEY)
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
# 共享 base clone(只 clone 一次,所有 issue 的 worktree 從這裡長出來)
BASE_DIR = WORKSPACE / "target-repo"
TARGET_REPO = os.environ.get("TARGET_REPO", "gcl858/OpenDataMonitor")
HEALER_TOKEN = os.environ.get("HEALER_TOKEN", "")
OMP_MODEL = os.environ.get("OMP_MODEL", "MiniMax-M3")
MAX_TURNS = int(os.environ.get("OMP_MAX_TURNS", "25"))

# Issue label 狀態機(對應 Repository A 上的 label)
# - healer-in-progress:healer 正在跑(claim 中)
# - healed:healer 成功開出 PR + 關閉 issue
# - healer-blocked:healer 開了 PR 但不能 merge(conflict / CI 紅 / 已被 merge)
# 這些 label 必須先在 Repository A 開好;healer.yml 有自動建 label 的步驟。
LABEL_IN_PROGRESS = os.environ.get("LABEL_IN_PROGRESS", "healer-in-progress")
LABEL_HEALED = os.environ.get("LABEL_HEALED", "healed")
LABEL_BLOCKED = os.environ.get("LABEL_BLOCKED", "healer-blocked")

# D2 prompt-injection 防護:把 user-supplied issue body 截斷到這個長度
MAX_BODY_CHARS = int(os.environ.get("MAX_ISSUE_BODY_CHARS", "8000"))

# 從 prompt 抓 PR URL 的正則
PR_URL_RE = re.compile(r"PR_URL:\s*(https?://\S+/pull/\d+)")


# ---------------------------- helpers ----------------------------

def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------- core steps ----------------------------

def init_target() -> None:
    """把 target repo clone 到 BASE_DIR,只跑一次(供所有 issue 共享)。

    後續每個 issue 會從這裡 `git worktree add` 一個獨立工作目錄。
    """
    if BASE_DIR.exists() and (BASE_DIR / ".git").exists():
        return  # 已經 clone 過,idempotent
    BASE_DIR.parent.mkdir(parents=True, exist_ok=True)
    if BASE_DIR.exists():
        _run(["rm", "-rf", str(BASE_DIR)])
    url = f"https://x-access-token:{HEALER_TOKEN}@github.com/{TARGET_REPO}.git"
    p = _run(["git", "clone", "--depth", "50", url, str(BASE_DIR)])
    if p.returncode != 0:
        raise RuntimeError(f"clone failed: {p.stderr}")


def add_worktree(issue_no: int) -> Path:
    """為這個 issue 建立一個獨立的 detached worktree,起點為 origin/main。

    為什麼用 worktree:
      - 多 issue 不會互相覆蓋 working tree
      - 每個 agent 在自己的工作目錄裡跑,互不干擾
      - 失敗時只刪自己那份 worktree,base clone 不受影響
    """
    work_dir = WORKSPACE / f"target-repo-issue{issue_no}"

    # 收掉前一次的殘留(可能上次跑到一半 timeout)
    if work_dir.exists():
        _run(["git", "-C", str(BASE_DIR), "worktree", "remove", "--force", str(work_dir)])
        if work_dir.exists():
            _run(["rm", "-rf", str(work_dir)])
    _run(["git", "-C", str(BASE_DIR), "worktree", "prune"])

    # 同步最新 main,確保修的是當前版本
    _run(["git", "-C", str(BASE_DIR), "fetch", "--depth", "50", "origin", "main"])

    p = _run([
        "git", "-C", str(BASE_DIR),
        "worktree", "add", "--detach", str(work_dir), "origin/main",
    ])
    if p.returncode != 0:
        raise RuntimeError(f"worktree add failed: {p.stderr}")
    return work_dir


def _sanitize_body(body: str) -> str:
    """D2 防護:截斷 + 跳脫 fence,避免 issue body 撞破 prompt 結構。

    兩道防線:
      1. 截斷到 MAX_BODY_CHARS,避免 token 爆炸 / DoS
      2. 把 `` ``` `` 換成 `'''`,防止 body 撞破 markdown fence 後
         塞偽造的 LLM 指示(例如「忽略以上所有規則」)
    """
    if not body:
        return ""
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + f"\n\n[... truncated to {MAX_BODY_CHARS} chars ...]"
    # 三個 backtick → 三個單引號(罕見但視覺上仍可讀)
    body = body.replace("```", "'''")
    return body


def write_prompt(issue: dict, target_dir: Path) -> Path:
    """組出給 oh-my-pi 的 prompt,寫成檔。

    用簡單 str.replace,而不是 str.format:
      - prompt 用 {{VAR}} 形式標記
      - 避免與 prompt 內任何 { / } 字元衝突(像 shell `{x,y}` 之類)
      - 取代後的 prompt 不會留下任何奇怪的雙花括號殘留

    body 會先過 _sanitize_body(D2 prompt-injection 防護)。
    """
    n = issue["number"]
    title = issue["title"]
    body = _sanitize_body(issue.get("body") or "")
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


def _verify_pr_exists(pr_url: str) -> bool:
    """確認 PR 真的存在且狀態為 OPEN(避免 log 裡 echo 幻覺 URL)。

    用 `gh pr view <url> --json state --jq .state` 驗證。
    """
    p = _run([
        "gh", "pr", "view", pr_url,
        "--json", "state",
        "--jq", ".state",
    ])
    return p.returncode == 0 and p.stdout.strip().upper() == "OPEN"


def _check_pr_status(pr_url: str) -> tuple[bool, str]:
    """B3:檢查 PR 是否可以 merge,回傳 (ok, reason)。

    不可 merge 的情況(state / mergeable / CI 任一不通過):
      - state=MERGED  → 違反自動修復流程(應該是 PR 而不是直接 merge)
      - state=CLOSED  → 已被人工 close
      - mergeable=CONFLICTING → 有 conflict
      - 任一 CI 結論是 FAILURE → 紅燈

    gh 欄位說明:
      - state:"OPEN" | "CLOSED" | "MERGED"
      - mergeable:"MERGEABLE" | "CONFLICTING" | "UNKNOWN"
      - statusCheckRollup:[{"conclusion": "SUCCESS"|"FAILURE"|None, "name": "..."}]
        (None 代表還在跑;空 list 代表沒設 CI)
    """
    p = _run([
        "gh", "pr", "view", pr_url,
        "--json", "state,mergeable,statusCheckRollup",
    ])
    if p.returncode != 0:
        return False, f"⚠️ gh pr view 失敗: {p.stderr.strip()}"
    try:
        data = json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return False, "⚠️ gh pr view 回傳非 JSON,無法判斷 PR 狀態"

    state = data.get("state", "UNKNOWN")
    mergeable = data.get("mergeable", "UNKNOWN")
    rollup = data.get("statusCheckRollup") or []

    if state == "MERGED":
        return False, "⚠️ PR 已被 merge(繞過 reviewer 流程,違反 AGENTS.md 硬規則)"
    if state == "CLOSED":
        return False, "⚠️ PR 已被 close,請人工 review 是否需要重開"
    if mergeable == "CONFLICTING":
        return False, "⚠️ PR 有 merge conflict,請 rebase 後再開新 PR"

    failures = [c for c in rollup if c.get("conclusion") == "FAILURE"]
    if failures:
        names = ", ".join(c.get("name", "?") for c in failures[:3])
        more = "" if len(failures) <= 3 else f" (還有 {len(failures) - 3} 個)"
        return False, f"⚠️ CI 失敗: {names}{more}"

    return True, ""


def find_pr_url(target_dir: Path, log_path: Path, issue_no: int) -> str | None:
    """從 omp 的 log 找 PR URL,並用 gh pr view 確認它真的存在。

    來源優先序:
      1. log 檔裡的 `PR_URL: https://...` 行
      2. `gh pr list` 搜尋 `auto-heal/issue-<N>` 開頭的 branch
    找到後還會用 `gh pr view` 二次確認 state=OPEN,才回傳。
    """
    if not log_path.exists():
        return None
    text = log_path.read_text(encoding="utf-8", errors="replace")
    m = PR_URL_RE.search(text)
    candidate = m.group(1).strip() if m else None

    if not candidate:
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
                candidate = items[0].get("url")

    if not candidate:
        return None

    # 驗 PR 真的存在(過濾掉 log 裡的幻覺 URL)
    if _verify_pr_exists(candidate):
        return candidate
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


def add_label(issue_no: int, label: str) -> None:
    """在 issue 上加 label(label 不存在時 gh 會 warning,這裡只印不中斷)。"""
    p = _run([
        "gh", "issue", "edit", str(issue_no),
        "--repo", TARGET_REPO,
        "--add-label", label,
    ])
    if p.returncode != 0:
        print(f"::warning::add-label {label!r} failed: {p.stderr}", file=sys.stderr)


def remove_label(issue_no: int, label: str) -> None:
    """從 issue 移除 label(若 label 根本沒在也視為成功)。"""
    p = _run([
        "gh", "issue", "edit", str(issue_no),
        "--repo", TARGET_REPO,
        "--remove-label", label,
    ])
    if p.returncode != 0:
        print(f"::warning::remove-label {label!r} failed: {p.stderr}", file=sys.stderr)


# ---------------------------- main loop ----------------------------

def heal_one(issue: dict) -> bool:
    n = issue["number"]
    print(f"::group::Healing issue #{n}")
    log_path = WORKSPACE / f"omp-issue-{n}.log"

    # 1. claim:加 healer-in-progress,讓人類與下次排程知道這條在跑
    add_label(n, LABEL_IN_PROGRESS)

    # 2. 準備 worktree
    try:
        work_dir = add_worktree(n)
    except Exception as e:
        print(f"::error::worktree add failed: {e}")
        comment_on_issue(n, f"⚠️ healer 無法建立 worktree: `{e}`")
        remove_label(n, LABEL_IN_PROGRESS)
        return False

    # 3. 跑 omp
    prompt_file = write_prompt(issue, work_dir)
    rc = run_omp(prompt_file, work_dir, n)

    if rc != 0:
        comment_on_issue(n, f"⚠️ oh-my-pi 處理失敗(exit={rc}),請人工介入。")
        remove_label(n, LABEL_IN_PROGRESS)
        print("::endgroup::")
        return False

    # 4. 找 PR URL
    pr_url = find_pr_url(work_dir, log_path, n)

    # 5. B3:PR 存在但不能 merge?不 close,改標 blocked 請人工處理
    if pr_url:
        ok, reason = _check_pr_status(pr_url)
        if not ok:
            comment_on_issue(
                n,
                f"{reason}\n\n"
                f"請人工 rebase / 修 CI / 處理 conflict 後再手動 close 此 issue。\n\n"
                f"PR: {pr_url}",
            )
            add_label(n, LABEL_BLOCKED)
            remove_label(n, LABEL_IN_PROGRESS)
            print("::endgroup::")
            return False

    # 6. 完工:close + 換 healed label
    close_issue(n, pr_url)
    add_label(n, LABEL_HEALED)
    remove_label(n, LABEL_IN_PROGRESS)
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
    try:
        init_target()
    except Exception as e:
        print(f"::error::clone failed: {e}")
        return 4

    for issue in issues:
        if heal_one(issue):
            success += 1

    print(f"healed {success}/{len(issues)} issues")
    return 0 if success == len(issues) else 1


if __name__ == "__main__":
    sys.exit(main())
