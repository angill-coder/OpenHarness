# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from run_experiment_loop import (  # noqa: E402
    ExperimentLoop,
    _safe_session_id,
    plan_next_action,
)


def _state(
    *,
    stopped=False,
    missing_reports=None,
    pending_judge=None,
    judge_complete=False,
    advance=True,
    no_improvement_streak=0,
):
    return {
        "current_version": "v2",
        "optimizer_stop": {
            "stopped": stopped,
            "reason": "达到停止条件" if stopped else None,
            "no_improvement_streak": no_improvement_streak,
        },
        "judge_progress": {
            "complete": judge_complete,
            "missing_report_case_ids": missing_reports or [],
            "pending_judge_case_ids": pending_judge or [],
        },
        "actions": {
            "advance": {
                "enabled": advance,
                "reason": None if advance else "不可推进",
            }
        },
    }


class ExperimentLoopPlanTest(unittest.TestCase):
    def test_stop_has_highest_priority(self):
        action = plan_next_action(
            _state(stopped=True, missing_reports=["c1"]),
            {"job": {"active": True, "job_id": "j1"}},
            3,
        )
        self.assertEqual(action["action"], "stop")

    def test_external_no_improvement_limit_stops(self):
        action = plan_next_action(
            _state(
                no_improvement_streak=3,
                missing_reports=["c1"],
            ),
            {"job": {"active": True, "job_id": "j1"}},
            3,
            3,
        )
        self.assertEqual(action["action"], "stop")
        self.assertIn("连续 3 个候选", action["reason"])

    def test_attaches_active_generation(self):
        action = plan_next_action(
            _state(missing_reports=["c1"]),
            {
                "job": {
                    "active": True,
                    "job_id": "j1",
                    "skill_version": "v2",
                },
                "jobs": [],
            },
            3,
        )
        self.assertEqual(action["action"], "wait_generation")
        self.assertEqual(action["job_id"], "j1")

    def test_starts_missing_reports_without_job(self):
        action = plan_next_action(
            _state(missing_reports=["c1", "c2"]),
            {"job": None, "jobs": []},
            3,
        )
        self.assertEqual(action["action"], "start_generation")
        self.assertEqual(action["case_ids"], ["c1", "c2"])

    def test_retries_only_failed_cases(self):
        job = {
            "job_id": "j1",
            "skill_version": "v2",
            "created_at": 2,
            "terminal": True,
            "failed_case_ids": ["c2"],
        }
        action = plan_next_action(
            _state(missing_reports=["c2"]),
            {"job": job, "jobs": [job]},
            3,
        )
        self.assertEqual(action["action"], "retry_generation")
        self.assertEqual(action["case_ids"], ["c2"])

    def test_generation_start_includes_frozen_memory_context(self):
        loop = ExperimentLoop(
            "http://127.0.0.1:9999",
            "memory-session",
            generation_memory_context="- 使用短段。",
            generation_memory_ids=["m-1"],
        )
        calls = []
        loop.api = lambda path, method, payload, timeout: (
            calls.append((path, method, payload, timeout))
            or {"job": {"job_id": "j1", "skill_version": "v0", "case_count": 1}}
        )

        loop._start_generation({"version": "v0", "case_ids": ["c1"]})

        payload = calls[0][2]
        self.assertEqual("- 使用短段。", payload["memory_context"])
        self.assertEqual(["m-1"], payload["memory_ids"])

    def test_generation_retry_limit_blocks(self):
        jobs = [
            {
                "job_id": "j%d" % index,
                "skill_version": "v2",
                "created_at": index,
                "terminal": True,
                "failed_case_ids": ["c2"],
            }
            for index in range(1, 4)
        ]
        action = plan_next_action(
            _state(missing_reports=["c2"]),
            {"job": jobs[-1], "jobs": jobs},
            3,
        )
        self.assertEqual(action["action"], "blocked")

    def test_judges_then_advances(self):
        judge = plan_next_action(
            _state(pending_judge=["c1"], judge_complete=False),
            {"job": None, "jobs": []},
            3,
        )
        self.assertEqual(judge["action"], "run_judge")
        advance = plan_next_action(
            _state(judge_complete=True),
            {"job": None, "jobs": []},
            3,
        )
        self.assertEqual(advance["action"], "advance")

    def test_session_id_validation(self):
        self.assertEqual(
            _safe_session_id("president-report-llm"),
            "president-report-llm",
        )
        with self.assertRaises(ValueError):
            _safe_session_id("../other")

    def test_successful_optimizer_candidate_resets_retry_counter(self):
        loop = ExperimentLoop(
            "http://127.0.0.1:8080",
            "president-report-llm",
        )
        loop._optimizer_attempts["v1"] = 2
        loop.api = lambda *args, **kwargs: {
            "advance_result": {
                "status": "proposed",
                "version": "v3",
                "message": "ok",
            }
        }
        loop.emit = lambda *args, **kwargs: None

        loop._advance({"version": "v1"})

        self.assertEqual(loop._optimizer_attempts["v1"], 0)

    def test_judge_and_optimizer_receive_selected_codex_model(self):
        loop = ExperimentLoop(
            "http://127.0.0.1:8080",
            "president-report-llm",
            judge_parallel=20,
            llm_backend="codex",
            llm_model="gpt-5.6-sol",
            llm_reasoning_effort="medium",
        )
        calls = []

        def fake_api(path, method="GET", payload=None, timeout=None):
            calls.append((path, method, payload))
            if path == "/api/run_judge_batch":
                return {"summary": {"status": "completed"}}
            return {
                "advance_result": {
                    "status": "proposed",
                    "version": "v2",
                }
            }

        loop.api = fake_api
        loop.emit = lambda *args, **kwargs: None
        loop._run_judge({"version": "v1", "pending_case_ids": []})
        loop._advance({"version": "v1"})

        for _, _, payload in calls:
            self.assertEqual(payload["llm_backend"], "codex")
            self.assertEqual(payload["llm_model"], "gpt-5.6-sol")
            self.assertEqual(payload["llm_reasoning_effort"], "medium")

    def test_judge_and_optimizer_can_use_different_backends(self):
        loop = ExperimentLoop(
            "http://127.0.0.1:8080",
            "president-report-llm",
            judge_llm_backend="codex",
            judge_llm_model="gpt-5.6-sol",
            optimizer_llm_backend="workbuddy",
            optimizer_llm_model="claude-opus-5",
        )
        calls = []

        def fake_api(path, method="GET", payload=None, timeout=None):
            calls.append((path, payload))
            if path == "/api/run_judge_batch":
                return {"summary": {"status": "completed"}}
            return {"advance_result": {"status": "proposed", "version": "v2"}}

        loop.api = fake_api
        loop.emit = lambda *args, **kwargs: None
        loop._run_judge({"version": "v1", "pending_case_ids": []})
        loop._advance({"version": "v1"})

        self.assertEqual("codex", calls[0][1]["llm_backend"])
        self.assertEqual("gpt-5.6-sol", calls[0][1]["llm_model"])
        self.assertEqual("workbuddy", calls[1][1]["llm_backend"])
        self.assertEqual("claude-opus-5", calls[1][1]["llm_model"])


if __name__ == "__main__":
    unittest.main()
