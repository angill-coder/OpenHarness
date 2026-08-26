import json
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

import feedback_router  # noqa: E402


class FeedbackRouterTest(unittest.TestCase):
    def test_routes_every_feedback_once(self):
        feedback = [
            {"feedback_id": "f1", "content": "所有图表都应标单位"},
            {"feedback_id": "f2", "content": "我个人偏好短段"},
        ]
        result = feedback_router.route_feedback(
            feedback, {"dimensions": []}, [], {},
            call_model=lambda *args, **kwargs: json.dumps({"routes": [
                {"feedback_id": "f1", "destination": "rubric", "reason": "通用", "confidence": 0.9},
                {"feedback_id": "f2", "destination": "memory", "reason": "个人偏好", "confidence": 0.8},
            ]}, ensure_ascii=False),
        )
        self.assertEqual(["rubric", "memory"], [item["destination"] for item in result])

    def test_rejects_missing_feedback(self):
        with self.assertRaisesRegex(ValueError, "遗漏"):
            feedback_router.route_feedback(
                [{"feedback_id": "f1", "content": "反馈"}], {}, [], {},
                call_model=lambda *args, **kwargs: '{"routes":[]}',
            )


if __name__ == "__main__":
    unittest.main()
