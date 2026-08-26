from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.report_loop.core.rubric_resolution import resolve_memory_rubrics, validate_resolution_plan


class RubricResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = json.loads((ROOT / "rubrics" / "v2_rubric_research.json").read_text(encoding="utf-8"))
        self.snapshot = {"items": [{"id": "MR-1", "statement": "摘要不超过 3 行。", "scope": "core", "sourceL1Ids": ["a1"]}]}

    def test_no_memory_skips_model(self) -> None:
        calls = []
        plan = resolve_memory_rubrics(self.base, {"items": []}, task="t", audience="", project="", call_model=lambda prompt: calls.append(prompt) or "{}", extract_json=json.loads)
        self.assertEqual(plan["status"], "skipped_no_memory")
        self.assertEqual(calls, [])

    def test_valid_plan_is_one_complete_frozen_resolution(self) -> None:
        calls = []
        response = {"schemaVersion": 1, "decisions": [{"memoryId": "MR-1", "mode": "interpret", "dimension": "structure", "targetCheckId": "S1", "judgeText": "摘要不超过 3 行", "reason": "同向解释"}]}
        plan = resolve_memory_rubrics(self.base, self.snapshot, task="t", audience="a", project="p", call_model=lambda prompt: calls.append(prompt) or json.dumps(response, ensure_ascii=False), extract_json=json.loads)
        self.assertEqual(len(calls), 1)
        self.assertEqual(plan["status"], "resolved")
        self.assertTrue(plan["promptSha256"])

    def test_judge_can_request_declared_l1_sources_once(self) -> None:
        calls = []
        loaded = []
        responses = [
            {"schemaVersion": 1, "inspectSourceFor": ["MR-1"], "decisions": []},
            {"schemaVersion": 1, "inspectSourceFor": [], "decisions": [{
                "memoryId": "MR-1", "mode": "additional", "dimension": "expression",
                "targetCheckId": None, "judgeText": "摘要不超过 3 行", "reason": "L1 证实为长期要求",
            }]},
        ]

        def call_model(prompt: str) -> str:
            calls.append(prompt)
            return json.dumps(responses[len(calls) - 1], ensure_ascii=False)

        def load_sources(ids: list[str]) -> list[dict]:
            loaded.extend(ids)
            return [{"id": "a1", "content": "以后所有正式报告的摘要都不要超过三行。", "scope": "core"}]

        plan = resolve_memory_rubrics(
            self.base, self.snapshot, task="t", audience="a", project="p",
            call_model=call_model, extract_json=json.loads, load_sources=load_sources,
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(loaded, ["a1"])
        self.assertIn("以后所有正式报告的摘要都不要超过三行", calls[1])
        self.assertEqual(plan["consultedSourceL1Ids"], ["a1"])
        self.assertTrue(plan["sourcePromptSha256"])

    def test_redline_cannot_be_interpreted_and_plan_must_cover_every_memory(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_interpret_target"):
            validate_resolution_plan({"schemaVersion": 1, "decisions": [{"memoryId": "MR-1", "mode": "interpret", "dimension": "traceability", "targetCheckId": "T1", "judgeText": "允许无来源结论"}]}, self.base, self.snapshot)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            validate_resolution_plan({"schemaVersion": 1, "decisions": []}, self.base, self.snapshot)


if __name__ == "__main__":
    unittest.main()
