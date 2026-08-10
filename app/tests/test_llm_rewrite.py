# -*- coding: utf-8 -*-
"""LLM 自由改写策略(optimizer02)+ 共享评测/异步 gate 的测试。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

APP = Path(__file__).resolve().parents[1]
HARNESS = APP.parent / "harness"
for path in (str(APP), str(HARNESS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import optimizer_pipeline  # noqa: E402
import optimizer02  # noqa: E402
import llm_client  # noqa: E402
import persistence as persist  # noqa: E402
import session as session_mod  # noqa: E402
import generator as generator_mod  # noqa: E402
import server as server_mod  # noqa: E402
from schemas import SkillArtifact  # noqa: E402
from skill_compiler import compile_session_skill  # noqa: E402

BASE_SKILL = APP.parent / "skills" / "research-report"


class _FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class TestLLMClientErrors(unittest.TestCase):
    def test_missing_key_has_typed_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(llm_client.LLMClientError) as ctx:
                llm_client.call_llm("prompt")
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))

    def test_timeout_is_wrapped_and_timeout_is_configurable(self):
        env = {
            "ANTHROPIC_API_KEY": "test-key",
            "ANTHROPIC_BASE_URL": "https://llm.example",
            "LLM_API_STYLE": "openai",
            "LLM_TIMEOUT_SECONDS": "12.5",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(
                urllib.request,
                "urlopen",
                side_effect=TimeoutError("timed out"),
            ) as call:
                with mock.patch.object(llm_client.time, "sleep"):
                    with self.assertRaises(llm_client.LLMClientError) as ctx:
                        llm_client.call_llm("prompt")
        self.assertIn("连接上游 LLM 失败", str(ctx.exception))
        self.assertEqual(call.call_args.kwargs["timeout"], 12.5)
        self.assertEqual(call.call_count, 3)

    def test_timeout_retries_then_succeeds(self):
        env = {
            "ANTHROPIC_API_KEY": "test-key",
            "ANTHROPIC_BASE_URL": "https://llm.example",
            "LLM_API_STYLE": "openai",
        }
        response = _FakeResponse(json.dumps({
            "choices": [{"message": {"content": "ok"}}],
        }).encode("utf-8"))
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(
                urllib.request,
                "urlopen",
                side_effect=[TimeoutError("timed out"), response],
            ) as call:
                with mock.patch.object(llm_client.time, "sleep") as sleep:
                    result = llm_client.call_llm(
                        "prompt",
                        timeout_seconds=600,
                        retries=1,
                    )
        self.assertEqual(result, "ok")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args.kwargs["timeout"], 600)
        sleep.assert_called_once_with(1)

    def test_invalid_provider_response_is_wrapped(self):
        env = {
            "ANTHROPIC_API_KEY": "test-key",
            "ANTHROPIC_BASE_URL": "https://llm.example",
            "LLM_API_STYLE": "openai",
        }
        response = _FakeResponse(json.dumps({}).encode("utf-8"))
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(
                urllib.request,
                "urlopen",
                return_value=response,
            ):
                with self.assertRaises(llm_client.LLMClientError) as ctx:
                    llm_client.call_llm("prompt")
        self.assertIn("响应格式无效", str(ctx.exception))

    def test_api_backend_uses_selected_custom_model(self):
        env = {
            "ANTHROPIC_API_KEY": "test-key",
            "ANTHROPIC_BASE_URL": "https://llm.example",
            "ANTHROPIC_JUDGE_MODEL": "fallback-model",
            "LLM_API_STYLE": "openai",
        }
        response = _FakeResponse(json.dumps({
            "choices": [{"message": {"content": "ok"}}],
        }).encode("utf-8"))
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(
                urllib.request,
                "urlopen",
                return_value=response,
            ) as call:
                result = llm_client.call_llm(
                    "prompt",
                    backend="api",
                    model="custom-provider-model",
                )
        self.assertEqual(result, "ok")
        request = call.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "custom-provider-model")

    def test_workbuddy_backend_uses_selected_model_and_extracts_text(self):
        event = json.dumps({
            "type": "assistant",
            "message": {
                "model": "claude-opus-4.8",
                "content": [{"type": "text", "text": '{"ok": true}'}],
            },
        })
        completed = SimpleNamespace(
            returncode=0,
            stdout=event + "\n",
            stderr="",
        )
        with mock.patch.object(
            llm_client,
            "discover_command",
            return_value=("/tmp/workbuddy",),
        ):
            with mock.patch.object(
                llm_client.subprocess,
                "run",
                return_value=completed,
            ) as run:
                result = llm_client.call_llm(
                    "judge prompt",
                    backend="workbuddy",
                    model="claude-opus-4.8",
                    retries=0,
                )
        self.assertEqual(result, '{"ok": true}')
        command = run.call_args.args[0]
        self.assertIn("--model", command)
        self.assertEqual(
            command[command.index("--model") + 1],
            "claude-opus-4.8",
        )
        self.assertEqual(command[-1], "judge prompt")
        self.assertIn("--no-session-persistence", command)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(
            command[command.index("--setting-sources") + 1],
            "",
        )
        environment = run.call_args.kwargs["env"]
        self.assertEqual(
            environment["CODEBUDDY_DISABLE_AUTO_MEMORY"],
            "1",
        )
        self.assertEqual(
            environment["CODEBUDDY_MEMORY_RELEVANCE_DISABLED"],
            "1",
        )

    def test_workbuddy_backend_rejects_unknown_model(self):
        with self.assertRaises(llm_client.LLMClientError) as ctx:
            llm_client.call_llm(
                "prompt",
                backend="workbuddy",
                model="unknown-model",
            )
        self.assertIn("不支持的 WorkBuddy 模型", str(ctx.exception))

    def test_codex_backend_uses_model_effort_and_ephemeral_exec(self):
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")

        def fake_run(command, **kwargs):
            output_path = Path(
                command[command.index("--output-last-message") + 1]
            )
            output_path.write_text('{"ok": true}', encoding="utf-8")
            self.assertEqual(kwargs["input"], "judge prompt")
            return completed

        with mock.patch.object(
            llm_client,
            "_discover_codex_command",
            return_value=("/tmp/codex",),
        ):
            with mock.patch.object(
                llm_client.subprocess,
                "run",
                side_effect=fake_run,
            ) as run:
                result = llm_client.call_llm(
                    "judge prompt",
                    backend="codex",
                    model="gpt-5.6-sol",
                    reasoning_effort="medium",
                    retries=0,
                )
        self.assertEqual(result, '{"ok": true}')
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/tmp/codex")
        self.assertLess(command.index("--ask-for-approval"), command.index("exec"))
        self.assertLess(command.index("--model"), command.index("exec"))
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertEqual(
            command[command.index("--model") + 1],
            "gpt-5.6-sol",
        )
        self.assertEqual(
            command[command.index("--config") + 1],
            'model_reasoning_effort="medium"',
        )
        self.assertEqual(command[-1], "-")

    def test_codex_backend_rejects_unknown_model_or_effort(self):
        with self.assertRaisesRegex(
            llm_client.LLMClientError,
            "不支持的 Codex 模型",
        ):
            llm_client.call_llm(
                "prompt",
                backend="codex",
                model="unknown-model",
            )
        with self.assertRaisesRegex(
            llm_client.LLMClientError,
            "不支持的 Codex 推理力度",
        ):
            llm_client.call_llm(
                "prompt",
                backend="codex",
                model="gpt-5.6-sol",
                reasoning_effort="extreme",
            )


class _FailingAdvanceSession:
    id = "advance-llm-error"

    def advance(
        self,
        account=None,
        llm_backend="api",
        llm_model=None,
        llm_reasoning_effort=None,
    ):
        raise llm_client.LLMClientError("上游请求超时")


class TestAdvanceHTTPError(unittest.TestCase):
    def test_llm_failure_returns_json_502(self):
        original_sessions = dict(server_mod.SESSIONS)
        original_service = server_mod.GENERATION_SERVICE
        server_mod.SESSIONS.clear()
        server_mod.SESSIONS[_FailingAdvanceSession.id] = (
            _FailingAdvanceSession()
        )
        server_mod.GENERATION_SERVICE = None
        httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            server_mod.Handler,
        )
        thread = threading.Thread(
            target=httpd.serve_forever,
            daemon=True,
        )
        thread.start()
        try:
            body = json.dumps({"id": _FailingAdvanceSession.id}).encode(
                "utf-8"
            )
            request = urllib.request.Request(
                "http://127.0.0.1:%d/api/advance"
                % httpd.server_port,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(ctx.exception.code, 502)
            payload = json.loads(
                ctx.exception.read().decode("utf-8")
            )
            self.assertIn("LLM 改写失败", payload["error"])
            self.assertIn("上游请求超时", payload["error"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)
            server_mod.SESSIONS.clear()
            server_mod.SESSIONS.update(original_sessions)
            server_mod.GENERATION_SERVICE = original_service


class TestEvaluateGate(unittest.TestCase):
    DIMS = ["traceability", "structure", "narrative", "insight", "coverage", "expression"]

    def _dims(self, overall, red=0, **over):
        d = {k: 4.0 for k in self.DIMS}
        d.update(over)
        d["overall"] = overall
        d["red_line_fails"] = red
        return d

    def test_adopt_when_target_up_no_regression(self):
        parent = self._dims(4.0)
        cand = self._dims(4.2, insight=4.5)
        adopt, verdict, _ = optimizer_pipeline.evaluate_gate(
            parent, cand, ["insight"], 0.15, self.DIMS)
        self.assertTrue(adopt)
        self.assertEqual(verdict, "adopted")

    def test_reject_on_regression_beyond_tol(self):
        parent = self._dims(4.0, structure=4.5)
        cand = self._dims(4.1, insight=4.6, structure=4.0)  # structure -0.5 > tol
        adopt, verdict, reasons = optimizer_pipeline.evaluate_gate(
            parent, cand, ["insight"], 0.15, self.DIMS)
        self.assertFalse(adopt)
        self.assertEqual(reasons["regressed_dim"], "structure")

    def test_reject_on_new_red_line(self):
        parent = self._dims(4.0, red=0)
        cand = self._dims(4.5, red=1)
        adopt, _, reasons = optimizer_pipeline.evaluate_gate(
            parent, cand, ["insight"], 0.15, self.DIMS)
        self.assertFalse(adopt)
        self.assertTrue(reasons["red_line_new"])

    def test_reject_when_no_improvement(self):
        parent = self._dims(4.0)
        cand = self._dims(4.0)
        adopt, _, _ = optimizer_pipeline.evaluate_gate(
            parent, cand, ["insight"], 0.15, self.DIMS)
        self.assertFalse(adopt)


class TestFreeformCompile(unittest.TestCase):
    def test_freeform_replaces_editable_keeps_structure(self):
        gen = generator_mod.generate_v0(
            "面向总裁，先收集背景、hypothesis 和材料重点分布，"
            "再按摘要、关键发现、启示三段式输出，数据多时用图表。",
            "research_insight",
            optimizer_mode="llm_rewrite",
        )
        sk = SkillArtifact.from_dict(gen["skill"])
        contract = sk.instructions.get("requirement_contract", "")
        self.assertIn("总裁/最高管理层", contract)
        self.assertIn("材料重点分布", contract)
        self.assertIn("严格三段式", contract)
        self.assertIn("markdown 表格或清晰图表", contract)
        prop = {"instructions_text": "## 硬规则（改写）\n1. 不编造、冲突不混用、单源降级、样本偏差、证据不足留白。",
                "change_summary": "t"}
        cand = optimizer02.apply_proposal(sk, prop, "v1")
        self.assertEqual(cand.instructions.get("mode"), "freeform")
        self.assertEqual(
            cand.instructions.get("requirement_contract"),
            contract,
        )
        with tempfile.TemporaryDirectory() as tmp:
            frozen = compile_session_skill(Path(tmp), "s", cand, BASE_SKILL)
            txt = (frozen.path / "references" / "instructions.md").read_text(encoding="utf-8")
        self.assertIn("## 硬规则（改写）", txt)          # 新正文进去了
        self.assertNotIn("生命线", txt)                   # 旧可编辑区被替换
        self.assertIn("本会话任务契约（冻结", txt)
        self.assertIn("材料重点分布", txt)
        self.assertIn("报告结构（面向高管，三部分", txt)   # 结构层保留
        self.assertIn("OPENHARNESS_DIRECTIVES", txt)      # manifest 保留
        self.assertIn("OPENHARNESS_VERSION_RULES_START", txt)

    def test_llm_scratch_v0_uses_requirement_and_rubric_not_base_rules(self):
        draft = "## 从零规则\n1. 所有事实有据。\n2. 红线逐条执行。"
        replies = [
            "```json\n%s\n```" % json.dumps({
                "instructions_text": draft,
                "draft_summary": "依据需求和 rubric 起草",
                "covered_redlines": ["T2", "T3", "T5", "E4"],
            }, ensure_ascii=False),
            json.dumps({"T2": True, "T3": True, "T5": True, "E4": True}),
        ]
        prompts = []

        def fake_call(prompt, **kwargs):
            prompts.append(prompt)
            return replies.pop(0)

        with mock.patch.object(llm_client, "call_llm", side_effect=fake_call):
            gen = generator_mod.generate_v0(
                "面向总裁分析用户增长，所有数据必须可追溯",
                "research_insight",
                optimizer_mode="llm_rewrite",
                v0_strategy="llm_scratch",
            )
        skill = SkillArtifact.from_dict(gen["skill"])
        self.assertEqual(skill.instructions["prose"], draft)
        self.assertEqual(skill.instructions["v0_strategy"], "llm_scratch")
        self.assertIn("面向总裁分析用户增长", prompts[0])
        self.assertIn('"dimensions"', prompts[0])
        self.assertNotIn("可回溯性（生命线）", prompts[0])
        self.assertIn("未读取基础 Skill", gen["rationale"])
        self.assertEqual(len(prompts), 2)

    def test_base_skill_v0_is_default_and_does_not_call_llm(self):
        with mock.patch.object(
            llm_client,
            "call_llm",
            side_effect=AssertionError("base_skill 不应调用 LLM"),
        ):
            gen = generator_mod.generate_v0(
                "生成调研洞察报告",
                "research_insight",
                optimizer_mode="llm_rewrite",
            )
        skill = SkillArtifact.from_dict(gen["skill"])
        self.assertEqual(skill.instructions["v0_strategy"], "base_skill")
        self.assertIn("可回溯性（生命线）", skill.instructions["prose"])

    def test_v0_strategy_survives_snapshot_restore(self):
        draft = "## 从零规则\n1. 所有事实与结论必须有素材支持。"
        replies = [
            json.dumps({"instructions_text": draft}),
            json.dumps({"T2": True, "T3": True, "T5": True, "E4": True}),
        ]
        old_base = persist._BASE
        with tempfile.TemporaryDirectory() as tmp:
            persist._BASE = tmp
            try:
                with mock.patch.object(
                    llm_client,
                    "call_llm",
                    side_effect=lambda *args, **kwargs: replies.pop(0),
                ):
                    session = session_mod.Session(
                        "scratch-v0-session",
                        "生成面向管理层的调研报告",
                        "research_insight",
                        optimizer_mode="llm_rewrite",
                        v0_strategy="llm_scratch",
                    )
                restored = session_mod.Session.restore(session.to_snapshot())
            finally:
                persist._BASE = old_base
        self.assertEqual(restored.v0_strategy, "llm_scratch")
        self.assertEqual(restored.view()["v0_strategy"], "llm_scratch")
        self.assertEqual(
            restored.versions[0]["skill"].instructions["prose"],
            draft,
        )


class TestRedlineGuard(unittest.TestCase):
    def setUp(self):
        self.rubric = generator_mod._build_rubric_research()
        self._orig = llm_client.call_llm

    def tearDown(self):
        llm_client.call_llm = self._orig

    def test_guard_rejects_when_redline_dropped(self):
        llm_client.call_llm = lambda p, **kwargs: '{"T2": true, "T3": false, "T5": true, "E4": true}'
        r = optimizer02._redline_guard("正文", self.rubric)
        self.assertFalse(r["ok"])
        self.assertIn("T3", r["dropped"])

    def test_guard_passes_when_all_kept(self):
        llm_client.call_llm = lambda p, **kwargs: '{"T2": true, "T3": true, "T5": true, "E4": true}'
        r = optimizer02._redline_guard("正文", self.rubric)
        self.assertTrue(r["ok"])

    def test_rewrite_uses_independent_long_timeout_and_retry(self):
        received = {}

        def fake_call(prompt, **kwargs):
            received.update(kwargs)
            return "ok"

        llm_client.call_llm = fake_call
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                optimizer02._call_rewrite_llm(
                    "正文",
                    llm_backend="codex",
                    llm_model="gpt-5.6-sol",
                    llm_reasoning_effort="high",
                ),
                "ok",
            )
        self.assertEqual(received["timeout_seconds"], "600")
        self.assertEqual(received["retries"], "2")
        self.assertEqual(received["backend"], "codex")
        self.assertEqual(received["model"], "gpt-5.6-sol")
        self.assertEqual(received["reasoning_effort"], "high")


def _fake_llm(rewrite_text):
    def _call(prompt, **kwargs):
        if "红线义务清单" in prompt:                 # 守卫调用
            return '{"T2": true, "T3": true, "T5": true, "E4": true}'
        return ('{"instructions_text": %s, "change_summary": "改写", '
                '"targets_failures": [], "preserved": [], "hypothesis": "h", '
                '"self_check_no_hack": true}') % _json_str(rewrite_text)
    return _call


def _json_str(s):
    import json
    return json.dumps(s, ensure_ascii=False)


class TestLLMRewriteSettleLoop(unittest.TestCase):
    """advance -> pending 候选(不动 current_idx)-> 真实判分 -> settle 采纳/回滚。"""

    def setUp(self):
        self._orig_base = persist._BASE
        self._tmp = tempfile.mkdtemp()
        persist._BASE = self._tmp
        # 从真实 research-run 借 3 个合法 case(读盘用真实 base)
        real = _load_real_cases()
        self.cases = real
        self._orig_llm = llm_client.call_llm

    def tearDown(self):
        persist._BASE = self._orig_base
        llm_client.call_llm = self._orig_llm
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _set_judge(self, s, ver, val):
        """给某版本所有 case 写 numeric 逐-check 判分(val ∈ {1,0.5,0})。"""
        all_checks = [c["id"] for d in s.rubric["dimensions"] for c in d["checks"]]
        s.report_outputs.setdefault(ver, {})
        s.judge_checks.setdefault(ver, {})
        for c in self.cases:
            cid = c["case_id"]
            s.report_outputs[ver][cid] = "报告正文 for %s@%s" % (cid, ver)
            s.judge_checks[ver][cid] = {"checks": {k: val for k in all_checks}, "reasoning": {}}

    def _advance_to_candidate(self, s, rewrite_text):
        llm_client.call_llm = _fake_llm(rewrite_text)
        return s.advance(None)

    def test_reject_rolls_back_to_parent(self):
        s = session_mod.Session("_t_reject", "调研洞察", "research_insight",
                                optimizer_mode="llm_rewrite")
        s.import_data(self.cases)
        self._set_judge(s, "v0", 1.0)   # v0 满分
        s.evaluate(None)
        res = self._advance_to_candidate(s, "## 硬规则（改写差）\n1. 不编造、冲突不混用、单源降级、样本偏差、证据不足留白。")
        self.assertEqual(res["advance_result"]["status"], "proposed")
        self.assertEqual(s.pending_idx, 1)
        self.assertEqual(s.current_idx, 0)              # 关键:未提前移动
        # 候选真实判分更差(全 0.5) -> 应回滚
        self._set_judge(s, "v1", 0.5)
        settle = s.settle_pending_candidate(None)
        self.assertEqual(settle["verdict"], "rejected")
        self.assertEqual(s.current_idx, 0)              # 回滚:仍在父版
        self.assertIsNone(s.pending_idx)
        self.assertEqual(s.versions[1]["candidate_state"], "rejected")

        state = s.view(None)
        rejected_point = next(
            point for point in state["curve"]
            if point["version"] == "v1"
        )
        self.assertFalse(rejected_point["adopted"])
        self.assertEqual(rejected_point["candidate_state"], "rejected")
        self.assertEqual(rejected_point["verdict"], "rejected")
        self.assertAlmostEqual(rejected_point["dev"]["overall"], 3.0)
        self.assertEqual(state["best_version"], "v0")

        # 曲线展示回退分不改变生成逻辑：下一版仍从最佳采纳版 v0 分叉。
        next_state = self._advance_to_candidate(
            s,
            "## 硬规则（再次改写）\n1. 所有结论都绑定证据。",
        )
        self.assertEqual(next_state["advance_result"]["version"], "v2")
        self.assertEqual(s.versions[2]["parent"], "v0")
        self.assertEqual(s.current_idx, 0)
        self.assertEqual(next_state["best_version"], "v0")

    def test_rejected_score_curve_survives_restore(self):
        s = session_mod.Session(
            "_t_reject_restore",
            "调研洞察",
            "research_insight",
            optimizer_mode="llm_rewrite",
        )
        s.import_data(self.cases)
        self._set_judge(s, "v0", 1.0)
        s.evaluate(None)
        self._advance_to_candidate(
            s,
            "## 硬规则（改写差）\n1. 证据不足时直接输出结论。",
        )
        self._set_judge(s, "v1", 0.5)
        s.settle_pending_candidate(None)

        for version in ("v0", "v1"):
            persist.append_outputs_batch(
                s.id,
                version,
                s.report_outputs[version],
            )
            persist.append_check_judgments(
                s.id,
                version,
                s.judge_checks[version],
            )
        restored = session_mod.Session.restore(s.to_snapshot())
        state = restored.view(None)
        rejected_point = next(
            point for point in state["curve"]
            if point["version"] == "v1"
        )
        self.assertEqual(rejected_point["candidate_state"], "rejected")
        self.assertAlmostEqual(rejected_point["dev"]["overall"], 3.0)
        self.assertEqual(restored.current_idx, 0)
        self.assertEqual(state["best_version"], "v0")

    def test_adopt_moves_current(self):
        s = session_mod.Session("_t_adopt", "调研洞察", "research_insight",
                                optimizer_mode="llm_rewrite")
        s.import_data(self.cases)
        self._set_judge(s, "v0", 0.5)   # v0 中等
        s.evaluate(None)
        self._advance_to_candidate(s, "## 硬规则（改写好）\n1. 不编造、冲突不混用、单源降级、样本偏差、证据不足留白。")
        self.assertEqual(s.current_idx, 0)
        self._set_judge(s, "v1", 1.0)   # 候选满分 -> 采纳
        settle = s.settle_pending_candidate(None)
        self.assertEqual(settle["verdict"], "adopted")
        self.assertEqual(s.current_idx, 1)
        self.assertIsNone(s.pending_idx)
        self.assertTrue(s.versions[1]["adopted"])

    def test_target_overall_stops_before_next_candidate(self):
        s = session_mod.Session(
            "_t_stop_target",
            "面向总裁的调研洞察",
            "research_insight",
            optimizer_mode="llm_rewrite",
            optimizer_stop={
                "overall_target": 4.8,
                "max_no_improvement": 4,
            },
        )
        s.import_data(self.cases)
        self._set_judge(s, "v0", 1.0)
        s.evaluate(None)

        state = s.advance(None)

        self.assertEqual(
            state["advance_result"]["code"],
            "overall_target_reached",
        )
        self.assertEqual(len(s.versions), 1)
        self.assertTrue(state["optimizer_stop"]["stopped"])
        self.assertFalse(state["actions"]["advance"]["enabled"])

    def test_four_candidates_without_overall_improvement_stop_loop(self):
        s = session_mod.Session(
            "_t_stop_patience",
            "面向总裁的调研洞察",
            "research_insight",
            optimizer_mode="llm_rewrite",
            optimizer_stop={
                "overall_target": 4.8,
                "max_no_improvement": 4,
            },
        )
        s.import_data(self.cases)
        self._set_judge(s, "v0", 0.5)
        s.evaluate(None)

        for index in range(1, 5):
            proposed = self._advance_to_candidate(
                s,
                "## 硬规则（无提升 %d）\n1. 保持现状。" % index,
            )
            self.assertEqual(
                proposed["advance_result"]["status"],
                "proposed",
            )
            self._set_judge(s, "v%d" % index, 0.5)
            s.settle_pending_candidate(None)

        stop = s.view(None)["optimizer_stop"]
        self.assertTrue(stop["stopped"])
        self.assertEqual(
            stop["code"],
            "no_improvement_patience_reached",
        )
        self.assertEqual(stop["no_improvement_streak"], 4)
        self.assertEqual(stop["evaluated_candidates"], 4)

        state = s.advance(None)
        self.assertEqual(state["advance_result"]["status"], "converged")
        self.assertEqual(len(s.versions), 5)


def _load_real_cases():
    base = persist._BASE
    # 临时切回真实 base 读 research-run 的 cases
    real_base = str((APP / "sessions"))
    persist._BASE = real_base
    try:
        snap = persist.load_snapshot("research-run")
        cases = (snap or {}).get("cases") or []
    finally:
        persist._BASE = base
    return cases


if __name__ == "__main__":
    unittest.main()
