# -*- coding: utf-8 -*-
from pathlib import Path
import unittest


APP = Path(__file__).resolve().parents[1]


class FrontendContractTest(unittest.TestCase):
    def test_judge_and_optimizer_send_llm_backend_selection(self):
        source = (APP / "app.js").read_text(encoding="utf-8")
        html = (APP / "index.html").read_text(encoding="utf-8")
        self.assertIn("readLlmSelection('judge')", source)
        self.assertIn("readLlmSelection('optimizer')", source)
        self.assertIn("id=\"judgeLlmBackend\"", html)
        self.assertIn("id=\"optimizerLlmBackend\"", html)
        self.assertIn('id="judgeApiModel"', html)
        self.assertIn('id="optimizerApiModel"', html)
        self.assertIn('<option value="codex">Codex CLI</option>', html)
        self.assertIn('id="judgeCodexModel"', html)
        self.assertIn('id="optimizerCodexModel"', html)
        self.assertIn('id="judgeCodexReasoning"', html)
        self.assertIn('id="optimizerCodexReasoning"', html)
        for model in (
            "claude-opus-5",
            "claude-opus-4.8",
            "gpt-5.6-sol",
        ):
            self.assertIn('value="%s"' % model, html)
        self.assertIn("backend==='codex'?'CodexModel':'ApiModel'", source)
        self.assertIn("result.llm_model=model", source)
        self.assertIn("result.llm_reasoning_effort=effort", source)
        self.assertIn("GEN_CONFIG.codex_reasoning_effort_default||'medium'", source)
        self.assertIn(
            '<option value="workbuddy" selected>WorkBuddy CLI</option>',
            html,
        )

    def test_api_model_input_allows_custom_names(self):
        html = (APP / "index.html").read_text(encoding="utf-8")
        self.assertIn(
            '<input id="judgeApiModel" list="evaluationApiModels"',
            html,
        )
        self.assertIn(
            '<input id="optimizerApiModel" list="evaluationApiModels"',
            html,
        )

    def test_judge_completion_rerenders_advance_button(self):
        source = (APP / "app.js").read_text(encoding="utf-8")
        self.assertIn(
            "finally{\n    JUDGE_RUNNING=false;stopJudgeProgressPoll();render();\n  }",
            source,
        )

    def test_frontend_accepts_unified_cases_document(self):
        source = (APP / "app.js").read_text(encoding="utf-8")
        html = (APP / "index.html").read_text(encoding="utf-8")
        self.assertIn("use_configured:true", source)
        self.assertIn("configuredDataBtn", html)
        self.assertIn("JSON.parse(raw)", source)

    def test_frontend_uses_backend_actions_for_loop_buttons(self):
        source = (APP / "app.js").read_text(encoding="utf-8")
        self.assertIn("STATE.actions&&STATE.actions.advance", source)
        self.assertIn(
            "STATE&&STATE.actions&&STATE.actions.run_judge",
            source,
        )
        self.assertIn(
            "STATE&&STATE.actions&&STATE.actions.run_generation",
            source,
        )

    def test_llm_rewrite_can_choose_v0_drafting_strategy(self):
        source = (APP / "app.js").read_text(encoding="utf-8")
        html = (APP / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="v0StrategySel"', html)
        self.assertIn('value="base_skill"', html)
        self.assertIn('value="llm_scratch"', html)
        self.assertIn("v0_strategy:v0Strategy", source)
        self.assertIn("syncV0StrategyVisibility", source)
        self.assertIn("LLM 正在起草 V0", source)

    def test_frontend_sends_generation_and_judge_parallelism(self):
        source = (APP / "app.js").read_text(encoding="utf-8")
        html = (APP / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="generationParallel"', html)
        self.assertIn('id="judgeParallel"', html)
        self.assertIn(
            "'/api/generation/start','POST',{\n      id:SID,idempotency_key:key,parallel",
            source,
        )
        self.assertIn(
            "id:SID,version:STATE.current_version,parallel",
            source,
        )
    def test_session_creation_captures_dashboard_user(self):
        source = (APP / "app.js").read_text(encoding="utf-8")
        html = (APP / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index('id="userInput"'), html.index('id="pidInput"'))
        for user in ("Angill", "Sijing", "Zoe"):
            self.assertIn(f'<option value="{user}"', html)
        self.assertIn("experiment_user:experimentUser", source)

    def test_evaluation_workflow_layout_removes_right_rail(self):
        source = (APP / "app.js").read_text(encoding="utf-8")
        html = (APP / "index.html").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns:340px minmax(0,1fr)", html)
        for element_id in ("curveView", "failView", "rubricView", "historyView"):
            self.assertNotIn(f'id="{element_id}"', html)
        self.assertNotIn("renderCurve(); renderFail(); renderRubric();", source)

    def test_optimizer_follows_judge_and_keeps_api_model_input(self):
        html = (APP / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index('id="outputCard"'), html.index('id="optimizerCard"'))
        self.assertLess(html.index('id="judgeStatus"'), html.index('id="optimizerLlmControls"'))
        self.assertIn('id="judgeApiModel"', html)
        self.assertIn('id="optimizerApiModel"', html)
        self.assertIn("Judge 调用或输出格式失败会自动重试", html)

    def test_runner_and_judge_show_separate_case_progress(self):
        source = (APP / "app.js").read_text(encoding="utf-8")
        html = (APP / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="generationCases"', html)
        self.assertIn('id="judgeCases"', html)
        self.assertIn("function renderRunnerCases()", source)
        self.assertIn("function renderJudgeCases()", source)
        self.assertIn("function reportProgressRow(c)", source)
        self.assertIn('role="progressbar"', source)
        self.assertIn("startJudgeProgressPoll()", source)

    def test_platform_switcher_replaces_backend_and_session_badges(self):
        html = (APP / "index.html").read_text(encoding="utf-8")
        source = (APP / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('id="backendBadge"', html)
        self.assertNotIn('id="sessBadge"', html)
        self.assertNotIn("getElementById('backendBadge')", source)
        self.assertNotIn("getElementById('sessBadge')", source)
        self.assertIn('<nav class="product-nav"', html)
