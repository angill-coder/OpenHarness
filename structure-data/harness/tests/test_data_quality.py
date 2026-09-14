# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HARNESS_DIR = Path(__file__).resolve().parents[1]
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

from data_workflow import (
    CodexJsonRunner,
    DataQualityCancelled,
    DataWorkflowRequest,
    _parser,
    run_data_workflow,
)


def _structured_data(case_id: str) -> dict:
    return {
        "schema": "openharness-structured-data/v1",
        "case_id": case_id,
        "items": [
            {
                "id": "EV-001",
                "type": "quantitative",
                "source_ref": "source.md / 第一节",
                "content": "来源支持第一个关键结论。",
            },
            {
                "id": "EV-002",
                "type": "qualitative",
                "source_ref": "source.md / 第二节",
                "content": "来源包含报告未采用的补充信息。",
            },
        ],
        "unresolved": [],
    }


def _audit(project: str, case_id: str) -> dict:
    return {
        "project": project,
        "case_id": case_id,
        "human_report_core": [
            {
                "id": "HR-001",
                "text": "第一个关键结论",
                "quote": "第一个关键结论",
                "location": "第一节",
                "importance": "critical",
            },
            {
                "id": "HR-002",
                "text": "第二个关键结论",
                "quote": "第二个关键结论",
                "location": "第二节",
                "importance": "critical",
            },
        ],
        "evidence_classifications": [
            {
                "evidence_id": "EV-001",
                "classification": "used",
                "human_report_ids": ["HR-001"],
                "reason": "直接支撑",
            },
            {
                "evidence_id": "EV-002",
                "classification": "noise",
                "human_report_ids": [],
                "reason": "未被采用",
            },
        ],
        "omissions": [
            {
                "human_report_id": "HR-002",
                "human_report_text": "第二个关键结论",
                "human_report_quote": "第二个关键结论",
                "human_report_location": "第二节",
                "severity": "critical",
                "search_note": "已回查全部 source",
                "reason": "没有充分支撑",
            }
        ],
        "conflicts": [],
        "structured_data_gaps": [],
        "noise_clusters": [
            {
                "theme": "未采用信息",
                "evidence_ids": ["EV-002"],
                "representative_text": "补充信息",
                "reason": "报告未采用",
            }
        ],
        "scope_risks": ["测试数据仅用于契约校验"],
        "assessment": "存在一项关键遗漏。",
        "recommendations": ["补充第二项论据。"],
    }


class DataQualityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_standalone_pipeline_builds_structured_data_and_audit(self) -> None:
        case_dir = self.root / "case-a"
        source = case_dir / "source"
        source.mkdir(parents=True)
        (source / "source.md").write_text("原始资料", encoding="utf-8")
        human_report = case_dir / "human_report.md"
        human_report.write_text("两个关键结论", encoding="utf-8")
        calls = []
        events = []

        def fake_run(_runner, *, prompt, schema_path, cwd):
            calls.append(schema_path.name)
            if schema_path.name == "structured_data.schema.json":
                return _structured_data("case-a"), 1.2
            return _audit("case-a", "case-a"), 2.3

        request = DataWorkflowRequest(
            output_root=self.root / "output",
            source_paths=(source,),
            human_report=human_report,
            case_id="case-a",
        )
        with patch.object(CodexJsonRunner, "run", autospec=True, side_effect=fake_run):
            result = run_data_workflow(request, progress_callback=events.append)

        self.assertTrue(result.succeeded)
        self.assertEqual(calls, ["structured_data.schema.json", "audit.schema.json"])
        case_result = result.cases[0]
        self.assertEqual(case_result.structured_data_status, "generated")
        self.assertEqual(case_result.audit_status, "generated")
        self.assertEqual(case_result.overall_score, 70.0)
        output = Path(case_result.output_dir)
        self.assertTrue((output / "structured_data.json").is_file())
        self.assertTrue((output / "audit.json").is_file())
        self.assertIn("综合质量分 | 70.0", (output / "audit.md").read_text())
        self.assertEqual(events[0]["event"], "workflow_started")
        self.assertEqual(events[-1]["event"], "workflow_completed")
        self.assertIn(
            ("stage_completed", "audit"),
            [(item["event"], item.get("stage")) for item in events],
        )

    def test_workflow_can_be_cancelled_before_start(self) -> None:
        source = self.root / "source"
        source.mkdir()
        request = DataWorkflowRequest(
            output_root=self.root / "output",
            source_paths=(source,),
            case_id="case-cancelled",
            stages=("structured_data",),
        )

        with self.assertRaises(DataQualityCancelled):
            run_data_workflow(request, should_cancel=lambda: True)

    def test_openharness_dataset_reuses_valid_structured_data(self) -> None:
        case_dir = self.root / "collection" / "中文项目"
        source = case_dir / "source"
        source.mkdir(parents=True)
        (source / "source.md").write_text("原始资料", encoding="utf-8")
        (case_dir / "structured_data.json").write_text(
            json.dumps(_structured_data("case-b"), ensure_ascii=False),
            encoding="utf-8",
        )
        dataset = self.root / "data.json"
        dataset.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "case_id": "case-b",
                            "turns": [{"round": 0, "prompt": "研究背景"}],
                            "input_files": [
                                {
                                    "source": "./collection/中文项目/source",
                                    "target": "materials",
                                }
                            ],
                            "human_report": {
                                "human_report_text": "两个关键结论"
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        def fake_run(_runner, *, prompt, schema_path, cwd):
            self.assertEqual(schema_path.name, "audit.schema.json")
            return _audit("中文项目", "case-b"), 1.0

        request = DataWorkflowRequest(
            output_root=self.root / "output",
            dataset=dataset,
        )
        with patch.object(CodexJsonRunner, "run", autospec=True, side_effect=fake_run):
            result = run_data_workflow(request)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.cases[0].structured_data_status, "reused")
        self.assertEqual(result.cases[0].audit_status, "generated")
        self.assertEqual(result.cases[0].project, "中文项目")

    def test_dataset_file_mappings_exclude_structured_data_from_sources_and_publish(self) -> None:
        case_dir = self.root / "collection" / "weekly"
        source = case_dir / "source"
        source.mkdir(parents=True)
        source_file = source / "interview.md"
        source_file.write_text("有效访谈证据", encoding="utf-8")
        structured_data = case_dir / "structured_data.json"
        structured_data.write_text(
            json.dumps(_structured_data("case-file-map"), ensure_ascii=False),
            encoding="utf-8",
        )
        dataset = self.root / "data.json"
        dataset.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "case_id": "case-file-map",
                            "turns": [{"round": 0, "prompt": "周报"}],
                            "input_files": [
                                {
                                    "source": "./collection/weekly/structured_data.json",
                                    "target": "materials/00_structured_data.json",
                                },
                                {
                                    "source": "./collection/weekly/source/interview.md",
                                    "target": "materials/source/interview.md",
                                },
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        replacement = _structured_data("case-file-map")
        replacement["items"][0]["content"] = "重新清洗后的证据。"

        def fake_run(_runner, *, prompt, schema_path, cwd):
            self.assertEqual(schema_path.name, "structured_data.schema.json")
            self.assertIn(str(source_file.resolve()), prompt)
            self.assertNotIn(str(structured_data.resolve()), prompt)
            return replacement, 1.0

        request = DataWorkflowRequest(
            output_root=self.root / "output",
            dataset=dataset,
            stages=("structured_data",),
            force_structured_data=True,
            publish_structured_data=True,
        )
        with patch.object(CodexJsonRunner, "run", autospec=True, side_effect=fake_run):
            result = run_data_workflow(request)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.cases[0].project, "weekly")
        published = json.loads(structured_data.read_text(encoding="utf-8"))
        self.assertEqual(published["items"][0]["content"], "重新清洗后的证据。")

    def test_cli_defaults_to_both_stages(self) -> None:
        args = _parser().parse_args(
            [
                "run",
                "--source",
                "source",
                "--case-id",
                "case-a",
                "--human-report",
                "human_report.pdf",
                "--output",
                "out",
            ]
        )
        self.assertFalse(args.structured_data_only)
        self.assertFalse(args.repair_structured_data)
        self.assertEqual(args.parallel, 1)

    def test_repair_stage_adds_source_grounded_structured_data_without_overwriting(self) -> None:
        case_dir = self.root / "case-repair"
        source = case_dir / "source"
        source.mkdir(parents=True)
        (source / "source.md").write_text(
            "第一项结论。第二项结论有原始资料支撑。",
            encoding="utf-8",
        )
        human_report = case_dir / "human_report.md"
        human_report.write_text("两个关键结论", encoding="utf-8")
        calls = []
        repair_prompt = ""

        def fake_run(_runner, *, prompt, schema_path, cwd):
            nonlocal repair_prompt
            calls.append(schema_path.name)
            if schema_path.name == "structured_data.schema.json":
                return _structured_data("case-repair"), 1.0
            if schema_path.name == "audit.schema.json":
                payload = _audit("case-repair", "case-repair")
                payload["omissions"] = []
                payload["structured_data_gaps"] = [
                    {
                        "id": "MG-001",
                        "human_report_ids": ["HR-002"],
                        "gap_type": "missing",
                        "importance": "critical",
                        "source_fact": "第二项结论有原始资料支撑。",
                        "source_ref": "source.md / 第二句",
                        "reason": "现有 Structured Data 未提取该事实。",
                    }
                ]
                return payload, 2.0
            repair_prompt = prompt
            return {
                "case_id": "case-repair",
                "additions": [
                    {
                        "gap_ids": ["MG-001"],
                        "type": "qualitative",
                        "source_ref": "source.md / 第二句",
                        "content": "原始资料明确支持第二项结论。",
                    }
                ],
                "skipped": [],
            }, 1.5

        request = DataWorkflowRequest(
            output_root=self.root / "output",
            source_paths=(source,),
            human_report=human_report,
            case_id="case-repair",
            stages=("structured_data", "audit", "repair"),
        )
        with patch.object(CodexJsonRunner, "run", autospec=True, side_effect=fake_run):
            result = run_data_workflow(request)

        self.assertTrue(result.succeeded)
        self.assertEqual(
            calls,
            [
                "structured_data.schema.json",
                "audit.schema.json",
                "structured_data_repair.schema.json",
            ],
        )
        case_result = result.cases[0]
        self.assertEqual(case_result.repair_status, "generated")
        self.assertEqual(case_result.structured_data_gap_count, 1)
        self.assertEqual(case_result.repaired_structured_data_items, 3)
        output = Path(case_result.output_dir)
        original = json.loads((output / "structured_data.json").read_text())
        repaired = json.loads(
            (output / "structured_data.repaired.json").read_text()
        )
        self.assertEqual(len(original["items"]), 2)
        self.assertEqual(len(repaired["items"]), 3)
        self.assertEqual(repaired["items"][-1]["id"], "EV-003")
        self.assertNotIn(str(human_report), repair_prompt)
        self.assertNotIn("两个关键结论", repair_prompt)

    def test_codex_runner_uses_one_read_only_structured_command(self) -> None:
        source = self.root / "source"
        source.mkdir()
        schema = self.root / "schema.json"
        schema.write_text("{}", encoding="utf-8")
        fake = self.root / "fake_codex.py"
        fake.write_text(
            "\n".join(
                [
                    "import json, pathlib, sys",
                    "args = sys.argv[1:]",
                    "target = pathlib.Path(args[args.index('--output-last-message') + 1])",
                    "target.write_text(json.dumps({'args': args, 'stdin': sys.stdin.read()}))",
                ]
            ),
            encoding="utf-8",
        )
        request = DataWorkflowRequest(
            output_root=self.root / "output",
            source_paths=(source,),
            case_id="case-c",
            stages=("structured_data",),
            codex_command=(sys.executable, str(fake)),
        )

        payload, _elapsed = CodexJsonRunner(request).run(
            prompt="structured prompt",
            schema_path=schema,
            cwd=self.root,
        )

        self.assertIn("--ephemeral", payload["args"])
        self.assertIn("--ignore-user-config", payload["args"])
        self.assertEqual(
            payload["args"][payload["args"].index("--sandbox") + 1],
            "read-only",
        )
        self.assertEqual(payload["stdin"], "structured prompt")

    def test_audit_retries_when_rounding_is_misclassified_as_conflict(self) -> None:
        case_dir = self.root / "case-d"
        source = case_dir / "source"
        source.mkdir(parents=True)
        (source / "source.md").write_text("原始资料", encoding="utf-8")
        human_report = case_dir / "human_report.md"
        human_report.write_text("两个关键结论", encoding="utf-8")
        audit_calls = 0

        def fake_run(_runner, *, prompt, schema_path, cwd):
            nonlocal audit_calls
            if schema_path.name == "structured_data.schema.json":
                return _structured_data("case-d"), 1.0
            audit_calls += 1
            payload = _audit("case-d", "case-d")
            if audit_calls == 1:
                payload["evidence_classifications"][0] = {
                    "evidence_id": "EV-001",
                    "classification": "conflict",
                    "human_report_ids": ["HR-001"],
                    "reason": "整数相差1，但四舍五入后排序和方向不受影响。",
                }
                payload["conflicts"] = [
                    {
                        "evidence_id": "EV-001",
                        "human_report_id": "HR-001",
                        "source_text": "来源为100",
                        "source_ref": "source.md",
                        "human_report_text": "报告为101",
                        "human_report_quote": "101",
                        "human_report_location": "第一节",
                        "conflict_type": "numeric",
                        "severity": "material",
                        "reason": "整数相差1，但四舍五入后排序和方向不受影响。",
                    }
                ]
            return payload, 1.0

        request = DataWorkflowRequest(
            output_root=self.root / "output",
            source_paths=(source,),
            human_report=human_report,
            case_id="case-d",
            retries=1,
        )
        with patch.object(CodexJsonRunner, "run", autospec=True, side_effect=fake_run):
            result = run_data_workflow(request)

        self.assertTrue(result.succeeded)
        self.assertEqual(audit_calls, 2)
        self.assertEqual(result.cases[0].overall_score, 70.0)


if __name__ == "__main__":
    unittest.main()
