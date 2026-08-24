from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "report-loop-guard.py"


class HookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = dict(os.environ)
        self.environment["RESEARCH_REPORT_LOOP_DIR"] = self.temporary.name
        self.session = "hook-test-session"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def call(self, mode: str, payload: dict) -> dict:
        completed = subprocess.run(
            [sys.executable, str(HOOK), mode],
            input=json.dumps({"session_id": self.session, **payload}, ensure_ascii=False),
            text=True,
            capture_output=True,
            env=self.environment,
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_written_report_requires_loop_then_finish(self) -> None:
        self.call("prompt", {"prompt": "根据这些访谈帮我写一份战略研究报告"})
        self.call(
            "post-tool",
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/report.md"},
                "tool_response": {"status": "ok"},
            },
        )
        blocked = self.call("stop", {})
        self.assertEqual(
            blocked["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.call(
            "post-tool",
            {
                "tool_name": "mcp__research-report-loop__report_loop_start",
                "tool_input": {},
                "tool_response": {
                    "structuredContent": {"status": "started", "runId": "report-abc"}
                },
            },
        )
        self.call(
            "post-tool",
            {
                "tool_name": "mcp__research-report-loop__report_loop_submit",
                "tool_input": {},
                "tool_response": {
                    "structuredContent": {"status": "judged", "nextAction": "deliver"}
                },
            },
        )
        still_blocked = self.call("stop", {})
        self.assertIn("finish", still_blocked["reason"])
        self.call(
            "post-tool",
            {
                "tool_name": "mcp__research-report-loop__report_loop_finish",
                "tool_input": {},
                "tool_response": {"structuredContent": {"status": "completed"}},
            },
        )
        allowed = self.call("stop", {})
        self.assertEqual(
            allowed["hookSpecificOutput"]["permissionDecision"], "allow"
        )

    def test_clarification_turn_is_not_blocked_before_artifact(self) -> None:
        self.call("prompt", {"prompt": "帮我写一份调研报告"})
        allowed = self.call("stop", {})
        self.assertEqual(
            allowed["hookSpecificOutput"]["permissionDecision"], "allow"
        )


if __name__ == "__main__":
    unittest.main()
