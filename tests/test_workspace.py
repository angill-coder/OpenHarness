"""工作区与交付目录约定的测试。

重点覆盖「用户看完报告要大改并重启 Loop」这条路径：同一报告必须沿用同一
reportId，在其下新建 loop-002，版本号和评测历史不能断。
"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from report_loop.core import workspace as w


class MaterialRootTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve()
        self.addCleanup(self._temp.cleanup)

    def test_single_directory_of_materials(self):
        materials = self.root / "项目" / "素材"
        materials.mkdir(parents=True)
        for name in ("访谈.docx", "问卷.xlsx"):
            (materials / name).touch()

        derived = w.derive_material_root([
            str(materials / "访谈.docx"),
            str(materials / "问卷.xlsx"),
        ])

        self.assertEqual(derived, materials)

    def test_directory_material_is_used_as_is(self):
        materials = self.root / "素材"
        materials.mkdir(parents=True)

        self.assertEqual(w.derive_material_root([str(materials)]), materials)

    def test_nested_materials_use_common_parent(self):
        project = self.root / "项目"
        (project / "访谈").mkdir(parents=True)
        (project / "数据").mkdir(parents=True)
        (project / "访谈" / "a.docx").touch()
        (project / "数据" / "b.csv").touch()

        derived = w.derive_material_root([
            str(project / "访谈" / "a.docx"),
            str(project / "数据" / "b.csv"),
        ])

        self.assertEqual(derived, project)

    def test_empty_material_list_is_rejected(self):
        with self.assertRaises(w.WorkspaceError):
            w.derive_material_root([])

    def test_scattered_materials_require_explicit_location(self):
        """退化到家目录时拒绝，而不是静默把产物写到家目录。"""
        with self.assertRaises(w.WorkspaceError):
            w.derive_material_root([str(Path.home() / "a.docx"), "/tmp/b.csv"])


class ReportDirTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.material_root = Path(self._temp.name).resolve()
        self.addCleanup(self._temp.cleanup)

    def test_defaults_to_report_subdir_of_materials(self):
        resolved = w.resolve_report_dir(material_root=self.material_root)
        self.assertEqual(resolved, self.material_root / "报告")

    def test_user_location_takes_precedence(self):
        requested = self.material_root / "交付物"
        resolved = w.resolve_report_dir(
            material_root=self.material_root,
            requested=str(requested),
        )
        self.assertEqual(resolved, requested)

    def test_blank_request_falls_back_to_default(self):
        resolved = w.resolve_report_dir(material_root=self.material_root, requested="   ")
        self.assertEqual(resolved, self.material_root / "报告")

    def test_writable_check_creates_the_directory(self):
        target = self.material_root / "报告"
        self.assertEqual(w.ensure_writable(target), target)
        self.assertTrue(target.is_dir())

    def test_paths_outside_the_report_dir_are_rejected(self):
        report_dir = w.ensure_writable(self.material_root / "报告")
        for outside in ("/etc/passwd", str(self.material_root / "escaped.md")):
            with self.assertRaises(w.WorkspaceError):
                w.assert_within(Path(outside), report_dir, label="outputPath")

    def test_paths_inside_the_report_dir_are_accepted(self):
        report_dir = w.ensure_writable(self.material_root / "报告")
        inside = report_dir / "留存分析-v1.md"
        self.assertEqual(w.assert_within(inside, report_dir, label="outputPath"), inside)


class ReportIdentityTests(unittest.TestCase):
    def test_report_id_carries_topic_and_first_created_time(self):
        report_id = w.new_report_id("留存分析", now=datetime(2026, 9, 15, 14, 30, 0))
        self.assertEqual(report_id, "留存分析-20260915-143000")
        self.assertEqual(w.parse_report_id(report_id), ("留存分析", "20260915-143000"))

    def test_unsafe_characters_are_stripped_from_topic(self):
        self.assertEqual(w.sanitize_topic('留存/分析:"报告"'), "留存分析报告")

    def test_blank_topic_is_rejected(self):
        with self.assertRaises(w.WorkspaceError):
            w.sanitize_topic("   ")

    def test_malformed_report_id_is_rejected(self):
        for bad in ("留存分析", "留存分析-2026", ""):
            with self.assertRaises(w.WorkspaceError):
                w.parse_report_id(bad)


class RestartLoopTests(unittest.TestCase):
    """同一报告重启 Loop 的目录行为。"""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name).resolve()
        self.report_dir = w.ensure_writable(root / "报告")
        self.topic = "留存分析"
        self.report_id = w.new_report_id(self.topic, now=datetime(2026, 9, 15, 14, 30, 0))
        self.addCleanup(self._temp.cleanup)

    def test_second_loop_reuses_report_id_and_increments_sequence(self):
        first = w.next_loop_dir(self.report_dir, self.report_id, now=datetime(2026, 9, 15, 14, 30, 0))
        first.mkdir(parents=True)
        second = w.next_loop_dir(self.report_dir, self.report_id, now=datetime(2026, 9, 16, 10, 30, 0))
        second.mkdir(parents=True)

        self.assertEqual(first.name, "loop-001-20260915-143000")
        self.assertEqual(second.name, "loop-002-20260916-103000")
        # 关键：两轮在同一报告工作目录下并列，历史不丢
        self.assertEqual(first.parent, second.parent)
        self.assertEqual(len(w.existing_loop_dirs(self.report_dir, self.report_id)), 2)

    def test_existing_report_id_is_discoverable_for_continuation(self):
        w.next_loop_dir(self.report_dir, self.report_id).mkdir(parents=True)

        found = w.find_existing_report_ids(self.report_dir, self.topic)

        self.assertEqual(found, [self.report_id])

    def test_unrelated_topics_are_not_treated_as_continuation(self):
        w.next_loop_dir(self.report_dir, self.report_id).mkdir(parents=True)
        other = w.new_report_id("增长复盘", now=datetime(2026, 9, 15, 15, 0, 0))
        w.next_loop_dir(self.report_dir, other).mkdir(parents=True)

        self.assertEqual(w.find_existing_report_ids(self.report_dir, self.topic), [self.report_id])
        self.assertEqual(w.find_existing_report_ids(self.report_dir, "增长复盘"), [other])

    def test_no_report_ids_before_any_loop_runs(self):
        self.assertEqual(w.find_existing_report_ids(self.report_dir, self.topic), [])
        self.assertEqual(w.existing_loop_dirs(self.report_dir, self.report_id), [])

    def test_material_rounds_increment_within_one_report(self):
        first = w.next_material_round_dir(self.report_dir, self.report_id)
        first.mkdir(parents=True)
        second = w.next_material_round_dir(self.report_dir, self.report_id)

        self.assertEqual(first.name, "r001")
        self.assertEqual(second.name, "r002")


class LoopPathsTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name).resolve()
        self.report_dir = w.ensure_writable(root / "报告")
        self.report_id = w.new_report_id("留存分析", now=datetime(2026, 9, 15, 14, 30, 0))
        self.loop_dir = w.next_loop_dir(self.report_dir, self.report_id)
        self.paths = w.LoopPaths.under(self.loop_dir)
        self.addCleanup(self._temp.cleanup)

    def test_layout_matches_the_convention(self):
        relative = lambda p: str(p.relative_to(self.loop_dir))
        self.assertEqual(relative(self.paths.round_request), "本轮需求.md")
        self.assertEqual(relative(self.paths.candidate(0)), "候选报告/R0.md")
        self.assertEqual(relative(self.paths.run_state), "评测与改写记录/run-state.json")
        self.assertEqual(relative(self.paths.resolution_plan), "评测与改写记录/resolution-plan.json")
        self.assertEqual(relative(self.paths.judgment("R1")), "评测与改写记录/judgments/R1.json")
        self.assertEqual(
            relative(self.paths.revision_brief("R2")),
            "评测与改写记录/revision-briefs/R2.json",
        )

    def test_candidates_start_at_r0_each_loop(self):
        self.assertEqual(w.next_candidate_index(self.paths), 0)
        self.paths.candidates_dir.mkdir(parents=True)
        self.paths.candidate(0).write_text("draft", encoding="utf-8")
        self.assertEqual(w.next_candidate_index(self.paths), 1)
        self.paths.candidate(1).write_text("rewrite", encoding="utf-8")
        self.assertEqual(w.next_candidate_index(self.paths), 2)

    def test_negative_candidate_index_is_rejected(self):
        with self.assertRaises(w.WorkspaceError):
            self.paths.candidate(-1)

    def test_no_directories_are_pre_created(self):
        """不预建空目录。"""
        self.assertFalse(self.loop_dir.exists())


class DeliveryVersionTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name).resolve()
        self.report_dir = w.ensure_writable(root / "报告")
        self.topic = "留存分析"
        self.addCleanup(self._temp.cleanup)

    def publish(self, version: int) -> Path:
        path = w.delivery_path(self.report_dir, self.topic, version)
        path.write_text(f"v{version}", encoding="utf-8")
        return path

    def test_first_delivery_is_v1(self):
        self.assertEqual(w.next_version(self.report_dir, self.topic), 1)
        self.assertEqual(
            w.delivery_path(self.report_dir, self.topic, 1).name,
            "留存分析-v1.md",
        )

    def test_loop_and_direct_rewrite_share_one_sequence(self):
        self.publish(1)  # 首轮 Loop 最佳报告
        self.assertEqual(w.next_version(self.report_dir, self.topic), 2)
        self.publish(2)  # 用户反馈后直接改写
        self.assertEqual(w.next_version(self.report_dir, self.topic), 3)
        self.publish(3)  # 再次 Loop 的最佳报告
        self.assertEqual(w.delivered_versions(self.report_dir, self.topic), [1, 2, 3])

    def test_older_versions_are_preserved(self):
        first = self.publish(1)
        self.publish(2)
        self.assertTrue(first.is_file())
        self.assertEqual(first.read_text(encoding="utf-8"), "v1")

    def test_other_topics_do_not_affect_numbering(self):
        self.publish(1)
        other = w.delivery_path(self.report_dir, "增长复盘", 1)
        other.write_text("other", encoding="utf-8")

        self.assertEqual(w.next_version(self.report_dir, self.topic), 2)
        self.assertEqual(w.next_version(self.report_dir, "增长复盘"), 2)

    def test_unversioned_files_are_ignored(self):
        (self.report_dir / "留存分析.md").write_text("no version", encoding="utf-8")
        (self.report_dir / "留存分析-终稿.md").write_text("no version", encoding="utf-8")

        self.assertEqual(w.delivered_versions(self.report_dir, self.topic), [])
        self.assertEqual(w.next_version(self.report_dir, self.topic), 1)

    def test_version_below_one_is_rejected(self):
        with self.assertRaises(w.WorkspaceError):
            w.delivery_path(self.report_dir, self.topic, 0)

    def test_ledger_locations(self):
        self.assertEqual(
            w.version_ledger_path(self.report_dir),
            self.report_dir / "版本说明.md",
        )
        material_root = self.report_dir.parent
        self.assertEqual(
            w.data_ledger_path(material_root),
            material_root / "数据版本说明.md",
        )
        self.assertEqual(
            w.structured_data_path(material_root),
            material_root / "structured_data.json",
        )


class ScanExclusionTests(unittest.TestCase):
    def test_generated_artifacts_are_excluded_from_material_scan(self):
        for name in ("报告", ".report-agent", "structured_data.json", "数据版本说明.md"):
            self.assertTrue(w.is_scan_excluded(name), name)

    def test_original_materials_are_not_excluded(self):
        for name in ("访谈.docx", "问卷.xlsx", "原始数据"):
            self.assertFalse(w.is_scan_excluded(name), name)

    def test_designated_report_workspace_is_excluded(self):
        self.assertTrue(
            w.is_scan_excluded("交付物", report_work_dirs=frozenset({"交付物"}))
        )


class HiddenAttributeTests(unittest.TestCase):
    def test_missing_directory_reports_false(self):
        self.assertFalse(w.hide_internal_dir(Path("/nonexistent-report-agent-dir")))

    def test_dot_prefixed_directory_is_already_hidden_on_posix(self):
        with tempfile.TemporaryDirectory() as temp:
            internal = Path(temp) / ".report-agent"
            internal.mkdir()
            # POSIX 依赖点号前缀；Windows 走 attrib，失败也不阻塞交付
            self.assertIsInstance(w.hide_internal_dir(internal), bool)


if __name__ == "__main__":
    unittest.main()
