# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


HARNESS = Path(__file__).resolve().parents[1]
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from clustering import (  # noqa: E402
    FailureMappingError,
    analyze_real_judgments,
    validate_optimizer_mappings,
)


class RealFailureAnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rubric = json.loads(
            (
                HARNESS
                / "artifacts"
                / "rubric_research.json"
            ).read_text(encoding="utf-8")
        )
        cls.check_ids = [
            check["id"]
            for dimension in cls.rubric["dimensions"]
            for check in dimension.get("checks", [])
        ]

    def test_partial_check_is_not_lost_to_rounded_dimension_score(self):
        checks = {check_id: 1.0 for check_id in self.check_ids}
        checks["T2"] = 0.5

        failures = analyze_real_judgments(
            {
                "case-a": {
                    "checks": checks,
                    "reasoning": {"T2": "存在一处无法核实的事实"},
                }
            },
            self.rubric,
        )

        self.assertEqual(failures[0]["pattern_id"], "trace_faithfulness")
        self.assertEqual(failures[0]["hit_count"], 1)
        self.assertEqual(
            failures[0]["directive_hint"],
            "verify_no_fabrication",
        )
        self.assertEqual(failures[0]["evidence"][0]["value"], 0.5)

    def test_all_met_produces_no_failure_report(self):
        checks = {check_id: 1.0 for check_id in self.check_ids}
        self.assertEqual(
            analyze_real_judgments(
                {"case-a": {"checks": checks}},
                self.rubric,
            ),
            [],
        )

    def test_missing_mapping_fails_validation_and_analysis(self):
        rubric = copy.deepcopy(self.rubric)
        rubric["dimensions"][0]["checks"][1].pop("optimizer")

        with self.assertRaises(FailureMappingError) as validation:
            validate_optimizer_mappings(rubric)
        self.assertIn("T2", validation.exception.check_ids)

        with self.assertRaises(FailureMappingError) as analysis:
            analyze_real_judgments(
                {
                    "case-a": {
                        "checks": {"T2": 0.0},
                    }
                },
                rubric,
            )
        self.assertIn("T2", analysis.exception.check_ids)


if __name__ == "__main__":
    unittest.main()
