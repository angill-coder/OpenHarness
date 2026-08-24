from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.report_loop.core.memory_rubric_provider import MemoryRubricProvider, scope_storage_key
from mcp.report_loop.core.rubric_compiler import compile_rubric


class MemoryRubricCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.memory_root = Path(self.temporary.name)
        self.repository = self.memory_root / "l2b-rubrics" / "personal" / "default"
        self.repository.mkdir(parents=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repository, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repository, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.repository, check=True)
        self.base = json.loads((ROOT / "rubrics" / "v2_rubric_research.json").read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, path: str, scope: str, items: list[dict], scope_value: str | None = None) -> None:
        target = self.repository / path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schemaVersion": 2, "scope": scope, "rubrics": items}
        if scope_value:
            payload["scopeValue"] = scope_value
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _item(item_id: str, description: str) -> dict:
        return {
            "id": item_id,
            "criterionKey": "expression.custom_summary",
            "operation": "add",
            "dimension": "expression",
            "label": "摘要简洁",
            "desc": description,
            "effect": "未满足时表达维度降档",
            "redline": False,
            "status": "active",
            "sourceL1Ids": ["atom-1"],
        }

    def _commit(self) -> None:
        (self.repository / "manifest.json").write_text(json.dumps({
            "schemaVersion": 1,
            "product": "research_insight",
            "version": "v1",
            "versionNumber": 1,
            "parentVersion": "v0",
            "baseRubricVersion": "v2.3",
            "updatedAt": "2026-08-23T00:00:00Z",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        subprocess.run(["git", "add", "--all"], cwd=self.repository, check=True)
        subprocess.run(["git", "commit", "-m", "seed"], cwd=self.repository, check=True, capture_output=True)

    def test_project_overrides_audience_and_core_with_same_stable_id(self) -> None:
        audience = "管理层"
        project = "时长分析"
        self._write("system/rubrics.json", "core", [self._item("MR-SUMMARY", "core")])
        self._write(
            f"audiences/{scope_storage_key(audience)}/rubrics.json",
            "audience",
            [self._item("MR-SUMMARY", "audience")],
            audience,
        )
        self._write(
            f"projects/{scope_storage_key(project)}/rubrics.json",
            "project",
            [self._item("MR-SUMMARY", "project")],
            project,
        )
        self._commit()

        compiled, metadata = compile_rubric(
            self.base,
            provider=MemoryRubricProvider(self.memory_root),
            audience=audience,
            project=project,
        )
        memory_checks = [
            check
            for dimension in compiled["dimensions"]
            for check in dimension["checks"]
            if check["id"] == "MR-SUMMARY"
        ]
        self.assertEqual(len(memory_checks), 1)
        self.assertEqual(memory_checks[0]["desc"], "project")
        self.assertEqual(memory_checks[0]["memory"]["overlays"][-1]["scope"], "project")
        self.assertEqual([item["scope"] for item in metadata["items"]], ["core", "audience", "project"])
        self.assertEqual(metadata["rubricSetVersion"], "v1")
        self.assertTrue(metadata["revision"])

    def test_scope_overlays_extend_then_project_override_one_criterion(self) -> None:
        audience = "管理层"
        project = "时长分析"
        base_key = "structure.s1"
        core = {
            **self._item("MR-CORE-SUMMARY", "core extension"),
            "criterionKey": base_key,
            "operation": "extend",
            "dimension": "structure",
            "requirements": [{"key": "summary.max_lines", "text": "摘要控制在2–3行"}],
        }
        audience_item = {
            **self._item("MR-AUD-SUMMARY", "audience extension"),
            "criterionKey": base_key,
            "operation": "extend",
            "dimension": "structure",
            "requirements": [{"key": "summary.first_item", "text": "第一条先给讨论事项"}],
        }
        project_item = {
            **self._item("MR-PROJECT-SUMMARY", "本项目不设置独立摘要。"),
            "criterionKey": base_key,
            "operation": "override",
            "dimension": "structure",
            "label": "项目开篇方式",
        }
        self._write("system/rubrics.json", "core", [core])
        self._write(f"audiences/{scope_storage_key(audience)}/rubrics.json", "audience", [audience_item], audience)
        self._write(f"projects/{scope_storage_key(project)}/rubrics.json", "project", [project_item], project)
        self._commit()

        compiled, metadata = compile_rubric(
            self.base,
            provider=MemoryRubricProvider(self.memory_root),
            audience=audience,
            project=project,
        )
        summary = next(
            check
            for dimension in compiled["dimensions"]
            for check in dimension["checks"]
            if check["criterionKey"] == base_key
        )
        self.assertEqual(summary["id"], "S1")
        self.assertEqual(summary["label"], "项目开篇方式")
        self.assertEqual(summary["desc"], "本项目不设置独立摘要。")
        self.assertEqual([item["operation"] for item in metadata["items"]], ["extend", "extend", "override"])

    def test_distinct_audience_and_project_criteria_are_both_kept(self) -> None:
        audience = "管理层"
        project = "时长分析"
        audience_item = {**self._item("MR-AUD-DECISION", "讨论项前置"), "criterionKey": "structure.decision_first", "dimension": "structure"}
        project_item = {**self._item("MR-PROJECT-METRIC", "拆解频次和单次时长"), "criterionKey": "insight.duration_decomposition", "dimension": "insight"}
        self._write("system/rubrics.json", "core", [])
        self._write(f"audiences/{scope_storage_key(audience)}/rubrics.json", "audience", [audience_item], audience)
        self._write(f"projects/{scope_storage_key(project)}/rubrics.json", "project", [project_item], project)
        self._commit()
        compiled, _ = compile_rubric(self.base, provider=MemoryRubricProvider(self.memory_root), audience=audience, project=project)
        ids = {check["id"] for dimension in compiled["dimensions"] for check in dimension["checks"]}
        self.assertIn("MR-AUD-DECISION", ids)
        self.assertIn("MR-PROJECT-METRIC", ids)

    def test_project_names_with_the_same_readable_slug_stay_isolated(self) -> None:
        values = ["A/B", "A-B", "A B"]
        self.assertEqual(len({scope_storage_key(value) for value in values}), 3)
        self._write("system/rubrics.json", "core", [])
        for index, value in enumerate(values):
            item = {
                **self._item(f"MR-PROJECT-{index}", f"只适用于 {value}"),
                "criterionKey": f"expression.project_{index}",
            }
            self._write(
                f"projects/{scope_storage_key(value)}/rubrics.json",
                "project",
                [item],
                value,
            )
        self._commit()

        provider = MemoryRubricProvider(self.memory_root)
        for index, value in enumerate(values):
            loaded = provider.load(project=value)
            project_items = [item for item in loaded["items"] if item["scope"] == "project"]
            self.assertEqual([item["id"] for item in project_items], [f"MR-PROJECT-{index}"])

    def test_scope_value_mismatch_fails_closed(self) -> None:
        requested = "A/B"
        self._write("system/rubrics.json", "core", [])
        self._write(
            f"projects/{scope_storage_key(requested)}/rubrics.json",
            "project",
            [self._item("MR-WRONG-PROJECT", "不得串入")],
            "A-B",
        )
        self._commit()

        loaded = MemoryRubricProvider(self.memory_root).load(project=requested)
        self.assertEqual(loaded["status"], "unavailable")
        self.assertEqual(loaded["items"], [])
        self.assertTrue(any("scopeValue mismatch" in warning for warning in loaded["warnings"]))

    def test_personal_dimension_is_conditional_and_reweights_base(self) -> None:
        item = {
            **self._item("MR-PERSONAL-CHANNEL", "交付时附一行可转发摘要"),
            "criterionKey": "personal.forwardable_summary",
            "dimension": "personal",
        }
        self._write("system/rubrics.json", "core", [item])
        self._commit()
        compiled, metadata = compile_rubric(self.base, provider=MemoryRubricProvider(self.memory_root))
        weights = {dimension["name"]: dimension["weight"] for dimension in compiled["dimensions"]}
        self.assertEqual(weights["personal"], 0.10)
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertAlmostEqual(weights["traceability"], 0.252)
        self.assertTrue(metadata["personalActive"])

    def test_memory_cannot_override_a_base_redline(self) -> None:
        item = {
            **self._item("MR-OVERRIDE-T1", "允许无来源结论。"),
            "criterionKey": "traceability.t1",
            "operation": "override",
            "dimension": "traceability",
        }
        self._write("system/rubrics.json", "core", [item])
        self._commit()
        compiled, metadata = compile_rubric(self.base, provider=MemoryRubricProvider(self.memory_root))
        check = next(
            check
            for dimension in compiled["dimensions"]
            for check in dimension["checks"]
            if check["id"] == "T1"
        )
        self.assertNotEqual(check["desc"], "允许无来源结论。")
        self.assertIn("memory_rubric_locked_redline:MR-OVERRIDE-T1:traceability.t1", metadata["warnings"])

    def test_missing_repository_falls_back_to_base(self) -> None:
        missing = self.memory_root / "missing"
        compiled, metadata = compile_rubric(
            self.base,
            provider=MemoryRubricProvider(missing),
        )
        self.assertEqual(metadata["status"], "empty")
        self.assertNotIn("personal", {item["name"] for item in compiled["dimensions"]})
        self.assertEqual(
            sum(len(item["checks"]) for item in compiled["dimensions"]),
            sum(len(item["checks"]) for item in self.base["dimensions"]),
        )


if __name__ == "__main__":
    unittest.main()
