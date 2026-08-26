import unittest
from unittest.mock import patch

from mcp.report_loop.core.judge_provider import (
    JudgeProviderError,
    JudgeSettings,
    call_judge,
    locked_report_judge_settings,
    normalize_provider,
)


class JudgeProviderTests(unittest.TestCase):
    def test_default_provider_is_workbuddy(self):
        settings = locked_report_judge_settings()
        self.assertEqual(settings.provider, "workbuddy")
        self.assertEqual(settings.model, "deepseek-v4-pro-ioa")
        self.assertEqual(settings.effort, "medium")

    def test_codex_provider_is_available_but_not_default(self):
        settings = locked_report_judge_settings("codex")
        self.assertEqual(normalize_provider("codex_cli"), "codex")
        self.assertEqual(settings.provider, "codex")
        self.assertEqual(settings.model, "gpt-5.6-sol")
        self.assertEqual(settings.effort, "medium")

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(JudgeProviderError):
            normalize_provider("unknown")

    @patch("mcp.report_loop.core.judge_provider.call_workbuddy")
    def test_call_judge_always_uses_workbuddy(self, workbuddy_call):
        workbuddy_call.return_value = "ok"
        settings = JudgeSettings("workbuddy", "deepseek-v4-pro-ioa", "medium")
        result = call_judge("prompt", settings=settings, timeout_seconds=12)
        self.assertEqual(result, "ok")
        workbuddy_call.assert_called_once_with(
            "prompt",
            model="deepseek-v4-pro-ioa",
            effort="medium",
            timeout_seconds=12,
        )

    @patch("mcp.report_loop.core.judge_provider.call_codex")
    def test_call_judge_can_use_codex(self, codex_call):
        codex_call.return_value = "ok"
        settings = JudgeSettings("codex", "gpt-5.6-sol", "medium")
        result = call_judge("prompt", settings=settings, timeout_seconds=12)
        self.assertEqual(result, "ok")
        codex_call.assert_called_once_with(
            "prompt",
            model="gpt-5.6-sol",
            effort="medium",
            timeout_seconds=12,
        )


if __name__ == "__main__":
    unittest.main()
