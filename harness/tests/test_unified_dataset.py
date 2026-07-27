# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


HARNESS = Path(__file__).resolve().parents[1]
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from workbuddy_batch.dataset import (  # noqa: E402
    UNIFIED_SCHEMA_VERSION,
    load_cases,
    load_openharness_rows,
    openharness_rows,
)


class UnifiedDatasetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.materials = self.root / "materials"
        self.materials.mkdir()
        (self.materials / "source.md").write_text(
            "source",
            encoding="utf-8",
        )
        self.dataset = self.root / "data.json"
        self.payload = {
            "schema_version": UNIFIED_SCHEMA_VERSION,
            "defaults": {"skills": ["research-report"]},
            "cases": [
                {
                    "case_id": "case-a",
                    "split": "dev",
                    "input": {
                        "topic": "A",
                        "brief": "生成报告 A",
                    },
                    "ground_truth": {},
                    "input_files": [
                        {
                            "source": "./materials",
                            "target": "materials",
                        }
                    ],
                    "turns": [
                        {
                            "round": 0,
                            "label": "task",
                            "prompt": "生成报告 A",
                        }
                    ],
                    "metadata": {"topic": "A"},
                }
            ],
        }
        self.dataset.write_text(
            json.dumps(self.payload, ensure_ascii=False),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_same_document_loads_for_workbuddy_and_openharness(self) -> None:
        wb_cases = load_cases(self.dataset)
        platform_rows = load_openharness_rows(self.dataset)

        self.assertEqual([case.case_id for case in wb_cases], ["case-a"])
        self.assertEqual(
            [row["case_id"] for row in platform_rows],
            ["case-a"],
        )
        self.assertEqual(wb_cases[0].metadata["split"], "dev")
        self.assertEqual(platform_rows[0]["split"], "dev")

    def test_evaluation_fields_do_not_enter_workbuddy_data(self) -> None:
        case = load_cases(self.dataset)[0]

        self.assertNotIn("input", case.data)
        self.assertNotIn("ground_truth", case.data)
        self.assertNotIn("split", case.data)
        self.assertEqual(
            case.metadata["openharness_case_id"],
            case.case_id,
        )

    def test_unified_document_requires_case_id_and_input(self) -> None:
        missing_input = json.loads(json.dumps(self.payload))
        missing_input["cases"][0].pop("input")
        with self.assertRaisesRegex(ValueError, "缺少 input"):
            openharness_rows(missing_input)

        missing_id = json.loads(json.dumps(self.payload))
        missing_id["cases"][0].pop("case_id")
        with self.assertRaisesRegex(ValueError, "缺少 case_id"):
            openharness_rows(missing_id)

    def test_legacy_dataset_derives_platform_fields(self) -> None:
        rows = openharness_rows(
            {
                "cases": [
                    {
                        "id": "wb-a",
                        "metadata": {
                            "openharness_case_id": "case-a",
                            "split": "test",
                            "topic": "A",
                        },
                        "prompt": "生成报告",
                    }
                ]
            }
        )

        self.assertEqual(rows[0]["case_id"], "case-a")
        self.assertEqual(rows[0]["split"], "test")
        self.assertEqual(rows[0]["ground_truth"], {})


if __name__ == "__main__":
    unittest.main()
