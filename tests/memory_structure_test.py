import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "resources/report"))
import memory_stats as m

EMPTY = "# 写作记忆\n\nrevision: 1\nenabled: true\nlastReflectionAt: null\n\n## Active L2\n"
RULE = "\n### MR-001 摘要结论前置\n\n- scope: core\n- sourceL1Ids: [L1-001]\n\n摘要先呈现结论。\n"


class StructureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.file = self.root / "MEMORY.md"

    def check(self, text):
        self.file.write_text(text, encoding="utf-8")
        before = self.file.read_bytes()
        result = m.validate(self.file)
        self.assertEqual(before, self.file.read_bytes())
        return result

    def test_template_empty_and_example_validate(self):
        template = (ROOT / "skills/report-agent/references/memory-template.md").read_text(encoding="utf-8")
        empty, sample = re.findall(r"```markdown\n(.*?)\n```", template, re.S)
        self.assertEqual(self.check(empty + "\n")["marker"], "MEMORY_FORMAT_OK")
        self.assertEqual(self.check(empty + "\n" + sample + "\n")["count"], 1)

    def test_screenshot_style_logs_and_navigation_need_review(self):
        legacy = EMPTY.replace("## Active L2", "## 设置与适用边界\n2026-09-29：用户这次要求扩大篇幅，未晋升。\n## 目录导航\nL0 共 12 条。\n## Active L2") + RULE
        result = self.check(legacy)
        self.assertEqual(result["marker"], "MEMORY_FORMAT_NEEDS_REVIEW")
        self.assertEqual(m.check(self.root, self.file)["marker"], "MEMORY_FORMAT_NEEDS_REVIEW")
        self.assertFalse((self.root / "memory-stats.json").exists())

    def test_legacy_title_needs_review_not_empty_success(self):
        for heading in ("Active L2B", "Active L2B Memory Rubrics", "Active L2B Index"):
            self.assertEqual(self.check(EMPTY.replace("Active L2", heading) + RULE)["marker"], "MEMORY_FORMAT_NEEDS_REVIEW")

    def test_existing_zero_and_high_revision_are_preserved(self):
        for revision in (0, 1, 19):
            self.assertEqual(self.check(EMPTY.replace("revision: 1", f"revision: {revision}"))["marker"], "MEMORY_FORMAT_OK")

    def test_settings_missing_duplicate_or_malformed(self):
        for change in (EMPTY.replace("enabled: true", "enabled: maybe"),
                       EMPTY.replace("revision: 1", "revision: -1"),
                       EMPTY.replace("revision: 1", "revision: 1\nrevision: 2"),
                       EMPTY.replace("lastReflectionAt: null", "lastReflectionAt: 昨天检查完成"),
                       EMPTY.replace("lastReflectionAt: null\n", "")):
            self.assertEqual(self.check(change)["marker"], "MEMORY_FORMAT_NEEDS_REVIEW")

    def test_scope_and_sources_required(self):
        for rule in (RULE.replace("core", "audience"), RULE.replace("core", "project"),
                     RULE.replace("[L1-001]", "[]"), RULE.replace("摘要先呈现结论。", ""),
                     RULE.replace("- scope: core", "- scope: core\n- scopeValue: 大家")):
            self.assertEqual(self.check(EMPTY + rule)["marker"], "MEMORY_FORMAT_NEEDS_REVIEW")

    def test_specific_audience_rule_is_structurally_valid(self):
        rule = RULE.replace("- scope: core", "- scope: audience\n- scopeValue: 董事会")
        rule += '- dimensionCandidate: {"name":"focus","label":"重点","reason":"独立判断"}\n'
        self.assertEqual(self.check(EMPTY + rule)["marker"], "MEMORY_FORMAT_OK")

    def test_duplicate_ids_extra_sections_and_count_preamble(self):
        for body in (RULE + RULE, RULE + "\n## Reflection 日志\n今日无变化。", "当前共 1 条\n" + RULE):
            self.assertEqual(self.check(EMPTY + body)["marker"], "MEMORY_FORMAT_NEEDS_REVIEW")

    def test_repeated_validation_has_no_writes(self):
        self.file.write_text(EMPTY + RULE, encoding="utf-8")
        first = m.validate(self.file)
        self.assertEqual(first, m.validate(self.file))
        self.assertEqual(list(self.root.iterdir()), [self.file])

    def test_valid_format_is_not_semantic_approval(self):
        result = self.check(EMPTY + RULE.replace("摘要先呈现结论。", "本次用户选择 R2。"))
        self.assertEqual(result["marker"], "MEMORY_FORMAT_OK")
        self.assertIn("Curator must verify", result["note"])

    def test_cli_review_is_exit_two_and_sync_refuses_bad_structure(self):
        self.file.write_text(EMPTY + "任务日志\n", encoding="utf-8")
        result = subprocess.run([sys.executable, str(ROOT / "resources/report/memory_stats.py"),
                                 "--root", str(self.root), "validate"], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["marker"], "MEMORY_FORMAT_NEEDS_REVIEW")
        self.assertEqual(m.check(self.root, self.file)["marker"], "MEMORY_FORMAT_NEEDS_REVIEW")
        self.assertFalse((self.root / "memory-stats.json").exists())


if __name__ == "__main__":
    unittest.main()
