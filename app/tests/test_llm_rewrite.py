# -*- coding: utf-8 -*-
"""LLM 自由改写策略(optimizer02)+ 共享评测/异步 gate 的测试。"""
from __future__ import annotations

import copy
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
import production_skill_policy  # noqa: E402
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

    def test_api_max_tokens_override_does_not_change_global_default(self):
        env = {
            "ANTHROPIC_API_KEY": "test-key",
            "ANTHROPIC_BASE_URL": "https://llm.example",
            "LLM_API_STYLE": "openai",
            "LLM_MAX_TOKENS": "8000",
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
                    max_tokens="12000",
                    retries=0,
                )
        self.assertEqual(result, "ok")
        body = json.loads(
            call.call_args.args[0].data.decode("utf-8")
        )
        self.assertEqual(body["max_tokens"], 12000)

    def test_empty_response_retries_then_succeeds(self):
        env = {
            "ANTHROPIC_API_KEY": "test-key",
            "ANTHROPIC_BASE_URL": "https://llm.example",
            "LLM_API_STYLE": "openai",
        }
        empty = _FakeResponse(json.dumps({
            "id": "empty-1",
            "choices": [{
                "finish_reason": "length",
                "message": {
                    "content": "",
                    "reasoning_content": "private reasoning",
                },
            }],
            "usage": {
                "completion_tokens": 12000,
                "completion_tokens_details": {
                    "reasoning_tokens": 12000,
                },
            },
        }).encode("utf-8"))
        success = _FakeResponse(json.dumps({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "valid"},
            }],
        }).encode("utf-8"))
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(
                urllib.request,
                "urlopen",
                side_effect=[empty, success],
            ) as call:
                with mock.patch.object(llm_client.time, "sleep") as sleep:
                    result = llm_client.call_llm(
                        "prompt",
                        max_tokens=12000,
                        retries=1,
                    )
        self.assertEqual(result, "valid")
        self.assertEqual(call.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_final_empty_response_has_sanitized_diagnostics(self):
        env = {
            "ANTHROPIC_API_KEY": "test-key",
            "ANTHROPIC_BASE_URL": "https://llm.example",
            "LLM_API_STYLE": "openai",
        }
        secret_reasoning = "never persist this reasoning"

        def empty_response(response_id):
            return _FakeResponse(json.dumps({
                "id": response_id,
                "choices": [{
                    "finish_reason": "length",
                    "message": {
                        "content": None,
                        "reasoning_content": secret_reasoning,
                    },
                }],
                "usage": {
                    "prompt_tokens": 43000,
                    "completion_tokens": 12000,
                    "total_tokens": 55000,
                    "completion_tokens_details": {
                        "reasoning_tokens": 12000,
                    },
                },
            }).encode("utf-8"))

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(
                urllib.request,
                "urlopen",
                side_effect=[empty_response("e1"), empty_response("e2")],
            ) as call:
                with mock.patch.object(llm_client.time, "sleep"):
                    with self.assertRaises(
                        llm_client.EmptyLLMResponseError
                    ) as ctx:
                        llm_client.call_llm(
                            "prompt",
                            max_tokens=12000,
                            retries=1,
                        )
        error = ctx.exception
        self.assertEqual(call.call_count, 2)
        self.assertEqual(error.diagnostics["max_tokens"], 12000)
        self.assertEqual(len(error.diagnostics["attempts"]), 2)
        last = error.diagnostics["attempts"][-1]
        self.assertEqual(last["finish_reason"], "length")
        self.assertEqual(last["usage"]["reasoning_tokens"], 12000)
        self.assertEqual(
            last["reasoning_content_chars"],
            len(secret_reasoning),
        )
        serialized = json.dumps(error.diagnostics, ensure_ascii=False)
        self.assertNotIn(secret_reasoning, serialized)
        self.assertIn("finish_reason=length", str(error))

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


class _EmptyAdvanceSession(_FailingAdvanceSession):
    id = "advance-empty-response"

    def advance(self, **_kwargs):
        raise llm_client.EmptyLLMResponseError(
            "上游 LLM 返回空内容",
            {
                "error_code": "empty_llm_response",
                "attempts": [{"finish_reason": "length"}],
            },
        )


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

    def test_empty_llm_failure_returns_machine_readable_code(self):
        original_sessions = dict(server_mod.SESSIONS)
        original_service = server_mod.GENERATION_SERVICE
        server_mod.SESSIONS.clear()
        server_mod.SESSIONS[_EmptyAdvanceSession.id] = _EmptyAdvanceSession()
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
            request = urllib.request.Request(
                "http://127.0.0.1:%d/api/advance"
                % httpd.server_port,
                data=json.dumps({
                    "id": _EmptyAdvanceSession.id,
                }).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(request, timeout=3)
            payload = json.loads(ctx.exception.read().decode("utf-8"))
            self.assertEqual(ctx.exception.code, 502)
            self.assertEqual(payload["code"], "empty_llm_response")
            self.assertEqual(
                payload["details"]["attempts"][0]["finish_reason"],
                "length",
            )
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

    def _holdout(self, champion_overall=4.0, candidate_overall=4.0, **candidate_overrides):
        champion = self._dims(champion_overall)
        candidate = self._dims(candidate_overall, **candidate_overrides)
        return {
            "available": True,
            "champion_scores": champion,
            "candidate_scores": candidate,
            "champion_hard": {"redline_failures": 0, "hard_floor_failures": 0},
            "candidate_hard": {"redline_failures": 0, "hard_floor_failures": 0},
        }

    def _hard(self, keys, floor=0):
        return {
            "redline_failures": len(keys),
            "hard_floor_failures": floor,
            "redline_failure_keys_available": True,
            "redline_failure_keys": [list(item) for item in keys],
        }

    def test_adopt_when_target_up_no_regression(self):
        parent = self._dims(4.0)
        cand = self._dims(4.2, insight=4.5)
        adopt, verdict, _ = optimizer_pipeline.evaluate_gate(
            parent,
            cand,
            ["insight"],
            0.15,
            self.DIMS,
            holdout=self._holdout(4.0, 4.01),
        )
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

    def test_adopt_when_redline_count_drops_despite_failure_key_churn(self):
        champion = self._dims(4.0, red=4)
        candidate = self._dims(4.2, red=3, insight=4.3)
        champion_keys = [
            ("case-a", "T1"),
            ("case-b", "T2"),
            ("case-c", "T3"),
            ("case-d", "T5"),
        ]
        candidate_keys = [
            ("case-a", "T1"),
            ("case-b", "T2"),
            ("case-new", "E5"),
        ]

        adopt, verdict, reasons = optimizer_pipeline.evaluate_gate(
            champion,
            candidate,
            ["insight"],
            0.15,
            self.DIMS,
            champion_hard=self._hard(champion_keys),
            candidate_hard=self._hard(candidate_keys),
        )

        self.assertTrue(adopt)
        self.assertEqual(verdict, "adopted")
        self.assertTrue(reasons["hard_failures_improved"])
        self.assertTrue(reasons["red_line_new"])
        self.assertEqual(
            reasons["new_redline_failure_keys"],
            [["case-new", "E5"]],
        )

    def test_adopt_when_redline_count_drops_without_new_failure_key(self):
        champion = self._dims(4.0, red=3)
        candidate = self._dims(4.0, red=2)
        champion_keys = [
            ("case-a", "T1"),
            ("case-b", "T2"),
            ("case-c", "T3"),
        ]
        candidate_keys = [
            ("case-a", "T1"),
            ("case-c", "T3"),
        ]

        adopt, verdict, reasons = optimizer_pipeline.evaluate_gate(
            champion,
            candidate,
            ["traceability"],
            0.15,
            self.DIMS,
            champion_hard=self._hard(champion_keys),
            candidate_hard=self._hard(candidate_keys),
        )

        self.assertTrue(adopt)
        self.assertEqual(verdict, "adopted")
        self.assertFalse(reasons["red_line_new"])
        self.assertEqual(
            reasons["resolved_redline_failure_keys"],
            [["case-b", "T2"]],
        )

    def test_holdout_allows_same_count_with_different_redline_key(self):
        champion = self._dims(4.0)
        candidate = self._dims(4.1, insight=4.2)
        holdout = self._holdout(4.0, 4.0)
        holdout["champion_hard"] = self._hard([("test-a", "T1")])
        holdout["candidate_hard"] = self._hard([("test-a", "T2")])

        adopt, _, reasons = optimizer_pipeline.evaluate_gate(
            champion,
            candidate,
            ["insight"],
            0.15,
            self.DIMS,
            champion_hard=self._hard([]),
            candidate_hard=self._hard([]),
            holdout=holdout,
        )

        self.assertTrue(adopt)
        self.assertTrue(reasons["holdout_passed"])
        self.assertTrue(reasons["holdout_red_line_new"])
        self.assertEqual(
            reasons["holdout_new_redline_failure_keys"],
            [["test-a", "T2"]],
        )

    def test_hard_failure_metrics_exposes_real_case_check_keys(self):
        session = SimpleNamespace(
            rubric={
                "dimensions": [
                    {
                        "name": "traceability",
                        "hard_floor": 3,
                        "checks": [
                            {"id": "T1", "redline": True},
                            {"id": "T2", "redline": True},
                        ],
                    }
                ]
            }
        )
        entry = {
            "_recs": [
                SimpleNamespace(
                    case_id="case-a",
                    dataset_split="dev",
                    judge_checks={"T1": 0, "T2": 1},
                    scores={"traceability": 2.5},
                ),
                SimpleNamespace(
                    case_id="case-b",
                    dataset_split="dev",
                    judge_checks={"T1": 1, "T2": 0},
                    scores={"traceability": 4.0},
                ),
            ],
            "dev": {"red_line_fails": 2},
        }

        metrics = optimizer_pipeline.hard_failure_metrics(session, entry)

        self.assertTrue(metrics["redline_failure_keys_available"])
        self.assertEqual(
            metrics["redline_failure_keys"],
            [["case-a", "T1"], ["case-b", "T2"]],
        )

    def test_reject_when_no_improvement(self):
        parent = self._dims(4.0)
        cand = self._dims(4.0)
        adopt, _, _ = optimizer_pipeline.evaluate_gate(
            parent, cand, ["insight"], 0.15, self.DIMS)
        self.assertFalse(adopt)

    def test_reject_v5_style_target_gain_with_lower_overall(self):
        champion = self._dims(
            3.713,
            red=12,
            traceability=2.6,
            structure=4.7,
            narrative=4.433,
            insight=4.1,
            coverage=4.6,
            expression=3.183,
        )
        candidate = self._dims(
            3.689,
            red=12,
            traceability=2.517,
            structure=4.633,
            narrative=4.733,
            insight=4.05,
            coverage=4.5,
            expression=3.133,
        )
        adopt, _, reasons = optimizer_pipeline.evaluate_gate(
            champion,
            candidate,
            ["narrative"],
            0.15,
            self.DIMS,
        )
        self.assertFalse(adopt)
        self.assertEqual(reasons["overall_delta"], -0.024)
        self.assertFalse(reasons["effective_overall_improvement"])

    def test_target_dimension_is_not_exempt_from_regression_check(self):
        champion = self._dims(4.0, insight=4.5)
        candidate = self._dims(4.1, insight=4.2)
        adopt, _, reasons = optimizer_pipeline.evaluate_gate(
            champion,
            candidate,
            ["insight"],
            0.15,
            self.DIMS,
            holdout=self._holdout(4.0, 4.1),
        )
        self.assertFalse(adopt)
        self.assertEqual(reasons["regressed_dims"], ["insight"])

    def test_0001_is_noise_not_real_improvement(self):
        champion = self._dims(4.0)
        candidate = self._dims(4.001, insight=4.001)
        adopt, _, reasons = optimizer_pipeline.evaluate_gate(
            champion,
            candidate,
            ["insight"],
            0.15,
            self.DIMS,
            holdout=self._holdout(4.0, 4.1),
        )
        self.assertFalse(adopt)
        self.assertFalse(reasons["effective_overall_improvement"])

    def test_reject_when_redline_reduction_trades_for_more_floor_failures(self):
        champion = self._dims(4.0, red=2)
        candidate = self._dims(3.98, red=1, traceability=3.9)
        adopt, _, reasons = optimizer_pipeline.evaluate_gate(
            champion,
            candidate,
            ["traceability"],
            0.15,
            self.DIMS,
            champion_hard=self._hard(
                [("case-a", "T1"), ("case-b", "T2")],
                floor=2,
            ),
            candidate_hard=self._hard([("case-a", "T1")], floor=3),
        )
        self.assertFalse(adopt)
        self.assertTrue(reasons["hard_failures_regressed"])
        self.assertFalse(reasons["hard_failures_not_worse"])

    def test_adopt_net_redline_improvement_when_exact_keys_are_unavailable(self):
        champion = self._dims(4.0, red=2)
        candidate = self._dims(4.1, red=1, insight=4.2)

        adopt, _, reasons = optimizer_pipeline.evaluate_gate(
            champion,
            candidate,
            ["insight"],
            0.15,
            self.DIMS,
            champion_hard={
                "redline_failures": 2,
                "hard_floor_failures": 0,
            },
            candidate_hard={
                "redline_failures": 1,
                "hard_floor_failures": 0,
            },
        )

        self.assertTrue(adopt)
        self.assertIsNone(reasons["red_line_new"])
        self.assertFalse(reasons["redline_failure_keys_verified"])
        self.assertEqual(reasons["adoption_path"], "hard_failures_reduced")

    def test_reject_hard_improvement_when_target_check_materially_regresses(self):
        champion = self._dims(4.0, red=4)
        candidate = self._dims(4.1, red=3, insight=4.2)

        adopt, _, reasons = optimizer_pipeline.evaluate_gate(
            champion,
            candidate,
            ["insight"],
            0.15,
            self.DIMS,
            champion_hard={"redline_failures": 4, "hard_floor_failures": 2},
            candidate_hard={"redline_failures": 3, "hard_floor_failures": 2},
            target_check={"available": True, "delta": -0.1},
        )

        self.assertFalse(adopt)
        self.assertFalse(reasons["target_check_stable"])
        self.assertIn("目标 check 回退", reasons["message"])

    def test_hard_improvement_uses_holdout_guard_when_available(self):
        champion = self._dims(4.0, red=4)
        candidate = self._dims(4.1, red=3, insight=4.2)
        holdout = self._holdout(4.0, 3.99)
        holdout["champion_hard"] = {
            "redline_failures": 1,
            "hard_floor_failures": 0,
        }
        holdout["candidate_hard"] = {
            "redline_failures": 2,
            "hard_floor_failures": 0,
        }

        adopt, _, reasons = optimizer_pipeline.evaluate_gate(
            champion,
            candidate,
            ["insight"],
            0.15,
            self.DIMS,
            champion_hard={"redline_failures": 4, "hard_floor_failures": 2},
            candidate_hard={"redline_failures": 3, "hard_floor_failures": 2},
            holdout=holdout,
            target_check={"available": True, "delta": 0.01},
        )

        self.assertFalse(adopt)
        self.assertFalse(reasons["holdout_guard_passed"])
        self.assertIn("holdout 保护未通过", reasons["message"])

    def test_hard_improvement_allows_small_holdout_overall_noise(self):
        champion = self._dims(4.0, red=4)
        candidate = self._dims(4.1, red=3, insight=4.2)
        holdout = self._holdout(4.0, 3.96)

        adopt, _, reasons = optimizer_pipeline.evaluate_gate(
            champion,
            candidate,
            ["insight"],
            0.15,
            self.DIMS,
            champion_hard={"redline_failures": 4, "hard_floor_failures": 2},
            candidate_hard={"redline_failures": 3, "hard_floor_failures": 2},
            holdout=holdout,
            target_check={"available": True, "delta": -0.05},
        )

        self.assertTrue(adopt)
        self.assertTrue(reasons["holdout_guard_passed"])
        self.assertFalse(reasons["holdout_passed"])
        self.assertTrue(reasons["target_check_stable"])

    def test_overall_improvement_requires_holdout(self):
        champion = self._dims(4.0)
        candidate = self._dims(4.05, insight=4.2)
        adopt, _, reasons = optimizer_pipeline.evaluate_gate(
            champion,
            candidate,
            ["insight"],
            0.15,
            self.DIMS,
        )
        self.assertFalse(adopt)
        self.assertFalse(reasons["holdout_available"])

    def test_overall_002_and_passing_holdout_can_adopt(self):
        champion = self._dims(4.0)
        candidate = self._dims(4.02, insight=4.2)
        adopt, _, reasons = optimizer_pipeline.evaluate_gate(
            champion,
            candidate,
            ["insight"],
            0.15,
            self.DIMS,
            holdout=self._holdout(4.0, 4.0),
        )
        self.assertTrue(adopt)
        self.assertEqual(
            reasons["adoption_path"],
            "overall_effective_and_holdout_passed",
        )

    def test_overall_0019_is_below_effective_threshold(self):
        champion = self._dims(4.0)
        candidate = self._dims(4.019, insight=4.2)

        adopt, _, reasons = optimizer_pipeline.evaluate_gate(
            champion,
            candidate,
            ["insight"],
            0.15,
            self.DIMS,
            holdout=self._holdout(4.0, 4.0),
        )

        self.assertFalse(adopt)
        self.assertFalse(reasons["effective_overall_improvement"])
        self.assertEqual(reasons["min_overall_improvement"], 0.02)

    def test_hydrate_gate_policy_migrates_legacy_005_threshold(self):
        rubric = {
            "gates": [{
                "id": "no_regression",
                "min_overall_improvement": 0.05,
            }],
        }

        optimizer_pipeline.hydrate_gate_policy(rubric)

        self.assertEqual(
            rubric["gates"][0]["min_overall_improvement"],
            0.02,
        )
        self.assertEqual(
            optimizer_pipeline.min_overall_improvement(rubric),
            0.02,
        )

    def test_overall_improvement_rejected_when_holdout_regresses(self):
        champion = self._dims(4.0)
        candidate = self._dims(4.1, insight=4.2)
        adopt, _, reasons = optimizer_pipeline.evaluate_gate(
            champion,
            candidate,
            ["insight"],
            0.15,
            self.DIMS,
            holdout=self._holdout(4.0, 3.9),
        )
        self.assertFalse(adopt)
        self.assertFalse(reasons["holdout_passed"])

    def test_historical_champion_uses_lexicographic_hard_failures_first(self):
        session = SimpleNamespace(
            rubric={
                "dimensions": [
                    {"name": dim, "hard_floor": 3 if dim == "traceability" else None, "checks": []}
                    for dim in self.DIMS
                ]
            },
            versions=[
                {"version": "v1", "adopted": True, "dev": self._dims(4.6, red=2)},
                {"version": "v2", "adopted": True, "dev": self._dims(4.1, red=1)},
                {"version": "v3", "adopted": True, "dev": self._dims(4.0, red=1)},
            ],
        )
        champion = optimizer_pipeline.champion_entry(session)
        self.assertEqual(champion["version"], "v2")


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
        prop = {"_compiled_instructions_text": "## 硬规则（改写）\n1. 不编造、冲突不混用、单源降级、样本偏差、证据不足留白。",
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
        self.assertIn("本次报告任务约束", txt)
        self.assertIn("材料重点分布", txt)
        self.assertIn("报告结构（面向高管，三部分", txt)   # 结构层保留
        self.assertNotIn("OPENHARNESS", txt)              # 编译元数据不进产物
        self.assertNotIn("directive", txt.lower())
        self.assertNotIn("rubric", contract.lower())
        self.assertNotRegex(contract, r"\bT[1-6]\b")

    def test_production_quality_projection_excludes_evaluation_metadata(self):
        rubric = generator_mod._build_rubric_research()
        serialized = json.dumps(
            production_skill_policy.quality_requirements(rubric),
            ensure_ascii=False,
        )
        self.assertNotIn('"id"', serialized)
        self.assertNotIn('"weight"', serialized)
        self.assertNotIn('"gates"', serialized)
        self.assertNotIn("champion", serialized.lower())
        self.assertNotIn("holdout", serialized.lower())
        self.assertNotRegex(serialized, r"\bT[1-6]\b")

    def test_llm_scratch_v0_uses_deidentified_content_rules_not_rubric(self):
        draft = "## 从零规则\n1. 所有事实有据。\n2. 所有强制要求逐条执行。"
        replies = [
            "```json\n%s\n```" % json.dumps({
                "instructions_text": draft,
                "draft_summary": "依据需求和 rubric 起草",
                "covered_redlines": [
                    "T1", "T2", "T3", "T5", "E4", "E5",
                ],
            }, ensure_ascii=False),
            json.dumps({
                "T1": True,
                "T2": True,
                "T3": True,
                "T5": True,
                "E4": True,
                "E5": True,
            }),
        ]
        prompts = []
        call_options = []

        def fake_call(prompt, **kwargs):
            prompts.append(prompt)
            call_options.append(kwargs)
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
        self.assertIn("生产内容要求", prompts[0])
        self.assertNotIn('"dimensions"', prompts[0])
        self.assertNotIn('"weight"', prompts[0])
        self.assertNotIn('"gates"', prompts[0])
        self.assertNotIn('"target"', prompts[0])
        self.assertNotIn('"T1"', prompts[0])
        self.assertNotIn("评分", prompts[0])
        self.assertNotIn("采纳", prompts[0])
        self.assertNotIn("gate", prompts[0].lower())
        self.assertNotIn("champion", prompts[0].lower())
        self.assertNotIn("holdout", prompts[0].lower())
        self.assertNotIn("可回溯性（生命线）", prompts[0])
        self.assertIn("未读取基础 Skill", gen["rationale"])
        self.assertEqual(len(prompts), 2)
        self.assertEqual(len(call_options), 2)
        for options in call_options:
            self.assertEqual(options["backend"], "codex")
            self.assertEqual(options["model"], "gpt-5.6-sol")
            self.assertEqual(options["reasoning_effort"], "medium")

    def test_llm_scratch_v0_rejects_evaluation_metadata_in_skill_text(self):
        leaked = (
            "## 评分与 Gate\n"
            "按 rubric 权重计算综合分，并与 champion 在 holdout 上比较。"
        )
        with mock.patch.object(
            llm_client,
            "call_llm",
            return_value=json.dumps({"instructions_text": leaked}),
        ) as call:
            with self.assertRaisesRegex(
                llm_client.LLMClientError,
                "评测元数据",
            ):
                generator_mod.generate_v0(
                    "生成管理层调研报告",
                    "research_insight",
                    optimizer_mode="llm_rewrite",
                    v0_strategy="llm_scratch",
                )
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.kwargs["backend"], "codex")
        self.assertEqual(call.call_args.kwargs["model"], "gpt-5.6-sol")
        self.assertEqual(call.call_args.kwargs["reasoning_effort"], "medium")

    def test_production_policy_rejects_scoring_heading_and_check_id(self):
        for leaked in ("## 评分规则", "请按 T1 执行"):
            with self.subTest(leaked=leaked):
                with self.assertRaisesRegex(ValueError, "评测元数据"):
                    production_skill_policy.validate_production_text(leaked)

    def test_compiler_strips_legacy_scoring_sections(self):
        gen = generator_mod.generate_v0(
            "生成管理层调研报告",
            "research_insight",
            optimizer_mode="llm_rewrite",
        )
        skill = SkillArtifact.from_dict(gen["skill"])
        skill.instructions["prose"] = (
            "## 内容规则\n所有结论都必须有素材支撑。\n\n"
            "建议质量目标：可回溯性不低于 4.2，综合质量不低于 4.0。\n\n"
            "## 评分与交付 Gate\n"
            "按 rubric 权重评分；候选稿与 champion 在 holdout 上比较后采纳。"
        )
        skill.instructions["requirement_contract"] += (
            "\n- 严格满足当前 rubric，并保留 T1/T2。"
        )
        with tempfile.TemporaryDirectory() as tmp:
            frozen = compile_session_skill(
                Path(tmp),
                "legacy",
                skill,
                BASE_SKILL,
            )
            text = (
                frozen.path / "references" / "instructions.md"
            ).read_text(encoding="utf-8")
        self.assertIn("所有结论都必须有素材支撑", text)
        self.assertNotIn("建议质量目标", text)
        self.assertNotIn("评分与交付", text)
        self.assertNotIn("champion", text.lower())
        self.assertNotIn("holdout", text.lower())

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
        self.assertIn("可回溯性（必须遵守）", skill.instructions["prose"])

    def test_v0_strategy_survives_snapshot_restore(self):
        draft = "## 从零规则\n1. 所有事实与结论必须有素材支持。"
        replies = [
            json.dumps({"instructions_text": draft}),
            json.dumps({
                "T1": True,
                "T2": True,
                "T3": True,
                "T5": True,
                "E4": True,
                "E5": True,
            }),
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


class TestOptimizerLLMCall(unittest.TestCase):
    def setUp(self):
        self._orig = llm_client.call_llm

    def tearDown(self):
        llm_client.call_llm = self._orig

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
        self.assertEqual(received["max_tokens"], "12000")

    def test_empty_rewrite_is_recorded_without_raw_reasoning(self):
        session = SimpleNamespace(opt_history=[])
        diagnostics = {
            "attempts": [{
                "finish_reason": "length",
                "reasoning_content_chars": 100,
            }],
        }
        error = llm_client.EmptyLLMResponseError(
            "上游 LLM 返回空内容",
            diagnostics,
        )
        with mock.patch.object(
            optimizer02,
            "_call_rewrite_llm",
            side_effect=error,
        ):
            with self.assertRaises(llm_client.EmptyLLMResponseError):
                optimizer02.propose(
                    session,
                    None,
                    [],
                    _optimizer_context(),
                )
        recorded = session.opt_history[-1]
        self.assertEqual(recorded["error_code"], "empty_llm_response")
        self.assertEqual(recorded["stage"], "diagnosis")
        self.assertEqual(recorded["model_calls"], 1)
        self.assertEqual(recorded["llm_diagnostics"], diagnostics)
        self.assertNotIn("raw_head", recorded)


def _experiment_packets():
    packets = []
    for check_index in range(1, 3):
        for case_index in range(1, 4):
            index = len(packets) + 1
            packets.append({
                "evidence_id": "EXP-%02d" % index,
                "pattern_id": "pattern-%d" % check_index,
                "case_id": "case-%d-%d" % (check_index, case_index),
                "check_id": "T%d" % check_index,
                "report_sentence": "报告原句 %d-%d" % (
                    check_index,
                    case_index,
                ),
                "evidence": "source-%d | 素材证据 %d" % (index, index),
                "judge_verdict": "T%d=0.5；未满足" % check_index,
                "expected_change_hint": "修复 T%d" % check_index,
            })
    return packets


def _diagnosis_payload(context, root_type="skill", selected_index=0):
    evidence = context["diagnosis_evidence"]
    diagnoses = []
    for candidate in context["diagnosis_candidates"]:
        packets = [
            item for item in evidence
            if item["check_id"] == candidate["check_id"]
            and item["pattern_id"] == candidate["pattern_id"]
        ]
        diagnoses.append({
            "check_id": candidate["check_id"],
            "pattern_id": candidate["pattern_id"],
            "root_cause_type": root_type if len(diagnoses) == selected_index else "skill",
            "rationale": "三条同 check 证据指向共同生成规则缺陷",
            "confidence": "high",
            "evidence_ids": [item["evidence_id"] for item in packets[:3]],
        })
    selected = dict(diagnoses[selected_index])
    selected["rationale"] = "失败覆盖多个 case，且本轮未处于冷却期"
    return {
        "diagnoses": diagnoses,
        "selected_target": selected,
        "deferred_failures": [
            {
                "check_id": item["check_id"],
                "pattern_id": item["pattern_id"],
                "reason": "本轮保持单一变量，留待后续实验",
            }
            for index, item in enumerate(diagnoses)
            if index != selected_index
        ],
    }


def _optimizer_payload(context, diagnosis=None):
    diagnosis = diagnosis or _diagnosis_payload(context)
    selected = diagnosis["selected_target"]
    packets = list(context.get("target_evidence") or [])
    if not packets:
        packets = [
            item for item in context["evidence_catalog"]
            if item["check_id"] == selected["check_id"]
            and item["pattern_id"] == selected["pattern_id"]
        ]
    selected_ids = set(
        selected.get("evidence_ids")
        or [item["evidence_id"] for item in packets[:3]]
    )
    packets = [
        item for item in packets if item["evidence_id"] in selected_ids
    ]
    redline_keys = list(
        (context.get("harness_declarations") or {}).get(
            "preservation_keys",
            [],
        )
    )
    if not redline_keys:
        redline_keys = [
            item["id"]
            for item in (context.get("guardrails") or {}).get(
                "redline_checks",
                [],
            )
        ]
    redlines = {check_id: True for check_id in redline_keys}
    return {
        "experiment": {
            "hypothesis": "替换规则 A 后目标 check 会改善",
            "examples": [{
                "evidence_id": item["evidence_id"],
                "case_id": item["case_id"],
                "check_id": item["check_id"],
                "report_sentence": item["report_sentence"],
                "evidence": item["evidence"],
                "judge_verdict": item["judge_verdict"],
                "expected_change": "改进 %s" % item["check_id"],
            } for item in packets],
            "success_criteria": [{
                "check_id": selected["check_id"],
                "expected": "均分提高且其余维度不回退",
            }],
            "rollback_condition": "目标 check 不升或其他维度回退",
        },
        "patch": {
            "add": [],
            "replace": [{"old_text": "规则 A", "new_text": "规则 A（实验）"}],
            "delete": [],
        },
        "change_summary": "做一项局部规则实验",
        "preserved": ["规则 B"],
        "redline_preservation": redlines,
        "self_check_no_hack": True,
    }


def _fake_llm(rewrite_text):
    def _call(prompt, **kwargs):
        if "## Diagnosis 上下文\n" in prompt:
            context_text = prompt.split("## Diagnosis 上下文\n", 1)[1].split(
                "\n## 输出格式", 1
            )[0]
            context = json.loads(context_text)
            return json.dumps(
                _diagnosis_payload(context),
                ensure_ascii=False,
            )
        context_text = prompt.split("## Patch 上下文\n", 1)[1].split(
            "\n## 输出格式", 1
        )[0]
        context = json.loads(context_text)
        parent = context["current_skill"]["instructions_text"]
        anchor = next(
            line for line in parent.splitlines()
            if line.strip() and parent.count(line) == 1
        )
        payload = _optimizer_payload(
            context,
            {"selected_target": context["experiment_key"]},
        )
        payload["patch"]["replace"] = [{
            "old_text": anchor,
            "new_text": anchor + "\n<!-- 实验：%s -->" % rewrite_text[:60],
        }]
        payload["change_summary"] = "局部可验证改写"
        return json.dumps(payload, ensure_ascii=False)
    return _call


def _json_str(s):
    import json
    return json.dumps(s, ensure_ascii=False)


def _optimizer_context(parent="规则 A\n规则 B"):
    packets = _experiment_packets()
    inventory = [
        {
            "check_id": "T%d" % index,
            "pattern_id": "pattern-%d" % index,
            "dimension": "dim-%d" % index,
            "redline": index == 1,
            "priority": index,
            "failure_mass": 6,
            "affected_case_count": 3,
            "replayable_evidence_count": 3,
            "cooldown_recommended": False,
        }
        for index in range(1, 3)
    ]
    return {
        "requirement": "生成调研洞察报告",
        "rubric": {"dimensions": []},
        "current_best": {"instructions_text": parent},
        "must_preserve": {"passing_checks": []},
        "open_failures": [
            {"pattern_id": "pattern-1"},
            {"pattern_id": "pattern-2"},
        ],
        "failure_inventory": inventory,
        "diagnosis_candidates": [dict(item) for item in inventory],
        "diagnosis_evidence": packets,
        "experiment_evidence": packets,
        "evidence_catalog": packets,
        "history": [],
        "tried_rejected": [],
        "guardrails": {"redline_checks": []},
        "production_requirement_by_check": {
            "T1": "每个事实必须有素材支撑。",
            "T2": "转述必须保持素材原意。",
        },
        "mandatory_production_requirements": [
            "每个事实必须有素材支撑。",
        ],
        "patch_constraints": {
            "max_instruction_chars": 200,
            "max_net_growth_chars": 40,
            "max_patch_operations": 6,
            "min_experiment_examples": 3,
            "max_experiment_examples": 5,
        },
        "root_cause_policy": {"deterministic_signals": []},
    }


class TestStructuredOptimizer(unittest.TestCase):
    def test_compile_patch_rejects_full_text_replacement(self):
        parent = "规则 A\n规则 B"
        with self.assertRaisesRegex(ValueError, "整个父版全文"):
            optimizer02._compile_patch(
                parent,
                {
                    "add": [],
                    "replace": [{"old_text": parent, "new_text": "全新全文"}],
                    "delete": [],
                },
                _optimizer_context(parent)["patch_constraints"],
            )

    def test_add_requires_paired_duplicate_deletion(self):
        with self.assertRaisesRegex(ValueError, "paired_delete"):
            optimizer02._compile_patch(
                "旧重复规则\n锚点",
                {
                    "add": [{
                        "after": "锚点",
                        "text": "新规则",
                        "paired_delete": "旧重复规则",
                    }],
                    "replace": [],
                    "delete": [],
                },
                _optimizer_context()["patch_constraints"],
            )

    def test_paired_add_delete_and_budgets_are_applied(self):
        candidate, patch, budget = optimizer02._compile_patch(
            "旧重复规则\n锚点",
            {
                "add": [{
                    "after": "锚点",
                    "text": "\n新规则",
                    "paired_delete": "旧重复规则",
                }],
                "replace": [],
                "delete": [{"old_text": "旧重复规则"}],
            },
            _optimizer_context()["patch_constraints"],
        )
        self.assertNotIn("旧重复规则", candidate)
        self.assertIn("新规则", candidate)
        self.assertEqual(patch["add"][0]["paired_delete"], "旧重复规则")
        self.assertEqual(budget["operation_count"], 2)

    def test_one_deleted_duplicate_cannot_cover_multiple_adds(self):
        with self.assertRaisesRegex(ValueError, "只能配对一个 add"):
            optimizer02._compile_patch(
                "旧重复规则\n锚点一\n锚点二",
                {
                    "add": [
                        {
                            "after": "锚点一",
                            "text": "新规则一",
                            "paired_delete": "旧重复规则",
                        },
                        {
                            "after": "锚点二",
                            "text": "新规则二",
                            "paired_delete": "旧重复规则",
                        },
                    ],
                    "replace": [],
                    "delete": [{"old_text": "旧重复规则"}],
                },
                _optimizer_context()["patch_constraints"],
            )

    def test_total_and_net_growth_budgets_block_patch(self):
        with self.assertRaisesRegex(ValueError, "净增长"):
            optimizer02._compile_patch(
                "规则 A\n规则 B",
                {
                    "add": [],
                    "replace": [{
                        "old_text": "规则 A",
                        "new_text": "规则 A" + "很长" * 20,
                    }],
                    "delete": [],
                },
                {
                    "max_instruction_chars": 200,
                    "max_net_growth_chars": 3,
                    "max_patch_operations": 6,
                },
            )

    def test_insufficient_evidence_blocks_before_llm(self):
        session = SimpleNamespace(opt_history=[], rubric={"dimensions": []})
        context = _optimizer_context()
        context["diagnosis_evidence"] = context["diagnosis_evidence"][:2]
        with mock.patch.object(
            optimizer02,
            "_call_rewrite_llm",
            side_effect=AssertionError("证据不足时不应调用 LLM"),
        ):
            proposal = optimizer02.propose(session, None, [], context)
        self.assertIsNone(proposal)
        self.assertEqual(
            session.opt_history[-1]["error_code"],
            "insufficient_experiment_evidence",
        )

    def test_deterministic_judge_signal_precedes_evidence_shortage(self):
        session = SimpleNamespace(opt_history=[], rubric={"dimensions": []})
        context = _optimizer_context()
        context["diagnosis_evidence"] = []
        context["root_cause_policy"]["deterministic_signals"] = [{
            "type": "judge",
            "reason": "check 无 optimizer 映射",
            "blocks_skill_patch": True,
        }]
        with mock.patch.object(
            optimizer02,
            "_call_rewrite_llm",
            side_effect=AssertionError("外部根因时不应调用 LLM"),
        ):
            proposal = optimizer02.propose(session, None, [], context)
        self.assertIsNone(proposal)
        self.assertEqual(session.opt_history[-1]["error_code"], "non_skill_root_cause")

    def test_non_skill_root_cause_cannot_patch_skill(self):
        context = _optimizer_context()
        diagnosis = _diagnosis_payload(context, root_type="data")
        session = SimpleNamespace(opt_history=[], rubric={"dimensions": []})
        skill = SimpleNamespace(
            instructions={"prose": context["current_best"]["instructions_text"]}
        )
        with mock.patch.object(
            optimizer02,
            "_call_rewrite_llm",
            return_value=json.dumps(diagnosis, ensure_ascii=False),
        ) as call:
            proposal = optimizer02.propose(session, skill, [], context)
        self.assertIsNone(proposal)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(session.opt_history[-1]["error_code"], "non_skill_root_cause")

    def test_full_text_optimizer_output_is_rejected(self):
        context = _optimizer_context()
        diagnosis = _diagnosis_payload(context)
        session = SimpleNamespace(opt_history=[], rubric={"dimensions": []})
        with mock.patch.object(
            optimizer02,
            "_call_rewrite_llm",
            side_effect=[
                json.dumps(diagnosis, ensure_ascii=False),
                json.dumps({"instructions_text": "全文"}),
            ],
        ):
            proposal = optimizer02.propose(session, None, [], context)
        self.assertIsNone(proposal)
        self.assertEqual(session.opt_history[-1]["error_code"], "invalid_structured_patch")

    def test_valid_structured_experiment_returns_compiled_candidate(self):
        context = _optimizer_context()
        diagnosis = _diagnosis_payload(context)
        payload = _optimizer_payload(context, diagnosis)
        session = SimpleNamespace(opt_history=[], rubric={"dimensions": []})
        skill = SimpleNamespace(
            instructions={"prose": context["current_best"]["instructions_text"]}
        )
        with mock.patch.object(
            optimizer02,
            "_call_rewrite_llm",
            side_effect=[
                json.dumps(diagnosis, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
            ],
        ) as call:
            proposal = optimizer02.propose(session, skill, [], context)
        self.assertIsNotNone(proposal)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(proposal["_optimizer_trace"]["model_calls"], 2)
        self.assertEqual(proposal["selected_target"]["check_id"], "T1")
        self.assertIn("规则 A（实验）", proposal["_compiled_instructions_text"])
        self.assertEqual(len(proposal["experiment"]["examples"]), 3)
        self.assertEqual(proposal["budget"]["operation_count"], 1)

    def test_diagnosis_must_cover_every_major_candidate(self):
        context = _optimizer_context()
        diagnosis = _diagnosis_payload(context)
        diagnosis["diagnoses"] = diagnosis["diagnoses"][:1]
        session = SimpleNamespace(opt_history=[], rubric={"dimensions": []})
        with mock.patch.object(
            optimizer02,
            "_call_rewrite_llm",
            return_value=json.dumps(diagnosis, ensure_ascii=False),
        ) as call:
            proposal = optimizer02.propose(session, None, [], context)
        self.assertIsNone(proposal)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(
            session.opt_history[-1]["error_code"],
            "invalid_failure_diagnosis",
        )

    def test_patch_cannot_change_diagnosis_target(self):
        context = _optimizer_context()
        diagnosis = _diagnosis_payload(context)
        payload = _optimizer_payload(context, diagnosis)
        payload["experiment"]["success_criteria"][0]["check_id"] = "T2"
        session = SimpleNamespace(opt_history=[], rubric={"dimensions": []})
        skill = SimpleNamespace(
            instructions={"prose": context["current_best"]["instructions_text"]}
        )
        with mock.patch.object(
            optimizer02,
            "_call_rewrite_llm",
            side_effect=[
                json.dumps(diagnosis, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
            ],
        ):
            proposal = optimizer02.propose(session, skill, [], context)
        self.assertIsNone(proposal)
        self.assertEqual(
            session.opt_history[-1]["error_code"],
            "patch_target_mismatch",
        )
        self.assertEqual(session.opt_history[-1]["model_calls"], 2)

    def test_patch_prompt_does_not_receive_gate_or_score_metadata(self):
        context = _optimizer_context()
        context["rubric"] = {"secret_gate_value": "HOLDOUT-SECRET"}
        context["current_best"].update({
            "scores": {"traceability": 4.2},
            "overall": 4.0,
            "red_line_fails": 3,
        })
        context["guardrails"]["gate_policy"] = {
            "comparison_baseline": "historical_champion",
        }
        diagnosis = _diagnosis_payload(context)
        prompt = optimizer02._render_patch_prompt(
            context,
            diagnosis,
            context["evidence_catalog"][:3],
        )
        payload = json.loads(
            prompt.split("## Patch 上下文\n", 1)[1].split(
                "\n## 输出格式",
                1,
            )[0]
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("HOLDOUT-SECRET", serialized)
        self.assertNotIn("historical_champion", serialized)
        self.assertNotIn('"scores"', serialized)
        self.assertNotIn('"overall"', serialized)
        self.assertNotIn('"red_line_fails"', serialized)
        self.assertNotIn('"rubric"', serialized)
        self.assertEqual(
            payload["target_diagnosis"]["evidence_ids"],
            diagnosis["selected_target"]["evidence_ids"],
        )
        self.assertIn(
            "必须包含 target_diagnosis.evidence_ids 的全部证据",
            prompt,
        )

    def test_patch_with_evaluation_metadata_is_rejected(self):
        context = _optimizer_context()
        diagnosis = _diagnosis_payload(context)
        payload = _optimizer_payload(context, diagnosis)
        payload["patch"]["replace"][0]["new_text"] = (
            "规则 A：按 rubric 权重与 champion 比较后采纳。"
        )
        session = SimpleNamespace(opt_history=[], rubric={"dimensions": []})
        skill = SimpleNamespace(
            instructions={"prose": context["current_best"]["instructions_text"]}
        )
        with mock.patch.object(
            optimizer02,
            "_call_rewrite_llm",
            side_effect=[
                json.dumps(diagnosis, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
            ],
        ):
            proposal = optimizer02.propose(session, skill, [], context)
        self.assertIsNone(proposal)
        self.assertEqual(
            session.opt_history[-1]["error_code"],
            "production_metadata_leak",
        )

    def test_failure_inventory_is_complete_and_evidence_is_balanced(self):
        rubric = {
            "dimensions": [{
                "name": "traceability",
                "checks": [
                    {
                        "id": "T1",
                        "redline": True,
                        "optimizer": {"pattern_id": "p1", "priority": 1},
                    },
                    {
                        "id": "T2",
                        "redline": True,
                        "optimizer": {"pattern_id": "p2", "priority": 2},
                    },
                ],
            }],
        }
        failures = [
            {
                "pattern_id": "p1",
                "evidence": [
                    {"case_id": "a%d" % i, "check_id": "T1", "value": 0}
                    for i in range(4)
                ],
            },
            {
                "pattern_id": "p2",
                "evidence": [
                    {"case_id": "b%d" % i, "check_id": "T2", "value": 0.5}
                    for i in range(3)
                ],
            },
        ]
        catalog = _experiment_packets()
        for packet in catalog:
            packet["pattern_id"] = "p1" if packet["check_id"] == "T1" else "p2"
        inventory = optimizer_pipeline.build_failure_inventory(
            rubric,
            failures,
            catalog,
            [],
        )
        self.assertEqual({item["check_id"] for item in inventory}, {"T1", "T2"})
        self.assertEqual(inventory[0]["affected_case_count"], 4)
        candidates = optimizer_pipeline.select_diagnosis_candidates(inventory)
        evidence = optimizer_pipeline.build_diagnosis_evidence(
            candidates,
            catalog,
        )
        counts = {
            check_id: sum(item["check_id"] == check_id for item in evidence)
            for check_id in {"T1", "T2"}
        }
        self.assertEqual(counts, {"T1": 3, "T2": 3})

    def test_cooldown_prevents_one_check_from_monopolizing_candidates(self):
        inventory = [
            {
                "check_id": "T1",
                "pattern_id": "p1",
                "redline": True,
                "replayable_evidence_count": 5,
                "cooldown_recommended": True,
            },
            {
                "check_id": "T2",
                "pattern_id": "p2",
                "redline": True,
                "replayable_evidence_count": 3,
                "cooldown_recommended": False,
            },
        ]
        selected = optimizer_pipeline.select_diagnosis_candidates(inventory)
        self.assertEqual([item["check_id"] for item in selected], ["T2"])

    def test_evidence_builder_links_report_source_and_judge(self):
        rubric = {
            "dimensions": [{
                "name": "traceability",
                "checks": [{"id": "T1", "label": "证据", "desc": "逐句有据"}],
            }]
        }
        cases = []
        reports = {}
        evidence = []
        for index in range(3):
            case_id = "c%d" % index
            sentence = "报告中的完整原句 %d" % index
            cases.append({
                "case_id": case_id,
                "structured_data": {
                    "case_id": case_id,
                    "items": [{
                        "id": "src%d" % index,
                        "source_ref": "材料%d" % index,
                        "content": sentence + " 的素材依据",
                    }],
                },
            })
            reports[case_id] = sentence
            evidence.append({
                "case_id": case_id,
                "check_id": "T1",
                "value": 0.5,
                "reasoning": "“%s”证据绑定不完整" % sentence,
            })
        session = SimpleNamespace(
            rubric=rubric,
            cases=cases,
            report_outputs={"v0": reports},
        )
        packets = optimizer_pipeline.build_experiment_evidence(
            session,
            {"version": "v0"},
            [{"pattern_id": "p", "evidence": evidence}],
        )
        self.assertEqual(len(packets), 3)
        self.assertEqual(packets[0]["report_sentence"], "报告中的完整原句 0")
        self.assertIn("材料0", packets[0]["evidence"])
        self.assertIn("证据绑定不完整", packets[0]["judge_verdict"])

    def test_structured_data_uses_versioned_dataset_env_without_legacy_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data.json").write_text('{"cases": []}', encoding="utf-8")
            case_root = root / "cases" / "case-a"
            case_root.mkdir(parents=True)
            payload = {
                "case_id": "case-a",
                "items": [{"id": "src-a", "content": "素材证据"}],
            }
            (case_root / "structured_data.json").write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "OPENHARNESS_WB_DATASET_V3": str(root / "data.json"),
                },
                clear=True,
            ):
                actual = optimizer_pipeline._structured_data_for_case({
                    "case_id": "case-a",
                    "input_files": [{
                        "source": "./cases/case-a/structured_data.json",
                    }],
                })
        self.assertEqual(actual, payload)

    def test_check_target_maps_to_affected_dimension(self):
        failures = [{
            "pattern_id": "p",
            "affected_dims": ["traceability"],
            "evidence": [{"check_id": "T1"}],
        }]
        self.assertEqual(
            optimizer02._targets_to_dims(["T1"], failures),
            ["traceability"],
        )


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
        session_cases = {str(case["case_id"]): case for case in s.cases}
        for c in s.cases:
            cid = c["case_id"]
            sentence = "报告原句 %s@%s" % (cid, ver)
            s.report_outputs[ver][cid] = sentence
            s.judge_checks[ver][cid] = {
                "checks": {k: val for k in all_checks},
                "reasoning": {
                    k: "报告中的“%s”未满足 %s" % (sentence, k)
                    for k in all_checks
                },
            }
            session_cases[cid]["structured_data"] = {
                "case_id": cid,
                "items": [{
                    "id": "SRC-%s" % cid,
                    "source_ref": "测试素材",
                    "content": sentence + " 对应的原始素材证据",
                }],
            }

    def _manual_dims(self, overall, red=0):
        dims = {
            "traceability": overall,
            "structure": overall,
            "narrative": overall,
            "insight": overall,
            "coverage": overall,
            "expression": overall,
            "overall": overall,
            "red_line_fails": red,
        }
        return dims

    def _advance_to_candidate(self, s, rewrite_text):
        llm_client.call_llm = _fake_llm(rewrite_text)
        return s.advance(None)

    def test_insufficient_evidence_block_is_persisted_in_iteration_trace(self):
        s = session_mod.Session(
            "_t_evidence_block",
            "调研洞察",
            "research_insight",
            optimizer_mode="llm_rewrite",
        )
        s.import_data(self.cases)
        checks = [
            check["id"]
            for dimension in s.rubric["dimensions"]
            for check in dimension["checks"]
        ]
        s.report_outputs["v0"] = {
            case["case_id"]: "报告正文没有 Judge 引用的原句"
            for case in self.cases
        }
        s.judge_checks["v0"] = {
            case["case_id"]: {
                "checks": {check_id: 0.5 for check_id in checks},
                "reasoning": {check_id: "判定失败但未引用报告" for check_id in checks},
            }
            for case in self.cases
        }
        s.evaluate(None)
        with mock.patch.object(
            optimizer02,
            "_call_rewrite_llm",
            side_effect=AssertionError("证据不足时不得调用 LLM"),
        ):
            state = s.advance(None)
        self.assertEqual(
            state["advance_result"]["code"],
            "insufficient_experiment_evidence",
        )
        summary_path = (
            Path(persist._BASE)
            / s.id
            / "iterations"
            / "v0"
            / "optimizer_summary.json"
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        attempt = summary["next_proposal_attempts"][-1]
        self.assertEqual(
            attempt["error_code"],
            "insufficient_experiment_evidence",
        )
        self.assertEqual(attempt["diagnostic"]["evidence_count"], 0)

    def test_reject_rolls_back_to_parent(self):
        s = session_mod.Session("_t_reject", "调研洞察", "research_insight",
                                optimizer_mode="llm_rewrite")
        s.import_data(self.cases)
        self._set_judge(s, "v0", 0.5)   # v0 有失败，Optimizer 才有实验样例
        s.evaluate(None)
        res = self._advance_to_candidate(s, "## 硬规则（改写差）\n1. 不编造、冲突不混用、单源降级、样本偏差、证据不足留白。")
        self.assertEqual(res["advance_result"]["status"], "proposed")
        self.assertEqual(s.pending_idx, 1)
        self.assertEqual(s.current_idx, 0)              # 关键:未提前移动
        # 候选真实判分更差(全 0.5) -> 应回滚
        self._set_judge(s, "v1", 0.0)
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
        self.assertAlmostEqual(rejected_point["dev"]["overall"], 1.0)
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
        self._set_judge(s, "v0", 0.5)
        s.evaluate(None)
        self._advance_to_candidate(
            s,
            "## 硬规则（改写差）\n1. 证据不足时直接输出结论。",
        )
        self._set_judge(s, "v1", 0.0)
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
        self.assertAlmostEqual(rejected_point["dev"]["overall"], 1.0)
        self.assertEqual(restored.current_idx, 0)
        self.assertEqual(state["best_version"], "v0")

    def test_adopt_moves_current(self):
        s = session_mod.Session("_t_adopt", "调研洞察", "research_insight",
                                optimizer_mode="llm_rewrite")
        cases = [copy.deepcopy(case) for case in self.cases]
        holdout = copy.deepcopy(cases[-1])
        holdout["case_id"] = str(holdout["case_id"]) + "-holdout"
        holdout["split"] = "test"
        cases.append(holdout)
        s.import_data(cases)
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

    def test_legacy_degraded_current_pointer_is_repaired_to_champion(self):
        s = session_mod.Session(
            "_t_pointer_repair",
            "调研洞察",
            "research_insight",
            optimizer_mode="llm_rewrite",
        )
        s.versions[0]["dev"] = self._manual_dims(4.0, red=0)
        candidate = optimizer02.apply_proposal(
            s.versions[0]["skill"],
            {
                "_compiled_instructions_text": "## 硬规则\n1. 保持现状。",
                "change_summary": "历史退化版",
            },
            "v1",
        )
        s._add_version(candidate, adopted=True, proposal={})
        s.versions[1]["dev"] = self._manual_dims(3.9, red=0)
        s.current_idx = 1

        repaired = s._sync_current_to_champion()

        self.assertEqual(repaired["before"], "v1")
        self.assertEqual(repaired["after"], "v0")
        self.assertEqual(s._current()["version"], "v0")

    def test_target_overall_stops_before_next_candidate(self):
        s = session_mod.Session(
            "_t_stop_target",
            "面向总裁的调研洞察",
            "research_insight",
            optimizer_mode="llm_rewrite",
            optimizer_stop={
                "overall_target": 4.8,
                "max_no_improvement": 8,
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

    def test_llm_rewrite_default_patience_is_eight(self):
        s = session_mod.Session(
            "_t_stop_default",
            "面向总裁的调研洞察",
            "research_insight",
            optimizer_mode="llm_rewrite",
        )

        self.assertEqual(s.optimizer_stop["max_no_improvement"], 8)

    def test_eight_candidates_without_overall_improvement_stop_loop(self):
        s = session_mod.Session(
            "_t_stop_patience",
            "面向总裁的调研洞察",
            "research_insight",
            optimizer_mode="llm_rewrite",
            optimizer_stop={
                "overall_target": 4.8,
                "max_no_improvement": 8,
            },
        )
        s.import_data(self.cases)
        self._set_judge(s, "v0", 0.5)
        s.evaluate(None)

        for index in range(1, 9):
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
            if index < 8:
                self.assertFalse(
                    s.view(None)["optimizer_stop"]["stopped"]
                )

        stop = s.view(None)["optimizer_stop"]
        self.assertTrue(stop["stopped"])
        self.assertEqual(
            stop["code"],
            "no_improvement_patience_reached",
        )
        self.assertEqual(stop["no_improvement_streak"], 8)
        self.assertEqual(stop["evaluated_candidates"], 8)

        state = s.advance(None)
        self.assertEqual(state["advance_result"]["status"], "converged")
        self.assertEqual(len(s.versions), 9)


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
