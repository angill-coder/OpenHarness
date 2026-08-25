import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mcp.report_loop.core.workbuddy_cli import call_workbuddy


class WorkBuddyCliStdinTests(unittest.TestCase):
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
                prompt, model="deepseek-v4-pro", effort="medium", timeout_seconds=12
            )

        command_args = run_mock.call_args.args[0]
        command_kwargs = run_mock.call_args.kwargs
