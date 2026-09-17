"""工作区路径推导与记忆授权的测试。

两条不变量：

1. **产物只落在素材目录内** —— 中间稿与历史不得散落到会话目录或系统目录，
   越界路径必须被拒绝，而不是静默写出去。
2. **记忆可写性在用到之前确认** —— 记忆目录在工作区之外，若等到 capture 才
   发现写不了，那一轮的用户反馈就已经丢了。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "resources/report/workspace.py"
MEMORY_ACCESS = ROOT / "resources/report/memory_access.py"


def run(script: Path, *args: str, env: dict[str, str] | None = None) -> tuple[int, dict]:
    merged = {**os.environ, **(env or {})}
    completed = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, encoding="utf-8", env=merged,
    )
    try:
        return completed.returncode, json.loads(completed.stdout)
    except json.JSONDecodeError:  # pragma: no cover - 便于调试
        raise AssertionError(
            f"stdout 不是 JSON: {completed.stdout!r} stderr={completed.stderr!r}"
        )


class WorkspaceResolveTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve()
        self.materials = self.root / "项目" / "素材"
        self.materials.mkdir(parents=True)
        (self.materials / "访谈.txt").write_text("访谈", encoding="utf-8")
        (self.materials / "问卷.xlsx").write_text("问卷", encoding="utf-8")
        self.addCleanup(self._temp.cleanup)

    def resolve(self, *extra: str) -> dict:
        code, payload = run(
            WORKSPACE, "resolve",
            "--material", str(self.materials / "访谈.txt"),
            "--material", str(self.materials / "问卷.xlsx"),
            "--topic", "留存分析", *extra,
        )
        self.assertEqual(code, 0, payload)
        return payload

    def test_products_land_beside_the_materials(self):
        payload = self.resolve()

        self.assertEqual(Path(payload["materialRoot"]), self.materials)
        self.assertEqual(Path(payload["reportDir"]), self.materials / "报告")
        # 论据表与数据版本说明在素材目录，不在报告目录
        self.assertEqual(
            Path(payload["structuredDataPath"]).parent, self.materials
        )
        self.assertEqual(Path(payload["dataLedgerPath"]).parent, self.materials)
        # 内部记录在报告目录下的隐藏目录里
        self.assertIn(".report-agent", payload["reportWorkDir"])
        self.assertTrue(
            Path(payload["reportWorkDir"]).is_relative_to(self.materials / "报告")
        )

    def test_no_work_area_is_created_elsewhere(self):
        """解析后除报告目录外不得新建其他目录。"""
        before = {p for p in self.root.rglob("*") if p.is_dir()}
        self.resolve()
        after = {p for p in self.root.rglob("*") if p.is_dir()}

        created = after - before
        for path in created:
            self.assertTrue(
                path.is_relative_to(self.materials / "报告"),
                f"在报告目录之外新建了 {path}",
            )

    def test_user_specified_report_dir_wins(self):
        target = self.root / "交付物"

        payload = self.resolve("--report-dir", str(target))

        self.assertEqual(Path(payload["reportDir"]), target)

    def test_second_session_continues_the_same_report(self):
        first = self.resolve()
        code, _ = run(
            WORKSPACE, "new-loop",
            "--report-dir", first["reportDir"],
            "--report-id", first["reportId"],
        )
        self.assertEqual(code, 0)
        (Path(first["reportWorkDir"]) / "loop-001-20260101-000000").mkdir(parents=True)

        second = self.resolve()

        self.assertEqual(second["reportId"], first["reportId"])
        self.assertTrue(second["continuedExistingReport"])

    def test_scattered_materials_require_explicit_location(self):
        code, payload = run(
            WORKSPACE, "resolve",
            "--material", str(Path.home() / "a.txt"),
            "--material", str(self.materials / "访谈.txt"),
            "--topic", "留存分析",
        )

        self.assertEqual(code, 1)
        self.assertEqual(payload["marker"], "WORKSPACE_FAILED")

    def test_missing_material_is_rejected(self):
        code, payload = run(
            WORKSPACE, "resolve",
            "--material", str(self.materials / "不存在.txt"),
            "--topic", "留存分析",
        )

        self.assertEqual(code, 1)
        self.assertIn("不存在", payload["error"])


class WorkspaceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve()
        self.materials = self.root / "素材"
        self.materials.mkdir(parents=True)
        (self.materials / "访谈.txt").write_text("访谈", encoding="utf-8")
        code, payload = run(
            WORKSPACE, "resolve",
            "--material", str(self.materials / "访谈.txt"),
            "--topic", "留存分析",
        )
        self.assertEqual(code, 0, payload)
        self.context = payload
        self.addCleanup(self._temp.cleanup)

    def check(self, path: str) -> tuple[int, dict]:
        return run(
            WORKSPACE, "check",
            "--report-dir", self.context["reportDir"],
            "--path", path, "--label", "outputPath",
        )

    def test_paths_inside_the_report_dir_pass(self):
        code, payload = self.check(self.context["nextVersionPath"])

        self.assertEqual(code, 0)
        self.assertEqual(payload["marker"], "WORKSPACE_PATH_OK")

    def test_system_and_home_paths_are_rejected(self):
        for outside in ("/etc/passwd", str(Path.home() / "Desktop" / "报告.md")):
            with self.subTest(path=outside):
                code, payload = self.check(outside)
                self.assertEqual(code, 1)
                self.assertEqual(payload["marker"], "WORKSPACE_FAILED")

    def test_material_root_itself_is_outside_the_report_dir(self):
        """产物不得散落到素材目录根，只能在报告目录内。"""
        code, payload = self.check(str(self.materials / "散落.md"))

        self.assertEqual(code, 1)
        self.assertIn("超出报告目录范围", payload["error"])

    def test_loop_sequence_increments_and_keeps_history(self):
        first_code, first = run(
            WORKSPACE, "new-loop",
            "--report-dir", self.context["reportDir"],
            "--report-id", self.context["reportId"],
        )
        self.assertEqual(first_code, 0)
        Path(first["loopDir"]).mkdir(parents=True)

        second_code, second = run(
            WORKSPACE, "new-loop",
            "--report-dir", self.context["reportDir"],
            "--report-id", self.context["reportId"],
        )

        self.assertEqual(second_code, 0)
        self.assertTrue(first["loopName"].startswith("loop-001-"))
        self.assertTrue(second["loopName"].startswith("loop-002-"))
        # 旧 Loop 目录仍在，历史不丢
        self.assertTrue(Path(first["loopDir"]).is_dir())

    def test_loop_paths_follow_the_convention(self):
        code, payload = run(
            WORKSPACE, "new-loop",
            "--report-dir", self.context["reportDir"],
            "--report-id", self.context["reportId"],
        )
        self.assertEqual(code, 0)

        loop = Path(payload["loopDir"])
        self.assertEqual(Path(payload["roundRequest"]).name, "本轮需求.md")
        self.assertEqual(Path(payload["candidatesDir"]).name, "候选报告")
        self.assertEqual(Path(payload["recordsDir"]).name, "评测与改写记录")
        self.assertEqual(Path(payload["judgmentsDir"]).parent, loop / "评测与改写记录")

    def test_delivery_versions_share_one_sequence(self):
        report_dir = Path(self.context["reportDir"])
        for version in (1, 2):
            (report_dir / f"留存分析-v{version}.md").write_text("x", encoding="utf-8")

        code, payload = run(
            WORKSPACE, "next-version",
            "--report-dir", str(report_dir), "--topic", "留存分析",
        )

        self.assertEqual(code, 0)
        self.assertEqual(payload["version"], 3)
        self.assertEqual(payload["deliveredVersions"], [1, 2])


class MemoryAccessTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve()
        self.fake_home = self.root / "home"
        self.fake_home.mkdir()
        self.addCleanup(self._temp.cleanup)

    def env(self, **extra: str) -> dict[str, str]:
        base = {"HOME": str(self.fake_home), "USERPROFILE": str(self.fake_home)}
        base.pop("REPORT_AGENT_MEMORY_DIR", None)
        return {**base, "REPORT_AGENT_MEMORY_DIR": "", **extra}

    def test_writable_home_needs_no_authorization(self):
        code, payload = run(MEMORY_ACCESS, "probe", env=self.env())

        self.assertEqual(code, 0)
        self.assertEqual(payload["marker"], "MEMORY_ACCESS_OK")
        self.assertEqual(
            Path(payload["memoryRoot"]), self.fake_home / "ReportAgentMemory"
        )
        self.assertFalse(payload["initialized"])

    def test_memory_root_is_outside_the_report_workspace(self):
        code, payload = run(MEMORY_ACCESS, "probe", env=self.env())

        self.assertEqual(code, 0)
        # 记忆跨项目共用，不在任何报告目录内
        self.assertNotIn("报告", payload["memoryRoot"])
        self.assertNotIn(".report-agent", payload["memoryRoot"])

    def test_unwritable_home_asks_for_authorization(self):
        locked = self.root / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)

        code, payload = run(
            MEMORY_ACCESS, "probe",
            env=self.env(HOME=str(locked), USERPROFILE=str(locked)),
        )

        # 需要授权不是脚本失败：主理人据此询问用户
        self.assertEqual(code, 0)
        self.assertEqual(payload["marker"], "MEMORY_ACCESS_NEEDS_AUTHORIZATION")
        self.assertFalse(payload["writable"])
        self.assertIn("reason", payload)
        self.assertIn("suggestedPrompt", payload)
        actions = {item["action"] for item in payload["options"]}
        self.assertEqual(actions, {"authorize", "relocate", "disable"})

    def test_relocation_is_remembered_across_sessions(self):
        target = self.root / "记忆位置"
        target.mkdir()

        code, recorded = run(
            MEMORY_ACCESS, "set-root", "--path", str(target), env=self.env()
        )
        self.assertEqual(code, 0)
        self.assertEqual(recorded["marker"], "MEMORY_ACCESS_RECORDED")

        code, payload = run(MEMORY_ACCESS, "probe", env=self.env())

        self.assertEqual(code, 0)
        self.assertEqual(payload["marker"], "MEMORY_ACCESS_OK")
        self.assertEqual(payload["source"], "recorded")
        self.assertEqual(Path(payload["memoryRoot"]), target.resolve())

    def test_relocation_to_an_unwritable_place_is_refused(self):
        locked = self.root / "locked-target"
        locked.mkdir()
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)

        code, payload = run(
            MEMORY_ACCESS, "set-root",
            "--path", str(locked / "memory"), env=self.env(),
        )

        self.assertEqual(code, 1)
        self.assertEqual(payload["marker"], "MEMORY_ACCESS_FAILED")

    def test_relative_path_is_refused(self):
        code, payload = run(
            MEMORY_ACCESS, "set-root", "--path", "ReportAgentMemory", env=self.env()
        )

        self.assertEqual(code, 1)
        self.assertIn("绝对路径", payload["error"])

    def test_environment_override_takes_precedence(self):
        target = self.root / "env-memory"
        target.mkdir()

        code, payload = run(
            MEMORY_ACCESS, "probe",
            env=self.env(REPORT_AGENT_MEMORY_DIR=str(target)),
        )

        self.assertEqual(code, 0)
        self.assertEqual(payload["source"], "environment")
        self.assertEqual(Path(payload["memoryRoot"]), target)

    def test_status_reports_where_memory_lives(self):
        code, payload = run(MEMORY_ACCESS, "status", env=self.env())

        self.assertEqual(code, 0)
        self.assertEqual(payload["marker"], "MEMORY_ACCESS_STATUS")
        self.assertEqual(
            Path(payload["defaultRoot"]), self.fake_home / "ReportAgentMemory"
        )
        self.assertIsNone(payload["recordedRoot"])

    def test_probe_leaves_no_residue(self):
        run(MEMORY_ACCESS, "probe", env=self.env())

        memory_root = self.fake_home / "ReportAgentMemory"
        self.assertTrue(memory_root.is_dir())
        # 探针文件必须清掉，不留垃圾在用户可见目录
        self.assertEqual(list(memory_root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
