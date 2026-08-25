import json
import threading
import unittest

from mcp.report_loop.core.judge_batch import judge_report


RUBRIC = {
    "dimensions": [
        {
            "name": "evidence",
            "checks": [{"id": "E1", "label": "evidence"}],
        }
    ]
}
CASE = {"case_id": "case-1", "input": {"intake": "test"}}


def build_prompt(_rubric, _report, _context):
    return "judge"


def valid(value="met"):
    return json.dumps(
        {"checks": {"E1": value}, "reasoning": {"E1": "reason"}}
    )


class JudgeFallbackTests(unittest.TestCase):
    def run_judge(self, primary, fallback, active=None):
        event = active or threading.Event()
        reasons = []

        def activate(reason):
            reasons.append(reason)
            event.set()

        result = judge_report(
            CASE,
            "report",
            RUBRIC,
            build_prompt,
            primary,
            json.loads,
            max_retries=1,
            fallback_call_model=fallback,
            fallback_active=event.is_set,
            activate_fallback=activate,
        )
        return result, event, reasons

    def test_transport_failure_uses_fallback(self):
        calls = []

        def primary(_prompt):
            calls.append("primary")
            raise RuntimeError("stream failed")

        def fallback(_prompt):
            calls.append("fallback")
            return valid()

        result, event, reasons = self.run_judge(primary, fallback)
        self.assertEqual(result["status"], "judged")
        self.assertEqual(calls, ["primary", "fallback"])
        self.assertTrue(event.is_set())
        self.assertEqual(reasons, ["stream failed"])
        self.assertTrue(result["judge_meta"]["fallback_used"])

    def test_invalid_primary_payload_uses_fallback(self):
        result, event, reasons = self.run_judge(
            lambda _prompt: json.dumps({"reasoning": {}}),
            lambda _prompt: valid(),
        )
        self.assertEqual(result["status"], "judged")
        self.assertTrue(event.is_set())
        self.assertIn("缺少 checks", reasons[0])

    def test_low_score_does_not_use_fallback(self):
        fallback_calls = []

        result, event, reasons = self.run_judge(
            lambda _prompt: valid("miss"),
            lambda _prompt: fallback_calls.append(True) or valid(),
        )
        self.assertEqual(result["checks"]["E1"], "miss")
        self.assertFalse(event.is_set())
        self.assertEqual(reasons, [])
        self.assertEqual(fallback_calls, [])
        self.assertFalse(result["judge_meta"]["fallback_used"])

    def test_active_circuit_skips_primary(self):
        event = threading.Event()
        event.set()
        primary_calls = []

        result, _, reasons = self.run_judge(
            lambda _prompt: primary_calls.append(True) or valid(),
            lambda _prompt: valid(),
            active=event,
        )
        self.assertEqual(result["status"], "judged")
        self.assertEqual(primary_calls, [])
        self.assertEqual(reasons, [])
        self.assertTrue(result["judge_meta"]["fallback_used"])


if __name__ == "__main__":
    unittest.main()
