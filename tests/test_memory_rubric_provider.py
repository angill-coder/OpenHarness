from __future__ import annotations

import sys
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from report_loop.core.memory_rubric_provider import MemoryRubricProvider
from report_loop.core.memory_store import MemoryRubric, MemoryStore
from report_loop.core.runtime import ReportLoopError
from report_loop.runner import run


class MemoryRubricProviderTests(unittest.TestCase):
    """L2B 快照读取。记忆是 Markdown，provider 只读不写。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.memory_root = Path(self.temporary.name).resolve() / "ReportAgentMemory"
        self.store = MemoryStore(self.memory_root)
        self.provider = MemoryRubricProvider(self.memory_root)

    def seed(self, *rubrics: MemoryRubric) -> None:
        self.store.initialize()
        self.store.write_episode("EP-001", feedback="用户明确的写作要求")
        state = self.store.read_state()
        for index, rubric in enumerate(rubrics, start=1):
            atom_id = f"L1-{index:03d}"
            self.store.write_atom(
                atom_id, content=f"原子证据 {index}", scope=rubric.scope,
                scopeValue=rubric.scopeValue, sourceEpisodeIds=["EP-001"],
            )
            rubric.sourceL1Ids = [atom_id]
            state.rubrics.append(rubric)
        self.store.save_state(state, advance_revision=True)

    def test_missing_memory_returns_empty_snapshot(self) -> None:
        snapshot = self.provider.load()

        self.assertEqual(snapshot["status"], "empty")
        self.assertEqual(snapshot["items"], [])
        self.assertIsNone(snapshot["revision"])

    def test_load_exposes_all_scope_candidates_for_model_resolution(self) -> None:
        self.seed(
            MemoryRubric(id="MR-CORE", statement="摘要不超过3行", scope="core"),
            MemoryRubric(id="MR-AUD", statement="对CEO先给结论", scope="audience",
                         scopeValue="CEO"),
            MemoryRubric(id="MR-PRJ", statement="Q3口径用月活", scope="project",
                         scopeValue="Q3"),
        )

        snapshot = self.provider.load(audience="CEO", project="Q3")

        self.assertEqual(snapshot["status"], "loaded")
        self.assertEqual(snapshot["revision"], "1")
        # 全部 Scope 都作为候选交给 Resolution Judge，由它结合任务解释
        self.assertEqual(
            [item["id"] for item in snapshot["items"]],
            ["MR-CORE", "MR-AUD", "MR-PRJ"],
        )
        self.assertEqual(
            [item["scope"] for item in snapshot["items"]],
            ["core", "audience", "project"],
        )

    def test_candidates_carry_source_l1_ids(self) -> None:
        self.seed(MemoryRubric(id="MR-CORE", statement="摘要不超过3行", scope="core"))

        item = self.provider.load()["items"][0]

        self.assertEqual(item["sourceL1Ids"], ["L1-001"])
        self.assertEqual(item["status"], "active")

    def test_load_sources_returns_only_requested_atoms(self) -> None:
        self.seed(MemoryRubric(id="MR-CORE", statement="摘要不超过3行", scope="core"))
        self.store.write_atom(
            "L1-OTHER", content="不该被返回", scope="core",
            sourceEpisodeIds=["EP-001"],
        )

        sources = self.provider.load_sources(["L1-001"])

        self.assertEqual([item["id"] for item in sources], ["L1-001"])
        self.assertEqual(sources[0]["sourceEpisodeIds"], ["EP-001"])

    def test_load_sources_skips_missing_ids(self) -> None:
        self.seed(MemoryRubric(id="MR-CORE", statement="摘要不超过3行", scope="core"))

        sources = self.provider.load_sources(["L1-001", "L1-gone"])

        self.assertEqual([item["id"] for item in sources], ["L1-001"])

    def test_explicit_disable_hides_existing_memory_rubrics(self) -> None:
        self.seed(MemoryRubric(id="MR-CORE", statement="摘要不超过3行", scope="core"))
        self.store.set_enabled(False)

        snapshot = self.provider.load()

        self.assertEqual(snapshot["status"], "disabled")
        self.assertEqual(snapshot["items"], [])
        # 关闭只是不参与评测，文件仍保留
        self.assertEqual(len(self.store.read_state().rubrics), 1)

    def test_unreadable_memory_falls_back_to_base_rubrics(self) -> None:
        """记忆损坏不阻断报告：退回 Base Rubrics 并记录 warning。"""
        self.memory_root.mkdir(parents=True)
        (self.memory_root / "MEMORY.md").write_text("# 没有 revision\n", encoding="utf-8")

        snapshot = self.provider.load()

        self.assertEqual(snapshot["status"], "unavailable")
        self.assertEqual(snapshot["items"], [])
        self.assertTrue(
            any("memory_unreadable" in warning for warning in snapshot["warnings"])
        )

    def test_duplicate_ids_across_scopes_remain_distinct(self) -> None:
        self.seed(
            MemoryRubric(id="MR-DUP", statement="core 版本", scope="core"),
            MemoryRubric(id="MR-DUP", statement="CEO 版本", scope="audience",
                         scopeValue="CEO"),
        )

        items = self.provider.load()["items"]

        ids = [item["id"] for item in items]
        self.assertEqual(len(set(ids)), 2)
        self.assertTrue(all(item.get("sourceMemoryId") == "MR-DUP" for item in items))

    def test_scope_value_missing_is_warned_not_crashed(self) -> None:
        self.store.initialize()
        raw = (self.memory_root / "MEMORY.md").read_text(encoding="utf-8")
        (self.memory_root / "MEMORY.md").write_text(
            raw.replace("（暂无）",
                        "### MR-BAD\n\n- statement: 缺少 scopeValue\n"
                        "- scope: audience\n- sourceL1Ids: L1-001\n"),
            encoding="utf-8",
        )

        snapshot = self.provider.load()

        self.assertEqual(snapshot["items"], [])
        self.assertIn("memory_rubric_scope_value_missing:MR-BAD", snapshot["warnings"])

    def test_dimension_candidate_is_passed_through(self) -> None:
        self.seed(MemoryRubric(
            id="MR-CORE", statement="给出可执行建议", scope="core",
            dimensionCandidate={"name": "actionability", "label": "可执行性",
                                "reason": "无法并入现有维度"},
        ))

        item = self.provider.load()["items"][0]

        self.assertEqual(item["dimensionCandidate"]["name"], "actionability")


class RunnerMemoryProviderTests(unittest.TestCase):
    def test_runner_uses_default_local_memory_without_mcp_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "report.md"
            output = root / "final.md"
            report.write_text("# report\n", encoding="utf-8")
            captured: dict = {}

            class FakeRuntime:
                def __init__(self, **kwargs):
                    captured.update(kwargs)

                def start(self, **kwargs):
                    return {"runId": "run-1"}

                def deadline_at(self, run_id):
                    return 9_999_999_999.0

                def submit(self, **kwargs):
                    return {
                        "status": "completed",
                        "nextAction": "deliver",
                        "bestArtifactPath": str(report),
                        "bestVersion": "v1",
                    }

            job = {
                "originalUserQuery": "写报告",
                "intakeContext": {},
                "hostModel": {"modelId": "deepseek-v4-flash-ioa"},
                "judgeProvider": "workbuddy",
                "audience": "",
                "project": "",
                "draftArtifactPath": str(report),
                "outputPath": str(output),
            }
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("REPORT_AGENT_MEMORY_DIR", None)
                run(job, runtime_factory=FakeRuntime)
            provider = captured["memory_provider"]
            # 默认指向用户主目录下可见的 ReportAgentMemory/，与插件安装目录无关
            self.assertEqual(
                provider.memory_root,
                (Path.home() / "ReportAgentMemory").resolve(),
            )

    def test_runner_delivers_v1_when_first_judge_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "report-v1.md"
            output = root / "report-final.md"
            report.write_text("# 可交付的 V1\n", encoding="utf-8")

            class FailingRuntime:
                def __init__(self, **kwargs):
                    pass

                def start(self, **kwargs):
                    return {"runId": "run-failed-judge"}

                def deadline_at(self, run_id):
                    return 9_999_999_999.0

                def submit(self, **kwargs):
                    raise ReportLoopError("Judge transport unavailable")

            job = {
                "originalUserQuery": "写报告",
                "intakeContext": {},
                "hostModel": {"modelId": "deepseek-v4-pro-ioa"},
                "judgeProvider": "workbuddy",
                "audience": "",
                "project": "",
                "draftArtifactPath": str(report),
                "outputPath": str(output),
            }
            result = run(job, runtime_factory=FailingRuntime)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["stopCode"], "judge_unavailable")
            self.assertEqual(result["judgedVersions"], 0)
            self.assertEqual(Path(result["finalArtifactPath"]), output.resolve())
            self.assertEqual(output.read_text(encoding="utf-8"), "# 可交付的 V1\n")
            self.assertEqual(result["rewriteRounds"], 0)
            # 候选与评测明细只留在隐藏目录，不再另建 <name>-versions/
            self.assertNotIn("versionsDirectory", result)

    def test_runner_exports_all_judged_report_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = root / "internal-run" / "reports"
            reports.mkdir(parents=True)
            (reports / "v1.md").write_text("# V1\n", encoding="utf-8")
            (reports / "v2.md").write_text("# V2\n", encoding="utf-8")
            output = root / "report-final.md"

            class FakeRuntime:
                def __init__(self, **kwargs):
                    pass

                def start(self, **kwargs):
                    return {"runId": "run-versions"}

                def deadline_at(self, run_id):
                    return 9_999_999_999.0

                def submit(self, **kwargs):
                    return {
                        "status": "completed",
                        "nextAction": "deliver",
                        "bestArtifactPath": str(reports / "v2.md"),
                        "bestVersion": "v2",
                        "bestScore": 5.0,
                        "judgedVersions": 2,
                    }

            job = {
                "originalUserQuery": "写报告",
                "intakeContext": {},
                "hostModel": {"modelId": "deepseek-v4-pro-ioa"},
                "judgeProvider": "workbuddy",
                "audience": "",
                "project": "",
                "draftArtifactPath": str(reports / "v1.md"),
                "outputPath": str(output),
            }
            result = run(job, runtime_factory=FakeRuntime)

            self.assertEqual(output.read_text(encoding="utf-8"), "# V2\n")
            self.assertEqual(result["rewriteRounds"], 1)
            # 交付最佳候选；历史候选保留在 Loop 目录内，不复制到交付目录旁
            self.assertNotIn("versionsDirectory", result)
            self.assertTrue((reports / "v1.md").is_file())


if __name__ == "__main__":
    unittest.main()
