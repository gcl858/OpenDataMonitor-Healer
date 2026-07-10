"""
E2E(dry-run)測試:用 mock 取代所有外部依賴,只驗證 dispatch_omp 的編排邏輯。

涵蓋:
  - heal_one 的 label 狀態機(healer-in-progress → healed,失敗時清除 in-progress)
  - find_pr_url 的 PR 真實性驗證(log 寫的 URL 也要打 gh pr view 確認)
  - _verify_pr_exists 對 OPEN / CLOSED / gh failure 三種情況的回應

不會真的跑 git / omp / gh。可在 CI 直接執行:
  python -m unittest scripts.tests.test_dispatch_dryrun
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# 讓 import dispatch_omp 找得到
TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import dispatch_omp  # noqa: E402


def make_issue(n: int = 42) -> dict:
    return {
        "number": n,
        "title": f"[auto-heal] TestIssue{n}",
        "body": "boom\ntraceback here",
        "url": f"https://github.com/foo/bar/issues/{n}",
    }


def make_fake_run(stdout: str = "", returncode: int = 0, stderr: str = ""):
    """產生一個假的 _run,把每次呼叫的 cmd 收進 calls 列表。"""
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        r = MagicMock()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = stderr
        return r

    return fake_run, calls


# ============================================================
# heal_one 編排邏輯(label 狀態機)
# ============================================================

class TestHealOneLabelStateMachine(unittest.TestCase):
    """驗證 heal_one 對 label 的操作順序:claim → work → finalize。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="healer-test-"))
        # 暫時把 dispatch_omp.WORKSPACE 改到 tmpdir,避免污染 repo
        self._orig_workspace = dispatch_omp.WORKSPACE
        self._orig_base = dispatch_omp.BASE_DIR
        dispatch_omp.WORKSPACE = self.tmpdir
        dispatch_omp.BASE_DIR = self.tmpdir / "target-repo"
        self.issue = make_issue(42)
        self.calls: list[tuple] = []

    def tearDown(self):
        dispatch_omp.WORKSPACE = self._orig_workspace
        dispatch_omp.BASE_DIR = self._orig_base
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _wire_mocks(
        self,
        worktree_ok: bool = True,
        omp_rc: int = 0,
        pr_url: str | None = "https://github.com/foo/bar/pull/123",
        pr_status: tuple[bool, str] = (True, ""),
    ):
        """把所有外部依賴 mock 掉,回傳 patch context managers 的 list。

        pr_status:(ok, reason) 餵給 mock 的 _check_pr_status。
        預設 (True, "") = PR OK,可走完 close + healed 流程。
        """
        work_dir = self.tmpdir / "target-repo-issue42"

        def add_label(issue_no, label):
            self.calls.append(("add_label", label))

        def remove_label(issue_no, label):
            self.calls.append(("remove_label", label))

        def add_worktree(issue_no):
            self.calls.append(("add_worktree", issue_no))
            if not worktree_ok:
                raise RuntimeError("disk full")
            work_dir.mkdir(parents=True, exist_ok=True)
            return work_dir

        def run_omp(prompt_file, target_dir, issue_no):
            self.calls.append(("run_omp", issue_no))
            return omp_rc

        def find_pr_url(target_dir, log_path, issue_no):
            self.calls.append(("find_pr_url", issue_no))
            return pr_url

        def check_pr_status(url):
            self.calls.append(("check_pr_status", url))
            return pr_status

        def close_issue(issue_no, pr):
            self.calls.append(("close_issue", pr))

        def comment(issue_no, msg):
            self.calls.append(("comment", issue_no))

        return [
            patch.object(dispatch_omp, "add_label", side_effect=add_label),
            patch.object(dispatch_omp, "remove_label", side_effect=remove_label),
            patch.object(dispatch_omp, "add_worktree", side_effect=add_worktree),
            patch.object(dispatch_omp, "run_omp", side_effect=run_omp),
            patch.object(dispatch_omp, "find_pr_url", side_effect=find_pr_url),
            patch.object(dispatch_omp, "_check_pr_status", side_effect=check_pr_status),
            patch.object(dispatch_omp, "close_issue", side_effect=close_issue),
            patch.object(dispatch_omp, "comment_on_issue", side_effect=comment),
        ]

    def test_happy_path_label_sequence(self):
        """成功路徑:claim → work → close → 換 healed → 移除 in-progress。"""
        patches = self._wire_mocks()
        for p in patches:
            p.start()
        try:
            ok = dispatch_omp.heal_one(self.issue)
        finally:
            for p in patches:
                p.stop()

        self.assertTrue(ok)
        expected = [
            ("add_label", "healer-in-progress"),
            ("add_worktree", 42),
            ("run_omp", 42),
            ("find_pr_url", 42),
            ("check_pr_status", "https://github.com/foo/bar/pull/123"),
            ("close_issue", "https://github.com/foo/bar/pull/123"),
            ("add_label", "healed"),
            ("remove_label", "healer-in-progress"),
        ]
        self.assertEqual(self.calls, expected)

    def test_worktree_failure_clears_in_progress_no_healed(self):
        """worktree 建失敗:加 in-progress,失敗,移除 in-progress,沒 healed。"""
        patches = self._wire_mocks(worktree_ok=False)
        for p in patches:
            p.start()
        try:
            ok = dispatch_omp.heal_one(self.issue)
        finally:
            for p in patches:
                p.stop()

        self.assertFalse(ok)
        labels_added = [c[1] for c in self.calls if c[0] == "add_label"]
        labels_removed = [c[1] for c in self.calls if c[0] == "remove_label"]
        self.assertIn("healer-in-progress", labels_added)
        self.assertNotIn("healed", labels_added)
        self.assertIn("healer-in-progress", labels_removed)

    def test_omp_failure_clears_in_progress_no_healed(self):
        """omp exit != 0:加 in-progress,跑 omp,失敗,移除 in-progress。"""
        patches = self._wire_mocks(omp_rc=1)
        for p in patches:
            p.start()
        try:
            ok = dispatch_omp.heal_one(self.issue)
        finally:
            for p in patches:
                p.stop()

        self.assertFalse(ok)
        labels_added = [c[1] for c in self.calls if c[0] == "add_label"]
        self.assertIn("healer-in-progress", labels_added)
        self.assertNotIn("healed", labels_added)
        # 也要有 remove 動作
        labels_removed = [c[1] for c in self.calls if c[0] == "remove_label"]
        self.assertIn("healer-in-progress", labels_removed)

    def test_no_pr_url_still_finalizes_with_healed_label(self):
        """find_pr_url 回 None:還是會 close + 換 healed(close 會顯示「無 PR」)。"""
        patches = self._wire_mocks(pr_url=None)
        for p in patches:
            p.start()
        try:
            ok = dispatch_omp.heal_one(self.issue)
        finally:
            for p in patches:
                p.stop()

        self.assertTrue(ok)
        labels_added = [c[1] for c in self.calls if c[0] == "add_label"]
        # 行為契約:即使沒抓到 PR URL,還是會把 issue 收掉並標 healed
        # (close 訊息會寫「🤖 已修復」但不附 PR 連結,human 之後可手動找)
        self.assertIn("healed", labels_added)
        # close 應該被呼叫,pr 為 None
        close_calls = [c for c in self.calls if c[0] == "close_issue"]
        self.assertEqual(close_calls, [("close_issue", None)])


