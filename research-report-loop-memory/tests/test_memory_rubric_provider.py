from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.report_loop.core.memory_rubric_provider import MemoryRubricProvider
from mcp.report_loop.core.runtime import ReportLoopError
from mcp.report_loop.runner import run


class MemoryRubricProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.memory_dir = self.root / "memory"
        (self.memory_dir / "settings.json").parent.mkdir(parents=True, exist_ok=True)
        (self.memory_dir / "settings.json").write_text(
            json.dumps({"schemaVersion": 1, "memoryEnabled": True}),
            encoding="utf-8",
        )
        self.repository = self.memory_dir / "l2b-rubrics" / "personal" / "default"
        self.repository.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=self.repository, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repository, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repository, check=True)
        self._write("manifest.json", {
            "schemaVersion": 1,
            "version": "v1",
            "versionNumber": 1,
        })
        self._write("system/rubrics.json", self._document("core", None, [
            self._rubric("MR-SHARED", "通用摘要要求。", "atom-core"),
        ]))
        self._write("audiences/总办-m/rubrics.json", self._document(
            "audience", "总办 M（Martin）", [
                self._rubric("MR-SHARED", "面向 M 时先给讨论事项。", "atom-m"),
            ],
        ))
        self._write("audiences/board/rubrics.json", self._document(
            "audience", "董事会", [self._rubric("MR-BOARD", "董事会版本突出风险。", "atom-board")],
        ))
        self._write("projects/ds/rubrics.json", self._document(
            "project", "DS 用户时长", [self._rubric("MR-DS", "拆解频次和单次时长。", "atom-ds")],
        ))
        subprocess.run(["git", "add", "."], cwd=self.repository, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=self.repository, check=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _rubric(item_id: str, statement: str, source: str) -> dict:
        return {
            "id": item_id,
            "statement": statement,
            "status": "active",
            "sourceL1Ids": [source],
        }

    @staticmethod
    def _document(scope: str, scope_value: str | None, rubrics: list[dict]) -> dict:
        value = {"schemaVersion": 3, "scope": scope, "rubrics": rubrics}
        if scope_value is not None:
            value["scopeValue"] = scope_value
        return value

    def _write(self, relative_path: str, value: dict) -> None:
        target = self.repository / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_load_exposes_all_scope_candidates_for_model_resolution(self) -> None:
        snapshot = MemoryRubricProvider(self.memory_dir).load(
            audience="总办M（总裁/最高管理层）",
            project="外部 AI 提效",
        )
        self.assertEqual(snapshot["status"], "loaded")
        self.assertEqual(snapshot["queryContext"], {
            "audience": "总办M（总裁/最高管理层）",
            "project": "外部 AI 提效",
        })
        self.assertEqual(
            {item["scopeValue"] for item in snapshot["items"] if item["scope"] == "audience"},
            {"总办 M（Martin）", "董事会"},
        )
        self.assertIn("DS 用户时长", {
            item["scopeValue"] for item in snapshot["items"] if item["scope"] == "project"
        })

    def test_duplicate_stable_ids_remain_distinct_resolution_candidates(self) -> None:
        items = MemoryRubricProvider(self.memory_dir).load()["items"]
        shared = [item for item in items if item.get("sourceMemoryId") == "MR-SHARED"]
        self.assertEqual(len(shared), 2)
        self.assertEqual(len({item["id"] for item in shared}), 2)

    def test_default_disabled_hides_existing_memory_rubrics(self) -> None:
        (self.memory_dir / "settings.json").unlink()
        snapshot = MemoryRubricProvider(self.memory_dir).load(
            audience="总办M（总裁/最高管理层）",
            project="DS 用户时长",
        )
        self.assertEqual(snapshot["status"], "disabled")
        self.assertEqual(snapshot["items"], [])
        self.assertEqual(snapshot["documents"], [])


class RunnerMemoryProviderTests(unittest.TestCase):
    def test_runner_uses_default_local_memory_without_mcp_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "report.md"
            output = root / "final.md"
            report.write_text("# report\n", encoding="utf-8")
            captured: dict = {}

            class FakeRuntime:
                def __init__(self, **kwargs):
                    captured.update(kwargs)

                def start(self, **kwargs):
                    return {"runId": "run-1"}

                def deadline_at(self, run_id):
                    return 9_999_999_999.0

                def submit(self, **kwargs):
                    return {
                        "status": "completed",
                        "nextAction": "deliver",
                        "bestArtifactPath": str(report),
                        "bestVersion": "v1",
                    }

            job = {
                "originalUserQuery": "写报告",
                "intakeContext": {},
                "hostModel": {"modelId": "deepseek-v4-flash-ioa"},
                "judgeProvider": "workbuddy",
                "audience": "",
                "project": "",
                "v1ArtifactPath": str(report),
                "outputPath": str(output),
            }
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("RESEARCH_REPORT_MEMORY_V2_0821_DIR", None)
                run(job, runtime_factory=FakeRuntime)
            provider = captured["memory_provider"]
            self.assertEqual(
                provider.memory_data_dir,
                (Path.home() / ".research-report-memory-v2-0821").resolve(),
            )

    def test_runner_delivers_v1_when_first_judge_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "report-v1.md"
            output = root / "report-final.md"
            report.write_text("# 可交付的 V1\n", encoding="utf-8")

            class FailingRuntime:
                def __init__(self, **kwargs):
                    pass

                def start(self, **kwargs):
                    return {"runId": "run-failed-judge"}

                def deadline_at(self, run_id):
                    return 9_999_999_999.0

                def submit(self, **kwargs):
                    raise ReportLoopError("Judge transport unavailable")

            job = {
                "originalUserQuery": "写报告",
                "intakeContext": {},
                "hostModel": {"modelId": "deepseek-v4-pro-ioa"},
                "judgeProvider": "workbuddy",
                "audience": "",
                "project": "",
                "v1ArtifactPath": str(report),
                "outputPath": str(output),
            }
            result = run(job, runtime_factory=FailingRuntime)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["stopCode"], "judge_unavailable")
            self.assertEqual(result["judgedVersions"], 0)
            self.assertEqual(Path(result["finalArtifactPath"]), output.resolve())
            self.assertEqual(output.read_text(encoding="utf-8"), "# 可交付的 V1\n")


if __name__ == "__main__":
    unittest.main()
