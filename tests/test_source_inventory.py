"""素材指纹扫描与数据版本登记的测试。

覆盖你描述的主场景：用户新增原始素材后，脚本自动认出 added/modified/
deleted，据此只清洗新增数据，登记数据变更，再由主 Agent 询问是否重写报告。
"""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "resources/evidence/scripts/source_inventory.py"
)

EVIDENCE_TEMPLATE = {
    "schema": "openharness-structured-data/v1",
    "case_id": "test-project",
    "items": [
        {
            "id": "EV-001",
            "type": "quantitative",
            "source_ref": "usage.csv / 第2行",
            "content": "2026年6月月均使用时长为12小时；材料未说明抽样方法。",
        }
    ],
    "unresolved": [],
}


def run(*args: str) -> tuple[int, dict]:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:  # pragma: no cover - 便于调试
        raise AssertionError(
            f"stdout 不是 JSON: {completed.stdout!r} stderr={completed.stderr!r}"
        )
    return completed.returncode, payload


class SourceInventoryTestCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve()
        self.materials = self.root / "素材"
        self.work = self.root / "work"
        (self.materials / "访谈").mkdir(parents=True)
        self.work.mkdir(parents=True)
        (self.materials / "usage.csv").write_text("month,hours\n2026-06,12\n", encoding="utf-8")
        (self.materials / "访谈" / "a.txt").write_text("用户A访谈", encoding="utf-8")
        self.scan_output = self.work / "素材扫描.json"
        self.addCleanup(self._temp.cleanup)

    # -- helpers ---------------------------------------------------------
    def scan(self, *extra: str) -> dict:
        code, payload = run(
            "scan", "--root", str(self.materials),
            "--output", str(self.scan_output), *extra,
        )
        self.assertEqual(code, 0, payload)
        return payload

    def publish_evidence(self, items: list[dict] | None = None) -> str:
        document = dict(EVIDENCE_TEMPLATE)
        if items is not None:
            document["items"] = items
        path = self.materials / "structured_data.json"
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def confirm(self, sha: str, summary: str) -> tuple[int, dict]:
        return run(
            "confirm", "--scan", str(self.scan_output),
            "--evidence-sha256", sha, "--summary", summary,
        )

    @property
    def ledger(self) -> Path:
        return self.materials / "数据版本说明.md"


class ScanTests(SourceInventoryTestCase):
    def test_first_scan_requires_full_review(self):
        result = self.scan()

        self.assertTrue(result["fullReviewRequired"])
        self.assertEqual(sorted(result["added"]), ["usage.csv", "访谈/a.txt"])
        self.assertEqual(result["modified"], [])
        self.assertEqual(result["deleted"], [])
        self.assertIsNone(result["baselineVersion"])

    def test_generated_artifacts_are_excluded(self):
        (self.materials / "报告").mkdir()
        (self.materials / "报告" / "旧稿.md").write_text("draft", encoding="utf-8")
        (self.materials / ".report-agent").mkdir()
        (self.materials / ".report-agent" / "state.json").write_text("{}", encoding="utf-8")
        (self.materials / "数据版本说明.md").write_text("# ledger", encoding="utf-8")
        (self.materials / "素材清单.json").write_text("{}", encoding="utf-8")
        (self.materials / ".DS_Store").write_text("junk", encoding="utf-8")
        (self.materials / "~$draft.docx").write_text("lock", encoding="utf-8")
        self.publish_evidence()

        result = self.scan()

        self.assertEqual(sorted(result["added"]), ["usage.csv", "访谈/a.txt"])

    def test_custom_report_workspace_can_be_excluded(self):
        (self.materials / "交付物").mkdir()
        (self.materials / "交付物" / "report.md").write_text("x", encoding="utf-8")

        result = self.scan("--exclude", "交付物")

        self.assertEqual(sorted(result["added"]), ["usage.csv", "访谈/a.txt"])
        self.assertEqual(result["excluded"], ["交付物"])

    def test_inventory_is_not_returned_to_the_session(self):
        """清单可能很大，落盘但不回传。"""
        result = self.scan()

        self.assertNotIn("inventory", result)
        stored = json.loads(self.scan_output.read_text(encoding="utf-8"))
        self.assertIn("inventory", stored)

    def test_missing_root_is_rejected(self):
        code, payload = run("scan", "--root", str(self.root / "nope"))

        self.assertEqual(code, 1)
        self.assertEqual(payload["marker"], "SOURCE_INVENTORY_FAILED")


