"""Markdown 记忆存储的测试。

对齐 report-memory-agent 契约：`~/ReportAgentMemory/` 可见目录、全 Markdown、
`MEMORY.md` 顶部 revision 单一版本号、`memory-history.md` 追加式、无全量索引、
Reflection 不依赖后台调度而是调用时补做。
"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from report_loop.core.memory_store import (
    ATOMS_DIR,
    EPISODES_DIR,
    HISTORY_FILE,
    MEMORY_FILE,
    MemoryRubric,
    MemoryState,
    MemoryStore,
    MemoryStoreError,
    default_memory_root,
)


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve() / "ReportAgentMemory"
        self.store = MemoryStore(self.root)
        self.addCleanup(self._temp.cleanup)

    def seed_rubric(self, **overrides) -> MemoryState:
        self.store.write_episode("EP-001", feedback="摘要太长，以后控制在3行内")
        self.store.write_atom(
            "L1-001", content="报告摘要不超过3行", scope="core",
            sourceEpisodeIds=["EP-001"],
        )
        state = self.store.read_state()
        state.rubrics.append(MemoryRubric(**{
            "id": "MR-SUMMARY",
            "statement": "摘要只保留核心观点，不超过3行",
            "scope": "core",
            "sourceL1Ids": ["L1-001"],
            **overrides,
        }))
        return self.store.save_state(state, advance_revision=True)


class LocationTests(unittest.TestCase):
    def test_default_root_is_visible_in_home(self):
        root = default_memory_root()

        self.assertEqual(root.parent, Path.home())
        self.assertEqual(root.name, "ReportAgentMemory")
        # 可见目录，不是隐藏点目录
        self.assertFalse(root.name.startswith("."))

    def test_relative_path_is_rejected(self):
        with self.assertRaises(MemoryStoreError):
            MemoryStore("ReportAgentMemory")


class InitializationTests(StoreTestCase):
    def test_first_use_creates_memory_file_at_revision_zero(self):
        state = self.store.initialize()

        self.assertEqual(state.revision, 0)
        self.assertTrue(state.enabled)
        self.assertIsNone(state.lastReflectionAt)
        self.assertEqual(state.rubrics, [])
        self.assertTrue((self.root / MEMORY_FILE).is_file())

    def test_initialize_is_idempotent(self):
        self.store.initialize()
        self.seed_rubric()

        state = self.store.initialize()

        self.assertEqual(state.revision, 1)
        self.assertEqual(len(state.rubrics), 1)

    def test_existing_content_without_memory_file_is_reported(self):
        """已有内容但缺少 MEMORY.md 时报告问题，不重新初始化或覆盖。"""
        self.root.mkdir(parents=True)
        (self.root / "L0-episodes").mkdir()
        (self.root / "L0-episodes" / "EP-999.md").write_text("旧数据", encoding="utf-8")

        with self.assertRaises(MemoryStoreError) as caught:
            self.store.initialize()

        self.assertIn(MEMORY_FILE, str(caught.exception))
        self.assertTrue((self.root / "L0-episodes" / "EP-999.md").is_file())

    def test_missing_revision_is_reported_not_guessed(self):
        self.root.mkdir(parents=True)
        (self.root / MEMORY_FILE).write_text("# 记忆\n\nenabled: true\n", encoding="utf-8")

        with self.assertRaises(MemoryStoreError) as caught:
            self.store.read_state()

        self.assertIn("revision", str(caught.exception))

    def test_only_markdown_is_written(self):
        self.store.initialize()
        self.seed_rubric()

        suffixes = {path.suffix for path in self.root.rglob("*") if path.is_file()}

        self.assertEqual(suffixes, {".md"})


class RevisionTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store.initialize()

    def test_revision_advances_only_on_persisted_change(self):
        state = self.seed_rubric()
        self.assertEqual(state.revision, 1)

        # 无文件变化不递增
        unchanged = self.store.save_state(
            self.store.read_state(), advance_revision=False
        )
        self.assertEqual(unchanged.revision, 1)

    def test_revision_keeps_incrementing_and_is_not_reset(self):
        self.seed_rubric()
        for _ in range(3):
            state = self.store.read_state()
            state.rubrics[0].statement += "。"
            state = self.store.save_state(state, advance_revision=True)

        self.assertEqual(state.revision, 4)

    def test_reflection_metadata_alone_does_not_advance_revision(self):
        self.seed_rubric()

        state = self.store.mark_reflected(now=datetime(2026, 9, 17, 17, 0, 0))

        self.assertEqual(state.revision, 1)
        self.assertEqual(state.lastReflectionAt, "2026-09-17T17:00:00")

    def test_state_is_reread_from_disk_each_time(self):
        """每次操作开始重新读取，不依赖旧上下文。"""
        self.seed_rubric()
        stale = self.store.read_state()

        # 模拟用户手工编辑 MEMORY.md
        raw = (self.root / MEMORY_FILE).read_text(encoding="utf-8")
        (self.root / MEMORY_FILE).write_text(
            raw.replace("revision: 1", "revision: 7"), encoding="utf-8"
        )

        fresh = self.store.read_state()

        self.assertEqual(stale.revision, 1)
        self.assertEqual(fresh.revision, 7)


class HistoryTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store.initialize()

    def test_history_records_revision_change_reason_and_sources(self):
        state = self.seed_rubric()

        self.store.append_history(
            revision=state.revision,
            change="新增 MR-SUMMARY",
            reason="用户明确表达长期要求",
            sourceIds=["EP-001", "L1-001"],
        )

        history = (self.root / HISTORY_FILE).read_text(encoding="utf-8")
        self.assertIn("## revision 1", history)
        self.assertIn("新增 MR-SUMMARY", history)
        self.assertIn("用户明确表达长期要求", history)
        self.assertIn("EP-001, L1-001", history)

    def test_history_appends_rather_than_replaces(self):
        self.seed_rubric()
        self.store.append_history(revision=1, change="新增", reason="首次")
        self.store.append_history(revision=2, change="合并", reason="去重")

        history = (self.root / HISTORY_FILE).read_text(encoding="utf-8")

        self.assertIn("## revision 1", history)
        self.assertIn("## revision 2", history)
        self.assertLess(history.index("revision 1"), history.index("revision 2"))

    def test_history_is_separate_from_memory_file(self):
        """不在 MEMORY 中累计操作日志。"""
        self.seed_rubric()
        self.store.append_history(revision=1, change="新增 MR-SUMMARY", reason="长期要求")

        memory = (self.root / MEMORY_FILE).read_text(encoding="utf-8")

        self.assertNotIn("## revision 1", memory)
        self.assertNotIn("长期要求", memory)


class NoGlobalIndexTests(StoreTestCase):
    """不维护全量 L0/L1 索引或 MEMORY 快照。"""

    def setUp(self):
        super().setUp()
        self.store.initialize()

    def test_atoms_are_found_by_id_under_scope_dirs(self):
        self.store.write_episode("EP-001", feedback="反馈")
        self.store.write_atom(
            "L1-A", content="core 规则", scope="core", sourceEpisodeIds=["EP-001"]
        )
        self.store.write_atom(
            "L1-B", content="给 CEO 的规则", scope="audience",
            scopeValue="CEO", sourceEpisodeIds=["EP-001"],
        )

        self.assertEqual(self.store.read_atom("L1-A")["scope"], "core")
        self.assertEqual(self.store.read_atom("L1-B")["scopeValue"], "CEO")
        self.assertIsNone(self.store.read_atom("L1-missing"))
        self.assertTrue((self.root / ATOMS_DIR / "core" / "L1-A.md").is_file())
        self.assertTrue((self.root / ATOMS_DIR / "audience" / "L1-B.md").is_file())

    def test_no_index_or_snapshot_files_exist(self):
        self.seed_rubric()

        names = {path.name for path in self.root.rglob("*") if path.is_file()}

        for forbidden in ("index.md", "index.json", "snapshot.md", "manifest.json"):
            self.assertNotIn(forbidden, names)

    def test_provenance_chain_is_preserved(self):
        """L2B → sourceL1Ids → L1 → sourceEpisodeIds → L0"""
        state = self.seed_rubric()

        rubric = state.rubrics[0]
        atom = self.store.read_atom(rubric.sourceL1Ids[0])
        episode = self.store.read_episode(atom["sourceEpisodeIds"][0])

        self.assertEqual(rubric.sourceL1Ids, ["L1-001"])
        self.assertEqual(atom["sourceEpisodeIds"], ["EP-001"])
        self.assertIn("摘要太长", episode)

    def test_atom_requires_source_episode(self):
        with self.assertRaises(MemoryStoreError):
            self.store.write_atom("L1-X", content="无来源", scope="core")


class ScopeTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store.initialize()

    def test_core_scope_rejects_scope_value(self):
        with self.assertRaises(MemoryStoreError):
            MemoryRubric(id="MR-X", statement="x", scope="core", scopeValue="CEO")

    def test_audience_scope_requires_scope_value(self):
        with self.assertRaises(MemoryStoreError):
            MemoryRubric(id="MR-X", statement="x", scope="audience")

    def test_invalid_scope_is_rejected(self):
        with self.assertRaises(MemoryStoreError):
            MemoryRubric(id="MR-X", statement="x", scope="team")

    def test_invalid_ids_are_rejected(self):
        with self.assertRaises(MemoryStoreError):
            MemoryRubric(id="SUMMARY", statement="x", scope="core")
        with self.assertRaises(MemoryStoreError):
            self.store.write_atom("atom-1", content="x", scope="core",
                                  sourceEpisodeIds=["EP-001"])
        with self.assertRaises(MemoryStoreError):
            self.store.episode_path("episode-1")


class ResolveTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store.initialize()

    def test_resolve_returns_l2b_with_source_ids_only(self):
        self.seed_rubric()

        result = self.store.resolve_candidates(audience="CEO", project="Q3")

        self.assertEqual(result["marker"], "MEMORY_RESOLVE_COMPLETED")
        self.assertTrue(result["enabled"])
        self.assertEqual(result["revision"], "1")
        candidate = result["candidates"][0]
        self.assertEqual(candidate["sourceL1Ids"], ["L1-001"])
        # 不展开全部 L1 原文
        self.assertNotIn("content", candidate)

    def test_resolve_succeeds_with_no_candidates(self):
        result = self.store.resolve_candidates()

        self.assertEqual(result["marker"], "MEMORY_RESOLVE_COMPLETED")
        self.assertEqual(result["candidates"], [])

    def test_disabled_memory_returns_no_candidates_but_succeeds(self):
        self.seed_rubric()
        self.store.set_enabled(False)

        result = self.store.resolve_candidates()

        self.assertEqual(result["marker"], "MEMORY_RESOLVE_COMPLETED")
        self.assertFalse(result["enabled"])
        self.assertEqual(result["candidates"], [])
        # 关闭时保留已有文件
        self.assertEqual(len(self.store.read_state().rubrics), 1)

    def test_dimension_candidate_round_trips(self):
        self.seed_rubric(dimensionCandidate={
            "name": "actionability",
            "label": "可执行性",
            "reason": "无法并入现有表达维度",
        })

        candidate = self.store.resolve_candidates()["candidates"][0]

        self.assertEqual(candidate["dimensionCandidate"]["name"], "actionability")
        self.assertEqual(candidate["dimensionCandidate"]["label"], "可执行性")


class InspectSourcesTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store.initialize()
        self.state = self.seed_rubric()

    def test_returns_only_requested_atoms(self):
        self.store.write_atom(
            "L1-OTHER", content="不该被返回", scope="core",
            sourceEpisodeIds=["EP-001"],
        )

        result = self.store.inspect_sources(
            revision=str(self.state.revision),
            requests=[{"memoryId": "MR-SUMMARY", "sourceL1Ids": ["L1-001"]}],
        )

        self.assertEqual(result["marker"], "MEMORY_SOURCE_INSPECTION_COMPLETED")
        returned = [item["id"] for item in result["evidence"][0]["sources"]]
        self.assertEqual(returned, ["L1-001"])
        self.assertNotIn("L1-OTHER", returned)

    def test_missing_ids_are_reported(self):
        result = self.store.inspect_sources(
            revision=str(self.state.revision),
            requests=[{"memoryId": "MR-SUMMARY", "sourceL1Ids": ["L1-001", "L1-gone"]}],
        )

        self.assertEqual(result["evidence"][0]["missingSourceL1Ids"], ["L1-gone"])

    def test_stale_revision_is_a_conflict(self):
        """revision 已变化时不混用版本。"""
        result = self.store.inspect_sources(revision="0", requests=[])

        self.assertEqual(result["marker"], "MEMORY_SOURCE_CONFLICT")
        self.assertEqual(result["revision"], "1")
        self.assertEqual(result["requestedRevision"], "0")


class ReflectionDueTests(StoreTestCase):
    """不依赖后台调度：调用时按 lastReflectionAt 判断是否补做。"""

    def setUp(self):
        super().setUp()
        self.store.initialize()

    def test_never_reflected_and_past_cutoff_is_due(self):
        self.assertTrue(self.store.reflection_due(now=datetime(2026, 9, 17, 16, 30)))
        self.assertTrue(self.store.reflection_due(now=datetime(2026, 9, 17, 23, 0)))

    def test_before_cutoff_on_first_day_is_not_due(self):
        self.assertFalse(self.store.reflection_due(now=datetime(2026, 9, 17, 9, 0)))

    def test_same_day_is_not_repeated(self):
        self.store.mark_reflected(now=datetime(2026, 9, 17, 17, 0))

        self.assertFalse(self.store.reflection_due(now=datetime(2026, 9, 17, 22, 0)))

    def test_previous_day_unreflected_is_due_even_before_cutoff(self):
        self.store.mark_reflected(now=datetime(2026, 9, 15, 17, 0))

        # 上一个自然日仍未复盘
        self.assertTrue(self.store.reflection_due(now=datetime(2026, 9, 17, 9, 0)))

    def test_next_day_after_cutoff_is_due(self):
        self.store.mark_reflected(now=datetime(2026, 9, 17, 17, 0))

        self.assertTrue(self.store.reflection_due(now=datetime(2026, 9, 18, 17, 0)))

    def test_disabled_memory_is_never_due(self):
        self.store.set_enabled(False)

        self.assertFalse(self.store.reflection_due(now=datetime(2026, 9, 25, 18, 0)))

    def test_corrupt_timestamp_falls_back_to_cutoff_check(self):
        raw = (self.root / MEMORY_FILE).read_text(encoding="utf-8")
        (self.root / MEMORY_FILE).write_text(
            raw.replace("lastReflectionAt: ", "lastReflectionAt: not-a-date"),
            encoding="utf-8",
        )

        self.assertTrue(self.store.reflection_due(now=datetime(2026, 9, 17, 18, 0)))

    def test_pending_episodes_are_discoverable_for_recovery(self):
        """中断的 Capture 留下的 Episode 供下一次 Reflection 恢复。"""
        self.store.write_episode("EP-001", feedback="已提炼")
        self.store.write_episode("EP-002", feedback="Capture 中断，尚未提炼")
        self.store.write_atom(
            "L1-001", content="规则", scope="core", sourceEpisodeIds=["EP-001"]
        )

        self.assertEqual(self.store.pending_episode_ids(), ["EP-002"])


class SettingsTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store.initialize()

    def test_memory_is_enabled_by_default(self):
        self.assertTrue(self.store.read_state().enabled)

    def test_toggling_advances_revision(self):
        disabled = self.store.set_enabled(False)
        self.assertFalse(disabled.enabled)
        self.assertEqual(disabled.revision, 1)

        enabled = self.store.set_enabled(True)
        self.assertTrue(enabled.enabled)
        self.assertEqual(enabled.revision, 2)

    def test_setting_same_value_is_a_noop(self):
        state = self.store.set_enabled(True)

        self.assertEqual(state.revision, 0)


class EpisodeContentTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store.initialize()

    def test_episode_keeps_feedback_and_context(self):
        self.store.write_episode(
            "EP-001",
            feedback="摘要太长，以后控制在3行内",
            task="留存分析",
            audience="CEO",
            project="Q3",
            conversation=[
                {"role": "assistant", "content": "这是报告摘要……"},
                {"role": "user", "content": "摘要太长"},
            ],
            beforeAfter="改前 5 行 → 改后 3 行",
        )

        raw = self.store.read_episode("EP-001")

        self.assertIn("摘要太长，以后控制在3行内", raw)
        self.assertIn("CEO", raw)
        self.assertIn("改前 5 行", raw)
        self.assertIn("**user**", raw)

    def test_existing_episode_is_not_overwritten(self):
        """captureId 重试时不重复创建 Episode。"""
        self.store.write_episode("EP-001", feedback="原始反馈")
        self.store.write_episode("EP-001", feedback="覆盖尝试")

        self.assertIn("原始反馈", self.store.read_episode("EP-001"))

    def test_episodes_live_in_their_own_directory(self):
        self.store.write_episode("EP-001", feedback="反馈")

        self.assertTrue((self.root / EPISODES_DIR / "EP-001.md").is_file())


if __name__ == "__main__":
    unittest.main()
