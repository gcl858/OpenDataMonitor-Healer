"""
驗證 prompts/fix_issue.md 模板沒被改壞,且所有 {{placeholder}} 都能正確被取代。

取代規則(與 scripts/dispatch_omp.py 對齊):
    {{target_repo}}    → target repo 完整名稱
    {{issue_number}}   → issue 編號
    {{issue_title}}    → issue 標題
    {{issue_url}}      → issue URL
    {{issue_body}}     → issue 內文
    {{target_dir}}     → 本地 clone 路徑
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROMPT_FILE = Path(__file__).resolve().parent.parent.parent / "prompts" / "fix_issue.md"


REQUIRED_PLACEHOLDERS = {
    "{{target_repo}}",
    "{{issue_number}}",
    "{{issue_title}}",
    "{{issue_url}}",
    "{{issue_body}}",
    "{{target_dir}}",
}


def _render(text: str, **values) -> str:
    """模擬 dispatch_omp.py 的取代邏輯。"""
    out = text
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", v)
    return out


class TestPromptTemplate(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PROMPT_FILE.exists(), f"找不到 prompt 檔: {PROMPT_FILE}")
        self.text = PROMPT_FILE.read_text(encoding="utf-8")

    def test_has_all_placeholders(self):
        for ph in REQUIRED_PLACEHOLDERS:
            self.assertIn(ph, self.text, f"prompt 缺少必要的 placeholder: {ph}")

    def test_only_known_placeholders(self):
        """任何剩下的 {{...}} 都應該是已知的,不該冒出新的。"""
        found = set(re.findall(r"\{\{([a-zA-Z_]+)\}\}", self.text))
        unknown = found - {ph.strip("{}") for ph in REQUIRED_PLACEHOLDERS}
        self.assertEqual(unknown, set(), f"prompt 有未知的 placeholder: {unknown}")

    def test_renders_with_sample(self):
        out = _render(
            self.text,
            target_repo="gcl858/OpenDataMonitor",
            issue_number="42",
            issue_title="[auto-heal] TestIssue",
            issue_url="https://github.com/gcl858/OpenDataMonitor/issues/42",
            issue_body="boom",
            target_dir="/tmp/healer-workspace/target-repo",
        )
        # 沒留下未替代的 placeholder
        self.assertNotIn("{{", out)
        # 重點資訊都得在
        for needle in (
            "OpenDataMonitor",
            "#42",
            "/tmp/healer-workspace/target-repo",
            "auto-heal/issue-42",
        ):
            self.assertIn(needle, out, f"render 結果缺少: {needle}")

    def test_no_literal_double_braces_in_rendered_output(self):
        """render 後不該出現 `{{` `}}` 殘留。"""
        out = _render(
            self.text,
            target_repo="x", issue_number="1", issue_title="t",
            issue_url="u", issue_body="b", target_dir="d",
        )
        self.assertNotIn("{{", out)
        self.assertNotIn("}}", out)


if __name__ == "__main__":
    unittest.main()
