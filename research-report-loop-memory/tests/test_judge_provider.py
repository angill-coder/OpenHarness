import unittest
from unittest.mock import patch

from mcp.report_loop.core.judge_provider import (
    JudgeProviderError,
    JudgeSettings,
    call_judge,
    locked_report_judge_settings,
    normalize_provider,
)


class WorkBuddyOnlyJudgeProviderTests(unittest.TestCase):
    def test_default_provider_is_workbuddy(self):
        settings = locked_report_judge_settings()
        self.assertEqual(settings.provider, "workbuddy")
        self.assertEqual(settings.model, "deepseek-v4-pro")
        self.assertEqual(settings.effort, "medium")

    def test_codex_provider_is_rejected(self):
        with self.assertRaisesRegex(JudgeProviderError, "Codex CLI"):
            normalize_provider("codex")

    def test_codex_cli_alias_is_rejected(self):
        with self.assertRaises(JudgeProviderError):
            normalize_provider("codex_cli")

    @patch("mcp.report_loop.core.judge_provider.call_workbuddy")
    def test_call_judge_always_uses_workbuddy(self, workbuddy_call):
        workbuddy_call.return_value = "ok"
        settings = JudgeSettings("workbuddy", "deepseek-v4-pro", "medium")
        result = call_judge("prompt", settings=settings, timeout_seconds=12)
        self.assertEqual(result, "ok")
        workbuddy_call.assert_called_once_with(
            "prompt",
            model="deepseek-v4-pro",
            effort="medium",
            timeout_seconds=12,
        )


if __name__ == "__main__":
    unittest.main()