class IncrementalScanTests(SourceInventoryTestCase):
    """用户新增素材后的增量识别 —— 用户无需指出改了哪个文件。"""

    def setUp(self):
        super().setUp()
        self.scan()
        sha = self.publish_evidence()
        code, _ = self.confirm(sha, "首次整理")
        self.assertEqual(code, 0)

    def test_added_modified_and_deleted_are_detected(self):
        (self.materials / "访谈" / "c.txt").write_text("用户C新访谈", encoding="utf-8")
        (self.materials / "访谈" / "a.txt").write_text("用户A访谈（修订）", encoding="utf-8")
        (self.materials / "usage.csv").unlink()

        result = self.scan()

        self.assertFalse(result["fullReviewRequired"])
        self.assertEqual(result["added"], ["访谈/c.txt"])
        self.assertEqual(result["modified"], ["访谈/a.txt"])
        self.assertEqual(result["deleted"], ["usage.csv"])
        self.assertEqual(result["baselineVersion"], "D1")

    def test_unchanged_files_are_counted_not_reparsed(self):
        (self.materials / "访谈" / "c.txt").write_text("新增", encoding="utf-8")

        result = self.scan()

        self.assertEqual(result["added"], ["访谈/c.txt"])
        self.assertEqual(result["unchangedCount"], 2)

    def test_identical_content_is_never_reported_as_modified(self):
        """不凭修改时间认定变化。"""
        target = self.materials / "访谈" / "a.txt"
        target.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")

        result = self.scan()

        self.assertEqual(result["modified"], [])
        self.assertEqual(result["unchangedCount"], 2)

    def test_rename_is_reported_as_delete_plus_add(self):
        (self.materials / "访谈" / "a.txt").rename(self.materials / "访谈" / "a-renamed.txt")

        result = self.scan()

        self.assertEqual(result["added"], ["访谈/a-renamed.txt"])
        self.assertEqual(result["deleted"], ["访谈/a.txt"])


class DataVersionTests(SourceInventoryTestCase):
    def test_first_registration_is_d1(self):
        self.scan()
        sha = self.publish_evidence()

        code, result = self.confirm(sha, "首次整理，提炼1条论据")

        self.assertEqual(code, 0)
        self.assertEqual(result["marker"], "DATA_VERSION_REGISTERED")
        self.assertEqual(result["dataVersion"], "D1")
        self.assertEqual(result["dataSha256"], sha)

    def test_version_increments_only_when_evidence_changes(self):
        self.scan()
        first = self.publish_evidence()
        self.confirm(first, "首次整理")

        # 论据表未变：只刷新素材指纹，不新增版本
        (self.materials / "访谈" / "b.txt").write_text("新素材但无新事实", encoding="utf-8")
        self.scan()
        code, unchanged = self.confirm(first, "只有排版变化")

        self.assertEqual(code, 0)
        self.assertEqual(unchanged["marker"], "DATA_VERSION_UNCHANGED")
        self.assertEqual(unchanged["dataVersion"], "D1")

        # 论据表真的变了：递增到 D2
        second = self.publish_evidence(items=[
            *EVIDENCE_TEMPLATE["items"],
            {
                "id": "EV-002",
                "type": "qualitative",
                "source_ref": "访谈/b.txt / 全文",
                "content": "用户B反馈新功能上手较慢。",
            },
        ])
        self.scan()
        code, registered = self.confirm(second, "新增EV-002")

        self.assertEqual(code, 0)
        self.assertEqual(registered["dataVersion"], "D2")

    def test_history_is_preserved_in_the_ledger(self):
        self.scan()
        first = self.publish_evidence()
        self.confirm(first, "首次整理")
        second = self.publish_evidence(items=[
            *EVIDENCE_TEMPLATE["items"],
            {"id": "EV-002", "type": "qualitative", "source_ref": "x", "content": "y"},
        ])
        self.scan()
        self.confirm(second, "第二次更新")

        ledger = self.ledger.read_text(encoding="utf-8")

        self.assertIn("| D1 |", ledger)
        self.assertIn("| D2 |", ledger)
        self.assertIn("首次整理", ledger)
        self.assertIn("第二次更新", ledger)

    def test_ledger_holds_fingerprints_not_evidence_text(self):
        self.scan()
        sha = self.publish_evidence()
        self.confirm(sha, "首次整理")

        ledger = self.ledger.read_text(encoding="utf-8")

        self.assertIn("当前素材指纹清单", ledger)
        self.assertIn("usage.csv", ledger)
        # 不含论据原文，不是数据快照
        self.assertNotIn("月均使用时长", ledger)

    def test_mismatched_evidence_fingerprint_is_refused(self):
        self.scan()
        self.publish_evidence()

        code, result = self.confirm("deadbeef", "摘要")

        self.assertEqual(code, 1)
        self.assertIn("数据版本未登记", result["error"])
        self.assertFalse(self.ledger.exists())

    def test_sources_changing_after_scan_is_refused(self):
        self.scan()
        sha = self.publish_evidence()
        (self.materials / "访谈" / "late.txt").write_text("扫描后偷偷新增", encoding="utf-8")

        code, result = self.confirm(sha, "摘要")

        self.assertEqual(code, 1)
        self.assertIn("数据版本未登记", result["error"])

    def test_missing_evidence_table_is_refused(self):
        self.scan()

        code, result = self.confirm("a" * 64, "摘要")

        self.assertEqual(code, 1)
        self.assertIn("数据版本未登记", result["error"])

    def test_corrupt_fingerprint_block_is_reported(self):
        self.ledger.write_text(
            "# 数据版本说明\n\n## 当前素材指纹清单\n\n```json\n{not json\n```\n",
            encoding="utf-8",
        )

        code, result = self.scan_expect_failure()

        self.assertEqual(code, 1)
        self.assertIn("格式损坏", result["error"])

    def scan_expect_failure(self) -> tuple[int, dict]:
        return run(
            "scan", "--root", str(self.materials), "--output", str(self.scan_output)
        )


