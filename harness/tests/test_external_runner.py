# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

HARNESS_DIR = Path(__file__).resolve().parents[1]
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

from external_run_models import (
    ExternalAttemptResult,
    ExternalRunRequest,
    ReportArtifact,
    ReportOutputContract,
)
from run_external import _parser
from workbuddy_batch.adapter import build_environment, build_round_command
from workbuddy_batch.models import BatchConfig, CaseSpec, InputFile
from workbuddy_runner import (
    ExternalRunConfigurationError,
    _case_attempt_run_id,
    run_external_cases,
)
from workbuddy_batch.runner import _workbuddy_session_id


class ExternalRunnerTest(unittest.TestCase):
    def test_internal_ids_remain_short_for_long_case_ids(self) -> None:
        case_id = "rr-realproject-new-" + ("very-long-case-" * 20)
        run_id = _case_attempt_run_id(case_id, 4)
        session_id = _workbuddy_session_id(run_id, case_id)

        self.assertLessEqual(len(run_id), 32)
        self.assertLessEqual(len(session_id), 40)
        self.assertNotEqual(
            run_id,
            _case_attempt_run_id(case_id, 3),
        )

    def test_default_parallelism_is_twenty(self) -> None:
        request = ExternalRunRequest(
            case_file=Path("case.json"),
            output_root=Path("generation_runs"),
            skill_version="test-v1",
            skill_name="research-report",
        )
        self.assertEqual(request.parallel, 20)
        self.assertEqual(
            BatchConfig(
                command=("workbuddy",),
                output_root=Path("generation_runs"),
            ).parallel,
            20,
        )
        self.assertEqual(
            _parser().parse_args(["--dataset", "case.json"]).parallel,
            20,
        )

    def test_runner_disables_workbuddy_user_and_project_memory(self) -> None:
        config = BatchConfig(
            command=("workbuddy",),
            output_root=Path("generation_runs"),
        )
        case = CaseSpec(case_id="memory-isolation", prompt="write report")
        command = build_round_command(
            config,
            case,
            "session-id",
            0,
            case.prompt,
            (),
        )
        self.assertEqual(
            command[command.index("--setting-sources") + 1],
            "",
        )
        environment = build_environment(config)
        self.assertEqual(environment["CODEBUDDY_DISABLE_AUTO_MEMORY"], "1")
        self.assertEqual(
            environment["CODEBUDDY_MEMORY_RELEVANCE_DISABLED"],
            "1",
        )
        self.assertEqual(
            environment["CODEBUDDY_MEMORY_EXTRACTION_DISABLED"],
            "1",
        )
        self.assertEqual(environment["CODEBUDDY_TEAM_MEMORY_ENABLED"], "0")

    def test_evidence_metadata_first_case_gets_evidence_reading_contract(self) -> None:
        config = BatchConfig(
            command=("workbuddy",),
            output_root=Path("generation_runs"),
        )
        case = CaseSpec(
            case_id="evidence-metadata-first",
            prompt="write report",
            input_files=(
                InputFile(
                    Path("evidence_metadata.json"),
                    "materials/00_evidence_metadata.json",
                ),
                InputFile(Path("source"), "materials/source"),
            ),
        )

        command = build_round_command(
            config,
            case,
            "session-id",
            0,
            case.prompt,
            (),
        )
        system_prompt = command[command.index("--append-system-prompt") + 1]

        self.assertIn("evidence-first reading contract", system_prompt)
        self.assertIn("materials/00_evidence_metadata.json", system_prompt)
        self.assertIn("materials/source/", system_prompt)
        self.assertIn("不要向上探索运行目录、case.json", system_prompt)

    def test_source_only_case_does_not_claim_evidence_metadata(self) -> None:
        config = BatchConfig(
            command=("workbuddy",),
            output_root=Path("generation_runs"),
        )
        case = CaseSpec(
            case_id="source-only",
            prompt="write report",
            input_files=(InputFile(Path("source"), "materials"),),
        )

        command = build_round_command(
            config,
            case,
            "session-id",
            0,
            case.prompt,
            (),
        )

        self.assertNotIn("evidence-first reading contract", "\n".join(command))

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

    def test_compacts_trace_and_removes_workspace(self) -> None:
        request = self._request(
            succeed_on_attempt=1,
            max_retries=0,
        )
        request = replace(
            request,
            environment={
                **request.environment,
                "FAKE_WB_STREAM_EVENT": "1",
            },
        )

        result = run_external_cases(request)

        self.assertTrue(result.succeeded)
        case_dir = (
            Path(result.cases[0].attempts[0].run_dir)
            / "cases"
            / "wb-case"
        )
        self.assertFalse((case_dir / "trace" / "workspace").exists())
        self.assertEqual(
            list((case_dir / "trace" / "rounds").glob("*/stdout.jsonl")),
            [],
        )
        events = [
            json.loads(line)
            for line in (
                case_dir / "trace" / "2_events.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(events)
        self.assertFalse(
            any(
                item.get("event", {}).get("type") == "stream_event"
                for item in events
            )
        )
        self.assertTrue(
            (case_dir / "artifacts" / "report.md").is_file()
        )

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

    def test_failed_case_retries_before_other_case_finishes(self) -> None:
        payload = json.loads(self.dataset.read_text(encoding="utf-8"))
        second = dict(payload["cases"][0])
        second["id"] = "wb-slow"
        second["metadata"] = {
            "openharness_case_id": "oh-slow",
            "split": "dev",
        }
        payload["cases"].append(second)
        self.dataset.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        retry_started = threading.Event()
        slow_observations = []
        active = 0
        max_active = 0
        lock = threading.Lock()

        def execute(case, identity, attempt, request, batch_config):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                if case.case_id == "wb-slow":
                    retry_started.wait(1)
                    slow_observations.append(retry_started.is_set())
                if case.case_id == "wb-case" and attempt == 2:
                    retry_started.set()
                generated = not (
                    case.case_id == "wb-case" and attempt == 1
                )
                report = (
                    ReportArtifact(
                        original_workspace_path="deliverables/report.md",
                        captured_path="/tmp/report.md",
                        sha256="a" * 64,
                        size=200,
                        mime_type="text/markdown",
                        text="有效报告" * 100,
                    )
                    if generated
                    else None
                )
                return ExternalAttemptResult(
                    attempt=attempt,
                    max_attempts=request.max_attempts,
                    wb_case_id=case.case_id,
                    openharness_case_id=identity[
                        "openharness_case_id"
                    ],
                    status=(
                        "generated" if generated else "artifact_missing"
                    ),
                    wb_status="success",
                    wb_run_id="%s-%s" % (case.case_id, attempt),
                    wb_session_id=None,
                    run_dir="/tmp/run",
                    trace_path="/tmp/run/trace",
                    manifest_path="/tmp/run/manifest.json",
                    duration_ms=1,
                    configured_model=request.model,
                    observed_models=(),
                    usage={},
                    error=None if generated else "missing",
                    report=report,
                )
            finally:
                with lock:
                    active -= 1

        request = replace(
            self._request(succeed_on_attempt=1, max_retries=2),
            parallel=2,
        )
        with patch(
            "workbuddy_runner._execute_case_attempt",
            side_effect=execute,
        ):
            result = run_external_cases(request)

        by_case = {item.wb_case_id: item for item in result.cases}
        self.assertTrue(result.succeeded)
        self.assertEqual(len(by_case["wb-case"].attempts), 2)
        self.assertEqual(len(by_case["wb-slow"].attempts), 1)
        self.assertEqual(slow_observations, [True])
        self.assertLessEqual(max_active, request.parallel)

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

    def test_unified_ground_truth_is_not_sent_to_workbuddy(self) -> None:
        payload = json.loads(self.dataset.read_text(encoding="utf-8"))
        payload["schema_version"] = "openharness-wb/v1"
        payload["cases"][0]["case_id"] = "oh-case"
        payload["cases"][0]["input"] = {"brief": "generate"}
        payload["cases"][0]["ground_truth"] = {
            "supported_claims": ["answer"]
        }
        self.dataset.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

        result = run_external_cases(
            self._request(succeed_on_attempt=1, max_retries=3)
        )

        self.assertTrue(result.succeeded)
        effective_case = json.loads(
            Path(
                result.cases[0].attempts[0].run_dir,
                "cases",
                "oh-case",
                "case.json",
            ).read_text(encoding="utf-8")
        )
        self.assertNotIn(
            "ground_truth",
            effective_case["case"]["data"],
        )

    def test_rejects_ground_truth_nested_in_generation_data(self) -> None:
        payload = json.loads(self.dataset.read_text(encoding="utf-8"))
        payload["cases"][0]["data"] = {
            "ground_truth": {"supported_claims": ["answer"]}
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

    def test_filters_by_openharness_case_id_and_reports_progress(self) -> None:
        updates = []
        request = replace(
            self._request(succeed_on_attempt=1, max_retries=3),
            openharness_case_ids=("oh-case",),
        )

        result = run_external_cases(
            request,
            progress_callback=lambda item: updates.append(
                (
                    item.status,
                    [case.status for case in item.cases],
                )
            ),
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(updates[0], ("running", ["queued"]))
        self.assertEqual(updates[1], ("running", ["running"]))
        self.assertEqual(updates[-1][0], "completed")

    def test_rejects_unknown_openharness_case_filter(self) -> None:
        request = replace(
            self._request(succeed_on_attempt=1, max_retries=3),
            openharness_case_ids=("missing-case",),
        )

        with self.assertRaisesRegex(
            ExternalRunConfigurationError,
            "missing-case",
        ):
            run_external_cases(request)
        self.assertFalse((self.root / "runs").exists())

    def test_can_cancel_before_first_attempt(self) -> None:
        result = run_external_cases(
            self._request(succeed_on_attempt=1, max_retries=3),
            should_cancel=lambda: True,
        )

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.cases[0].status, "cancelled")
        self.assertEqual(result.cases[0].attempts, [])


if __name__ == "__main__":
    unittest.main()
