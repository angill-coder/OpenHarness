import json
import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

import feedback_acceptance  # noqa: E402


class FeedbackAcceptanceTest(unittest.TestCase):
    def test_evaluates_with_verifiable_markdown_evidence(self):
        feedback = [{
            "feedback_id": "fb-1",
            "scope": "inline",
            "content": "不要保留中间稿语言",
        }]
        contexts = {"fb-1": {
            "skill_versions": ["baseline", "v10", "v11"],
            "reports": [{
                "phase": "baseline",
                "skill_version": "v9",
                "case_id": "case-1",
                "report_text": "# 报告\n\n单一信源，待验证。",
            }, {
                "phase": "iteration_1",
                "skill_version": "v10",
                "case_id": "case-1",
                "report_text": "# 报告\n\n当前访谈只支持方向性判断。",
            }, {
                "phase": "iteration_2",
                "skill_version": "v11",
                "case_id": "case-1",
                "report_text": "# 报告\n\n当前访谈只支持方向性判断。",
            }],
        }}

        def fake_model(prompt, **kwargs):
            self.assertIn("两轮 Skill 迭代", prompt)
            return json.dumps({
                "feedback_id": "fb-1",
                "status": "followed",
                "stability": "stable",
                "failure_layer": "none",
                "reason": "连续两轮均已转为终稿语言",
                "evidence": [{
                    "phase": "iteration_2",
                    "skill_version": "v11",
                    "case_id": "case-1",
                    "quote": "当前访谈只支持方向性判断。",
                    "assessment": "已转为决策边界",
                }, {
                    "phase": "iteration_2",
                    "skill_version": "v11",
                    "case_id": "case-1",
                    "quote": "不存在的证据",
                    "assessment": "应被过滤",
                }],
                "next_action": "人工确认后采纳",
                "rubric_suggestions": [],
            }, ensure_ascii=False)

        result = feedback_acceptance.evaluate(
            feedback,
            contexts,
            {"llm_backend": "codex", "llm_model": "gpt-test"},
            call_model=fake_model,
        )

        self.assertEqual("followed", result["overall_status"])
        self.assertEqual(
            "不要保留中间稿语言",
            result["feedback_results"][0]["feedback_content"],
        )
        self.assertEqual(1, len(result["feedback_results"][0]["evidence"]))
        self.assertEqual(
            "当前访谈只支持方向性判断。",
            result["feedback_results"][0]["evidence"][0]["quote"],
        )

    def test_resume_skips_completed_feedback(self):
        feedback = [
            {"feedback_id": "fb-1", "content": "意见一"},
            {"feedback_id": "fb-2", "content": "意见二"},
        ]
        contexts = {
            value["feedback_id"]: {"reports": []} for value in feedback
        }
        existing = {
            "fb-1": {
                "feedback_id": "fb-1",
                "status": "followed",
                "stability": "stable",
                "failure_layer": "none",
                "reason": "已完成",
                "evidence": [],
                "next_action": "无",
                "rubric_suggestions": [],
            }
        }
        called = []

        def fake_model(prompt, **kwargs):
            called.append(prompt)
            return json.dumps({
                "feedback_id": "fb-2",
                "status": "not_followed",
                "stability": "not_improved",
                "failure_layer": "skill_translation_failure",
                "reason": "仍未执行",
                "evidence": [],
                "next_action": "继续优化 Skill",
                "rubric_suggestions": [],
            }, ensure_ascii=False)

        result = feedback_acceptance.evaluate(
            feedback, contexts, {}, existing_results=existing,
            call_model=fake_model,
        )

        self.assertEqual(1, len(called))
        self.assertEqual("not_followed", result["overall_status"])
        self.assertEqual(2, len(result["feedback_results"]))


if __name__ == "__main__":
    unittest.main()
