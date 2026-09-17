"""报告目录布局的端到端测试。

验证 Loop 产物真的落在 `报告/.report-agent/<报告标识>/loop-NNN-时间戳/` 下，
交付版本发布为 `报告/<报告主题>-vN.md`，以及用户大改重启 Loop 时沿用同一
报告标识、版本号接着递增。
"""

import json
import tempfile
import unittest
from pathlib import Path

from report_loop.core.workspace import (
    CANDIDATES_DIR_NAME,
    RECORDS_DIR_NAME,
    delivered_versions,
    existing_loop_dirs,
    find_existing_report_ids,
)
from report_loop.runner import JobError, load_job, run


class StubRuntime:
    """最小 Runtime：记录 start 收到的路径，把候选写进 Loop 目录。"""

    instances: list["StubRuntime"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.loop_dir: Path | None = None
        StubRuntime.instances.append(self)

    def start(self, **kwargs):
        self.start_kwargs = kwargs
        self.loop_dir = Path(kwargs["loopDir"])
        records = self.loop_dir / RECORDS_DIR_NAME
        records.mkdir(parents=True, exist_ok=True)
        (records / "run-state.json").write_text("{}", encoding="utf-8")
        (records / "resolution-plan.json").write_text("{}", encoding="utf-8")
        if kwargs.get("roundRequest"):
            (self.loop_dir / "本轮需求.md").write_text(
                kwargs["roundRequest"], encoding="utf-8"
            )
        candidates = self.loop_dir / CANDIDATES_DIR_NAME
        candidates.mkdir(parents=True, exist_ok=True)
        self.best = candidates / "R0.md"
        self.best.write_text("# 本轮最佳报告\n", encoding="utf-8")
        (records / "judgments").mkdir(parents=True, exist_ok=True)
        (records / "judgments" / "R0.json").write_text("{}", encoding="utf-8")
        return {"runId": "report-stub0001"}

    def deadline_at(self, run_id):
        import time

        return time.time() + 600

    def run_directory(self, run_id):
        return self.loop_dir

    def submit(self, **kwargs):
        return {
            "status": "completed",
            "nextAction": "deliver",
            "bestVersion": "v1",
            "bestScore": 4.8,
            "bestArtifactPath": str(self.best),
            "judgedVersions": 1,
            "stopCode": "target_reached",
            "stopReason": "达到目标分数",
            "scoresComparableToHistory": True,
        }

    def finish(self, **kwargs):
        return self.submit()


class LayoutTestCase(unittest.TestCase):
    def setUp(self):
        StubRuntime.instances.clear()
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve()
        self.materials = self.root / "项目" / "素材"
        self.materials.mkdir(parents=True)
        (self.materials / "访谈.txt").write_text("用户访谈内容", encoding="utf-8")
        self.draft = self.materials / "draft.md"
        self.draft.write_text("# 初稿\n" + "内容。\n" * 50, encoding="utf-8")
        self.addCleanup(self._temp.cleanup)

    def job(self, **overrides) -> dict:
        payload = {
            "schemaVersion": 2,
            "originalUserQuery": "写一份留存分析报告",
            "reportTopic": "留存分析",
            "intakeContext": {
                "reportBackground": {"value": "给管理层看"},
                "materialHypothesis": {"value": "留存在改善"},
                "priorityMaterials": [
                    {"path": str(self.materials / "访谈.txt"), "displayName": "访谈"}
                ],
                "userInputEvidence": {
                    "reportBackground": "给管理层看，用于资源决策",
                    "materialHypothesis": "我想验证留存是否改善",
                    "priorityMaterials": "重点看访谈",
                },
            },
            "draftArtifactPath": str(self.draft),
            "hostModel": {},
        }
        payload.update(overrides)
        return payload

    def write_job(self, payload: dict) -> Path:
        path = self.root / "job.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def load(self, **overrides) -> dict:
        from unittest.mock import patch

        with patch(
            "report_loop.runner.resolve_host_model_id",
            return_value="stub-model",
        ):
            return load_job(self.write_job(self.job(**overrides)))

    @property
    def report_dir(self) -> Path:
        return self.materials / "报告"


class DerivedLayoutTests(LayoutTestCase):
    def test_report_dir_defaults_beside_materials(self):
        job = self.load()

        self.assertEqual(Path(job["materialRoot"]), self.materials)
        self.assertEqual(Path(job["reportDir"]), self.report_dir)
        self.assertEqual(job["reportTopic"], "留存分析")
        self.assertTrue(job["reportId"].startswith("留存分析-"))

    def test_output_path_is_optional(self):
        job = self.load()
        self.assertIsNone(job["outputPath"])

    def test_output_path_outside_report_dir_is_rejected(self):
        with self.assertRaises(JobError) as caught:
            self.load(
                reportDir=str(self.report_dir),
                outputPath="/etc/passwd",
            )

        self.assertIn("超出报告目录范围", str(caught.exception))

    def test_loop_artifacts_land_inside_report_dir(self):
        job = self.load()

        result = run(job, runtime_factory=StubRuntime)

        loop_dir = Path(result["loopDir"])
        self.assertTrue(loop_dir.is_dir())
        # 关键：Loop 产物在报告目录内，不在系统目录或会话目录
        self.assertEqual(
            loop_dir.parent.parent, self.report_dir / ".report-agent"
        )
        self.assertRegex(loop_dir.name, r"^loop-001-\d{8}-\d{6}$")
        self.assertTrue((loop_dir / CANDIDATES_DIR_NAME / "R0.md").is_file())
        self.assertTrue((loop_dir / RECORDS_DIR_NAME / "run-state.json").is_file())
        self.assertTrue(
            (loop_dir / RECORDS_DIR_NAME / "judgments" / "R0.json").is_file()
        )

    def test_best_candidate_is_published_as_v1(self):
        job = self.load()

        result = run(job, runtime_factory=StubRuntime)

        delivered = Path(result["finalArtifactPath"])
        self.assertEqual(delivered.name, "留存分析-v1.md")
        self.assertEqual(delivered.parent, self.report_dir)
        self.assertEqual(delivered.read_text(encoding="utf-8"), "# 本轮最佳报告\n")

    def test_version_ledger_records_the_delivery(self):
        job = self.load(dataVersion="D2", dataSha256="a" * 64)

        result = run(job, runtime_factory=StubRuntime)

        ledger = Path(result["versionLedgerPath"]).read_text(encoding="utf-8")
        self.assertIn("留存分析-v1.md", ledger)
        self.assertIn("D2", ledger)
        self.assertIn("4.8 分", ledger)
        self.assertIn("达到目标分数", ledger)

    def test_round_request_is_stored_in_the_loop_dir(self):
        job = self.load(roundRequest="本轮要突出留存下滑的原因")

        result = run(job, runtime_factory=StubRuntime)

        request = Path(result["loopDir"]) / "本轮需求.md"
        self.assertIn("留存下滑", request.read_text(encoding="utf-8"))


class RestartLoopEndToEndTests(LayoutTestCase):
    """用户看完报告要大改并重启 Loop。"""

    def test_second_loop_reuses_report_id_and_bumps_version(self):
        first = run(self.load(), runtime_factory=StubRuntime)
        first_loop = Path(first["loopDir"])
        report_id = first["reportId"]

        # 用户大改：沿用同一报告标识重启 Loop
        second = run(
            self.load(
                reportId=report_id,
                roundRequest="整体重写，改成决策建议导向",
            ),
            runtime_factory=StubRuntime,
        )
        second_loop = Path(second["loopDir"])

        self.assertEqual(second["reportId"], report_id)
        # 两轮并列在同一报告工作目录下，首轮完整保留
        self.assertEqual(first_loop.parent, second_loop.parent)
        self.assertRegex(second_loop.name, r"^loop-002-")
        self.assertTrue((first_loop / CANDIDATES_DIR_NAME / "R0.md").is_file())
        self.assertEqual(
            len(existing_loop_dirs(self.report_dir, report_id)), 2
        )

        # 交付版本递增，旧版保留
        self.assertEqual(
            Path(second["finalArtifactPath"]).name, "留存分析-v2.md"
        )
        self.assertTrue((self.report_dir / "留存分析-v1.md").is_file())
        self.assertEqual(delivered_versions(self.report_dir, "留存分析"), [1, 2])

    def test_report_id_is_reused_automatically_for_the_same_topic(self):
        first = run(self.load(), runtime_factory=StubRuntime)

        # 新会话未传 reportId：按主题查到已有标识并沿用，不另建报告
        second = run(self.load(), runtime_factory=StubRuntime)

        self.assertEqual(second["reportId"], first["reportId"])
        self.assertEqual(
            find_existing_report_ids(self.report_dir, "留存分析"),
            [first["reportId"]],
        )

    def test_ledger_accumulates_one_row_per_delivery(self):
        run(self.load(), runtime_factory=StubRuntime)
        result = run(self.load(roundRequest="大改"), runtime_factory=StubRuntime)

        ledger = Path(result["versionLedgerPath"]).read_text(encoding="utf-8")

        self.assertIn("留存分析-v1.md", ledger)
        self.assertIn("留存分析-v2.md", ledger)
        self.assertIn("当前版本：留存分析-v2.md", ledger)

    def test_different_topic_starts_its_own_report(self):
        first = run(self.load(), runtime_factory=StubRuntime)
        other = run(
            self.load(reportTopic="增长复盘", originalUserQuery="写增长复盘"),
            runtime_factory=StubRuntime,
        )

        self.assertNotEqual(other["reportId"], first["reportId"])
        self.assertEqual(
            Path(other["finalArtifactPath"]).name, "增长复盘-v1.md"
        )
        self.assertEqual(delivered_versions(self.report_dir, "留存分析"), [1])
        self.assertEqual(delivered_versions(self.report_dir, "增长复盘"), [1])


class UserSpecifiedLocationTests(LayoutTestCase):
    def test_explicit_report_dir_takes_precedence(self):
        target = self.root / "交付物"

        job = self.load(reportDir=str(target))
        result = run(job, runtime_factory=StubRuntime)

        self.assertEqual(Path(job["reportDir"]), target)
        self.assertEqual(Path(result["finalArtifactPath"]).parent, target)
        self.assertTrue((target / ".report-agent").is_dir())

    def test_explicit_output_path_is_honoured(self):
        target = self.report_dir / "自定义名称.md"

        result = run(
            self.load(reportDir=str(self.report_dir), outputPath=str(target)),
            runtime_factory=StubRuntime,
        )

        self.assertEqual(Path(result["finalArtifactPath"]), target)
        self.assertEqual(target.read_text(encoding="utf-8"), "# 本轮最佳报告\n")


if __name__ == "__main__":
    unittest.main()
