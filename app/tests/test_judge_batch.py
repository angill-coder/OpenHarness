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
from server import _build_judge_prompt, _judge_parallelism  # noqa: E402
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


def build_prompt(_rubric, report, case_context):
    return json.dumps(
        {"report": report, "case_context": case_context},
        ensure_ascii=False,
    )


def extract_json(text):
    return json.loads(text)


class JudgeBatchTest(unittest.TestCase):
    def test_judge_parallel_override_has_no_artificial_cap(self):
        self.assertEqual(_judge_parallelism(200), 200)
        with self.assertRaisesRegex(ValueError, "至少为 1"):
            _judge_parallelism(0)
        with self.assertRaisesRegex(ValueError, "整数"):
            _judge_parallelism(1.5)

    def test_server_prompt_includes_ground_truth_for_traceability(self):
        prompt = _build_judge_prompt(
            RUBRIC,
            "report",
            {
                "case_id": "case-a",
                "input": {"brief": "A"},
                "ground_truth": {"reference_report_text": "事实 A"},
            },
        )
        self.assertIn("ground_truth", prompt)
        self.assertIn("事实 A", prompt)
        self.assertIn("任务信息", prompt)

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

    def test_prompt_context_includes_ground_truth(self):
        prompts = []

        def call_model(prompt):
            prompts.append(json.loads(prompt))
            return json.dumps(
                {
                    "checks": {"Q1": "met", "Q2": "met"},
                    "reasoning": {},
                }
            )

        judge_cases(
            [
                {
                    "case_id": "case-a",
                    "input": {"brief": "A"},
                    "ground_truth": {"secret": "answer"},
                }
            ],
            {"case-a": "report-a"},
            RUBRIC,
            build_prompt,
            call_model,
            extract_json,
        )

        self.assertEqual(
            prompts[0]["case_context"]["ground_truth"],
            {"secret": "answer"},
        )
        self.assertEqual(
            prompts[0]["case_context"]["input"],
            {"brief": "A"},
        )

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

    def test_result_callback_persists_each_success_as_completed(self):
        completed = []
        results = judge_cases(
            [
                {"case_id": "case-a", "input": {}},
                {"case_id": "case-b", "input": {}},
            ],
            {"case-a": "A", "case-b": "B"},
            RUBRIC,
            build_prompt,
            lambda _prompt: json.dumps(
                {
                    "checks": {"Q1": "met", "Q2": "met"},
                    "reasoning": {},
                }
            ),
            extract_json,
            on_result=lambda item: completed.append(
                item["case_id"]
            ),
        )
        self.assertEqual(set(completed), {"case-a", "case-b"})
        self.assertTrue(
            all(item["status"] == "judged" for item in results)
        )


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
        self.assertEqual(state["version_status"], "optimizable")
        self.assertTrue(state["actions"]["advance"]["enabled"])
        self.assertEqual(state["failure_report"], [])
        self.assertNotIn("calib", state)
        self.assertNotIn("check_calib", state)
        record = state["current_eval"][0]
        self.assertIn("check_judge", record)
        self.assertNotIn("human_label", record)
        self.assertNotIn("check_human", record)
        self.assertNotIn("dims_human", record)

    def test_restore_ignores_legacy_human_judge_state(self):
        session = Session(
            "legacy-human-state",
            "生成调研洞察报告",
            "research_insight",
        )
        session.import_data(
            [
                {
                    "case_id": "case-a",
                    "input": {"brief": "A"},
                    "ground_truth": {},
                    "split": "dev",
                }
            ]
        )
        snapshot = session.to_snapshot()
        self.assertNotIn("human_labels", snapshot)

        snapshot["human_labels"] = {
            "v0": {"legacy-user": {"case-a": {"traceability": 5}}}
        }
        session_dir = Path(persist._BASE) / session.id
        (session_dir / "check_labels.jsonl").write_text(
            json.dumps(
                {
                    "version": "v0",
                    "account": "legacy-user",
                    "case_id": "case-a",
                    "checks": {"V1": 1.0},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        restored = Session.restore(snapshot)
        state = restored.view("legacy-user")
        self.assertFalse(hasattr(restored, "human_labels"))
        self.assertFalse(hasattr(restored, "human_checks"))
        self.assertNotIn("human_label", state["current_eval"][0])

    def test_real_judge_failure_proposes_pending_version(self):
        session = Session(
            "judge-proposal",
            "生成调研洞察报告",
            "research_insight",
        )
        session.import_data(
            [
                {
                    "case_id": "case-a",
                    "input": {"brief": "A"},
                    "ground_truth": {},
                }
            ]
        )
        session.import_output("case-a", "report A")
        checks = {
            check["id"]: "met"
            for dimension in session.rubric["dimensions"]
            for check in dimension.get("checks", [])
        }
        checks["T2"] = "partial"
        state = session.set_judge_checks_batch(
            {
                "case-a": {
                    "checks": checks,
                    "reasoning": {"T2": "unsupported"},
                }
            }
        )
        self.assertEqual(
            state["failure_report"][0]["pattern_id"],
            "trace_fabrication",
        )

        # 本用例只验证真实 failure → optimizer proposal 的状态流，
        # 因此显式模拟一个尚未在基线启用的目标开关。
        session._current()["skill"].directives()[
            "verify_no_fabrication"
        ] = False
        state = session.advance()

        self.assertEqual(state["advance_result"]["status"], "proposed")
        self.assertEqual(state["current_version"], "v1")
        self.assertEqual(state["version_status"], "awaiting_generation")
        self.assertFalse(state["actions"]["advance"]["enabled"])
        self.assertNotIn(
            "v1",
            [item["version"] for item in state["curve"]],
        )

    def test_report_change_invalidates_completed_judge(self):
        session = Session(
            "judge-invalidate",
            "生成调研洞察报告",
            "research_insight",
        )
        session.import_data(
            [{"case_id": "case-a", "input": {"brief": "A"}}]
        )
        session.import_output("case-a", "report A")
        checks = {
            check["id"]: "met"
            for dimension in session.rubric["dimensions"]
            for check in dimension.get("checks", [])
        }
        session.set_judge_checks_batch(
            {"case-a": {"checks": checks}}
        )

        state = session.import_output("case-a", "report B")

        self.assertFalse(state["judge_progress"]["complete"])
        self.assertEqual(
            state["judge_progress"]["pending_judge_case_ids"],
            ["case-a"],
        )
        self.assertTrue(state["actions"]["run_judge"]["enabled"])

    def test_partial_judge_results_resume_after_restore(self):
        session = Session(
            "judge-resume",
            "生成调研洞察报告",
            "research_insight",
        )
        session.import_data(
            [
                {"case_id": "case-a", "input": {"brief": "A"}},
                {"case_id": "case-b", "input": {"brief": "B"}},
            ]
        )
        session.import_output("case-a", "report A")
        session.import_output("case-b", "report B")
        checks = {
            check["id"]: "met"
            for dimension in session.rubric["dimensions"]
            for check in dimension.get("checks", [])
        }
        session.set_judge_checks_batch(
            {"case-a": {"checks": checks}},
            evaluate_now=False,
        )

        restored = Session.restore(
            persist.load_snapshot("judge-resume")
        )
        state = restored.view()

        self.assertEqual(state["judge_progress"]["judged_cases"], 1)
        self.assertEqual(
            state["judge_progress"]["pending_judge_case_ids"],
            ["case-b"],
        )
        self.assertTrue(state["actions"]["run_judge"]["enabled"])

    def test_unmapped_failed_check_blocks_instead_of_converging(self):
        session = Session(
            "judge-unmapped",
            "生成调研洞察报告",
            "research_insight",
        )
        session.import_data(
            [{"case_id": "case-a", "input": {"brief": "A"}}]
        )
        session.import_output("case-a", "report A")
        checks = {
            check["id"]: "met"
            for dimension in session.rubric["dimensions"]
            for check in dimension.get("checks", [])
        }
        checks["T2"] = "miss"
        for dimension in session.rubric["dimensions"]:
            for check in dimension.get("checks", []):
                if check["id"] == "T2":
                    check.pop("optimizer")
        state = session.set_judge_checks_batch(
            {"case-a": {"checks": checks}}
        )

        self.assertEqual(state["version_status"], "blocked")
        self.assertIn("T2", state["failure_mapping_error"])
        self.assertFalse(state["actions"]["advance"]["enabled"])

    def test_unfixable_real_failure_is_blocked_not_converged(self):
        session = Session(
            "judge-no-change",
            "生成调研洞察报告",
            "research_insight",
        )
        session.import_data(
            [{"case_id": "case-a", "input": {"brief": "A"}}]
        )
        session.import_output("case-a", "report A")
        session._current()["skill"].directives()[
            "verify_no_fabrication"
        ] = True
        checks = {
            check["id"]: "met"
            for dimension in session.rubric["dimensions"]
            for check in dimension.get("checks", [])
        }
        checks["T2"] = "partial"
        session.set_judge_checks_batch(
            {"case-a": {"checks": checks}}
        )

        state = session.advance()

        self.assertEqual(state["advance_result"]["status"], "blocked")
        self.assertEqual(
            state["advance_result"]["code"],
            "optimizer_no_applicable_change",
        )


if __name__ == "__main__":
    unittest.main()
