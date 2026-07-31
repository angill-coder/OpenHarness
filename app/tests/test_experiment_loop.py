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
):
    return {
        "current_version": "v2",
        "optimizer_stop": {
            "stopped": stopped,
            "reason": "达到停止条件" if stopped else None,
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


if __name__ == "__main__":
    unittest.main()
