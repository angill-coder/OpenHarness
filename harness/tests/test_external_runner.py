# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parents[1]
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

from external_run_models import ExternalRunRequest, ReportOutputContract
from workbuddy_runner import (
    ExternalRunConfigurationError,
    run_external_cases,
)


class ExternalRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.materials = self.root / "materials"
        self.materials.mkdir()
        (self.materials / "source.txt").write_text(
            "fake source",
            encoding="utf-8",
        )
        self.skill = self.root / "research-report"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text(
            "---\nname: research-report\n---\n# Fake Skill\n",
            encoding="utf-8",
        )
        self.dataset = self.root / "case.json"
        self.dataset.write_text(
            json.dumps(
                {
                    "defaults": {"skills": ["research-report"]},
                    "cases": [
                        {
                            "id": "wb-case",
                            "metadata": {
                                "openharness_case_id": "oh-case",
                                "split": "dev",
                            },
                            "input_files": [
                                {
                                    "source": "./materials",
                                    "target": "materials",
                                }
                            ],
                            "turns": [
                                {
                                    "round": 0,
                                    "label": "task",
                                    "prompt": "先盘点材料。",
                                },
                                {
                                    "round": 1,
                                    "label": "intake_answers",
                                    "prompt": "这是完整 intake。",
                                },
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.fake_cli = Path(__file__).with_name("fake_workbuddy.py")

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _request(
        self,
        *,
        succeed_on_attempt: int,
        max_retries: int,
    ) -> ExternalRunRequest:
        return ExternalRunRequest(
            case_file=self.dataset,
            output_root=self.root / "runs",
            skill_version="test-v1",
            session_id="test-session",
            skill_path=self.skill,
            model="fake-model",
            parallel=1,
            timeout_seconds=20,
            stall_timeout_seconds=5,
            max_report_retries=max_retries,
            output_contract=ReportOutputContract(min_bytes=100),
            command=(sys.executable, str(self.fake_cli)),
            allowed_material_roots=(self.root,),
            environment={
                "FAKE_WB_STATE_FILE": str(self.root / "fake-state.json"),
                "FAKE_WB_SUCCEED_ON_ATTEMPT": str(succeed_on_attempt),
            },
        )

    def test_retries_failed_case_until_report_exists(self) -> None:
        result = run_external_cases(
            self._request(succeed_on_attempt=3, max_retries=3)
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.status, "completed")
        case = result.cases[0]
        self.assertEqual(case.openharness_case_id, "oh-case")
        self.assertEqual(case.status, "generated")
        self.assertEqual(len(case.attempts), 3)
        self.assertEqual(
            [item.status for item in case.attempts],
            ["artifact_missing", "artifact_missing", "generated"],
        )
        self.assertEqual(
            len({item.wb_session_id for item in case.attempts}),
            3,
        )
        self.assertIsNotNone(case.report)
        self.assertTrue(Path(result.output_dir, "generation_result.json").is_file())

        effective_case = json.loads(
            Path(
                case.attempts[-1].run_dir,
                "cases",
                "wb-case",
                "case.json",
            ).read_text(encoding="utf-8")
        )
        source_last = effective_case["case"]["metadata"]["source_turns"][-1][
            "prompt"
        ]
        effective_last = effective_case["case"]["turns"][-1]["prompt"]
        self.assertEqual(source_last, "这是完整 intake。")
        self.assertIn("OpenHarness 最终交付指令", effective_last)

    def test_marks_retry_exhausted_after_max_attempts(self) -> None:
        result = run_external_cases(
            self._request(succeed_on_attempt=0, max_retries=2)
        )

        self.assertFalse(result.succeeded)
        self.assertEqual(result.status, "failed")
        case = result.cases[0]
        self.assertEqual(case.status, "retry_exhausted")
        self.assertEqual(len(case.attempts), 3)
        self.assertTrue(
            all(item.status == "artifact_missing" for item in case.attempts)
        )

    def test_rejects_missing_material_before_starting_workbuddy(self) -> None:
        (self.materials / "source.txt").unlink()
        self.materials.rmdir()

        with self.assertRaises(ExternalRunConfigurationError):
            run_external_cases(
                self._request(succeed_on_attempt=1, max_retries=3)
            )
        self.assertFalse((self.root / "runs").exists())

    def test_records_cli_error_and_retries_when_report_is_missing(self) -> None:
        request = self._request(succeed_on_attempt=0, max_retries=1)
        request = replace(
            request,
            environment={
                **request.environment,
                "FAKE_WB_CLI_ERROR": "1",
            },
        )

        result = run_external_cases(request)

        case = result.cases[0]
        self.assertEqual(case.status, "retry_exhausted")
        self.assertEqual(len(case.attempts), 2)
        self.assertTrue(
            all(item.wb_status == "cli_error" for item in case.attempts)
        )
        self.assertTrue(
            all(item.status == "artifact_missing" for item in case.attempts)
        )

    def test_rejects_ground_truth_leak_before_starting_workbuddy(self) -> None:
        payload = json.loads(self.dataset.read_text(encoding="utf-8"))
        payload["cases"][0]["ground_truth"] = {
            "supported_claims": ["answer"]
        }
        self.dataset.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ExternalRunConfigurationError,
            "ground_truth",
        ):
            run_external_cases(
                self._request(succeed_on_attempt=1, max_retries=3)
            )
        self.assertFalse((self.root / "runs").exists())


if __name__ == "__main__":
    unittest.main()
