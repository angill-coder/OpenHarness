from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.report_loop.core.rubric_compiler import compile_rubric


class MemoryRubricCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = json.loads((ROOT / "rubrics" / "v2_rubric_research.json").read_text(encoding="utf-8"))
        self.snapshot = {
            "status": "loaded", "revision": "abc", "rubricSetVersion": "v3", "warnings": [],
            "items": [
                {"id": "MR-SUMMARY", "statement": "摘要控制在 2–3 行。", "status": "active", "sourceL1Ids": ["atom-1"], "scope": "core"},
                {"id": "MR-DECISION", "statement": "开头先给讨论事项。", "status": "active", "sourceL1Ids": ["atom-2"], "scope": "audience", "scopeValue": "管理委员会"},
            ],
        }

    def test_additional_and_interpret_apply_without_mutating_base(self) -> None:
        original = copy.deepcopy(self.base)
        plan = {"schemaVersion": 1, "status": "resolved", "promptVersion": "v1", "decisions": [
            {"memoryId": "MR-SUMMARY", "mode": "interpret", "dimension": "structure", "targetCheckId": "S1", "judgeText": "摘要最多 3 行", "reason": "场景解释"},
            {"memoryId": "MR-DECISION", "mode": "additional", "dimension": "structure", "targetCheckId": None, "judgeText": "开头明确列出讨论事项", "reason": "新增检查"},
        ]}
        compiled, metadata = compile_rubric(self.base, memory_snapshot=self.snapshot, resolution_plan=plan)
        self.assertEqual(self.base, original)
        structure = next(item for item in compiled["dimensions"] if item["name"] == "structure")
        summary = next(item for item in structure["checks"] if item["id"] == "S1")
        self.assertIn("本轮评判以以下场景解释为准", summary["desc"])
        self.assertIn("摘要最多 3 行", summary["desc"])
        extra = next(item for item in structure["checks"] if item["id"].startswith("M-"))
        self.assertEqual(extra["memory"]["memoryId"], "MR-DECISION")
        self.assertEqual(metadata["appliedMemoryRubricIds"], ["MR-SUMMARY", "MR-DECISION"])
        self.assertNotIn("personal", {item["name"] for item in compiled["dimensions"]})
        self.assertEqual([item["weight"] for item in compiled["dimensions"]], [item["weight"] for item in self.base["dimensions"]])

    def test_ignore_and_failed_resolution_fall_back_to_base(self) -> None:
        for plan in [
            {"schemaVersion": 1, "status": "resolved", "promptVersion": "v1", "decisions": [
                {"memoryId": item["id"], "mode": "ignore", "dimension": None, "targetCheckId": None, "judgeText": "", "reason": "duplicate"}
                for item in self.snapshot["items"]
            ]},
            {"schemaVersion": 1, "status": "failed_base_only", "promptVersion": "v1", "decisions": [], "error": "timeout"},
        ]:
            compiled, metadata = compile_rubric(self.base, memory_snapshot=self.snapshot, resolution_plan=plan)
            expected = copy.deepcopy(self.base)
            expected["baseVersion"] = self.base["version"]
            expected["memoryResolution"] = compiled["memoryResolution"]
            self.assertEqual(compiled, expected)
            self.assertEqual(metadata["appliedMemoryRubricIds"], [])


if __name__ == "__main__":
    unittest.main()
