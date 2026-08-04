# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from judge import dim_from_checks, overall  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
