import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

import rubrics_loop  # noqa: E402
import persistence  # noqa: E402
import server  # noqa: E402
import session  # noqa: E402


def sample_rubric():
    return {
        "product": "research_insight",
        "version": "v2.3",
        "dimensions": [{
            "name": "traceability",
            "name_zh": "可回溯性",
            "weight": 1.0,
            "criteria": "有据且忠实",
            "anchors": {str(i): "锚点%d" % i for i in range(1, 6)},
            "checks": [{
                "id": "T1",
                "label": "论断有据",
                "desc": "事实和结论均有素材支撑",
                "effect": "miss=红线",
                "redline": True,
                "optimizer": {"pattern_id": "trace_evidence"},
            }],
        }],
        "target": {"overall": 4.0},
    }


class FakeService(rubrics_loop.RubricsLoopService):
    def report(
        self, session_id, skill_version, case_id,
        expected_report_sha256="", expected_rubric_sha256="",
    ):
        rubric = self._state(session_id)["rubric"]
        report_text = "# 报告\n\n事实和结论。"
        report_hash = rubrics_loop.text_sha256(report_text)
        rubric_hash = rubrics_loop.json_sha256(rubric)
        if expected_report_sha256 and expected_report_sha256 != report_hash:
            raise rubrics_loop.RubricsLoopError("报告已变化")
        if expected_rubric_sha256 and expected_rubric_sha256 != rubric_hash:
            raise rubrics_loop.RubricsLoopError("Rubrics 已变化")
        return {
            "session_id": session_id,
            "skill_version": skill_version,
            "case_id": case_id,
            "report_text": report_text,
            "report_sha256": report_hash,
            "rubric_version": rubric["version"],
            "rubric_sha256": rubric_hash,
            "rubric_snapshot_available": True,
            "annotatable": True,
            "source": "test",
        }


class RubricsLoopTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"
        session = self.sessions / "exp-1"
        session.mkdir(parents=True)
        (session / "state.json").write_text(json.dumps({
            "id": "exp-1",
            "product_id": "research_insight",
            "rubric": sample_rubric(),
            "versions": [],
            "cases": [],
        }), encoding="utf-8")
        self.service = FakeService(
            self.root, self.sessions, self.root / "registry"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_feedback_batch_binds_rubric_and_accepts_multiple_reports(self):
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v8", "case_id": "case-1",
        }, "alice")
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "inline", "这里表述有歧义",
            {"skill_version": "v8", "case_id": "case-1"},
            quote="事实和结论", account="alice",
        )
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "结构缺少主线",
            {"skill_version": "v9", "case_id": "case-2"},
            account="alice",
        )

        self.assertEqual(2, len(batch["report_refs"]))
        self.assertEqual(2, len(batch["feedback"]))
        self.assertEqual(
            rubrics_loop.json_sha256(sample_rubric()),
            batch["rubric_sha256"],
        )

    def test_routes_memory_before_rubrics_optimizer(self):
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-1",
        }, "alice")
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "图表都必须标单位",
            {"skill_version": "v9", "case_id": "case-1"}, account="alice",
        )
        rubric_feedback_id = batch["feedback"][0]["feedback_id"]
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "我个人偏好短段",
            {"skill_version": "v9", "case_id": "case-1"}, account="alice",
        )
        memory_feedback_id = batch["feedback"][1]["feedback_id"]
        batch = self.service.route_batch_feedback(
            "exp-1", batch["batch_id"], {}, "alice",
            call_model=lambda *args, **kwargs: json.dumps({"routes": [
                {"feedback_id": rubric_feedback_id, "destination": "rubric", "reason": "通用", "confidence": 0.9},
                {"feedback_id": memory_feedback_id, "destination": "memory", "reason": "个人", "confidence": 0.9},
            ]}, ensure_ascii=False),
        )
        memory_calls = []
        batch = self.service.confirm_feedback_routing(
            "exp-1", batch["batch_id"], {}, {}, "alice",
            process_memory=lambda feedback, context, config: memory_calls.append(feedback["feedback_id"]) or {
                "status": "stored", "episode_id": "ep-1", "written_ids": ["m-1"], "profiles_written": 1,
            },
        )
        self.assertEqual([memory_feedback_id], memory_calls)
        prompts = []
        self.service.propose_candidate(
            "exp-1", batch["batch_id"], {}, "alice",
            call_model=lambda prompt, **kwargs: prompts.append(prompt) or json.dumps({
                "candidate_rubric": sample_rubric(), "operations": [],
                "feedback_analysis": [{
                    "feedback_id": rubric_feedback_id, "category": "rubric",
                    "existing_check_ids": ["T1"], "decision": "covered", "reason": "已有覆盖",
                }], "summary": "无需修改",
            }, ensure_ascii=False),
        )
        payload = json.loads(prompts[0].split("\n## 输入\n", 1)[1])
        self.assertEqual([rubric_feedback_id], [item["feedback_id"] for item in payload["feedback"]])
        self.assertIn(
            "若该 Feedback 被任一 operation 引用，decision 必须使用对应的修改 operation",
            prompts[0],
        )

    def test_confirm_runs_memory_and_rubrics_optimizer_in_parallel(self):
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-1",
        }, "alice")
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "图表都必须标单位",
            {"skill_version": "v9", "case_id": "case-1"}, account="alice",
        )
        rubric_feedback_id = batch["feedback"][0]["feedback_id"]
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "我个人偏好短段",
            {"skill_version": "v9", "case_id": "case-1"}, account="alice",
        )
        memory_feedback_id = batch["feedback"][1]["feedback_id"]
        batch = self.service.route_batch_feedback(
            "exp-1", batch["batch_id"], {}, "alice",
            call_model=lambda *args, **kwargs: json.dumps({"routes": [
                {"feedback_id": rubric_feedback_id, "destination": "rubric", "reason": "通用", "confidence": 0.9},
                {"feedback_id": memory_feedback_id, "destination": "memory", "reason": "个人", "confidence": 0.9},
            ]}, ensure_ascii=False),
        )
        barrier = threading.Barrier(2, timeout=2)

        def process_memory(*args, **kwargs):
            barrier.wait()
            return {
                "status": "stored", "episode_id": "ep-1",
                "written_ids": ["m-1"], "profiles_written": 1,
            }

        def propose(prompt, **kwargs):
            barrier.wait()
            return json.dumps({
                "candidate_rubric": sample_rubric(),
                "operations": [{
                    "op": "update_check", "check_id": "T1",
                    "feedback_ids": [rubric_feedback_id],
                }],
                "feedback_analysis": [{
                    "feedback_id": rubric_feedback_id,
                    "category": "rubric", "existing_check_ids": ["T1"],
                    "decision": "update_check", "reason": "强化单位要求",
                }],
                "summary": "强化 T1",
            }, ensure_ascii=False)

        result = self.service.confirm_and_propose_candidate(
            "exp-1", batch["batch_id"],
            {rubric_feedback_id: "rubric", memory_feedback_id: "memory"},
            {memory_feedback_id: "store"}, {}, {}, "alice",
            process_memory=process_memory, call_candidate_model=propose,
        )

        self.assertIsNotNone(result["candidate"])
        self.assertEqual("completed", result["batch"]["routing"]["status"])
        memory_route = next(
            item for item in result["batch"]["routing"]["routes"]
            if item["feedback_id"] == memory_feedback_id
        )
        self.assertEqual("stored", memory_route["memory_result"]["status"])
        self.assertEqual(
            result["candidate"]["candidate_id"],
            result["batch"]["latest_candidate_id"],
        )

    def test_memory_only_routing_completes_iteration_without_candidate(self):
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-1",
        }, "alice")
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "我偏好更短的段落",
            {"skill_version": "v9", "case_id": "case-1"}, account="alice",
        )
        feedback_id = batch["feedback"][0]["feedback_id"]
        batch = self.service.route_batch_feedback(
            "exp-1", batch["batch_id"], {}, "alice",
            call_model=lambda *args, **kwargs: json.dumps({"routes": [{
                "feedback_id": feedback_id, "destination": "memory",
                "reason": "个人偏好", "confidence": 0.9,
            }]}, ensure_ascii=False),
        )
        batch = self.service.confirm_feedback_routing(
            "exp-1", batch["batch_id"], {}, {}, "alice",
            process_memory=lambda *args, **kwargs: {
                "status": "pending", "episode_id": "ep-1",
                "written_ids": [], "profiles_written": 0,
            },
            memory_actions={feedback_id: "pending"},
        )
        self.assertEqual("completed", batch["status"])
        self.assertEqual("local", batch["memory_user"])
        self.assertEqual(
            "pending", batch["routing"]["routes"][0]["memory_action"]
        )
        history = self.service.list_iterations("exp-1")
        iteration = history["groups"][0]["iterations"][0]
        self.assertEqual(1, iteration["routing_summary"]["memory_count"])
        self.assertEqual(1, iteration["routing_summary"]["memory_saved_count"])
        self.assertIsNone(history["active"]["batch_id"])

    def test_batch_rejects_unknown_memory_user(self):
        with self.assertRaisesRegex(
            rubrics_loop.RubricsLoopError, "不支持的 Memory 用户"
        ):
            self.service.create_batch(
                "exp-1", {"skill_version": "v9", "case_id": "case-1"},
                "alice", "unknown",
            )

    def test_inline_feedback_accepts_rendered_soft_line_break(self):
        original_report = self.service.report

        def report_with_soft_line_break(*args, **kwargs):
            report = original_report(*args, **kwargs)
            report["report_text"] = "第一句。\n第二句。"
            report["report_sha256"] = rubrics_loop.text_sha256(
                report["report_text"]
            )
            return report

        self.service.report = report_with_soft_line_break
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-1",
        })
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "inline", "请拆成两个短段",
            {"skill_version": "v9", "case_id": "case-1"},
            quote="第一句。 第二句。",
        )

        self.assertEqual(1, len(batch["feedback"]))
        self.assertEqual("第一句。\n第二句。", batch["feedback"][0]["quote"])

    def test_rendered_section_with_table_resolves_to_markdown(self):
        markdown = """## 二、核心发现

### 1. AI Coding 已贯穿创意到存量迭代

开头有 **重要判断**。

**表 1｜效率案例**

| 场景 | 投入 |
|---|---|
| CodeBuddy | 3 天 |
| YouTube | 待验证 |

结尾判断。
"""
        rendered = """1. AI Coding 已贯穿创意到存量迭代
开头有 重要判断。
表 1｜效率案例
场景 投入
CodeBuddy 3 天
YouTube 待验证
结尾判断。"""

        quote = rubrics_loop.resolve_markdown_selection(markdown, rendered)

        self.assertTrue(quote.startswith("### 1. AI Coding"))
        self.assertIn("**重要判断**", quote)
        self.assertIn("**表 1｜效率案例**", quote)
        self.assertIn("| 场景 | 投入 |", quote)
        self.assertIn("|---|---|", quote)
        self.assertTrue(quote.endswith("结尾判断。"))

    def test_feedback_can_be_updated_before_submission(self):
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-1",
        })
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "原始意见",
            {"skill_version": "v9", "case_id": "case-1"},
            account="alice",
        )
        feedback_id = batch["feedback"][0]["feedback_id"]

        batch = self.service.update_feedback(
            "exp-1", batch["batch_id"], feedback_id, "修改后的意见", "bob"
        )

        self.assertEqual("修改后的意见", batch["feedback"][0]["content"])
        self.assertEqual("bob", batch["feedback"][0]["updated_by"])

    def test_iteration_history_restores_candidate_before_empty_draft(self):
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-1",
        })
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "表格需要拆分",
            {"skill_version": "v9", "case_id": "case-1"},
        )

        def fake_model(prompt, **kwargs):
            return json.dumps({
                "candidate_rubric": sample_rubric(),
                "operations": [{
                    "op": "update_check",
                    "check_id": "T1",
                    "feedback_ids": [batch["feedback"][0]["feedback_id"]],
                }],
                "feedback_analysis": [],
                "unhandled_feedback_ids": [],
                "summary": "更新 T1",
            }, ensure_ascii=False)

        candidate = self.service.propose_candidate(
            "exp-1", batch["batch_id"],
            {"llm_backend": "codex", "llm_model": "gpt-test"},
            call_model=fake_model,
        )
        empty_draft = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-2",
        })

        history = self.service.list_iterations("exp-1")

        self.assertEqual(candidate["candidate_id"], history["active"]["candidate_id"])
        self.assertEqual(batch["batch_id"], history["active"]["batch_id"])
        self.assertNotEqual(empty_draft["batch_id"], history["active"]["batch_id"])
        self.assertEqual(1, len(history["groups"]))
        iteration = history["groups"][0]["iterations"][0]
        self.assertEqual(1, iteration["feedback_count"])
        self.assertEqual(["T1"], iteration["candidates"][0]["modified_check_ids"])

    def test_all_iteration_history_groups_sessions_and_marks_running(self):
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-1",
        })
        self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "表格需要拆分",
            {"skill_version": "v9", "case_id": "case-1"},
        )

        history = self.service.list_all_iterations()

        self.assertEqual(1, len(history["sessions"]))
        self.assertEqual("exp-1", history["sessions"][0]["session_id"])
        self.assertEqual(1, history["sessions"][0]["iteration_count"])
        self.assertEqual(0, history["sessions"][0]["active_experiment_count"])

    def test_imported_rubric_does_not_restore_candidate_from_old_parent(self):
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-1",
        })
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "表格需要拆分",
            {"skill_version": "v9", "case_id": "case-1"},
        )

        def fake_model(prompt, **kwargs):
            return json.dumps({
                "candidate_rubric": sample_rubric(),
                "operations": [],
                "feedback_analysis": [],
                "unhandled_feedback_ids": [],
                "summary": "无需修改",
            }, ensure_ascii=False)

        candidate = self.service.propose_candidate(
            "exp-1", batch["batch_id"], {}, call_model=fake_model
        )
        state_path = self.sessions / "exp-1" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["rubric"]["version"] = "v2.3-imported"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        history = self.service.list_iterations("exp-1")

        self.assertIsNone(history["active"]["candidate_id"])
        self.assertNotEqual(candidate["candidate_id"], history["active"]["candidate_id"])
        self.assertEqual(1, len(history["groups"]))

    def test_optimizer_candidate_validates_and_adopts_immutable_version(self):
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-1",
        })
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "要求更明确",
            {"skill_version": "v9", "case_id": "case-1"},
        )
        candidate_rubric = copy.deepcopy(sample_rubric())
        candidate_rubric["dimensions"][0]["checks"][0]["desc"] = "事实与结论有素材支撑"

        def fake_model(prompt, **kwargs):
            self.assertIn("要求更明确", prompt)
            return json.dumps({
                "candidate_rubric": candidate_rubric,
                "operations": [{
                    "op": "update_check", "check_id": "T1",
                    "feedback_ids": [batch["feedback"][0]["feedback_id"]],
                }],
                "feedback_analysis": [],
                "unhandled_feedback_ids": [],
                "summary": "收紧 T1",
            }, ensure_ascii=False)

        candidate = self.service.propose_candidate(
            "exp-1", batch["batch_id"],
            {"llm_backend": "api", "llm_model": "test"},
            call_model=fake_model,
        )
        adopted = self.service.adopt_candidate(
            "exp-1", candidate["candidate_id"], "owner"
        )

        self.assertTrue(candidate["validation"]["ok"])
        self.assertEqual("v2.4", adopted["version"])
        version_path = (
            self.root / "registry" / "research_insight"
            / "versions" / "v2.4.json"
        )
        self.assertTrue(version_path.is_file())
        original = json.loads(
            (self.sessions / "exp-1" / "state.json").read_text(encoding="utf-8")
        )
        self.assertEqual("v2.3", original["rubric"]["version"])

    def test_candidate_budget_rejects_growth(self):
        parent = sample_rubric()
        candidate = copy.deepcopy(parent)
        candidate["dimensions"][0]["checks"][0]["desc"] += "大量新增文字"
        result = rubrics_loop.validate_candidate_rubric(parent, candidate)
        self.assertFalse(result["ok"])
        self.assertIn("Rubrics 判定文本不得超过父版本", result["errors"])

    def test_candidate_can_be_staged_and_next_round_uses_draft_history(self):
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-1",
        })
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "把有据要求写得更明确",
            {"skill_version": "v9", "case_id": "case-1"},
        )
        first_rubric = copy.deepcopy(sample_rubric())
        first_rubric["dimensions"][0]["checks"][0]["desc"] = "事实结论均有素材支撑"

        def first_model(prompt, **kwargs):
            return json.dumps({
                "candidate_rubric": first_rubric,
                "operations": [{
                    "op": "update_check", "check_id": "T1",
                    "feedback_ids": [batch["feedback"][0]["feedback_id"]],
                }],
                "feedback_analysis": [],
                "unhandled_feedback_ids": [],
                "summary": "收紧 T1",
            }, ensure_ascii=False)

        first = self.service.propose_candidate(
            "exp-1", batch["batch_id"], {}, call_model=first_model
        )
        staged = self.service.stage_candidate(
            "exp-1", first["candidate_id"], "owner"
        )
        self.assertEqual("staged", staged["candidate"]["status"])
        self.assertEqual(1, len(staged["draft"]["revisions"]))

        second_batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-2",
        })
        self.assertEqual(
            first["candidate_rubric_sha256"],
            second_batch["working_rubric_sha256"],
        )
        second_batch = self.service.add_feedback(
            "exp-1", second_batch["batch_id"], "report", "同时要求直接事实不编造",
            {"skill_version": "v9", "case_id": "case-2"},
        )
        second_rubric = copy.deepcopy(first_rubric)
        second_rubric["dimensions"][0]["checks"][0]["desc"] = "事实结论有素材支撑"

        def second_model(prompt, **kwargs):
            self.assertIn('"historical_changes"', prompt)
            self.assertIn("收紧 T1", prompt)
            return json.dumps({
                "candidate_rubric": second_rubric,
                "operations": [{
                    "op": "update_check", "check_id": "T1",
                    "history_action": "extend",
                    "feedback_ids": [
                        second_batch["feedback"][0]["feedback_id"]
                    ],
                }],
                "feedback_analysis": [],
                "unhandled_feedback_ids": [],
                "summary": "继续强化 T1",
            }, ensure_ascii=False)

        second = self.service.propose_candidate(
            "exp-1", second_batch["batch_id"], {}, call_model=second_model
        )
        self.assertTrue(second["validation"]["ok"])
        self.assertEqual(["T1"], second["validation"]["repeated_check_ids"])
        self.assertEqual(
            first["candidate_rubric_sha256"],
            second["working_parent_rubric_sha256"],
        )
        second_staged = self.service.stage_candidate(
            "exp-1", second["candidate_id"], "owner",
            history_conflict_confirmed=True,
        )
        with self.assertRaisesRegex(
            rubrics_loop.RubricsLoopError, "不是待验证草案的最新累计版本"
        ):
            self.service.create_experiment(
                "exp-1", first["candidate_id"], {}
            )
        with self.assertRaisesRegex(
            rubrics_loop.RubricsLoopError, "累计范围不一致"
        ):
            self.service.create_experiment(
                "exp-1", second["candidate_id"], {},
                selected_batch_ids=[second_batch["batch_id"]],
            )
        experiment = self.service.create_experiment(
            "exp-1", second["candidate_id"], {
                "skill_iteration_rounds": 2,
            }, "owner", selected_batch_ids=[
                batch["batch_id"], second_batch["batch_id"]
            ],
        )
        self.assertEqual(
            [batch["batch_id"], second_batch["batch_id"]],
            experiment["included_batch_ids"],
        )
        self.service.update_experiment(
            "exp-1", experiment["experiment_id"], {"status": "completed"}
        )

        history = self.service.list_iterations("exp-1")
        summaries = [
            candidate
            for group in history["groups"]
            for iteration in group["iterations"]
            for candidate in iteration["candidates"]
        ]
        first_summary = next(
            item for item in summaries
            if item["candidate_id"] == first["candidate_id"]
        )
        self.assertEqual(2, len(second_staged["draft"]["revisions"]))
        self.assertTrue(first_summary["cumulative_validation"]["included"])
        self.assertFalse(
            first_summary["cumulative_validation"]["is_latest_revision"]
        )
        self.assertEqual(
            "completed",
            first_summary["cumulative_validation"]["experiment_status"],
        )
        experiment_summary = next(
            stored_experiment
            for group in history["groups"]
            for iteration in group["iterations"]
            for stored_experiment in iteration["experiments"]
            if stored_experiment["experiment_id"] == experiment["experiment_id"]
        )
        self.assertEqual(
            [batch["batch_id"], second_batch["batch_id"]],
            experiment_summary["included_batch_ids"],
        )

    def test_legacy_candidate_without_working_parent_can_start_draft(self):
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-1",
        })
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "强化 T1",
            {"skill_version": "v9", "case_id": "case-1"},
        )
        candidate = self.service.propose_candidate(
            "exp-1", batch["batch_id"], {},
            call_model=lambda *args, **kwargs: json.dumps({
                "candidate_rubric": sample_rubric(),
                "operations": [{
                    "op": "update_check", "check_id": "T1",
                    "feedback_ids": [batch["feedback"][0]["feedback_id"]],
                }],
                "feedback_analysis": [], "summary": "旧候选",
            }, ensure_ascii=False),
        )
        candidate.pop("working_parent_rubric", None)
        candidate.pop("working_parent_rubric_sha256", None)
        self.service.update_candidate("exp-1", candidate["candidate_id"], candidate)
        stored_batch = self.service.get_batch("exp-1", batch["batch_id"])
        stored_batch.pop("working_rubric", None)
        stored_batch.pop("working_rubric_sha256", None)
        rubrics_loop._atomic_write(
            self.service._batch_path("exp-1", batch["batch_id"]), stored_batch
        )

        staged = self.service.stage_candidate(
            "exp-1", candidate["candidate_id"], "owner"
        )

        self.assertEqual("staged", staged["candidate"]["status"])
        self.assertEqual(
            candidate["parent_rubric_sha256"],
            staged["draft"]["revisions"][0]["working_parent_rubric_sha256"],
        )

    def test_history_conflict_requires_explanation_and_confirmation(self):
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-1",
        })
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "先修改 T1",
            {"skill_version": "v9", "case_id": "case-1"},
        )
        first_rubric = copy.deepcopy(sample_rubric())
        first_rubric["dimensions"][0]["checks"][0]["desc"] = "事实有素材支撑"
        first = self.service.propose_candidate(
            "exp-1", batch["batch_id"], {},
            call_model=lambda *args, **kwargs: json.dumps({
                "candidate_rubric": first_rubric,
                "operations": [{
                    "op": "update_check", "check_id": "T1",
                    "feedback_ids": [batch["feedback"][0]["feedback_id"]],
                }],
                "feedback_analysis": [], "summary": "修改 T1",
            }, ensure_ascii=False),
        )
        self.service.stage_candidate("exp-1", first["candidate_id"])

        second_batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-2",
        })
        second_batch = self.service.add_feedback(
            "exp-1", second_batch["batch_id"], "report", "换一种 T1 口径",
            {"skill_version": "v9", "case_id": "case-2"},
        )
        replacement = copy.deepcopy(first_rubric)
        replacement["dimensions"][0]["checks"][0]["desc"] = "结论有据"
        second = self.service.propose_candidate(
            "exp-1", second_batch["batch_id"], {},
            call_model=lambda *args, **kwargs: json.dumps({
                "candidate_rubric": replacement,
                "operations": [{
                    "op": "update_check", "check_id": "T1",
                    "history_action": "replace", "conflict": True,
                    "conflict_resolution": "新反馈要求把范围收窄到结论",
                    "feedback_ids": [
                        second_batch["feedback"][0]["feedback_id"]
                    ],
                }],
                "feedback_analysis": [], "summary": "替换 T1",
            }, ensure_ascii=False),
        )
        self.assertTrue(
            second["validation"]["requires_history_conflict_confirmation"]
        )
        with self.assertRaises(rubrics_loop.RubricsLoopError):
            self.service.stage_candidate("exp-1", second["candidate_id"])
        staged = self.service.stage_candidate(
            "exp-1", second["candidate_id"], history_conflict_confirmed=True
        )
        self.assertEqual(2, len(staged["draft"]["revisions"]))
        self.assertEqual(
            [batch["batch_id"], second_batch["batch_id"]],
            staged["candidate"]["feedback_batch_ids"],
        )
        experiment = self.service.create_experiment(
            "exp-1", second["candidate_id"], {}
        )
        self.assertEqual("created", experiment["status"])
        self.assertEqual(
            "validating",
            self.service.get_draft(
                "exp-1", staged["draft"]["draft_id"]
            )["status"],
        )

    def test_failed_experiment_retries_with_same_id_and_session(self):
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-1",
        })
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "强化 T1",
            {"skill_version": "v9", "case_id": "case-1"},
        )
        candidate = self.service.propose_candidate(
            "exp-1", batch["batch_id"], {},
            call_model=lambda *args, **kwargs: json.dumps({
                "candidate_rubric": sample_rubric(),
                "operations": [{
                    "op": "update_check", "check_id": "T1",
                    "feedback_ids": [batch["feedback"][0]["feedback_id"]],
                }],
                "feedback_analysis": [], "summary": "强化 T1",
            }, ensure_ascii=False),
        )
        experiment = self.service.create_experiment(
            "exp-1", candidate["candidate_id"], {"runner_model": "old"}
        )
        self.service.update_experiment("exp-1", experiment["experiment_id"], {
            "status": "failed",
            "experiment_session_id": "existing-validation-session",
            "error": "dataset missing",
            "finished_at": 123.0,
        })
        self.service.update_candidate(
            "exp-1", candidate["candidate_id"], {"status": "validated"}
        )

        retried = self.service.retry_experiment(
            "exp-1", experiment["experiment_id"], candidate["candidate_id"],
            {"runner_model": "new"}, "owner",
        )

        self.assertEqual(experiment["experiment_id"], retried["experiment_id"])
        self.assertEqual(
            "existing-validation-session", retried["experiment_session_id"]
        )
        self.assertEqual("created", retried["status"])
        self.assertEqual(1, retried["retry_count"])
        self.assertEqual("dataset missing", retried["attempts"][0]["error"])
        self.assertNotIn("error", retried)
        current_candidate = self.service.get_candidate(
            "exp-1", candidate["candidate_id"]
        )
        self.assertEqual("running", current_candidate["status"])
        self.assertEqual(
            experiment["experiment_id"], current_candidate["experiment_id"]
        )
        with self.assertRaises(rubrics_loop.RubricsLoopError):
            self.service.retry_experiment(
                "exp-1", experiment["experiment_id"],
                candidate["candidate_id"], {},
            )

    def test_acceptance_retry_preserves_completed_skill_loop(self):
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-1",
        })
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "强化 T1",
            {"skill_version": "v9", "case_id": "case-1"},
        )
        candidate = self.service.propose_candidate(
            "exp-1", batch["batch_id"], {},
            call_model=lambda *args, **kwargs: json.dumps({
                "candidate_rubric": sample_rubric(),
                "operations": [{
                    "op": "update_check", "check_id": "T1",
                    "feedback_ids": [batch["feedback"][0]["feedback_id"]],
                }],
                "feedback_analysis": [], "summary": "强化 T1",
            }, ensure_ascii=False),
        )
        experiment = self.service.create_experiment(
            "exp-1", candidate["candidate_id"], {
                "judge": {"llm_backend": "codex", "llm_model": "judge-old"},
            }
        )
        self.assertEqual(2, experiment["config"]["skill_iteration_rounds"])
        self.assertEqual(
            experiment["config"]["judge"], experiment["config"]["acceptance"]
        )
        self.service.update_experiment("exp-1", experiment["experiment_id"], {
            "status": "failed",
            "phase": "feedback_acceptance",
            "experiment_session_id": "existing-validation-session",
            "loop_completed_at": 456.0,
            "final_state": {"curve": [{"version": "v9"}, {"version": "v10"}, {"version": "v11"}]},
            "comparison": {"kept": True},
            "error": "acceptance timeout",
        })
        self.service.update_candidate(
            "exp-1", candidate["candidate_id"], {"status": "validated"}
        )

        retried = self.service.retry_experiment(
            "exp-1", experiment["experiment_id"], candidate["candidate_id"],
            {"skill_iteration_rounds": 5, "acceptance": {
                "llm_backend": "codex", "llm_model": "acceptance-new",
            }},
        )

        self.assertEqual("feedback_acceptance", retried["phase"])
        self.assertEqual(456.0, retried["loop_completed_at"])
        self.assertEqual(2, retried["config"]["skill_iteration_rounds"])
        self.assertEqual("acceptance-new", retried["config"]["acceptance"]["llm_model"])
        self.assertEqual(3, len(retried["final_state"]["curve"]))
        self.assertEqual({"kept": True}, retried["comparison"])

    def test_acceptance_result_is_persisted_and_reused(self):
        batch = self.service.create_batch("exp-1", {
            "skill_version": "v9", "case_id": "case-1",
        })
        batch = self.service.add_feedback(
            "exp-1", batch["batch_id"], "report", "正文不要中间稿表述",
            {"skill_version": "v9", "case_id": "case-1"},
        )
        feedback_id = batch["feedback"][0]["feedback_id"]
        candidate = self.service.propose_candidate(
            "exp-1", batch["batch_id"], {},
            call_model=lambda *args, **kwargs: json.dumps({
                "candidate_rubric": sample_rubric(),
                "operations": [{
                    "op": "update_check", "check_id": "T1",
                    "feedback_ids": [feedback_id],
                }],
                "feedback_analysis": [{
                    "feedback_id": feedback_id,
                    "category": "rubric",
                    "existing_check_ids": ["T1"],
                    "decision": "update",
                    "reason": "强化终稿要求",
                }],
                "summary": "强化 T1",
            }, ensure_ascii=False),
        )
        experiment = self.service.create_experiment(
            "exp-1", candidate["candidate_id"], {
                "acceptance": {"llm_backend": "codex", "llm_model": "gpt-test"},
            }
        )
        self.service.update_experiment("exp-1", experiment["experiment_id"], {
            "experiment_session_id": "validation-session",
            "loop_completed_at": 1.0,
            "final_state": {"curve": [
                {"version": "v9"}, {"version": "v10"}, {"version": "v11"},
            ]},
        })
        self.service._experiment_report = (
            lambda session_id, version, case_id:
            "# 报告\n\n已转为最终汇报语言。"
        )
        calls = []

        def fake_acceptance(prompt, **kwargs):
            calls.append(prompt)
            return json.dumps({
                "feedback_id": feedback_id,
                "status": "followed",
                "stability": "stable",
                "failure_layer": "none",
                "reason": "两轮均遵循",
                "evidence": [{
                    "phase": "iteration_2", "skill_version": "v11",
                    "case_id": "case-1", "quote": "已转为最终汇报语言。",
                    "assessment": "符合反馈",
                }],
                "next_action": "人工确认",
                "rubric_suggestions": [],
            }, ensure_ascii=False)

        result = self.service.evaluate_feedback_acceptance(
            "exp-1", experiment["experiment_id"], fake_acceptance
        )
        reused = self.service.evaluate_feedback_acceptance(
            "exp-1", experiment["experiment_id"],
            lambda *args, **kwargs: self.fail("已完成结果不应重复调用模型"),
        )

        self.assertEqual(1, len(calls))
        self.assertEqual("followed", result["overall_status"])
        self.assertEqual("followed", reused["overall_status"])
        self.assertEqual(
            "completed",
            self.service.get_acceptance(
                "exp-1", experiment["experiment_id"]
            )["status"],
        )

    def test_candidate_summary_extracts_result_from_mapping_text(self):
        self.assertEqual(
            "只修改 E3",
            rubrics_loop._summary_text(
                "{'operation_count': 1, 'result': '只修改 E3'}"
            ),
        )

    def test_judge_score_summary_matches_dimension_and_redline_rules(self):
        summary = rubrics_loop._judgment_score_summary(
            {"checks": {"T1": 0.5}}, sample_rubric()
        )
        self.assertEqual(3.0, summary["overall"])
        self.assertEqual(3.0, summary["dimensions"][0]["score"])

        redline = rubrics_loop._judgment_score_summary(
            {"checks": {"T1": 0.0}}, sample_rubric()
        )
        self.assertEqual(1.0, redline["overall"])

    def test_experiment_clone_uses_candidate_rubric_without_outputs(self):
        old_base = persistence._BASE
        old_sessions = server.SESSIONS
        try:
            persistence._BASE = str(self.root / "clone-sessions")
            server.SESSIONS = {}
            source = session.Session(
                "source", "调研汇报助手", "research_insight",
                optimizer_mode="llm_rewrite",
            )
            server.SESSIONS[source.id] = source
            candidate = {
                "candidate_id": "rc-test",
                "parent_rubric_version": source.rubric["version"],
                "candidate_rubric": copy.deepcopy(source.rubric),
                "candidate_rubric_sha256": rubrics_loop.json_sha256(source.rubric),
            }
            experiment = {
                "experiment_id": "rx-test1234",
                "config": {},
            }

            target_id = server._clone_rubric_experiment_session(
                source, candidate, experiment
            )
            target = server.SESSIONS[target_id]

            self.assertEqual(1, len(target.versions))
            self.assertEqual([], target.cases)
            self.assertEqual({}, target.report_outputs)
            self.assertIn("-candidate-", target.rubric["version"])
            self.assertEqual("imported", target.rubric_source["kind"])
            self.assertEqual(
                "rubrics-loop-rc-test.json",
                target.rubric_source["filename"],
            )
            self.assertEqual(
                source._current()["version"], target._current()["version"]
            )
        finally:
            persistence._BASE = old_base
            server.SESSIONS = old_sessions


if __name__ == "__main__":
    unittest.main()