class DataBindingCheckTests(SourceInventoryTestCase):
    """写作与 Judge 前后核验数据绑定，防止沿用旧评分。"""

    def test_missing_ledger_is_reported(self):
        code, result = run("check", "--root", str(self.materials))

        self.assertEqual(code, 0)
        self.assertEqual(result["marker"], "DATA_VERSION_MISSING")

    def test_query_returns_current_version(self):
        self.scan()
        sha = self.publish_evidence()
        self.confirm(sha, "首次整理")

        code, result = run("check", "--root", str(self.materials))

        self.assertEqual(code, 0)
        self.assertEqual(result["marker"], "DATA_VERSION_CURRENT")
        self.assertEqual(result["dataVersion"], "D1")
        self.assertTrue(result["matchesLedger"])

    def test_matching_binding_is_ok(self):
        self.scan()
        sha = self.publish_evidence()
        self.confirm(sha, "首次整理")

        code, result = run(
            "check", "--root", str(self.materials),
            "--version", "D1", "--sha256", sha,
        )

        self.assertEqual(code, 0)
        self.assertEqual(result["marker"], "DATA_VERSION_OK")

    def test_stale_binding_is_flagged_as_changed(self):
        self.scan()
        first = self.publish_evidence()
        self.confirm(first, "首次整理")
        second = self.publish_evidence(items=[
            *EVIDENCE_TEMPLATE["items"],
            {"id": "EV-002", "type": "qualitative", "source_ref": "x", "content": "y"},
        ])
        self.scan()
        self.confirm(second, "更新")

        code, result = run(
            "check", "--root", str(self.materials),
            "--version", "D1", "--sha256", first,
        )

        self.assertEqual(code, 0)
        self.assertEqual(result["marker"], "DATA_VERSION_CHANGED")
        self.assertEqual(result["currentVersion"], "D2")

    def test_externally_modified_evidence_is_flagged(self):
        """共享论据表被外部改动时，旧绑定必须失效。"""
        self.scan()
        sha = self.publish_evidence()
        self.confirm(sha, "首次整理")
        (self.materials / "structured_data.json").write_text(
            json.dumps({**EVIDENCE_TEMPLATE, "case_id": "tampered"}, ensure_ascii=False),
            encoding="utf-8",
        )

        code, result = run(
            "check", "--root", str(self.materials),
            "--version", "D1", "--sha256", sha,
        )

        self.assertEqual(code, 0)
        self.assertEqual(result["marker"], "DATA_VERSION_CHANGED")


class StructuredDataSchemaTests(unittest.TestCase):
    def test_schema_ships_with_the_plugin(self):
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "resources/evidence/structured_data.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertEqual(
            schema["properties"]["schema"]["const"],
            "openharness-structured-data/v1",
        )
        self.assertEqual(
            sorted(schema["required"]),
            ["case_id", "items", "schema", "unresolved"],
        )
        self.assertFalse(schema["additionalProperties"])
        item = schema["properties"]["items"]["items"]
        self.assertEqual(
            sorted(item["required"]), ["content", "id", "source_ref", "type"]
        )
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(item["properties"]["id"]["pattern"], "^EV-[0-9]{3,}$")


if __name__ == "__main__":
    unittest.main()
