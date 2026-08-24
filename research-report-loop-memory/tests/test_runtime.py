from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.report_loop.core.runtime import ReportLoopError, ReportLoopRuntime
from mcp.report_loop.core import workbuddy_cli


CHECK_PATTERN = re.compile(r"^- ([^（\s]+)（", re.MULTILINE)


class PersonalRubricProvider:
    def load(self, *, audience: str = "", project: str = "") -> dict:
        return {
            "status": "loaded",
            "revision": "memory-head",
            "rubricSetVersion": "v1",
            "documents": [{"path": "system/rubrics.json", "scope": "core"}],
            "warnings": [],
            "items": [{
                "id": "P1",
                "criterionKey": "personal.forwardable_summary",
                "operation": "add",
                "dimension": "personal",
                "label": "可转发摘要",
                "desc": "交付时包含一行可以直接转发的摘要。",
                "effect": "缺少可转发摘要会增加二次编辑成本。",
                "redline": False,
                "status": "active",
                "sourceL1Ids": ["atom-personal"],
                "scope": "core",
                "scopeValue": None,
                "sourcePath": "system/rubrics.json",
            }],
        }


class JudgeFixture:
    def __init__(
        self,
        versions: list[dict[str, str]],
        *,
        delay_seconds: float = 0,
    ) -> None:
        self.versions = versions
        self.calls = 0
        self.delay_seconds = delay_seconds
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def __call__(self, prompt: str) -> str:
        with self.lock:
            call_index = self.calls
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay_seconds:
                time.sleep(self.delay_seconds)
            version_index = min(call_index // 6, len(self.versions) - 1)
            check_ids = CHECK_PATTERN.findall(prompt)
            values = self.versions[version_index]
            return json.dumps(
                {
                    "checks": {
                        check_id: values.get(check_id, "met")
                        for check_id in check_ids
                    },
                    "reasoning": {
                        check_id: f"fixture:{values.get(check_id, 'met')}"
                        for check_id in check_ids
                    },
                },
                ensure_ascii=False,
            )
        finally:
            with self.lock:
                self.active -= 1


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.report = self.root / "report.md"
        self.report.write_text("# 报告\n\n" + "有效报告内容。" * 120, encoding="utf-8")
        self.rubric = ROOT / "rubrics" / "v2_rubric_research.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def runtime(self, judge: JudgeFixture) -> ReportLoopRuntime:
        return ReportLoopRuntime(
            data_dir=self.root / "data",
            rubric_path=self.rubric,
            judge_call=judge,
        )

    def start(self, runtime: ReportLoopRuntime) -> str:
        result = runtime.start(
            task="根据给定材料撰写战略研究报告",
            audience="管理层",
            project="测试项目",
            artifactPath=str(self.report),
        )
        self.assertEqual(result["judgeModel"], "deepseek-v4-flash-ioa")
        self.assertEqual(result["judgeEffort"], "medium")
        self.assertEqual(result["judgeStrategy"], "per_dimension")
        self.assertEqual(result["judgeParallelism"], 6)
        return result["runId"]

    def test_six_dimensions_run_concurrently(self) -> None:
        judge = JudgeFixture([{}], delay_seconds=0.08)
        runtime = self.runtime(judge)
        run_id = self.start(runtime)
        runtime.submit(runId=run_id, artifactPath=str(self.report))
        self.assertEqual(judge.calls, 6)
        self.assertEqual(judge.max_active, 6)
        state = runtime.status(runId=run_id)
        meta = state["revisions"][0]["judgment"]["judgeMeta"]
        self.assertEqual(meta["dimension_parallelism"], 6)

    def test_personal_rubric_adds_a_seventh_parallel_judge_only_when_active(self) -> None:
        judge = JudgeFixture([{}], delay_seconds=0.05)
        runtime = ReportLoopRuntime(
            data_dir=self.root / "personal-data",
            rubric_path=self.rubric,
            judge_call=judge,
            memory_provider=PersonalRubricProvider(),
        )
        started = runtime.start(
            task="根据给定材料撰写战略研究报告",
            audience="管理层",
            project="测试项目",
            artifactPath=str(self.report),
        )
        self.assertEqual(started["rubricSetVersion"], "v1")
        self.assertTrue(started["personalRubricsActive"])
        self.assertEqual(started["judgeParallelism"], 7)
        result = runtime.submit(runId=started["runId"], artifactPath=str(self.report))
        self.assertEqual(judge.calls, 7)
        self.assertIn("personal", result["dimensions"])

    def test_target_five_stops_after_first_version(self) -> None:
        judge = JudgeFixture([{}])
        runtime = self.runtime(judge)
        run_id = self.start(runtime)
        result = runtime.submit(runId=run_id, artifactPath=str(self.report))
        self.assertEqual(judge.calls, 6)
        self.assertEqual(result["overall"], 5.0)
        self.assertEqual(result["nextAction"], "deliver")
        self.assertEqual(result["stopCode"], "target_reached")
        final = runtime.finish(runId=run_id)
        self.assertEqual(final["bestVersion"], "v1")
        self.assertTrue(Path(final["bestArtifactPath"]).is_file())

    def test_max_three_versions_returns_best_adopted_version(self) -> None:
        partial = {check_id: "partial" for check_id in self._check_ids()}
        improved = {**partial, "I1": "met"}
        judge = JudgeFixture([partial, improved, improved])
        runtime = self.runtime(judge)
        run_id = self.start(runtime)
        first = runtime.submit(runId=run_id, artifactPath=str(self.report))
        self.assertEqual(first["nextAction"], "revise")
        second = runtime.submit(runId=run_id, artifactPath=str(self.report))
        self.assertEqual(second["decision"], "accepted")
        third = runtime.submit(runId=run_id, artifactPath=str(self.report))
        self.assertEqual(third["decision"], "rejected")
        self.assertEqual(third["stopCode"], "max_versions_reached")
        self.assertEqual(third["bestVersion"], "v2")
        self.assertEqual(judge.calls, 18)

    def test_rejected_regression_never_becomes_revision_baseline(self) -> None:
        partial = {check_id: "partial" for check_id in self._check_ids()}
        regression = {**partial, "I1": "met", "T1": "miss"}
        judge = JudgeFixture([partial, regression])
        runtime = self.runtime(judge)
        run_id = self.start(runtime)
        runtime.submit(runId=run_id, artifactPath=str(self.report))
        second = runtime.submit(runId=run_id, artifactPath=str(self.report))
        self.assertEqual(second["decision"], "rejected")
        self.assertEqual(second["bestVersion"], "v1")
        self.assertEqual(second["revisionBrief"]["baseVersion"], "v1")
        self.assertIn("T1", {item["checkId"] for item in second["revisionBrief"]["avoid"]})

    def test_finish_cannot_bypass_active_loop(self) -> None:
        partial = {check_id: "partial" for check_id in self._check_ids()}
        runtime = self.runtime(JudgeFixture([partial]))
        run_id = self.start(runtime)
        runtime.submit(runId=run_id, artifactPath=str(self.report))
        with self.assertRaises(ReportLoopError):
            runtime.finish(runId=run_id)
        result = runtime.finish(runId=run_id, reason="judge_unavailable")
        self.assertEqual(result["stopCode"], "judge_unavailable")

    def _check_ids(self) -> list[str]:
        rubric = json.loads(self.rubric.read_text(encoding="utf-8"))
        return [check["id"] for dimension in rubric["dimensions"] for check in dimension["checks"]]


class McpServerTests(unittest.TestCase):
    def test_initialize_and_tool_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(os.environ)
            environment["RESEARCH_REPORT_LOOP_DIR"] = temporary
            requests = "\n".join(
                [
                    json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05"},
                    }),
                    json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
                    "",
                ]
            )
            completed = subprocess.run(
                [sys.executable, str(ROOT / "mcp" / "report_loop" / "server.py")],
                input=requests,
                text=True,
                capture_output=True,
                env=environment,
                timeout=10,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            responses = [json.loads(line) for line in completed.stdout.splitlines()]
            self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "research-report-loop")
            names = {item["name"] for item in responses[1]["result"]["tools"]}
            self.assertEqual(
                names,
                {
                    "report_loop_start",
                    "report_loop_submit",
                    "report_loop_finish",
                    "report_loop_status",
                },
            )


class WorkBuddyCliTests(unittest.TestCase):
    def test_judge_is_single_turn_medium_effort_by_default(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                json.dumps({
                    "type": "result",
                    "result": '{"checks": {}}',
                })
                + "\n"
            ),
            stderr="",
        )
        with (
            mock.patch.object(
                workbuddy_cli,
                "discover_command",
                return_value=("codebuddy",),
            ),
            mock.patch.object(
                workbuddy_cli.subprocess,
                "run",
                return_value=completed,
            ) as run,
        ):
            workbuddy_cli.call_workbuddy("judge this report")
        args = run.call_args.args[0]
        self.assertEqual(
            args[args.index("--model") + 1],
            "deepseek-v4-flash-ioa",
        )
        self.assertEqual(args[args.index("--effort") + 1], "medium")
        self.assertEqual(args[args.index("--max-turns") + 1], "1")


if __name__ == "__main__":
    unittest.main()
