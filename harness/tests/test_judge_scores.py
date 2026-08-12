# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from judge import dim_from_checks, overall, score_check_judgment  # noqa: E402


class JudgeDecimalScoreTest(unittest.TestCase):
    def test_dimension_score_preserves_three_decimals(self):
        rubric = {
            "dimensions": [
                {
                    "name": "quality",
                    "weight": 1.0,
                    "checks": [
                        {"id": "Q1"},
                        {"id": "Q2"},
                        {"id": "Q3"},
                    ],
                }
            ]
        }

        scores = dim_from_checks(
            {"Q1": 1.0, "Q2": 0.5, "Q3": 0.5},
            rubric,
        )

        self.assertEqual(scores, {"quality": 3.667})
        self.assertEqual(overall(scores, rubric), 3.667)

    def test_redline_still_caps_fractional_dimension_at_two(self):
        rubric = {
            "dimensions": [
                {
                    "name": "quality",
                    "weight": 1.0,
                    "checks": [
                        {"id": "Q1", "redline": True},
                        {"id": "Q2"},
                        {"id": "Q3"},
                    ],
                }
            ]
        }

        scores = dim_from_checks(
            {"Q1": 0.0, "Q2": 1.0, "Q3": 1.0},
            rubric,
        )

        self.assertEqual(scores, {"quality": 2.0})

    def test_authoritative_check_summary_includes_scores_overall_and_gate(self):
        rubric = {
            "dimensions": [
                {
                    "name": "quality",
                    "weight": 0.75,
                    "hard_floor": 3,
                    "checks": [
                        {"id": "Q1", "redline": True},
                        {"id": "Q2"},
                    ],
                },
                {
                    "name": "style",
                    "weight": 0.25,
                    "checks": [{"id": "S1"}],
                },
            ]
        }

        result = score_check_judgment(
            {"Q1": 0.0, "Q2": 1.0, "S1": 1.0},
            rubric,
        )

        self.assertEqual({"quality": 2.0, "style": 5.0}, result["scores"])
        self.assertEqual(2.75, result["overall"])
        self.assertEqual(["Q1"], result["redline_checks"])
        self.assertEqual(["quality"], result["hard_floor_failures"])
        self.assertTrue(result["case_failed_gate"])

    def test_partial_redline_check_does_not_count_as_redline_hit(self):
        rubric = {
            "dimensions": [{
                "name": "quality",
                "weight": 1.0,
                "checks": [{"id": "Q1", "redline": True}],
            }]
        }

        result = score_check_judgment({"Q1": 0.5}, rubric)

        self.assertEqual({"quality": 3.0}, result["scores"])
        self.assertEqual([], result["redline_checks"])
        self.assertFalse(result["case_failed_gate"])


if __name__ == "__main__":
    unittest.main()
