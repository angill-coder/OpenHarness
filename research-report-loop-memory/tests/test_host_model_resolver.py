from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from mcp.report_loop.core.host_model_resolver import (
    HostModelResolutionError,
    resolve_host_model_id,
)


class HostModelResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / ".workbuddy"
        self.workspace = self.root / "workspace"
        self.job_path = self.workspace / "case" / "report_loop_job.json"
        self.job_path.parent.mkdir(parents=True)
        self.job_path.write_text("{}", encoding="utf-8")
        self.previous_home = os.environ.get("RESEARCH_REPORT_LOOP_WB_HOME")
        self.previous_override = os.environ.pop("RESEARCH_REPORT_LOOP_HOST_MODEL_ID", None)
        os.environ["RESEARCH_REPORT_LOOP_WB_HOME"] = str(self.home)

    def tearDown(self) -> None:
        if self.previous_home is None:
            os.environ.pop("RESEARCH_REPORT_LOOP_WB_HOME", None)
        else:
            os.environ["RESEARCH_REPORT_LOOP_WB_HOME"] = self.previous_home
        if self.previous_override is not None:
            os.environ["RESEARCH_REPORT_LOOP_HOST_MODEL_ID"] = self.previous_override
        else:
            os.environ.pop("RESEARCH_REPORT_LOOP_HOST_MODEL_ID", None)
        self.temporary.cleanup()

    def write_session(self, session_id: str, heartbeat: int | None = None) -> None:
        sessions = self.home / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / f"{session_id}.json").write_text(
            json.dumps({
                "kind": "interactive",
                "sessionId": session_id,
                "cwd": str(self.workspace),
                "lastHeartbeat": heartbeat or int(time.time() * 1000),
            }),
            encoding="utf-8",
        )

    def write_trace(self, session_id: str, model_id: str) -> None:
        trace = self.home / "projects" / "workspace" / f"{session_id}.jsonl"
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text(
            json.dumps({
                "timestamp": int(time.time() * 1000),
                "sessionId": session_id,
                "providerData": {
                    "agent": "cli",
                    "requestModelId": model_id,
                },
            }) + "\n",
            encoding="utf-8",
        )

    def write_sidecar(self, session_id: str) -> None:
        Path(f"{self.job_path}.session.json").write_text(
            json.dumps({"version": 1, "sessionId": session_id}),
            encoding="utf-8",
        )

    def test_sidecar_selects_exact_session_when_same_workspace_has_two(self) -> None:
        self.write_session("session-0001")
        self.write_session("session-0002")
        self.write_trace("session-0001", "deepseek-v4-pro-ioa")
        self.write_trace("session-0002", "gpt-5.6-sol")
        self.write_sidecar("session-0001")
        self.assertEqual(resolve_host_model_id(self.job_path), "deepseek-v4-pro-ioa")

    def test_unique_active_workspace_session_is_safe_fallback(self) -> None:
        self.write_session("session-0001")
        self.write_trace("session-0001", "deepseek-v4-pro-ioa")
        self.assertEqual(resolve_host_model_id(self.job_path), "deepseek-v4-pro-ioa")

    def test_multiple_active_workspace_sessions_fail_without_sidecar(self) -> None:
        self.write_session("session-0001")
        self.write_session("session-0002")
        with self.assertRaisesRegex(HostModelResolutionError, "host_session_ambiguous"):
            resolve_host_model_id(self.job_path)


if __name__ == "__main__":
    unittest.main()
