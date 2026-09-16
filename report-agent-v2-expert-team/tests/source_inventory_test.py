import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest


MODULE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "resources/evidence/scripts/source_inventory.py"))
scan, confirm, digest, check = (MODULE[name] for name in ("scan", "confirm", "digest", "check"))


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="report-agent-inventory-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "中文 空格项目"
        self.root.mkdir()
        self.source = self.root / "访谈.md"
        self.source.write_text("旧论据", encoding="utf-8")
        self.evidence = self.root / "structured_data.json"
        self.evidence.write_text(json.dumps({"schema": "openharness-structured-data/v1", "case_id": "test",
            "items": [{"id": "EV-001", "type": "qualitative", "source_ref": "访谈.md", "content": "旧论据"}],
            "unresolved": []}, ensure_ascii=False), encoding="utf-8")
        self.counter = 0

    def run_scan(self, exclude=()):
        self.counter += 1
        self.work = self.root / "报告" / "测试" / "Agent运行记录" / "评测与改写记录" / "素材处理" / str(self.counter)
        self.scanned = self.work / "素材扫描.json"
        return scan(self.root, self.scanned, exclude)

    def publish(self):
        return confirm(self.scanned, digest(self.evidence), "本次更新摘要")

    def baseline(self):
        self.run_scan()
        self.publish()

    def test_first_scan_and_unchanged(self):
        result = self.run_scan()
        self.assertTrue(result["fullReviewRequired"])
        self.assertFalse((self.root / "数据版本说明.md").exists())
        self.assertEqual(result["changes"]["added"], ["访谈.md"])
        self.publish()
        result = self.run_scan()
        self.assertFalse(result["fullReviewRequired"])
        self.assertEqual(result["changes"], {"added": [], "modified": [], "deleted": []})
        self.assertEqual(result["unchangedCount"], 1)

    def test_add_delete_and_same_size_same_mtime_edit(self):
        self.baseline()
        old_stat = self.source.stat()
        self.source.write_text("新论据", encoding="utf-8")
        os.utime(self.source, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        self.assertEqual(self.source.stat().st_size, old_stat.st_size)
        (self.root / "新增.md").write_text("新增", encoding="utf-8")
        result = self.run_scan()
        self.assertEqual(result["changes"]["modified"], ["访谈.md"])
        self.assertEqual(result["changes"]["added"], ["新增.md"])
        self.publish()
        self.source.unlink()
        self.assertEqual(self.run_scan()["changes"]["deleted"], ["访谈.md"])

    def test_timestamp_only_is_unchanged(self):
        self.baseline()
        st = self.source.stat()
        os.utime(self.source, ns=(st.st_atime_ns, st.st_mtime_ns + 1000000000))
        self.assertEqual(self.run_scan()["changes"]["modified"], [])

    def test_rename_is_add_and_delete(self):
        self.baseline()
        self.source.rename(self.root / "新名字.md")
        result = self.run_scan()
        self.assertEqual(result["changes"]["deleted"], ["访谈.md"])
        self.assertEqual(result["changes"]["added"], ["新名字.md"])

    def test_unconfirmed_changes_are_still_pending(self):
        self.baseline()
        before = (self.root / "数据版本说明.md").read_bytes()
        self.source.write_text("修改", encoding="utf-8")
        self.run_scan()  # Simulate parse failure: do not confirm.
        self.assertEqual((self.root / "数据版本说明.md").read_bytes(), before)
        self.assertEqual(self.run_scan()["changes"]["modified"], ["访谈.md"])

    def test_confirm_rejects_sources_changed_after_scan(self):
        self.baseline()
        before = (self.root / "数据版本说明.md").read_bytes()
        self.run_scan()
        self.source.write_text("途中修改", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Sources changed"):
            self.publish()
        self.assertEqual((self.root / "数据版本说明.md").read_bytes(), before)

    def test_confirm_rejects_evidence_hash_mismatch_and_concurrent_manifest(self):
        self.baseline()
        self.run_scan()
        with self.assertRaisesRegex(ValueError, "differs"):
            confirm(self.scanned, "0" * 64, "修改")
        manifest = self.root / "数据版本说明.md"
        manifest.write_bytes(manifest.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "concurrently"):
            self.publish()

    def test_external_evidence_edit_requires_full_review(self):
        self.baseline()
        self.evidence.write_bytes(self.evidence.read_bytes() + b" ")
        result = self.run_scan()
        self.assertTrue(result["fullReviewRequired"])
        self.assertEqual(result["baselineStatus"], "evidence_changed")

    def test_outputs_excluded_and_changed_scope_invalidates_baseline(self):
        custom = self.root / "自定义产物"
        custom.mkdir()
        (custom / "report.md").write_text("报告", encoding="utf-8")
        result = self.run_scan([str(custom)])
        self.assertEqual(result["changes"]["added"], ["访谈.md"])
        self.publish()
        self.assertFalse(self.run_scan([str(custom)])["fullReviewRequired"])
        self.assertEqual(self.run_scan()["baselineStatus"], "scope_changed")
        with self.assertRaisesRegex(ValueError, "Cannot exclude"):
            self.run_scan([str(self.root)])

    def test_corrupt_manifest_is_not_silently_replaced(self):
        manifest = self.root / "数据版本说明.md"
        manifest.write_text("broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_scan()
        self.assertEqual(manifest.read_text(encoding="utf-8"), "broken")

    def test_cli_handles_unicode_spaces_and_failed_scan_does_not_publish(self):
        script = Path(__file__).resolve().parents[1] / "resources/evidence/scripts/source_inventory.py"
        output = self.root / "报告" / "中文 空格" / "素材扫描.json"
        args = [sys.executable, "-B", str(script), "scan", "--root", str(self.root), "--output", str(output)]
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changes"]["added"], ["访谈.md"])
        self.assertFalse((self.root / "数据版本说明.md").exists())
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 1)  # Existing scan must not be overwritten.
        self.assertIn("error", json.loads(result.stderr))

    def test_data_version_log_and_no_snapshots(self):
        self.run_scan()
        self.assertEqual(self.publish()["dataVersion"], "D1")
        self.run_scan()
        unchanged = self.publish()
        self.assertFalse(unchanged["versionChanged"])
        self.assertEqual(unchanged["dataVersion"], "D1")
        self.run_scan()
        evidence = json.loads(self.evidence.read_text(encoding="utf-8"))
        evidence["items"][0]["content"] = "已更正的论据"
        self.evidence.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.publish()["dataVersion"], "D2")
        text = (self.root / "数据版本说明.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("| D1 |"), 1)
        self.assertEqual(text.count("| D2 |"), 1)
        self.assertIn("本次更新摘要", text)
        self.assertNotIn("已更正的论据", text)
        self.assertFalse((self.root / "素材清单.json").exists())
        self.assertEqual(list((self.root / "报告").rglob("structured_data.json")), [])

    def test_check_rejects_stale_version_hash_and_raw_sources(self):
        self.baseline()
        expected = digest(self.evidence)
        self.assertEqual(check(self.root, "D1", expected)["status"], "ok")
        with self.assertRaisesRegex(ValueError, "DATA_VERSION_CHANGED"):
            check(self.root, "D2", expected)
        with self.assertRaisesRegex(ValueError, "DATA_VERSION_CHANGED"):
            check(self.root, "D1", "0" * 64)
        self.source.write_text("未重新登记的素材", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "DATA_VERSION_CHANGED"):
            check(self.root, "D1", expected)

    def test_old_manifest_is_not_misread_as_source_or_trusted(self):
        (self.root / "素材清单.json").write_text("old", encoding="utf-8")
        result = self.run_scan()
        self.assertTrue(result["fullReviewRequired"])
        self.assertEqual(result["changes"]["added"], ["访谈.md"])

    def test_cli_confirm_and_check_version_without_data_copies(self):
        self.run_scan()
        script = Path(__file__).resolve().parents[1] / "resources/evidence/scripts/source_inventory.py"
        prefix = [sys.executable, "-B", str(script)]
        expected = digest(self.evidence)
        result = subprocess.run(prefix + ["confirm", "--scan", str(self.scanned), "--evidence-sha256", expected,
            "--summary", "新增访谈，纠正样本口径"], capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["dataVersion"], "D1")
        args = prefix + ["check", "--root", str(self.root), "--version", "D1", "--sha256", expected]
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.evidence.write_bytes(self.evidence.read_bytes() + b" ")
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn("DATA_VERSION_CHANGED", json.loads(result.stderr)["error"])


if __name__ == "__main__":
    unittest.main()
