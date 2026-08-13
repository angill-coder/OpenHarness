# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP = Path(__file__).resolve().parents[1]
HARNESS = APP.parent / "harness"
for path in (str(APP), str(HARNESS)):
    if path not in sys.path:
        sys.path.insert(0, path)

from judge_batch import (  # noqa: E402
    JUDGE_STRATEGY_PER_DIMENSION,
    JUDGE_STRATEGY_SIX_AGENT,
    _delivery_constraints_context,
    judge_cases,
    normalize_judge_strategy,
)
import persistence as persist  # noqa: E402
import generator as generator_mod  # noqa: E402
from server import (  # noqa: E402
    _build_judge_prompt,
    _judge_parallelism,
    _judge_summary,
    _load_structured_data,
    _llm_selection,
)
from session import Session  # noqa: E402


def _load_v2_rubric():
    return json.loads(
        (HARNESS / "artifacts" / "v2_rubric_research.json").read_text(
            encoding="utf-8"
        )
    )


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
    def test_length_constraint_falls_back_to_user_prompt(self):
        context = _delivery_constraints_context(
            {
                "turns": [
                    {"round": 0, "prompt": "任务"},
                    {
                        "round": 1,
                        "prompt": "4. 报告篇幅：控制在4页以内。",
                    },
                ]
            }
        )
        self.assertEqual(context["max_pages"], 4)
        self.assertEqual(context["max_chars"], 4000)
        self.assertEqual(context["chars_per_page"], 1000)

    def test_judge_and_optimizer_default_to_workbuddy_opus(self):
        for purpose in ("judge", "optimizer"):
            backend, model, effort = _llm_selection({}, purpose)
            self.assertEqual(backend, "workbuddy")
            self.assertEqual(model, "claude-opus-4.8")
            self.assertIsNone(effort)

    def test_api_backend_preserves_per_request_model(self):
        for purpose in ("judge", "optimizer"):
            backend, model, effort = _llm_selection(
                {
                    "llm_backend": "api",
                    "llm_model": "custom-provider-model",
                },
                purpose,
            )
            self.assertEqual(backend, "api")
            self.assertEqual(model, "custom-provider-model")
            self.assertIsNone(effort)

    def test_codex_backend_defaults_to_gpt56_sol_and_medium(self):
        for purpose in ("judge", "optimizer"):
            backend, model, effort = _llm_selection(
                {"llm_backend": "codex"},
                purpose,
            )
            self.assertEqual(backend, "codex")
            self.assertEqual(model, "gpt-5.6-sol")
            self.assertEqual(effort, "medium")

    def test_codex_backend_preserves_selected_effort(self):
        backend, model, effort = _llm_selection(
            {
                "llm_backend": "codex",
                "llm_model": "gpt-5.6-sol",
                "llm_reasoning_effort": "high",
            },
            "judge",
        )
        self.assertEqual((backend, model, effort), (
            "codex", "gpt-5.6-sol", "high",
        ))

    def test_judge_parallel_override_has_no_artificial_cap(self):
        self.assertEqual(_judge_parallelism(200), 200)
        with self.assertRaisesRegex(ValueError, "至少为 1"):
            _judge_parallelism(0)
        with self.assertRaisesRegex(ValueError, "整数"):
            _judge_parallelism(1.5)

    def test_server_prompt_discards_human_report(self):
        prompt = _build_judge_prompt(
            RUBRIC,
            "report",
            {
                "case_id": "case-a",
                "background": {"input": {"brief": "A"}},
                "human_report": {"human_report_text": "事实 A"},
            },
        )
        self.assertNotIn("Human Report", prompt)
        self.assertNotIn("事实 A", prompt)
        self.assertIn('"Q1": "met"', prompt)

    def test_server_prompt_includes_length_constraint_and_measured_stats(self):
        prompt = _build_judge_prompt(
            RUBRIC,
            "报告正文",
            {
                "case_id": "case-a",
                "delivery_constraints": {
                    "max_pages": 2,
                    "max_chars": 2000,
                },
                "report_stats": {
                    "visible_chars": 2150,
                    "estimated_pages_at_1000_chars": 2.15,
                },
            },
        )
        self.assertIn("用户确认的交付篇幅", prompt)
        self.assertIn('"max_chars": 2000', prompt)
        self.assertIn("平台计算的报告长度", prompt)
        self.assertIn('"visible_chars": 2150', prompt)

    def test_judges_all_cases_and_preserves_dataset_order(self):
        cases = [
            {"case_id": "case-a", "human_report": {"answer": "A"}},
            {"case_id": "case-b", "human_report": {"answer": "B"}},
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
        trace = results[1]["judge_trace"]
        self.assertEqual(trace["status"], "completed")
        self.assertEqual(trace["strategy"], "single_call")
        self.assertEqual(len(trace["calls"]), 1)
        self.assertEqual(trace["calls"][0]["status"], "completed")
        self.assertEqual(len(trace["calls"][0]["promptSha256"]), 64)
        self.assertIn('"checks"', trace["calls"][0]["response"])

    def test_prompt_context_excludes_human_report(self):
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
                    "human_report": {"secret": "answer"},
                }
            ],
            {"case-a": "report-a"},
            RUBRIC,
            build_prompt,
            call_model,
            extract_json,
        )

        self.assertNotIn("human_report", prompts[0]["case_context"])
        self.assertEqual(
            prompts[0]["case_context"]["background"]["input"],
            {"brief": "A"},
        )

    def test_single_call_receives_all_available_context(self):
        prompts = []
        evidence = {
            "schema": "openharness-structured-data/v1",
            "case_id": "case-a",
            "items": [{"id": "EV-001"}],
            "unresolved": [],
        }

        def call_model(prompt):
            prompts.append(json.loads(prompt))
            return json.dumps(
                {
                    "checks": {"Q1": "met", "Q2": "met"},
                    "reasoning": {},
                }
            )

        results = judge_cases(
            [
                {
                    "case_id": "case-a",
                    "turns": [
                        {"round": 0, "prompt": "任务"},
                        {
                            "round": 1,
                            "prompt": "背景\n4. 报告篇幅：控制在3页以内。",
                        },
                    ],
                    "human_report": {"human_report_text": "HR"},
                    "delivery_constraints": {
                        "max_pages": 3,
                        "max_chars": 3000,
                        "chars_per_page": 1000,
                    },
                    "structured_data": evidence,
                }
            ],
            {"case-a": "report-a"},
            RUBRIC,
            build_prompt,
            call_model,
            extract_json,
            strategy="single_call",
        )

        self.assertEqual(len(prompts), 1)
        self.assertEqual(
            set(prompts[0]["case_context"]),
            {
                "case_id",
                "background",
                "delivery_constraints",
                "report_stats",
                "structured_data",
            },
        )
        self.assertEqual(
            prompts[0]["case_context"]["delivery_constraints"]["max_chars"],
            3000,
        )
        self.assertIn(
            "控制在3页以内",
            prompts[0]["case_context"]["delivery_constraints"]["user_prompt"],
        )
        self.assertGreater(
            prompts[0]["case_context"]["report_stats"]["visible_chars"],
            0,
        )
        self.assertEqual(
            results[0]["judge_meta"],
            {"strategy": "single_call", "model_calls": 1},
        )

    def test_missing_report_and_model_error_do_not_abort_batch(self):
        cases = [
            {"case_id": "case-a", "human_report": {}},
            {"case_id": "case-b", "human_report": {}},
            {"case_id": "case-c", "human_report": {}},
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
            [{"case_id": "case-a", "human_report": {}}],
            {"case-a": "report"},
            RUBRIC,
            build_prompt,
            lambda _prompt: json.dumps({"checks": {"Q1": "met"}}),
            extract_json,
        )
        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("Q2", results[0]["error"])

    def test_judge_retries_three_times_then_succeeds(self):
        attempts = []

        def call_model(_prompt):
            attempts.append(1)
            if len(attempts) <= 3:
                raise RuntimeError("temporary failure")
            return json.dumps(
                {
                    "checks": {"Q1": "met", "Q2": "met"},
                    "reasoning": {},
                }
            )

        results = judge_cases(
            [{"case_id": "case-a"}],
            {"case-a": "report"},
            RUBRIC,
            build_prompt,
            call_model,
            extract_json,
        )

        self.assertEqual(len(attempts), 4)
        self.assertEqual(results[0]["status"], "judged")
        self.assertEqual(results[0]["judge_meta"]["retries"], 3)
        self.assertEqual(
            [call["attempt"] for call in results[0]["judge_trace"]["calls"]],
            [1, 2, 3, 4],
        )

    def test_judge_stops_after_three_retries(self):
        attempts = []

        def call_model(_prompt):
            attempts.append(1)
            raise RuntimeError("provider unavailable")

        results = judge_cases(
            [{"case_id": "case-a"}],
            {"case-a": "report"},
            RUBRIC,
            build_prompt,
            call_model,
            extract_json,
        )

        self.assertEqual(len(attempts), 4)
        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("已重试 3 次", results[0]["error"])

    def test_invalid_schema_is_retried_in_place(self):
        attempts = []

        def call_model(_prompt):
            attempts.append(1)
            if len(attempts) == 1:
                return json.dumps({"reasoning": {"Q1": "missing checks"}})
            return json.dumps(
                {
                    "checks": {"Q1": "met", "Q2": "met"},
                    "reasoning": {},
                }
            )

        results = judge_cases(
            [{"case_id": "case-a"}],
            {"case-a": "report"},
            RUBRIC,
            build_prompt,
            call_model,
            extract_json,
        )

        self.assertEqual(len(attempts), 2)
        self.assertEqual(results[0]["status"], "judged")
        self.assertEqual(results[0]["judge_meta"]["retries"], 1)

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

    def test_per_dimension_routes_only_allowed_context(self):
        rubric = {
            "dimensions": [
                {"name": "traceability", "checks": [{"id": "T1"}]},
                {"name": "structure", "checks": [{"id": "S1"}]},
                {"name": "narrative", "checks": [{"id": "N1"}]},
                {"name": "insight", "checks": [{"id": "I1"}]},
                {"name": "coverage", "checks": [{"id": "V1"}]},
                {"name": "expression", "checks": [{"id": "E1"}]},
            ]
        }
        prompts = {}

        def dimension_prompt(current_rubric, report, case_context):
            dimension = current_rubric["dimensions"][0]
            return json.dumps(
                {
                    "dimension": dimension["name"],
                    "ids": [item["id"] for item in dimension["checks"]],
                    "report": report,
                    "case_context": case_context,
                },
                ensure_ascii=False,
            )

        def call_model(prompt):
            payload = json.loads(prompt)
            prompts[payload["dimension"]] = payload["case_context"]
            check_id = payload["ids"][0]
            return json.dumps(
                {
                    "checks": {check_id: "met"},
                    "reasoning": {check_id: payload["dimension"]},
                }
            )

        results = judge_cases(
            [
                {
                    "case_id": "case-a",
                    "turns": [
                        {"round": 0, "prompt": "任务"},
                        {
                            "round": 1,
                            "prompt": "背景\n4. 报告篇幅：控制在2页以内。",
                        },
                    ],
                    "human_report": {"human_report_text": "HR"},
                    "delivery_constraints": {
                        "max_pages": 2,
                        "max_chars": 2000,
                        "chars_per_page": 1000,
                    },
                    "structured_data": {
                        "schema": "openharness-structured-data/v1",
                        "case_id": "case-a",
                        "items": [{"id": "EV-001"}],
                        "unresolved": [],
                    },
                }
            ],
            {"case-a": "报告"},
            rubric,
            dimension_prompt,
            call_model,
            extract_json,
            strategy=JUDGE_STRATEGY_PER_DIMENSION,
        )

        self.assertEqual(
            set(prompts["traceability"]),
            {"case_id", "background", "structured_data"},
        )
        self.assertEqual(
            set(prompts["insight"]),
            {"case_id", "background", "structured_data"},
        )
        self.assertEqual(
            set(prompts["coverage"]),
            {
                "case_id",
                "background",
                "structured_data",
            },
        )
        for dimension in ("structure", "narrative"):
            self.assertEqual(set(prompts[dimension]), {"case_id"})
        self.assertEqual(
            set(prompts["expression"]),
            {"case_id", "delivery_constraints", "report_stats"},
        )
        self.assertEqual(
            prompts["expression"]["delivery_constraints"]["max_chars"],
            2000,
        )
        self.assertIn(
            "控制在2页以内",
            prompts["expression"]["delivery_constraints"]["user_prompt"],
        )
        self.assertEqual(
            results[0]["checks"],
            {
                "T1": "met",
                "S1": "met",
                "N1": "met",
                "I1": "met",
                "V1": "met",
                "E1": "met",
            },
        )
        self.assertEqual(
            results[0]["judge_meta"],
            {"strategy": "per_dimension", "model_calls": 6},
        )

    def test_per_dimension_failure_preserves_successful_dimensions(self):
        rubric = {
            "dimensions": [
                {"name": "structure", "checks": [{"id": "S1"}]},
                {"name": "narrative", "checks": [{"id": "N1"}]},
            ]
        }

        def dimension_prompt(current_rubric, _report, _context):
            return current_rubric["dimensions"][0]["name"]

        def call_model(prompt):
            if prompt == "narrative":
                raise RuntimeError("provider unavailable")
            return json.dumps(
                {"checks": {"S1": "met"}, "reasoning": {}}
            )

        results = judge_cases(
            [{"case_id": "case-a"}],
            {"case-a": "report"},
            rubric,
            dimension_prompt,
            call_model,
            extract_json,
            strategy=JUDGE_STRATEGY_PER_DIMENSION,
        )
        self.assertEqual(results[0]["status"], "partial")
        self.assertEqual(results[0]["checks"], {"S1": "met"})
        self.assertIn("narrative", results[0]["error"])

    def test_per_dimension_resume_only_runs_missing_dimensions(self):
        rubric = {
            "dimensions": [
                {"name": "structure", "checks": [{"id": "S1"}]},
                {"name": "narrative", "checks": [{"id": "N1"}]},
            ]
        }
        calls = []

        def dimension_prompt(current_rubric, _report, _context):
            dimension = current_rubric["dimensions"][0]
            return json.dumps(
                {
                    "name": dimension["name"],
                    "check_id": dimension["checks"][0]["id"],
                }
            )

        def call_model(prompt):
            payload = json.loads(prompt)
            calls.append(payload["name"])
            return json.dumps(
                {
                    "checks": {payload["check_id"]: "met"},
                    "reasoning": {},
                }
            )

        results = judge_cases(
            [{"case_id": "case-a"}],
            {"case-a": "report"},
            rubric,
            dimension_prompt,
            call_model,
            extract_json,
            strategy=JUDGE_STRATEGY_PER_DIMENSION,
            existing_judgments={
                "case-a": {
                    "checks": {"S1": "met"},
                    "reasoning": {"S1": "preserved"},
                }
            },
        )

        self.assertEqual(calls, ["narrative"])
        self.assertEqual(results[0]["status"], "judged")
        self.assertEqual(
            results[0]["checks"],
            {"S1": "met", "N1": "met"},
        )
        self.assertEqual(results[0]["reasoning"]["S1"], "preserved")

    def test_per_dimension_call_count_follows_rubric(self):
        rubric = {
            "dimensions": [
                {"name": "structure", "checks": [{"id": "S1"}]},
                {"name": "narrative", "checks": [{"id": "N1"}]},
                {"name": "empty", "checks": []},
            ]
        }
        calls = []

        def dimension_prompt(current_rubric, _report, _context):
            dimension = current_rubric["dimensions"][0]
            return json.dumps(
                {
                    "name": dimension["name"],
                    "check_id": dimension["checks"][0]["id"],
                }
            )

        def call_model(prompt):
            payload = json.loads(prompt)
            calls.append(payload["name"])
            return json.dumps(
                {
                    "checks": {payload["check_id"]: "met"},
                    "reasoning": {},
                }
            )

        results = judge_cases(
            [{"case_id": "case-a"}],
            {"case-a": "report"},
            rubric,
            dimension_prompt,
            call_model,
            extract_json,
            strategy=JUDGE_STRATEGY_PER_DIMENSION,
        )

        self.assertEqual(calls, ["structure", "narrative"])
        self.assertEqual(results[0]["judge_meta"]["model_calls"], 2)
        summary = _judge_summary(
            results,
            parallel=5,
            strategy=JUDGE_STRATEGY_PER_DIMENSION,
            dimension_count=2,
        )
        self.assertEqual(summary["model_calls_per_case"], 2)

    def test_structured_data_is_loaded_from_case_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "data.json"
            dataset.write_text("{}", encoding="utf-8")
            case_dir = root / "collection" / "case-a"
            source_dir = case_dir / "source"
            source_dir.mkdir(parents=True)
            payload = {
                "schema": "openharness-structured-data/v1",
                "case_id": "case-a",
                "items": [{"id": "EV-001"}],
                "unresolved": [],
            }
            (case_dir / "structured_data.json").write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            prepared = _load_structured_data(
                [
                    {
                        "case_id": "case-a",
                        "input_files": [
                            {
                                "source": "./collection/case-a/source",
                                "target": "materials",
                            }
                        ],
                    }
                ],
                dataset,
            )
        self.assertEqual(prepared[0]["structured_data"], payload)

    def test_structured_data_prefers_explicit_input_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "data.json"
            dataset.write_text("{}", encoding="utf-8")
            case_dir = root / "collection" / "case-a"
            source_dir = case_dir / "source"
            source_dir.mkdir(parents=True)
            payload = {
                "schema": "openharness-structured-data/v1",
                "case_id": "case-a",
                "items": [{"id": "EV-001"}],
                "unresolved": [],
            }
            structured = case_dir / "structured_data.json"
            structured.write_text(json.dumps(payload), encoding="utf-8")

            prepared = _load_structured_data(
                [{
                    "case_id": "case-a",
                    "input_files": [
                        {"source": "./collection/case-a/structured_data.json"},
                        {"source": "./collection/case-a/source/material.xlsx"},
                    ],
                }],
                dataset,
            )

        self.assertEqual(prepared[0]["structured_data"], payload)

    def test_missing_structured_data_fails_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "data.json"
            dataset.write_text("{}", encoding="utf-8")
            (root / "case-a" / "source").mkdir(parents=True)
            with self.assertRaisesRegex(
                ValueError,
                "Structured Data 预检失败",
            ):
                _load_structured_data(
                    [
                        {
                            "case_id": "case-a",
                            "input_files": [
                                {"source": "./case-a/source"}
                            ],
                        }
                    ],
                    dataset,
                )

    def test_invalid_judge_strategy_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_judge_strategy("unknown")

    def test_legacy_six_agent_name_maps_to_per_dimension(self):
        self.assertEqual(
            normalize_judge_strategy(JUDGE_STRATEGY_SIX_AGENT),
            JUDGE_STRATEGY_PER_DIMENSION,
        )


class ModelOnlySessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_base = persist._BASE
        persist._BASE = str(Path(self.tmp.name) / "sessions")

    def tearDown(self):
        persist._BASE = self.old_base
        self.tmp.cleanup()

    def test_direct_judgment_preserves_decimals_across_restore(self):
        session = Session(
            "judge-decimal-restore",
            "生成调研洞察报告",
            "research_insight",
        )
        session.import_data(
            [
                {
                    "case_id": "case-a",
                    "input": {"brief": "A"},
                    "human_report": {},
                    "split": "dev",
                }
            ]
        )

        state = session.import_judgment(
            "case-a",
            {
                "traceability": 4.2561,
                "structure": 3.3334,
            },
        )

        self.assertEqual(
            state["current_eval"][0]["scores"]["traceability"],
            4.256,
        )
        self.assertEqual(
            state["current_eval"][0]["scores"]["structure"],
            3.333,
        )

        restored = Session.restore(
            persist.load_snapshot("judge-decimal-restore")
        )
        restored_scores = restored.view()["current_eval"][0]["scores"]
        self.assertEqual(restored_scores["traceability"], 4.256)
        self.assertEqual(restored_scores["structure"], 3.333)

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
                    "human_report": {},
                    "split": "dev",
                },
                {
                    "case_id": "case-b",
                    "input": {"brief": "B"},
                    "human_report": {},
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
                    "human_report": {},
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
        with mock.patch.object(
            generator_mod,
            "_build_rubric_research",
            side_effect=_load_v2_rubric,
        ):
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
                    "human_report": {},
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
            "trace_faithfulness",
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

    def test_ready_report_can_be_judged_before_all_reports_exist(self):
        session = Session(
            "judge-ready-partial",
            "生成调研洞察报告",
            "research_insight",
        )
        session.import_data(
            [
                {"case_id": "case-a", "input": {"brief": "A"}},
                {"case_id": "case-b", "input": {"brief": "B"}},
            ]
        )
        state = session.import_output("case-a", "report A")

        self.assertEqual(
            state["judge_progress"]["judgeable_case_ids"],
            ["case-a"],
        )
        self.assertTrue(state["actions"]["run_judge"]["enabled"])
        self.assertFalse(state["actions"]["advance"]["enabled"])

    def test_experiment_user_round_trips_through_snapshot(self):
        session = Session(
            "user-round-trip",
            "生成调研洞察报告",
            "research_insight",
            experiment_user="Zoe",
        )
        self.assertEqual(session.view()["experiment_user"], "Zoe")
        restored = Session.restore(session.to_snapshot())
        self.assertEqual(restored.view()["experiment_user"], "Zoe")

if __name__ == "__main__":
    unittest.main()
