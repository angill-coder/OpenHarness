from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "app", ROOT / "harness"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from report_loop_gate import evaluate_candidate_gate
from report_loop_service import ReportLoopService
from report_loop_settings import ReportLoopSettings
from report_scoring import score_labeled_check_judgment


class ReportLoopCoreTests(unittest.TestCase):
    def test_default_stop_policy(self):
        parameters = inspect.signature(ReportLoopService.create_run).parameters
        self.assertEqual(parameters["overall_target"].default, 5.0)
        self.assertEqual(parameters["max_no_improvement"].default, 2)
        self.assertEqual(parameters["max_elapsed_seconds"].default, 3600)

    def test_settings_are_report_loop_only(self):
        fields = set(ReportLoopSettings.__dataclass_fields__)
        self.assertNotIn("dataset_path", fields)
        self.assertNotIn("output_root", fields)
        self.assertIn("timeout_seconds", fields)
        self.assertIn("min_report_bytes", fields)

    def test_gate_accepts_improvement_without_regression(self):
        result = evaluate_candidate_gate(
            {"overall": 3.4, "insight": 3.5, "structure": 4.0, "red_line_fails": 0},
            {"overall": 3.2, "insight": 3.0, "structure": 4.0, "red_line_fails": 0},
            ["insight"],
            ["insight", "structure"],
        )
        self.assertTrue(result["accepted"])

    def test_scoring_caps_redline_dimension(self):
        rubric = {
            "dimensions": [{
                "name": "traceability",
                "weight": 1.0,
                "hard_floor": 2.5,
                "checks": [
                    {"id": "source", "redline": True},
                    {"id": "scope"},
                ],
            }]
        }
        result = score_labeled_check_judgment(
            {"source": "miss", "scope": "met"}, rubric
        )
        self.assertEqual(result["scores"]["traceability"], 2.0)
        self.assertEqual(result["redline_checks"], ["source"])
        self.assertTrue(result["case_failed_gate"])


if __name__ == "__main__":
    unittest.main()
