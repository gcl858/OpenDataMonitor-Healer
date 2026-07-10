# OpenDataMonitor-Healer (Repository B)

> AI 自動修復 Repository A (`gcl858/OpenDataMonitor`) 抓取失敗的 self-healing 系統。

## 這個倉庫在幹嘛

當 Repository A 的 `monitor.yml` 抓取政府開放資料失敗時,它會建立一個帶有
`auto-heal` label 的 ISSUE。

**這個倉庫 (Repository B)** 排程輪詢那些 issue,然後:

1. Clone Repository A 到 workspace(只 clone 一次,每個 issue 各自用 worktree)
2. 把 issue 內容塞進 prompt(做過 prompt-injection 防護),丟給 [oh-my-pi](https://omp.sh) AI Coding Agent
3. oh-my-pi 自己讀程式碼、抓當前 HTML、修 selector、開 PR
4. healer 抓 PR URL、用 `gh pr view` 雙重驗證、檢查 mergeable,留言 + 關閉 issue

處理單一 issue 通常在 **30 分鐘內** 完成(`timeout-minutes: 25` 為上限);
加上每日 8:00 (UTC) 排程間隔,從 issue 建立到 close **最長約 24 小時 30 分鐘**。

## 系統結構

```
┌────────────────────────────┐         ┌────────────────────────────┐
│ Repository A               │         │ Repository B (本倉庫)      │
│ gcl858/OpenDataMonitor     │         │ OpenDataMonitor-Healer     │
│                            │         │                            │
│ monitor.yml                │  issue  │ healer.yml                 │
│   ↓ 失敗                   │ ──────▶ │   ↓ 每日 8:00 輪詢        │
│ auto_issue.py 開 ISSUE     │         │ poll_issues.py             │
│                            │         │   ↓ 有 issue               │
│                            │         │ dispatch_omp.py            │
│                            │         │   ↓                        │
│                            │  PR     │ oh-my-pi ──────push───────▶│
│                            │ ◀────── │  (改 code + 測試 + 開 PR) │
└────────────────────────────┘         └────────────────────────────┘
```

## 運作流程

每天 8:00 (UTC) 觸發後,healer 跑完一輪大概像這樣:

```
poll_issues.py           拿 Repository A 上 label=auto-heal 的 open issues
  ↓
dispatch_omp.py          對每個 issue 跑 6 步:
  ├─ 1. add_label(healer-in-progress)         ← claim,避免重複處理
  ├─ 2. git worktree add                       ← 每個 issue 獨立工作目錄
  ├─ 3. write_prompt(issue, worktree)          ← body 過 D2 防護(截斷 + fence 跳脫)
  ├─ 4. omp -p ...                             ← agent 讀 code、改、跑測試、開 PR
  ├─ 5. find_pr_url                            ← log 抓 URL + gh pr view 驗存在
  ├─ 6. _check_pr_status                       ← 檢查能 merge 嗎
  │     ├─ OK     → close_issue + add_label(healed)
  │     └─ blocked → 留言 + add_label(healer-blocked) + 不 close
  └─ 7. remove_label(healer-in-progress)       ← 無論成功或失敗都清掉
  ↓
Upload omp-issue-*.log(artifact,保留 30 天)
  ↓
失敗時通知 Telegram(可選)
```

### Issue 標籤狀態機

| Label | 顏色 | 意義 |
|---|---|---|
| `healer-in-progress` | 黃 `FBCA04` | healer 正在處理這條(claim 中) |
| `healed` | 綠 `0E8A16` | healer 開出 PR + 已 close issue |
| `healer-blocked` | 橘 `D93F0B` | healer 開了 PR 但**不能 merge**(conflict / CI 紅 / 已被 merge),等人工處理 |

> workflow 第一次跑會自動在 Repository A 建立這三個 label(已存在會跳過),
> 不需要手動建。

## 檔案地圖

```
OpenDataMonitor-Healer/
├── .github/
│   └── workflows/
│       └── healer.yml          # 主 workflow:每日 8:00 (UTC) 跑一次
├── AGENTS.md                   # oh-my-pi 啟動時自動讀的專案說明
├── prompts/
│   ├── fix_issue.md            # 給 oh-my-pi 的修復 prompt 模板
│   └── system.md               # 補充系統指示
├── scripts/
│   ├── poll_issues.py          # 拉 Repository A 的 auto-heal issues
│   ├── dispatch_omp.py         # 把 issue 派給 oh-my-pi、收回結果
│   └── tests/
│       ├── test_omp_runs.py         # 確認 omp 裝好、scripts 沒 syntax error
│       ├── test_prompt_format.py    # 確認 prompt 模板可正確 render
│       └── test_dispatch_dryrun.py  # E2E: heal_one 編排 + find_pr_url + PR status + body sanitize
├── .gitignore
├── CHANGELOG.md                # 各階段改動記錄
├── README.md                   # 你正在看的這個檔
└── requirements.txt            # (空)Python helper 沒額外依賴
```

## 快速開始

### 1. 建立 Secrets(在 GitHub 倉庫 settings)

| Secret | 用途 |
| --- | --- |
| `MINIMAX_API_KEY` | oh-my-pi 用的 LLM(預設模型 `MiniMax-M3`) |
| `HEALER_TOKEN` | Fine-grained PAT,只給 Repository A 的 `Contents` + `Issues` + `PRs` 權限 |
| `EMAIL_TO` | (可選)完成時寄信給你,目前預留尚未實作 |
| `TELEGRAM_BOT_TOKEN` | (可選)失敗時推 telegram |
| `TELEGRAM_CHAT_ID` | (可選)同上 |

### 2. 修改 `healer.yml`

把 `repository: <你的帳號>/OpenDataMonitor-Healer` 改成你自己的 GitHub 帳號。

### 3. 第一次執行

到 **Actions** tab → 選 `healer` workflow → **Run workflow**(用 `workflow_dispatch`)。

第一次會比較慢(下載 omp + clone target repo),約 2-3 分鐘。

### 4. 觀察結果

```bash
# 看 Repository B 自己的 healer log
gh run list --workflow healer.yml --limit 5

# 下載 healer 的 log artifact(失敗時除錯用,30 天保留)
gh run download --repo <你的帳號>/OpenDataMonitor-Healer --name omp-logs-<run-id>

# 看 Repository A 有沒有被開 PR(那代表 oh-my-pi 動手了)
gh pr list --repo gcl858/OpenDataMonitor --state all

# 看 auto-heal issues 狀態
gh issue list --repo gcl858/OpenDataMonitor --label auto-heal --state all

# 看各 label 分布
gh issue list --repo gcl858/OpenDataMonitor --label healed            --state all
gh issue list --repo gcl858/OpenDataMonitor --label healer-blocked    --state all
gh issue list --repo gcl858/OpenDataMonitor --label healer-in-progress --state all
```

## 觸發鏈路(兩種方案)

### 方案 A — 輪詢(預設,推薦)

`healer.yml` 設 `cron: "0 8 * * *"`,每天 8:00 (UTC) 跑一次,有 issue 就處理,沒事 0 秒結束。

優點:簡單、Repository A 不需要知道 B 的存在。
缺點:最多延遲 24 小時。

### 方案 B — repository_dispatch(即時)

把 `healer.yml` 裡的 `schedule:` 註解掉,打開 `repository_dispatch:`。

然後讓 Repository A 在失敗時多打一步:

```bash
curl -X POST \
  -H "Authorization: token ${HEALER_TOKEN}" \
  https://api.github.com/repos/<你>/OpenDataMonitor-Healer/dispatches \
  -d '{"event_type":"auto-heal","client_payload":{"issue_number":42}}'
```

優點:即時。缺點:Repository A 需要知道 HEALER_TOKEN。
**務必用 fine-grained PAT + 短 expiry + 只給 Repository B 權限。**

## 安全與成本控制

### 分支保護(在 Repository A 設)

- ✅ Require PR before merging
- ✅ Require 1 approval(你自己)
- ❌ **不要**允許 bot 跳過 review

### 硬性閘門(已寫進 `dispatch_omp.py` / `AGENTS.md`)

oh-my-pi **不能**:

- ❌ 改 `.github/workflows/`
- ❌ 改 `requirements.txt` 引入可疑套件
- ❌ commit `.env` / token
- ❌ force push / merge 自己的 PR
- ❌ 刪 `data/` 下的歷史 CSV

### 額外保護(healer 自己做的)

- **Per-issue worktree**: 每個 issue 跑在 `git worktree` 獨立目錄,多 issue 不會互相覆蓋
- **PR 雙重驗證**: log 寫的 PR URL 會用 `gh pr view` 二次確認 `state=OPEN`,過濾掉幻覺 URL
- **Mergeable 檢查**: PR 開了但有 conflict / CI 紅 / 已被 merge,**不會 close issue**,改標 `healer-blocked` 等人工
- **Prompt injection 防護**: issue body 截斷到 8000 字 + 把 ` ``` ` 換成 `'''` + 模板加「user-supplied 不可信資料」警告,避免惡意 issue 拐走 LLM
- **Label 鎖定**: 開始時加 `healer-in-progress`、結束時清掉,人類與下次排程都能看見狀態

### 預算控制

- `timeout-minutes: 25` 在 workflow 層,超過自動 kill
- `OMP_MAX_TURNS=25` 限制 agent 來回次數
- 用便宜模型(MiniMax-M3 / gpt-mini)跑簡單任務,留 sonnet 給大改

## 故障排查

### Q1. `omp: command not found`

```yaml
- name: Debug install
  if: failure()
  run: |
    ls -la ~/.local/bin/ || true
    which omp || echo "no omp"
    cat $HOME/.local/share/omp/install.log 2>/dev/null || true
```

可能:網路拉不下 `https://omp.sh/install` → 改用 self-hosted runner。

### Q2. omp 跑完但 `git diff` 是空的

通常是 prompt 不夠具體。修法:
- 在 Repository A 的 ISSUE body 寫詳細(HTML 截圖、舊/新 selector)
- 在 `AGENTS.md` 把常見失敗模式寫得更細
- 改 `healer.yml` 的 job-level `env.OMP_MODEL` 換更強的模型(目前預設 `MiniMax-M3`)

### Q3. PR 開了但 reviewer 沒看到

可以加 Telegram / Slack 通知(workflow 裡已有 placeholder)。

### Q4. issue 被標 `healer-blocked` 怎麼辦

代表 healer 開了 PR 但 mergeable 檢查擋下來(有 conflict / CI 紅 / 已被 merge / 已被 close)。
到 issue 留言找 PR URL(`gh pr list --search "auto-heal/issue-N" in:head` 也可),
手動 rebase / 修 CI / 重開 PR,完成後**手動 close issue** 並把 `healer-blocked` 換成 `healed`。

如果 healer 沒抓到 PR URL,可能是:
- agent 推 branch 失敗(看 artifact `omp-logs-<run-id>`)
- agent 沒照 PR body 模板印 `PR_URL: https://...`(看 `prompts/fix_issue.md`)

## 開發 / 本地測試

```bash
# 確認 omp 裝好
omp --version

# 跑全部 34 個測試(快速驗證改動沒破)
python -m unittest scripts.tests.test_dispatch_dryrun \
                  scripts.tests.test_omp_runs \
                  scripts.tests.test_prompt_format

# 個別跑
python -m unittest scripts.tests.test_prompt_format     # 4 個 — prompt 模板
python -m unittest scripts.tests.test_omp_runs         # 4 個 — omp 裝沒裝、scripts 語法
python -m unittest scripts.tests.test_dispatch_dryrun  # 26 個 — E2E:heal_one / find_pr_url / _check_pr_status / _sanitize_body

# 確認 scripts syntax 沒錯(更快的快速檢查)
python -m py_compile scripts/poll_issues.py
python -m py_compile scripts/dispatch_omp.py

# 拉一次 issue 看格式(需要 $HEALER_TOKEN)
export HEALER_TOKEN=ghp_xxx
python scripts/poll_issues.py | jq .
```

## 授權

MIT(對齊 Repository A 的授權)。

## 相關連結

- Repository A: <https://github.com/gcl858/OpenDataMonitor>
- oh-my-pi: <https://omp.sh>
- 完整建置說明(本倉庫的設計文件):見 conversation log / wiki
