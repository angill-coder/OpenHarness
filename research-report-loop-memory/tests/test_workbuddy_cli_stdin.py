import unittest
import os
from types import SimpleNamespace
from unittest.mock import patch

from mcp.report_loop.core.workbuddy_cli import build_environment, call_workbuddy


class WorkBuddyCliStdinTests(unittest.TestCase):
    def test_nested_cli_environment_excludes_desktop_internal_context(self):
        source = {
            "HOME": "/Users/example",
            "PATH": "/usr/bin:/bin",
            "LANG": "zh_CN.UTF-8",
            "CODEBUDDY_CONFIG_DIR": "/Users/example/.workbuddy",
            "CODEBUDDY_SERVICE_PROXY_URL": "http://127.0.0.1/internal/hooks/services/invoke",
            "CODEBUDDY_GATEWAY_PASSWORD": "desktop-secret",
            "CODEBUDDY_HOST": "workbuddy-desktop",
            "SERVER__PORT": "53799",
            "WORKBUDDY_PAC_RPC_SOCKET": "/tmp/workbuddy-pac.sock",
            "WORKBUDDY_FS_PROTECTION_ROLE": "daemon",
            "RESEARCH_REPORT_LOOP_JUDGE_TIMEOUT": "120",
        }
        with patch.dict(os.environ, source, clear=True):
            environment = build_environment(("codebuddy",))

        self.assertEqual(environment["HOME"], "/Users/example")
        self.assertEqual(environment["CODEBUDDY_CONFIG_DIR"], "/Users/example/.workbuddy")
        self.assertEqual(environment["WORKBUDDY_CONFIG_DIR"], "/Users/example/.workbuddy")
        self.assertEqual(environment["RESEARCH_REPORT_LOOP_JUDGE_TIMEOUT"], "120")
        for key in (
            "CODEBUDDY_SERVICE_PROXY_URL",
            "CODEBUDDY_GATEWAY_PASSWORD",
            "CODEBUDDY_HOST",
            "SERVER__PORT",
            "WORKBUDDY_PAC_RPC_SOCKET",
            "WORKBUDDY_FS_PROTECTION_ROLE",
        ):
            self.assertNotIn(key, environment)

    def test_long_prompt_is_sent_via_stdin_not_argv(self):
        prompt = "证" * 40000
        completed = SimpleNamespace(returncode=0, stdout="stream", stderr="")

        with (
            patch(
                "mcp.report_loop.core.workbuddy_cli.discover_command",
                return_value=("workbuddy",),
            ),
            patch(
                "mcp.report_loop.core.workbuddy_cli.build_environment",
                return_value={},
            ),
            patch(
                "mcp.report_loop.core.workbuddy_cli.parse_stream_output",
                return_value="OK",
            ),
            patch(
                "mcp.report_loop.core.workbuddy_cli.subprocess.run",
                return_value=completed,
            ) as run_mock,
        ):
            result = call_workbuddy(
                prompt, model="deepseek-v4-pro-ioa", effort="medium", timeout_seconds=12
            )

        command_args = run_mock.call_args.args[0]
        command_kwargs = run_mock.call_args.kwargs
