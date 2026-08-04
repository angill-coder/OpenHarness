# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
HARNESS = APP.parent / "harness"
for path in (str(APP), str(HARNESS)):
    if path not in sys.path:
        sys.path.insert(0, path)

from external_run_models import (  # noqa: E402
    ExternalBatchResult,
    ExternalCaseResult,
    ReportArtifact,
)
from generation_jobs import (  # noqa: E402
    GenerationJobService,
    GenerationSettings,
    SUPPORTED_WB_MODELS,
)
from generation_models import GenerationCaseState, GenerationJob  # noqa: E402
import persistence as persist  # noqa: E402
from session import Session  # noqa: E402


def _now():
    return datetime.now(timezone.utc).isoformat()


def _artifact(case_id: str) -> ReportArtifact:
    text = "# %s\n\n%s" % (case_id, "有效报告正文。" * 100)
    return ReportArtifact(
        original_workspace_path="deliverables/report.md",
        captured_path="/tmp/%s.md" % case_id,
        sha256=("a" if case_id == "case-a" else "b") * 64,
        size=len(text.encode("utf-8")),
        mime_type="text/markdown",
        text=text,
    )


class FakeRunner:
    def __init__(self, fail_once=()):
        self.fail_once = set(fail_once)
        self.calls = []
        self.requests = []
        self.started = threading.Event()
        self.release = None

    def __call__(
        self,
        request,
        progress_callback=None,
        should_cancel=None,
    ):
        ids = list(request.openharness_case_ids)
        self.calls.append(ids)
        self.requests.append(request)
        self.started.set()
        if self.release is not None:
            self.release.wait(3)
        cases = []
        for case_id in ids:
            should_fail = (
                case_id in self.fail_once
                and sum(case_id in call for call in self.calls) == 1
            )
            report = None if should_fail else _artifact(case_id)
            cases.append(
                ExternalCaseResult(
                    wb_case_id="wb-" + case_id,
                    openharness_case_id=case_id,
                    split="dev",
                    status=(
                        "retry_exhausted"
                        if should_fail
                        else "generated"
                    ),
                    report=report,
                )
            )
        generated = sum(item.report is not None for item in cases)
        status = (
            "completed"
            if generated == len(cases)
            else ("partial" if generated else "failed")
        )
        result = ExternalBatchResult(
            generation_id="gen-%02d" % len(self.calls),
            session_id=request.session_id,
            skill_version=request.skill_version,
            status=status,
            output_dir="/tmp/gen-%02d" % len(self.calls),
            created_at=_now(),
            finished_at=_now(),
            cases=cases,
        )
        if progress_callback:
            progress_callback(result)
        return result


class GenerationJobServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_base = persist._BASE
        persist._BASE = str(self.root / "sessions")
        self.dataset = self.root / "case.json"
        self.dataset.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "id": "wb-a",
                            "metadata": {
                                "openharness_case_id": "case-a",
                                "split": "dev",
                            },
                            "prompt": "生成 A 报告",
                        },
                        {
                            "id": "wb-b",
                            "metadata": {
                                "openharness_case_id": "case-b",
                                "split": "dev",
                            },
                            "prompt": "生成 B 报告",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.skill = self.root / "skill"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text(
            "---\nname: research-report\n---\n# Test\n",
            encoding="utf-8",
        )
        skill_references = self.skill / "references"
        skill_references.mkdir()
        (skill_references / "instructions.md").write_text(
            "# Test Instructions\n\n"
            "<!-- OPENHARNESS_DIRECTIVES: [] -->\n\n"
            "<!-- OPENHARNESS_VERSION_RULES_START -->\n"
            "<!-- 当前基线没有 optimizer 增量规则。 -->\n"
            "<!-- OPENHARNESS_VERSION_RULES_END -->\n",
            encoding="utf-8",
        )
        self.session = Session(
            "test-session",
            "生成面向高管的调研洞察报告",
            "research_insight",
        )
        self.session.import_data(
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
        self.settings = GenerationSettings(
            dataset_path=self.dataset,
            output_root=self.root / "runs",
            skill_path=self.skill,
            model="fake-model",
            models=(
                "fake-model",
                "deepseek-v4-pro-ioa",
                "retry-model",
                "retry-override-model",
            ),
            parallel=2,
            max_report_retries=3,
            timeout_seconds=10,
            stall_timeout_seconds=5,
            max_concurrent_jobs=1,
            min_report_bytes=10,
        )

    def tearDown(self):
        persist._BASE = self.old_base
        self.tmp.cleanup()

    def test_default_dataset_path_is_repository_local_data_json(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = GenerationSettings.from_env()
        expected = {
            version: APP.parent / "data" / "research-report" / version / "data.json"
            for version in ("v1", "v2", "v3")
        }
        self.assertEqual(settings.dataset_path, expected["v1"])
        self.assertEqual(dict(settings.dataset_paths), expected)
        self.assertEqual(settings.parallel, 20)
        self.assertEqual(settings.model, "deepseek-v4-pro-ioa")
        self.assertEqual(
            settings.models[:2],
            ("deepseek-v4-pro-ioa", "hy3-ioa"),
        )
        self.assertNotIn("auto", settings.models)
        self.assertNotIn("echo", settings.models)
        self.assertEqual(settings.models, SUPPORTED_WB_MODELS)

    def test_session_metadata_routes_to_versioned_dataset(self):
        v2_dataset = self.root / "v2" / "data.json"
        v2_dataset.parent.mkdir()
        v2_dataset.write_text("{}", encoding="utf-8")
        meta_path = self.root / "sessions" / "test-session" / "meta.json"
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        metadata["experiment_data"] = {"id": "v2", "label": "Data v2"}
        meta_path.write_text(json.dumps(metadata), encoding="utf-8")
        settings = GenerationSettings(
            dataset_path=self.dataset,
            output_root=self.root / "runs-routed",
            dataset_paths=(("v1", self.dataset), ("v2", v2_dataset)),
        )
        service = GenerationJobService(
            {"test-session": self.session},
            settings=settings,
            runner_func=lambda *_args, **_kwargs: None,
        )

        self.assertEqual(
            service.dataset_path_for_session("test-session"),
            v2_dataset.resolve(),
        )

    def test_batch_import_is_idempotent_and_evaluates_once(self):
        calls = 0
        original = self.session.evaluate

        def counted(account=None):
            nonlocal calls
            calls += 1
            return original(account)

        self.session.evaluate = counted
        outputs = {
            "case-a": _artifact("case-a").text,
            "case-b": _artifact("case-b").text,
        }
        first = self.session.import_generated_outputs(
            outputs,
            "v0",
            "gen-idempotent",
            traces={
                "case-a": {"status": "success", "operations": []},
            },
        )
        second = self.session.import_generated_outputs(
            outputs,
            "v0",
            "gen-idempotent",
        )
        self.assertEqual(calls, 1)
        self.assertEqual(
            first["generation_import"]["imported_case_ids"],
            ["case-a", "case-b"],
        )
        self.assertEqual(
            second["generation_import"]["skipped_case_ids"],
            ["case-a", "case-b"],
        )
        output_lines = (
            self.root
            / "sessions"
            / "test-session"
            / "outputs.jsonl"
        ).read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(output_lines), 2)
        persisted = [json.loads(line) for line in output_lines]
        self.assertEqual(
            persisted[0]["generation_trace"]["status"],
            "success",
        )
        self.assertNotIn("generation_trace", persisted[1])

    def test_inline_session_cases_are_snapshotted_for_runner(self):
        self.dataset.write_text(
            json.dumps(
                {
                    "cases": [
                        {"id": "other", "prompt": "unrelated case"}
                    ]
                }
            ),
            encoding="utf-8",
        )
        fake = FakeRunner()
        service = GenerationJobService(
            {"test-session": self.session},
            self.settings,
            fake,
        )

        job, reused = service.start(
            "test-session",
            "tester",
            idempotency_key="inline-session-data",
        )
        done = service.wait(job.job_id)

        self.assertFalse(reused)
        self.assertEqual(done.status, "completed")
        dataset_path = Path(done.dataset_path)
        self.assertEqual(
            dataset_path.parent.parent.name,
            "_session_datasets",
        )
        payload = json.loads(dataset_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [item["case_id"] for item in payload["cases"]],
            ["case-a", "case-b"],
        )
        prompt = payload["cases"][0]["turns"][0]["prompt"]
        self.assertIn("用户输入 JSON", prompt)
        self.assertIn('"brief": "A"', prompt)
        self.assertNotIn("ground_truth", prompt)

    def test_job_completes_and_imports_all_reports(self):
        fake = FakeRunner()
        service = GenerationJobService(
            {"test-session": self.session},
            self.settings,
            fake,
        )
        job, reused = service.start(
            "test-session",
            "tester",
            idempotency_key="one-click",
        )
        self.assertFalse(reused)
        done = service.wait(job.job_id)
        self.assertEqual(done.status, "completed")
        self.assertEqual(done.imported_count, 2)
        self.assertEqual(fake.calls, [["case-a", "case-b"]])
        self.assertEqual(done.skill_mode, "session_artifact")
        self.assertEqual(
            fake.requests[0].skill_path,
            Path(done.skill_ref),
        )
        self.assertTrue((Path(done.skill_ref) / "SKILL.md").is_file())
        self.assertTrue(
            (
                Path(done.skill_ref)
                / "references"
                / "instructions.md"
            ).is_file()
        )
        self.assertEqual(done.compiler_version, "session-skill/v3")
        self.assertIsNotNone(done.base_skill_hash)
        self.assertIn("case-a", self.session.report_outputs["v0"])
        self.assertIn("case-b", self.session.report_outputs["v0"])

    def test_each_report_is_imported_before_the_batch_finishes(self):
        first_imported = threading.Event()
        release = threading.Event()

        def staged_runner(
            request,
            progress_callback=None,
            should_cancel=None,
        ):
            first = ExternalBatchResult(
                generation_id="gen-staged",
                session_id=request.session_id,
                skill_version=request.skill_version,
                status="running",
                output_dir="/tmp/gen-staged",
                created_at=_now(),
                finished_at=_now(),
                cases=[
                    ExternalCaseResult(
                        wb_case_id="wb-case-a",
                        openharness_case_id="case-a",
                        split="dev",
                        status="generated",
                        report=_artifact("case-a"),
                    ),
                    ExternalCaseResult(
                        wb_case_id="wb-case-b",
                        openharness_case_id="case-b",
                        split="dev",
                        status="running",
                    ),
                ],
            )
            progress_callback(first)
            first_imported.set()
            release.wait(3)
            final = ExternalBatchResult(
                generation_id="gen-staged",
                session_id=request.session_id,
                skill_version=request.skill_version,
                status="completed",
                output_dir="/tmp/gen-staged",
                created_at=_now(),
                finished_at=_now(),
                cases=[
                    first.cases[0],
                    ExternalCaseResult(
                        wb_case_id="wb-case-b",
                        openharness_case_id="case-b",
                        split="dev",
                        status="generated",
                        report=_artifact("case-b"),
                    ),
                ],
            )
            progress_callback(final)
            return final

        service = GenerationJobService(
            {"test-session": self.session},
            self.settings,
            staged_runner,
        )
        job, _ = service.start("test-session", "tester")

        self.assertTrue(first_imported.wait(1))
        in_progress = service.get(job.job_id)
        self.assertEqual(in_progress.imported_count, 1)
        self.assertTrue(in_progress.cases[0].imported)
        self.assertEqual(in_progress.cases[0].status, "imported")
        self.assertFalse(in_progress.cases[1].imported)
        self.assertIn("case-a", self.session.report_outputs["v0"])
        self.assertNotIn("case-b", self.session.report_outputs["v0"])

        release.set()
        done = service.wait(job.job_id)
        self.assertEqual(done.status, "completed")
        self.assertEqual(done.imported_count, 2)
        self.assertIn("case-b", self.session.report_outputs["v0"])

    def test_job_uses_requested_parallel_with_backend_limit(self):
        fake = FakeRunner()
        service = GenerationJobService(
            {"test-session": self.session},
            self.settings,
            fake,
        )
        job, _ = service.start(
            "test-session",
            "tester",
            parallel=1,
            model="deepseek-v4-pro-ioa",
        )
        done = service.wait(job.job_id)
        self.assertEqual(done.parallel, 1)
        self.assertEqual(done.model, "deepseek-v4-pro-ioa")
        self.assertEqual(fake.requests[0].parallel, 1)
        self.assertEqual(
            fake.requests[0].model,
            "deepseek-v4-pro-ioa",
        )

        with self.assertRaisesRegex(ValueError, "至少为 1"):
            service.start(
                "test-session",
                "tester",
                parallel=0,
            )
        with self.assertRaisesRegex(ValueError, "不能为空"):
            service.start(
                "test-session",
                "tester",
                model="  ",
            )
        with self.assertRaisesRegex(ValueError, "不支持"):
            service.start(
                "test-session",
                "tester",
                model="not-a-workbuddy-model",
            )

    def test_partial_job_can_retry_only_failed_case(self):
        fake = FakeRunner(fail_once={"case-b"})
        service = GenerationJobService(
            {"test-session": self.session},
            self.settings,
            fake,
        )
        first, _ = service.start(
            "test-session",
            "tester",
            model="retry-model",
        )
        first = service.wait(first.job_id)
        self.assertEqual(first.status, "partial")
        self.assertEqual(first.failed_case_ids, ["case-b"])

        retry, _ = service.retry(
            first.job_id,
            "tester",
            parallel=1,
            model="retry-override-model",
        )
        retry = service.wait(retry.job_id)
        self.assertEqual(retry.status, "completed")
        self.assertEqual(retry.parent_job_id, first.job_id)
        self.assertEqual(retry.parallel, 1)
        self.assertEqual(retry.model, "retry-override-model")
        self.assertEqual(
            fake.requests[-1].model,
            "retry-override-model",
        )
        self.assertEqual(fake.calls[-1], ["case-b"])
        self.assertIn("case-b", self.session.report_outputs["v0"])

    def test_second_start_reuses_active_session_job(self):
        fake = FakeRunner()
        fake.release = threading.Event()
        service = GenerationJobService(
            {"test-session": self.session},
            self.settings,
            fake,
        )
        first, _ = service.start("test-session", "tester")
        self.assertTrue(fake.started.wait(1))
        second, reused = service.start("test-session", "tester")
        self.assertTrue(reused)
        self.assertEqual(first.job_id, second.job_id)
        fake.release.set()
        self.assertEqual(
            service.wait(first.job_id).status,
            "completed",
        )

    def test_changed_execution_skill_blocks_automatic_import(self):
        fake = FakeRunner()
        fake.release = threading.Event()
        service = GenerationJobService(
            {"test-session": self.session},
            self.settings,
            fake,
        )
        job, _ = service.start("test-session", "tester")
        self.assertTrue(fake.started.wait(1))
        (Path(job.skill_ref) / "SKILL.md").write_text(
            "---\nname: research-report\n---\n# Changed\n",
            encoding="utf-8",
        )
        fake.release.set()

        done = service.wait(job.job_id)
        self.assertEqual(done.status, "failed")
        self.assertIn("执行 Skill 文件", done.error)
        self.assertNotIn(
            "v0",
            self.session.report_outputs,
        )

    def test_active_job_is_marked_interrupted_after_restart(self):
        now = 1.0
        job = GenerationJob(
            job_id="job-before-restart",
            session_id="test-session",
            account="tester",
            skill_version="v0",
            skill_artifact_hash="a" * 64,
            execution_skill_hash="b" * 64,
            dataset_path=str(self.dataset),
            skill_mode="fixed_path",
            skill_ref=str(self.skill),
            model="fake",
            parallel=1,
            max_report_retries=3,
            timeout_seconds=10,
            stall_timeout_seconds=5,
            created_at=now,
            updated_at=now,
            compiler_version=None,
            status="running",
            cases=[GenerationCaseState("case-a", "dev")],
        )
        persist.save_generation_job(
            "test-session",
            job.job_id,
            job.to_dict(),
        )
        service = GenerationJobService(
            {"test-session": self.session},
            self.settings,
            FakeRunner(),
        )
        restored = service.get(job.job_id)
        self.assertEqual(restored.status, "interrupted")
        self.assertIn("服务重启", restored.error)


if __name__ == "__main__":
    unittest.main()