# ============================================================
# find_pr_url 與 _verify_pr_exists
# ============================================================

class TestVerifyPrExists(unittest.TestCase):
    """_verify_pr_exists 對 OPEN / CLOSED / gh failure 的回應。"""

    def test_open_state_returns_true(self):
        fake_run, calls = make_fake_run(stdout="OPEN", returncode=0)
        with patch.object(dispatch_omp, "_run", side_effect=fake_run):
            ok = dispatch_omp._verify_pr_exists("https://github.com/foo/bar/pull/1")
        self.assertTrue(ok)
        self.assertEqual(calls, [
            ["gh", "pr", "view", "https://github.com/foo/bar/pull/1",
             "--json", "state", "--jq", ".state"],
        ])

    def test_closed_state_returns_false(self):
        fake_run, _ = make_fake_run(stdout="CLOSED", returncode=0)
        with patch.object(dispatch_omp, "_run", side_effect=fake_run):
            ok = dispatch_omp._verify_pr_exists("https://github.com/foo/bar/pull/1")
        self.assertFalse(ok)

    def test_merged_state_returns_false(self):
        fake_run, _ = make_fake_run(stdout="MERGED", returncode=0)
        with patch.object(dispatch_omp, "_run", side_effect=fake_run):
            ok = dispatch_omp._verify_pr_exists("https://github.com/foo/bar/pull/1")
        self.assertFalse(ok)

    def test_gh_failure_returns_false(self):
        fake_run, _ = make_fake_run(stdout="", returncode=1, stderr="not found")
        with patch.object(dispatch_omp, "_run", side_effect=fake_run):
            ok = dispatch_omp._verify_pr_exists("https://github.com/foo/bar/pull/1")
        self.assertFalse(ok)


