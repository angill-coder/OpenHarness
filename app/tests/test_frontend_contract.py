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
            '<option value="api" selected>API</option>',
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

    def test_frontend_imports_session_local_rubric_json(self):
        source = (APP / "app.js").read_text(encoding="utf-8")
        html = (APP / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="rubricFileInput"', html)
        self.assertIn('accept=".json,application/json"', html)
        self.assertNotIn('id="rubricImportBtn"', html)
        self.assertIn("JSON.parse(await file.text())", source)
        self.assertIn("api('/api/rubric/import','POST'", source)
        self.assertIn("STATE.rubric_source||{}", source)
        self.assertIn(
            "getElementById('rubricFileInput').onchange=async()=>",
            source,
        )

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

    def test_rubrics_loop_keeps_three_model_configs_independent(self):
        source = (APP / "rubrics_loop_ui" / "app.js").read_text(
            encoding="utf-8"
        )
        html = (APP / "rubrics_loop_ui" / "index.html").read_text(
            encoding="utf-8"
        )
        styles = (APP / "rubrics_loop_ui" / "styles.css").read_text(
            encoding="utf-8"
        )
        for prefix in ("optimizer", "skill", "judge"):
            self.assertIn('id="%sBackend"' % prefix, html)
            self.assertIn('id="%sModel"' % prefix, html)
            self.assertIn('id="%sEffort"' % prefix, html)
        self.assertIn("modelPayload('optimizer')", source)
        self.assertIn("modelPayload('skill')", source)
        self.assertIn("modelPayload('judge')", source)
        self.assertIn("$(prefix+'Effort').value", source)
        self.assertIn("['api','codex','workbuddy']", source)
        self.assertIn("||'api'", source)

    def test_rubrics_loop_uses_runner_model_select_and_plain_validation_label(self):
        source = (APP / "rubrics_loop_ui" / "app.js").read_text(
            encoding="utf-8"
        )
        html = (APP / "rubrics_loop_ui" / "index.html").read_text(
            encoding="utf-8"
        )
        styles = (APP / "rubrics_loop_ui" / "styles.css").read_text(
            encoding="utf-8"
        )
        self.assertIn('<select id="runnerModel">', html)
        self.assertNotIn('<input id="runnerModel"', html)
        self.assertIn("state.config.models||state.config.evaluation_models", source)
        self.assertIn("runnerDefault=state.config.model", source)
        self.assertIn("$('runnerModel').value.trim()", source)
        self.assertIn("验证新 Rubrics", html)
        self.assertIn("查看验证实验详情", html)
        self.assertNotIn("打开现有 Skill Loop", html)
        self.assertIn("existingExperiment?'查看验证实验详情':'验证新 Rubrics'", source)
        self.assertIn("if(existing)renderExperiment()", source)
        self.assertIn("result.loop_completed_at?'仅重试 Rubrics 验收'", source)
        self.assertIn("experiment_id:retrying?state.experiment.experiment_id:null", source)
        self.assertIn('id="skillIterationRounds"', html)
        self.assertIn('value="2"', html)
        self.assertIn('id="acceptanceBackend"', html)
        self.assertIn("acceptance:modelPayload('acceptance')", source)
        self.assertIn("Rubrics 验收", html)
        self.assertNotIn("Feedback AI 验收", html)
        self.assertIn("function skillLoopProgress(result,rounds)", source)
        self.assertIn("fresh.live_state=await api('/api/session?id='", source)
        self.assertIn("Skill Optimizer 生成下一版", source)
        self.assertIn("experiment-running-dots", source)
        self.assertIn(".experiment-running-dots i", styles)
        self.assertNotIn("配置累计草案验证实验", source)
        self.assertIn("function confirmRedlineChanges(candidate)", source)
        self.assertIn("if(!changes.length)return true", source)
        self.assertIn("设为红线：", source)
        self.assertIn("取消红线：", source)
        self.assertIn("红线会影响维度封顶和最终得分", source)
        self.assertNotIn("如有红线变化，确认已审核", source)

    def test_rubrics_loop_uses_reading_and_annotation_layout(self):
        source = (APP / "rubrics_loop_ui" / "app.js").read_text(
            encoding="utf-8"
        )
        html = (APP / "rubrics_loop_ui" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('class="version-item" open', source)
        self.assertIn('id="judgeScore"', html)
        self.assertIn('id="currentReportTools"', html)
        self.assertIn("本轮反馈", html)
        self.assertNotIn("Feedback Batch", html)
        self.assertNotIn("开始新一轮", html)
        self.assertNotIn("跨报告共性意见", html)
        self.assertIn('id="feedbackInput"', html)
        self.assertIn('id="feedbackScope"', html)
        self.assertIn("继续批注其他报告", html)
        self.assertIn("提交已有批注", html)
        self.assertIn("state.selectedQuote?'inline':'report'", source)
        self.assertIn("右侧会出现批注输入框", source)
        self.assertIn("Enter 添加反馈 · Shift+Enter 换行", html)
        self.assertIn("Rubrics Optimizer 模型设置", html)
        self.assertIn('id="optimizerRunStatus"', html)
        self.assertIn("event.key==='Enter'&&!event.shiftKey", source)
        self.assertIn("data-edit-feedback", source)
        self.assertIn('id="historyGroups"', html)
        self.assertIn('id="historyHint"', html)
        self.assertIn('class="history-session"', source)
        self.assertIn("api('/api/rubrics-loop/iterations')", source)
        self.assertIn('data-history-session=', source)
        self.assertIn("await switchSession(sessionId)", source)
        self.assertIn("/api/rubrics-loop/iterations", source)
        self.assertIn("restoreWorkflow()", source)
        self.assertIn("data-open-iteration", source)
        self.assertNotIn("restoreDraft()", source)
        self.assertIn("state.batch||{report_refs:[],feedback:[]}", source)
        self.assertIn('<div class="md-table"><table><thead><tr>', source)
        self.assertIn("replace(/\\*\\*([^*]+)\\*\\*/g", source)

        styles = (APP / "rubrics_loop_ui" / "styles.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("body .selector{position:static;grid-column:1/-1;max-height:460px}", styles)
        self.assertIn("body .report-panel{grid-column:1}", styles)
        self.assertIn("body .batch-panel{grid-column:2", styles)

    def test_rubrics_loop_candidate_review_is_readable_and_diffable(self):
        source = (APP / "rubrics_loop_ui" / "app.js").read_text(
            encoding="utf-8"
        )
        html = (APP / "rubrics_loop_ui" / "index.html").read_text(
            encoding="utf-8"
        )
        styles = (APP / "rubrics_loop_ui" / "styles.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("候选 Rubrics 审核", html)
        self.assertLess(
            html.index("Rubrics 迭代历史"),
            html.index('id="candidatePanel"'),
        )
        self.assertIn(
            'id="candidatePanel" class="panel candidate-panel hidden"', html
        )
        self.assertIn('id="closeCandidateReview"', html)
        self.assertIn("反馈处理结果", html)
        self.assertIn("Rubrics 变更对照", html)
        self.assertIn('id="candidateRubricTable"', html)
        self.assertIn('<details class="candidate-rubric-section">', html)
        self.assertNotIn('<details class="candidate-rubric-section" open>', html)
        self.assertLess(html.index('class="review-action-section"'), html.index('class="candidate-rubric-section"'))
        self.assertIn("高级编辑：查看或修改原始 JSON", html)
        self.assertNotIn("查看或修改候选 Rubrics JSON", html)
        self.assertIn("function candidateDiff", source)
        self.assertIn("function renderCandidateAnalysis", source)
        self.assertIn("function renderCandidateChanges", source)
        self.assertIn("function renderCandidateTable", source)
        self.assertIn("现有 Rubrics 已覆盖 · 无需修改", source)
        self.assertIn("属于任务配置 · 不修改通用 Rubrics", source)
        self.assertIn("修改前", source)
        self.assertIn("修改后", source)
        self.assertIn("<th>维度</th><th>Check ID</th><th>Check 内容</th>", source)
        self.assertNotIn('<th>红线</th>', source)
        self.assertIn("changed-row", styles)
        self.assertIn("diff-sides", styles)
        self.assertIn("height:clamp(360px,58vh,560px)", styles)
        self.assertIn('id="rubricFileInput"', html)
        self.assertIn("/api/rubric/import", source)
        self.assertIn("默认 Rubric 文件和其他 Session 未修改", source)
        self.assertIn("更换当前 Session Rubric", html)
        self.assertIn('id="stageCandidate"', html)
        self.assertIn("暂存到待验证草案", html)
        self.assertIn("/api/rubrics-loop/candidates/stage", source)
        self.assertIn("待验证 Rubrics 草案", source)
        self.assertIn("working_parent_rubric", source)
        self.assertIn("requestedCandidate=query.get('candidate_id')", source)
        self.assertIn("if(active.batch_id&&!active.candidate_id)", source)
        self.assertNotIn("groupIndex===0?' open'", source)
        self.assertIn("history-iteration${selected?' selected':''}", source)
        self.assertIn('<section class="history-group">', source)
        self.assertNotIn('<details class="history-group"', source)
        self.assertIn(".history-group>header", styles)
        self.assertIn('class="history-feedback-item${hasQuote?', source)
        self.assertIn("当时选中的原文", source)
        self.assertIn("history-quote-tooltip", source)
        self.assertIn(".history-feedback-item.has-quote:hover", styles)
        self.assertIn("function historyCandidateStatus(candidate)", source)
        self.assertIn("已随累计草案验证", source)
        self.assertIn("cumulative-validation-note", source)
        self.assertIn("/api/rubrics-loop/feedback/resolve-selection", source)
        self.assertIn("result.markdown_quote", source)
        self.assertIn("rendered_quote:renderedQuote", source)
        self.assertIn("已选中 Markdown 原文", html)
        self.assertIn("悬浮查看原文批注", source)
        self.assertIn("当时选中的 Markdown 原文", source)
        self.assertIn("analysis-quote-tooltip", source)
        self.assertIn(".analysis-item.scoped{border-left-color:#687482}", styles)
        self.assertIn(".analysis-item.scoped .analysis-head span{color:#a6b0bd}", styles)
        self.assertNotIn(".analysis-item.scoped{border-left-color:#d29b50}", styles)
        self.assertIn("只替换当前 Session 的 Rubric", html)
        self.assertIn("function sortCaseIdsByFileName", source)
        self.assertIn("localeCompare(String(caseFileName(session,left))", source)
        self.assertIn("body .topbar .brand{font-size:16px", styles)
        self.assertIn("body .topbar nav a{display:inline-flex", styles)
