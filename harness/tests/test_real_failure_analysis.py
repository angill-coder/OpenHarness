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
                / "v2_rubric_research.json"
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

    def test_research_rubric_v23_has_compact_stable_check_set(self):
        expected_by_dimension = {
            "traceability": ["T1", "T2", "T3", "T4", "T5", "T6"],
            "structure": ["S1", "S4", "S5"],
            "narrative": ["N2", "N4", "N5"],
            "insight": ["I1", "I2", "I3", "I4"],
            "coverage": ["V1", "V2", "V3"],
            "expression": ["E1", "E2", "E3", "E4", "E5", "E6"],
        }
        actual_by_dimension = {
            dimension["name"]: [
                check["id"] for check in dimension.get("checks", [])
            ]
            for dimension in self.rubric["dimensions"]
        }
        redlines = {
            check["id"]
            for dimension in self.rubric["dimensions"]
            for check in dimension.get("checks", [])
            if check.get("redline")
        }

        self.assertEqual(self.rubric["version"], "v2.3")
        self.assertEqual(actual_by_dimension, expected_by_dimension)
        self.assertEqual(len(self.check_ids), 25)
        self.assertEqual(redlines, {"T1", "T2", "T3", "T5", "E5"})

    def test_v23_does_not_replace_default_research_rubric(self):
        default_rubric = json.loads(
            (
                HARNESS
                / "artifacts"
                / "rubric_research.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(default_rubric["version"], "v0")
        self.assertNotEqual(default_rubric, self.rubric)

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
