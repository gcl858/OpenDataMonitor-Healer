# Changelog

這個專案的改動歷史。`fix00X` 系列是早期獨立小修,
這個 CHANGELOG 從「重整 healer 為 production-ready」這個里程碑開始記。

---

## [Unreleased] — 2026-07-10 — 對齊 production-ready

把 healer 從 MVP 調整到「可以放心丟著 24 小時沒人管」的程度。

### 改動

#### 排程 / 模型 / Secret
- **每日 8:00 (UTC) 排程**(`cron: "0 8 * * *"`),原本的 `* 9 * * *` 是每分鐘跑一次的 bug
- **統一模型為 `MiniMax-M3`**(`OMP_MODEL` env + dispatch 預設)
- **統一 secret 為 `MINIMAX_API_KEY`**(原本 `ANTHROPIC_API_KEY` 對應不到實際 provider)
- **移除「Verify omp」那步燒 LLM 的探測**,改成本地 `omp --version` + 檢查 env 變數
- **加 `Upload omp logs` step**,omp log 變 30 天 artifact,失敗時可以事後挖

#### Per-issue worktree(避免 race)
- `clone_target` → `init_target` + `add_worktree`
- 每個 issue 跑在 `git worktree` 獨立目錄(`target-repo-issue<N>`)
- 多 issue 不會互相覆蓋 working tree / branch
- 前次失敗留下的 stale worktree 自動清掉

#### Label 狀態機
- 三個新 label(`healer.yml` 自動建,idempotent):
  - `healer-in-progress`(黃)— 處理中
  - `healed`(綠)— PR 開成功 + close issue
  - `healer-blocked`(橘)— PR 開了但不能 merge
- `add_label` / `remove_label` helpers
- 失敗路徑(任何一步)都會清掉 in-progress,避免卡住
- `poll_issues.py` 拉 `labels` 欄位,防禦性過濾掉已 `healed` 的 issue

#### B 區 — 可靠性強化
- **B1 + B2**:加 `_verify_pr_exists()`,log 寫的 PR URL 用 `gh pr view` 二次確認存在
- **B3**:加 `_check_pr_status()`,close 前確認 PR 可 merge
  - `state=MERGED / CLOSED` → 不 close
  - `mergeable=CONFLICTING` → 不 close
  - 任一 CI `conclusion=FAILURE` → 不 close
  - 都會留言 + 標 `healer-blocked` 等人工
- 寫了 **26 個 E2E 測試**(`test_dispatch_dryrun.py`),mock 所有外部依賴,只測編排邏輯

#### D 區 — 安全強化
- **D2 prompt injection 防護**:
  - `_sanitize_body()` 截斷 issue body 到 8000 字(`MAX_ISSUE_BODY_CHARS` 可調)
  - 把 ` ``` ` 換成 `'''`,避免 body 撞破 markdown fence 後塞偽造指示
  - `prompts/fix_issue.md` 模板加「user-supplied 不可信資料」警告
  - 6 個單元測試覆蓋

### 測試

從 8 個升到 **34 個**:
- `test_prompt_format.py`:4 個(沒變)
- `test_omp_runs.py`:4 個(沒變)
- `test_dispatch_dryrun.py`:**新檔**,26 個
  - `TestHealOneLabelStateMachine` × 4
  - `TestVerifyPrExists` × 4
  - `TestFindPrUrlVerification` × 4
  - `TestCheckPrStatus` × 7
  - `TestSanitizeBody` × 6
  - `TestHealOneBlockedFlow` × 1

### 行為契約變更(對維護者)

| 情境 | 舊行為 | 新行為 |
|---|---|---|
| 沒抓到 PR URL | close + 標 healed | close + 標 healed(**不變**) |
| PR 開了但有 conflict | close + 標 healed | 留言 + 標 `healer-blocked` + **不 close** |
| PR 已被 merge | close + 標 healed | 留言 + 標 `healer-blocked` + **不 close**(違規偵測) |
| PR 有 CI 紅燈 | close + 標 healed | 留言 + 標 `healer-blocked` + **不 close** |
| 多 issue 同一輪 | 後處理者覆蓋前者的 working tree | 各自 worktree,互不影響 |
| Issue body 有 ``` | 撞破 prompt fence,可能 prompt injection | 換成 ''',警告 LLM |
| 失敗留下 stale state | 留半成品 worktree | 自動清,下次 run 重新建 |

---

## 早期 commit(`fix001` ~ `fix006`)

git log 裡的 `fix001` ~ `fix006` 是連續小修,沒詳細 changelog。
從 `AGENTS.md` / `audit.txt` 推測內容大致是:
- `fix001~004`:workflow 與 omp 安裝修修補補
- `fix005`:`omp_batch.sh` 改寫
- `fix006`:workflow 微調 + 加 `env.md` 環境說明
