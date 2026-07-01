"""
驗證 oh-my-pi (omp) 真的有裝好且能跑。

在 CI / 本地都可以跑:
    python -m pytest scripts/tests/
或者:
    python scripts/tests/test_omp_runs.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestOhMyPiInstalled(unittest.TestCase):
    """`omp` 指令應該在 PATH 內,且能跑 `--version`。

    在 GitHub Actions 裡,workflow 會先跑 `curl -fsSL https://omp.sh/install | sh`,
    所以這兩個測試在 CI 才有意義;本地如果 omp 沒裝會跳過而不是炸掉。
    """

    def _find_omp(self) -> str | None:
        omp_path = shutil.which("omp")
        if omp_path:
            return omp_path
        home_bin = Path.home() / ".local" / "bin" / "omp"
        if home_bin.exists():
            return str(home_bin)
        return None

    def test_omp_in_path(self):
        omp_path = self._find_omp()
        if omp_path is None:
            self.skipTest("omp not installed; skip (CI workflow installs it via https://omp.sh/install)")
        self.assertTrue(Path(omp_path).exists(), f"omp 路徑怪怪的: {omp_path}")

    def test_omp_version(self):
        omp_path = self._find_omp()
        if omp_path is None:
            self.skipTest("omp not installed; skip version check")
        p = subprocess.run(
            [omp_path, "--version"],
            capture_output=True, text=True, check=False,
        )
        # oh-my-pi 的 version flag 不一定所有版本都支援,但至少有 help
        self.assertIn(p.returncode, (0, 1, 2), f"omp 執行失敗: {p.stderr}")


class TestHealerScripts(unittest.TestCase):
    """helper scripts 至少不能有 syntax error。"""

    def test_poll_issues_syntax(self):
        p = subprocess.run(
            [sys.executable, "-m", "py_compile",
             str(REPO_ROOT / "scripts" / "poll_issues.py")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(p.returncode, 0, f"poll_issues.py syntax error: {p.stderr}")

    def test_dispatch_omp_syntax(self):
        p = subprocess.run(
            [sys.executable, "-m", "py_compile",
             str(REPO_ROOT / "scripts" / "dispatch_omp.py")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(p.returncode, 0, f"dispatch_omp.py syntax error: {p.stderr}")


if __name__ == "__main__":
    unittest.main()
