import unittest
from unittest.mock import patch

from report_loop.core.codex_cli import DEFAULT_MODEL as CODEX_DEFAULT_MODEL
from report_loop.core.judge_model import (
    CODEX_JUDGE_MODEL,
    JUDGE_EFFORT,
    JUDGE_MODEL,
)
from report_loop.core.judge_provider import (
    JudgeProviderError,
    JudgeSettings,
    call_judge,
    judge_settings_are_pinned,
    locked_report_judge_settings,
    normalize_provider,
)
from report_loop.core.workbuddy_cli import DEFAULT_MODEL as WORKBUDDY_DEFAULT_MODEL


class JudgeProviderTests(unittest.TestCase):
    def test_default_provider_is_workbuddy(self):
        settings = locked_report_judge_settings()
        self.assertEqual(settings.provider, "workbuddy")
        self.assertEqual(settings.model, "gpt-5.6-sol")
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

    @patch("report_loop.core.judge_provider.call_workbuddy")
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

    @patch("report_loop.core.judge_provider.call_codex")
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


class JudgeModelOverrideTests(unittest.TestCase):
    """Judge model stays pinned by default but is overridable on demand.

    Pinning keeps scores comparable across runs, which the Memory Curator
    relies on. Platform upgrades edit judge_model.py; individual runs may
    override via Job field or environment variable, and any override is
    reported so the shifted baseline is auditable.
    """

    def test_pinned_by_default(self):
        settings = locked_report_judge_settings("workbuddy")
        self.assertEqual(settings.model, JUDGE_MODEL)
        self.assertEqual(settings.effort, JUDGE_EFFORT)
        self.assertTrue(judge_settings_are_pinned(settings))

    def test_explicit_model_overrides_pin(self):
        settings = locked_report_judge_settings("workbuddy", model="better-model-2027")
        self.assertEqual(settings.model, "better-model-2027")
        self.assertFalse(judge_settings_are_pinned(settings))

    def test_explicit_effort_overrides_pin(self):
        settings = locked_report_judge_settings("workbuddy", effort="high")
        self.assertEqual(settings.effort, "high")
        self.assertFalse(judge_settings_are_pinned(settings))

    def test_environment_variable_is_honoured(self):
        with patch.dict(
            "os.environ",
            {"RESEARCH_REPORT_LOOP_JUDGE_MODEL": "env-model"},
            clear=False,
        ):
            self.assertEqual(
                locked_report_judge_settings("workbuddy").model,
                "env-model",
            )

    def test_explicit_argument_beats_environment_variable(self):
        with patch.dict(
            "os.environ",
            {"RESEARCH_REPORT_LOOP_JUDGE_MODEL": "env-model"},
            clear=False,
        ):
            self.assertEqual(
                locked_report_judge_settings("workbuddy", model="job-model").model,
                "job-model",
            )

    def test_provider_specific_environment_variable(self):
        with patch.dict(
            "os.environ",
            {"RESEARCH_REPORT_LOOP_WB_MODEL": "wb-specific"},
            clear=False,
        ):
            self.assertEqual(
                locked_report_judge_settings("workbuddy").model,
                "wb-specific",
            )

    def test_unsupported_effort_is_rejected_before_the_run(self):
        with self.assertRaises(JudgeProviderError):
            locked_report_judge_settings("workbuddy", effort="turbo")

    def test_empty_model_is_rejected(self):
        with patch.dict(
            "os.environ",
            {"RESEARCH_REPORT_LOOP_JUDGE_MODEL": "   "},
            clear=False,
        ):
            with self.assertRaises(JudgeProviderError):
                locked_report_judge_settings("workbuddy")

    def test_codex_pin_is_tracked_separately(self):
        settings = locked_report_judge_settings("codex")
        self.assertTrue(judge_settings_are_pinned(settings))
        overridden = locked_report_judge_settings("codex", model="other")
        self.assertFalse(judge_settings_are_pinned(overridden))

    def test_single_source_of_truth_is_judge_model_module(self):
        """workbuddy_cli/codex_cli must not restate the model literal."""
        self.assertEqual(WORKBUDDY_DEFAULT_MODEL, JUDGE_MODEL)
        self.assertEqual(CODEX_DEFAULT_MODEL, CODEX_JUDGE_MODEL)


if __name__ == "__main__":
    unittest.main()
