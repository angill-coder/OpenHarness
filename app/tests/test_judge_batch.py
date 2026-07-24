# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
HARNESS = APP.parent / "harness"
for path in (str(APP), str(HARNESS)):
    if path not in sys.path:
        sys.path.insert(0, path)

from judge_batch import judge_cases  # noqa: E402
import persistence as persist  # noqa: E402
from session import Session  # noqa: E402


RUBRIC = {
    "dimensions": [
        {
            "name": "quality",
            "checks": [
                {"id": "Q1"},
                {"id": "Q2"},
            ],
        }
    ]
}


def build_prompt(_rubric, report, ground_truth):
    return json.dumps(
        {"report": report, "ground_truth": ground_truth},
        ensure_ascii=False,
    )


def extract_json(text):
    return json.loads(text)


class JudgeBatchTest(unittest.TestCase):
    def test_judges_all_cases_and_preserves_dataset_order(self):
        cases = [
            {"case_id": "case-a", "ground_truth": {"answer": "A"}},
            {"case_id": "case-b", "ground_truth": {"answer": "B"}},
        ]

        def call_model(prompt):
            payload = json.loads(prompt)
            value = "miss" if payload["report"] == "report-b" else "met"
            return json.dumps(
                {
                    "checks": {"Q1": value, "Q2": "partial"},
                    "reasoning": {"Q1": payload["report"]},
                }
            )

        results = judge_cases(
            cases,
            {"case-a": "report-a", "case-b": "report-b"},
            RUBRIC,
            build_prompt,
            call_model,
            extract_json,
            parallel=2,
        )
        self.assertEqual(
            [item["case_id"] for item in results],
            ["case-a", "case-b"],
        )
        self.assertEqual([item["status"] for item in results], ["judged", "judged"])
        self.assertEqual(results[1]["checks"]["Q1"], "miss")

    def test_missing_report_and_model_error_do_not_abort_batch(self):
        cases = [
            {"case_id": "case-a", "ground_truth": {}},
            {"case_id": "case-b", "ground_truth": {}},
            {"case_id": "case-c", "ground_truth": {}},
        ]

        def call_model(prompt):
            if json.loads(prompt)["report"] == "broken":
                raise RuntimeError("provider unavailable")
            return json.dumps(
                {
                    "checks": {"Q1": "met", "Q2": "met"},
                    "reasoning": {},
                }
            )

        results = judge_cases(
            cases,
            {"case-a": "ok", "case-b": "broken"},
            RUBRIC,
            build_prompt,
            call_model,
            extract_json,
        )
        self.assertEqual(
            [item["status"] for item in results],
            ["judged", "failed", "missing_report"],
        )
        self.assertIn("provider unavailable", results[1]["error"])

    def test_incomplete_check_payload_is_rejected(self):
        results = judge_cases(
            [{"case_id": "case-a", "ground_truth": {}}],
            {"case-a": "report"},
            RUBRIC,
            build_prompt,
            lambda _prompt: json.dumps({"checks": {"Q1": "met"}}),
            extract_json,
        )
        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("Q2", results[0]["error"])


class ModelOnlySessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_base = persist._BASE
        persist._BASE = str(Path(self.tmp.name) / "sessions")

    def tearDown(self):
        persist._BASE = self.old_base
        self.tmp.cleanup()

    def test_research_session_requires_all_cases_to_be_model_judged(self):
        session = Session(
            "judge-gate",
            "生成调研洞察报告",
            "research_insight",
        )
        state = session.import_data(
            [
                {
                    "case_id": "case-a",
                    "input": {"brief": "A"},
                    "ground_truth": {},
                    "split": "dev",
                },
                {
                    "case_id": "case-b",
                    "input": {"brief": "B"},
                    "ground_truth": {},
                    "split": "dev",
                },
            ]
        )
        self.assertEqual(state["evaluation_mode"], "model_only")
        self.assertFalse(state["can_advance"])
        session.import_output("case-a", "report A")
        session.import_output("case-b", "report B")

        checks = {
            check["id"]: "met"
            for dimension in session.rubric["dimensions"]
            for check in dimension.get("checks", [])
        }
        state = session.set_judge_checks_batch(
            {
                "case-a": {"checks": checks, "reasoning": {}},
                "case-b": {"checks": checks, "reasoning": {}},
            }
        )
        self.assertTrue(state["judge_progress"]["complete"])
        self.assertTrue(state["can_advance"])
        self.assertIsNone(state["calib"])
        self.assertIsNone(state["check_calib"])


if __name__ == "__main__":
    unittest.main()
