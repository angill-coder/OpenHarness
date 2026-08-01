# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
HARNESS = APP.parent / "harness"
for path in (str(APP), str(HARNESS)):
    if path not in sys.path:
        sys.path.insert(0, path)

from _data_audit import (  # noqa: E402
    DataQualityBatchResult,
    DataQualityCancelled,
    DataQualityCaseResult,
)
from data_quality_jobs import (  # noqa: E402
    DataQualityJobService,
    DataQualitySettings,
)
import persistence as persist  # noqa: E402
from session import Session  # noqa: E402


class FakeQualityRunner:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancel_mode = False

    def __call__(self, request, progress_callback=None, should_cancel=None):
        self.started.set()
        if self.cancel_mode:
            self.release.wait(2)
            if should_cancel and should_cancel():
                raise DataQualityCancelled("数据质检已取消")
        cases = []
        for index, case_id in enumerate(request.case_ids):
            if progress_callback:
                progress_callback({"event": "case_started", "case_id": case_id})
                progress_callback(
                    {
                        "event": "stage_started",
                        "case_id": case_id,
                        "stage": "audit",
                    }
                )
            score = 80.0 + index * 10
            case_output = request.output_root / case_id
            case_output.mkdir(parents=True, exist_ok=True)
            (case_output / "audit.json").write_text(
                json.dumps(
                    {
                        "projects": [
                            {
                                "metrics": {
                                    "omission_score": 70.0 + index * 10,
                                    "conflict_score": 90.0 + index * 10,
                                    "signal_score": 80.0 + index * 10,
                                }
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = DataQualityCaseResult(
                case_id=case_id,
                project=case_id,
                status="success",
                output_dir=str(case_output),
                metadata_status="generated",
                audit_status="generated",
                overall_score=score,
            )
            cases.append(result)
            if progress_callback:
                progress_callback(
                    {
                        "event": "case_completed",
                        "case_id": case_id,
                        "status": "success",
                        "overall_score": score,
                    }
                )
        return DataQualityBatchResult(
            status="success",
            output_root=str(request.output_root),
            started_at="2026-07-31T00:00:00+08:00",
            finished_at="2026-07-31T00:01:00+08:00",
            cases=cases,
        )


class DataQualityJobServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dataset = self.root / "data.json"
        self.dataset.write_text(
            json.dumps({"cases": []}),
            encoding="utf-8",
        )
        self.runner = FakeQualityRunner()
        self.service = DataQualityJobService(
            settings=DataQualitySettings(
                output_root=self.root / "quality",
                model="test-model",
                parallel=2,
            ),
            runner_func=self.runner,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _wait_terminal(self, job_id):
        deadline = time.time() + 3
        while time.time() < deadline:
            job = self.service.get(job_id)
            if not job.active:
                return job
            time.sleep(0.01)
        self.fail("quality job did not finish")

    def test_background_job_reports_case_scores_and_average(self):
        job = self.service.start(
            session_id="session-a",
            dataset_path=self.dataset,
            case_ids=["case-a", "case-b"],
            repair_metadata=True,
        )
        result = self._wait_terminal(job.job_id)

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.average_score, 85.0)
        self.assertEqual(result.median_score, 85.0)
        self.assertEqual(result.average_omission_score, 75.0)
        self.assertEqual(result.average_conflict_score, 95.0)
        self.assertEqual(result.average_signal_score, 85.0)
        self.assertEqual(
            [item.overall_score for item in result.cases],
            [80.0, 90.0],
        )
        self.assertEqual(result.cases[0].omission_score, 70.0)
        self.assertEqual(result.cases[0].conflict_score, 90.0)
        self.assertEqual(result.cases[0].signal_score, 80.0)
        self.assertTrue(result.repair_metadata)

    def test_active_job_can_be_cancelled(self):
        self.runner.cancel_mode = True
        job = self.service.start(
            session_id="session-b",
            dataset_path=self.dataset,
            case_ids=["case-a"],
        )
        self.assertTrue(self.runner.started.wait(1))

        self.service.cancel(job.job_id)
        self.runner.release.set()
        result = self._wait_terminal(job.job_id)

        self.assertEqual(result.status, "cancelled")
        self.assertTrue(result.cancel_requested)

    def test_completed_scores_restore_from_markdown_run_directory(self):
        first = self.service.start(
            session_id="session-restored",
            dataset_path=self.dataset,
            case_ids=["case-a"],
        )
        completed = self._wait_terminal(first.job_id)
        summary_path = Path(completed.output_root) / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "status": "success",
                    "average_case_score": 80.0,
                    "median_case_score": 80.0,
                    "cases": [
                        {
                            "case_id": "case-a",
                            "status": "success",
                            "overall_score": 80.0,
                            "output_dir": str(
                                Path(completed.output_root) / "case-a"
                            ),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        restored_service = DataQualityJobService(
            settings=self.service.settings,
            runner_func=self.runner,
        )

        restored = restored_service.latest_for_session(
            "session-restored"
        )

        self.assertIsNotNone(restored)
        self.assertEqual(restored.average_score, 80.0)
        self.assertEqual(restored.average_omission_score, 70.0)
        self.assertEqual(restored.average_conflict_score, 90.0)
        self.assertEqual(restored.average_signal_score, 80.0)


class DataQualitySessionStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_base = persist._BASE
        persist._BASE = str(self.root / "sessions")

    def tearDown(self):
        persist._BASE = self.old_base
        self.tmp.cleanup()

    def test_configured_dataset_source_survives_snapshot_restore(self):
        dataset = self.root / "data.json"
        dataset.write_text("{}", encoding="utf-8")
        session = Session(
            "quality-session",
            "生成调研洞察报告",
            "research_insight",
        )
        session.import_data(
            [
                {
                    "case_id": "case-a",
                    "input": {"brief": "测试"},
                    "ground_truth": {},
                    "split": "dev",
                }
            ],
            data_source={
                "kind": "configured",
                "dataset_path": str(dataset),
            },
        )

        restored = Session.restore(session.to_snapshot())
        quality = restored.view()["data_quality"]

        self.assertTrue(quality["available"])
        self.assertEqual(quality["dataset_path"], str(dataset))


if __name__ == "__main__":
    unittest.main()