class TestFindPrUrlVerification(unittest.TestCase):
    """find_pr_url 應該只回傳過 _verify_pr_exists 的 URL。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="healer-test-"))
        self.log = self.tmpdir / "omp-issue-42.log"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_log_url_returned_when_verified(self):
        self.log.write_text("some output\nPR_URL: https://github.com/foo/bar/pull/123\n")
        with patch.object(dispatch_omp, "_verify_pr_exists", return_value=True) as v:
            url = dispatch_omp.find_pr_url(self.tmpdir, self.log, 42)
        self.assertEqual(url, "https://github.com/foo/bar/pull/123")
        v.assert_called_once_with("https://github.com/foo/bar/pull/123")

    def test_log_url_rejected_when_not_verified(self):
        self.log.write_text("PR_URL: https://github.com/foo/bar/pull/999\n")
        with patch.object(dispatch_omp, "_verify_pr_exists", return_value=False) as v:
            url = dispatch_omp.find_pr_url(self.tmpdir, self.log, 42)
        self.assertIsNone(url)
        v.assert_called_once_with("https://github.com/foo/bar/pull/999")

    def test_missing_log_returns_none(self):
        # self.log 沒建立
        with patch.object(dispatch_omp, "_verify_pr_exists") as v:
            url = dispatch_omp.find_pr_url(self.tmpdir, self.log, 42)
        self.assertIsNone(url)
        v.assert_not_called()

    def test_log_without_pr_url_falls_back_to_gh_pr_list(self):
        """log 裡沒 PR_URL → 用 gh pr list 找。"""
        self.log.write_text("no URL here, sorry\n")
        fake_run, _ = make_fake_run(
            stdout='[{"url": "https://github.com/foo/bar/pull/77", "number": 77}]',
            returncode=0,
        )
        with patch.object(dispatch_omp, "_run", side_effect=fake_run), \
             patch.object(dispatch_omp, "_verify_pr_exists", return_value=True) as v:
            url = dispatch_omp.find_pr_url(self.tmpdir, self.log, 42)
        self.assertEqual(url, "https://github.com/foo/bar/pull/77")
        v.assert_called_once_with("https://github.com/foo/bar/pull/77")


# ============================================================
# B3: PR mergeable 檢查 — 失敗時不 close,改標 healer-blocked
# ============================================================

class TestCheckPrStatus(unittest.TestCase):
    """_check_pr_status 對各種 PR 狀態的回應。"""

    def test_open_mergeable_no_ci_is_ok(self):
        data = json.dumps({"state": "OPEN", "mergeable": "MERGEABLE", "statusCheckRollup": []})
        fake_run, calls = make_fake_run(stdout=data, returncode=0)
        with patch.object(dispatch_omp, "_run", side_effect=fake_run):
            ok, reason = dispatch_omp._check_pr_status("https://github.com/foo/bar/pull/1")
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        # 確認 cmd 結構正確
        self.assertEqual(calls[0][:3], ["gh", "pr", "view"])

    def test_merged_state_is_blocked(self):
        data = json.dumps({"state": "MERGED", "mergeable": "MERGEABLE", "statusCheckRollup": []})
        fake_run, _ = make_fake_run(stdout=data, returncode=0)
        with patch.object(dispatch_omp, "_run", side_effect=fake_run):
            ok, reason = dispatch_omp._check_pr_status("https://github.com/foo/bar/pull/1")
        self.assertFalse(ok)
        self.assertIn("merge", reason.lower())

    def test_closed_state_is_blocked(self):
        data = json.dumps({"state": "CLOSED", "mergeable": "MERGEABLE", "statusCheckRollup": []})
        fake_run, _ = make_fake_run(stdout=data, returncode=0)
        with patch.object(dispatch_omp, "_run", side_effect=fake_run):
            ok, reason = dispatch_omp._check_pr_status("https://github.com/foo/bar/pull/1")
        self.assertFalse(ok)
        self.assertIn("close", reason.lower())

    def test_conflict_is_blocked(self):
        data = json.dumps({"state": "OPEN", "mergeable": "CONFLICTING", "statusCheckRollup": []})
        fake_run, _ = make_fake_run(stdout=data, returncode=0)
        with patch.object(dispatch_omp, "_run", side_effect=fake_run):
            ok, reason = dispatch_omp._check_pr_status("https://github.com/foo/bar/pull/1")
        self.assertFalse(ok)
        self.assertIn("conflict", reason.lower())

    def test_ci_failure_is_blocked(self):
        data = json.dumps({
            "state": "OPEN", "mergeable": "MERGEABLE",
            "statusCheckRollup": [
                {"conclusion": "SUCCESS", "name": "lint"},
                {"conclusion": "FAILURE", "name": "tests"},
            ],
        })
        fake_run, _ = make_fake_run(stdout=data, returncode=0)
        with patch.object(dispatch_omp, "_run", side_effect=fake_run):
            ok, reason = dispatch_omp._check_pr_status("https://github.com/foo/bar/pull/1")
        self.assertFalse(ok)
        self.assertIn("tests", reason)
        self.assertIn("CI", reason)

    def test_ci_pending_is_ok(self):
        """CI 還在跑(conclusion=None)不算失敗,等它跑完再說。"""
        data = json.dumps({
            "state": "OPEN", "mergeable": "MERGEABLE",
            "statusCheckRollup": [{"conclusion": None, "name": "tests", "status": "IN_PROGRESS"}],
        })
        fake_run, _ = make_fake_run(stdout=data, returncode=0)
        with patch.object(dispatch_omp, "_run", side_effect=fake_run):
            ok, _ = dispatch_omp._check_pr_status("https://github.com/foo/bar/pull/1")
        self.assertTrue(ok)

    def test_gh_failure_is_blocked(self):
        fake_run, _ = make_fake_run(stdout="", returncode=1, stderr="not found")
        with patch.object(dispatch_omp, "_run", side_effect=fake_run):
            ok, reason = dispatch_omp._check_pr_status("https://github.com/foo/bar/pull/1")
        self.assertFalse(ok)
        self.assertIn("gh pr view", reason)


# ============================================================
# D2: prompt-injection 防護 — body 截斷 + fence 跳脫
# ============================================================

class TestSanitizeBody(unittest.TestCase):
    """_sanitize_body 對長 body / backtick / 空字串的處理。"""

    def test_empty_body_returns_empty(self):
        self.assertEqual(dispatch_omp._sanitize_body(""), "")

    def test_short_body_unchanged(self):
        self.assertEqual(dispatch_omp._sanitize_body("hello world"), "hello world")

    def test_long_body_truncated_with_marker(self):
        long = "a" * 10000
        out = dispatch_omp._sanitize_body(long)
        self.assertIn("[... truncated", out)
        # 截斷後的長度應該比原 body 短很多
        self.assertLess(len(out), 8500)

    def test_backticks_replaced_with_single_quotes(self):
        out = dispatch_omp._sanitize_body("```python\nprint('bad')\n```")
        # 不該再有任何 triple backtick
        self.assertNotIn("```", out)
        self.assertIn("'''", out)

    def test_truncation_happens_before_fence_replacement(self):
        """先截斷再跳脫,避免 truncate marker 自己被換掉。"""
        long_with_fence = "```\n" + ("x" * 9000) + "\n```"
        out = dispatch_omp._sanitize_body(long_with_fence)
        # 截斷 marker 應該還在(沒被 backtick 邏輯動到)
        self.assertIn("[... truncated", out)
        # 開頭的 ``` 應該已被換成 '''
        self.assertTrue(out.startswith("'''"))

    def test_max_chars_env_override_respected(self):
        with patch.object(dispatch_omp, "MAX_BODY_CHARS", 100):
            out = dispatch_omp._sanitize_body("a" * 500)
            self.assertIn("[... truncated", out)
            self.assertLess(len(out), 200)


# ============================================================
# heal_one blocked flow 整合測試
# ============================================================

class TestHealOneBlockedFlow(unittest.TestCase):
    """PR 不能 merge 時:不 close、加 healer-blocked、留言請人處理。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="healer-test-"))
        self._orig_workspace = dispatch_omp.WORKSPACE
        self._orig_base = dispatch_omp.BASE_DIR
        dispatch_omp.WORKSPACE = self.tmpdir
        dispatch_omp.BASE_DIR = self.tmpdir / "target-repo"
        self.issue = make_issue(42)
        self.calls: list[tuple] = []

    def tearDown(self):
        dispatch_omp.WORKSPACE = self._orig_workspace
        dispatch_omp.BASE_DIR = self._orig_base
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _patch_all(self, pr_status: tuple[bool, str]):
        work_dir = self.tmpdir / "target-repo-issue42"
        work_dir.mkdir(parents=True, exist_ok=True)

        def add_label(issue_no, label):
            self.calls.append(("add_label", label))

        def remove_label(issue_no, label):
            self.calls.append(("remove_label", label))

        def add_worktree(issue_no):
            self.calls.append(("add_worktree", issue_no))
            return work_dir

        def run_omp(prompt_file, target_dir, issue_no):
            self.calls.append(("run_omp", issue_no))
            return 0  # 模擬 omp 成功

        def find_pr_url(target_dir, log_path, issue_no):
            self.calls.append(("find_pr_url", issue_no))
            return "https://github.com/foo/bar/pull/123"

        def comment(issue_no, msg):
            self.calls.append(("comment", issue_no, msg))

        def close_issue(issue_no, pr):
            self.calls.append(("close_issue", pr))

        def check_pr_status(url):
            self.calls.append(("check_pr_status", url))
            return pr_status

        return [
            patch.object(dispatch_omp, "add_label", side_effect=add_label),
            patch.object(dispatch_omp, "remove_label", side_effect=remove_label),
            patch.object(dispatch_omp, "add_worktree", side_effect=add_worktree),
            patch.object(dispatch_omp, "run_omp", side_effect=run_omp),
            patch.object(dispatch_omp, "find_pr_url", side_effect=find_pr_url),
            patch.object(dispatch_omp, "close_issue", side_effect=close_issue),
            patch.object(dispatch_omp, "comment_on_issue", side_effect=comment),
            patch.object(dispatch_omp, "_check_pr_status", side_effect=check_pr_status),
        ]

    def test_pr_blocked_does_not_close_adds_blocked_label(self):
        patches = self._patch_all(pr_status=(False, "⚠️ PR 有 merge conflict"))
        for p in patches:
            p.start()
        try:
            ok = dispatch_omp.heal_one(self.issue)
        finally:
            for p in patches:
                p.stop()

        self.assertFalse(ok)
        labels_added = [c[1] for c in self.calls if c[0] == "add_label"]
        self.assertIn("healer-blocked", labels_added)
        self.assertNotIn("healed", labels_added, "blocked 不該加 healed")
        # close 不該被呼叫
        close_calls = [c for c in self.calls if c[0] == "close_issue"]
        self.assertEqual(close_calls, [], "blocked 不該 close issue")
        # 留言應該包含 conflict 訊息
        comments = [c for c in self.calls if c[0] == "comment"]
        self.assertEqual(len(comments), 1)
        self.assertIn("merge conflict", comments[0][2])
        # in-progress 應該被清掉
        labels_removed = [c[1] for c in self.calls if c[0] == "remove_label"]
        self.assertIn("healer-in-progress", labels_removed)


if __name__ == "__main__":
    unittest.main()
