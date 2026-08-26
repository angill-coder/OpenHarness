import unittest
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcp.report_loop.core.workbuddy_cli import (
    _mac_workbuddy_command,
    build_environment,
    call_workbuddy,
    discover_command,
)


NATIVE_PATH = type(Path.cwd())


class WorkBuddyCliStdinTests(unittest.TestCase):
    def test_mac_discovery_accepts_a_non_system_applications_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "Applications/WorkBuddy.app"
            cli = app / "Contents/Resources/app.asar.unpacked/cli/bin/codebuddy"
            cli.parent.mkdir(parents=True)
            cli.touch(mode=0o755)
            self.assertEqual(_mac_workbuddy_command([app]), (str(cli),))

    def test_discovery_uses_host_provided_codebuddy_and_node_on_windows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "CompanyWorkBuddy"
            cli = root / "resources/app.asar.unpacked/cli/bin/codebuddy"
            node = Path(temporary) / "runtime/node.exe"
            cli.parent.mkdir(parents=True)
            node.parent.mkdir(parents=True)
            cli.touch()
            node.touch()
            source = {
                "CODEBUDDY_CODE_PATH": str(cli),
                "CODEBUDDY_CODE_NODE_PATH": str(node),
                "USERPROFILE": temporary,
            }
            with (
                patch.dict(os.environ, source, clear=True),
                patch("mcp.report_loop.core.workbuddy_cli.os.name", "nt"),
                patch("mcp.report_loop.core.workbuddy_cli.Path", NATIVE_PATH),
            ):
                self.assertEqual(discover_command(), (str(node), str(cli)))

    def test_discovery_checks_program_files_on_windows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "WorkBuddy"
            executable = root / "WorkBuddy.exe"
            cli = root / "resources/app.asar.unpacked/cli/bin/codebuddy"
            cli.parent.mkdir(parents=True)
            executable.touch()
            cli.touch()
            with (
                patch.dict(
                    os.environ,
                    {"ProgramFiles": temporary, "USERPROFILE": temporary},
                    clear=True,
                ),
                patch("mcp.report_loop.core.workbuddy_cli.os.name", "nt"),
                patch("mcp.report_loop.core.workbuddy_cli.Path", NATIVE_PATH),
            ):
                self.assertEqual(discover_command(), (str(executable), str(cli)))

    def test_standalone_windows_cli_precedes_desktop_fallback(self):
        standalone = str(Path(tempfile.gettempdir()) / "codebuddy.exe")
        desktop = ("WorkBuddy.exe", "embedded-codebuddy")

        def which(name):
            return standalone if name == "codebuddy" else None

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("mcp.report_loop.core.workbuddy_cli.os.name", "nt"),
            patch("mcp.report_loop.core.workbuddy_cli.shutil.which", side_effect=which),
            patch(
                "mcp.report_loop.core.workbuddy_cli._windows_desktop_command",
                return_value=desktop,
            ) as desktop_mock,
        ):
            self.assertEqual(discover_command(), (standalone,))

        desktop_mock.assert_not_called()

    def test_nested_cli_environment_excludes_desktop_internal_context(self):
        source = {
            "HOME": "/Users/example",
            "PATH": "/usr/bin:/bin",
            "LANG": "zh_CN.UTF-8",
            "CODEBUDDY_CONFIG_DIR": "/Users/example/.workbuddy",
            "WORKBUDDY_CONFIG_DIR": "/Users/example/company-workbuddy",
            "CODEBUDDY_SERVICE_PROXY_URL": "http://127.0.0.1/internal/hooks/services/invoke",
            "CODEBUDDY_GATEWAY_PASSWORD": "desktop-secret",
            "CODEBUDDY_HOST": "workbuddy-desktop",
            "SERVER__PORT": "53799",
            "WORKBUDDY_PAC_RPC_SOCKET": "/tmp/workbuddy-pac.sock",
            "WORKBUDDY_FS_PROTECTION_ROLE": "daemon",
            "RESEARCH_REPORT_LOOP_JUDGE_TIMEOUT": "120",
            "WORKBUDDY_EXTRA_PATHS": "/managed/bin",
            "CODEBUDDY_CODE_PATH": "/Applications/WorkBuddy/codebuddy",
        }
        with patch.dict(os.environ, source, clear=True):
            environment = build_environment(("codebuddy",))

        self.assertEqual(environment["HOME"], "/Users/example")
        expected_config = str(Path("/Users/example/company-workbuddy").expanduser())
        self.assertEqual(environment["CODEBUDDY_CONFIG_DIR"], expected_config)
        self.assertEqual(environment["WORKBUDDY_CONFIG_DIR"], expected_config)
        self.assertEqual(environment["RESEARCH_REPORT_LOOP_JUDGE_TIMEOUT"], "120")
        self.assertEqual(environment["WORKBUDDY_EXTRA_PATHS"], "/managed/bin")
        self.assertEqual(environment["CODEBUDDY_CODE_PATH"], "/Applications/WorkBuddy/codebuddy")
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

        self.assertEqual(result, "OK")
        self.assertNotIn(prompt, command_args)
        self.assertEqual(command_kwargs["input"], prompt)
        self.assertEqual(command_kwargs["timeout"], 12)

    def test_explicit_loop_budget_is_capped_by_judge_timeout(self):
        completed = SimpleNamespace(returncode=0, stdout="stream", stderr="")
        with (
            patch.dict(
                os.environ,
                {"RESEARCH_REPORT_LOOP_JUDGE_TIMEOUT": "30"},
                clear=True,
            ),
            patch(
                "mcp.report_loop.core.workbuddy_cli.discover_command",
                return_value=("codebuddy",),
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
            self.assertEqual(call_workbuddy("judge", timeout_seconds=3600), "OK")

        self.assertEqual(run_mock.call_args.kwargs["timeout"], 30)
