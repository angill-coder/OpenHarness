# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


APP = Path(__file__).resolve().parents[1]
HARNESS = APP.parent / "harness"
for path in (str(APP), str(HARNESS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import iteration_trace  # noqa: E402
import persistence as persist  # noqa: E402
from session import Session  # noqa: E402


class IterationTraceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_base = persist._BASE
        persist._BASE = str(Path(self.temp.name) / "sessions")
        self.session = Session(
            "trace-session",
            "为腾讯总办生成决策报告",
            "research_insight",
            optimizer_mode="llm_rewrite",
        )
        self.session.import_data(
            [
                {
                    "case_id": "case-a",
                    "split": "dev",
                    "input": {"brief": "形成报告"},
                    "turns": [
                        {
                            "round": 0,
                            "label": "task",
                            "prompt": "请为总办生成决策报告",
                        },
                        {
                            "round": 1,
                            "label": "intake_answers",
                            "prompt": (
                                "背景：总办决策；hypothesis：效率提升；"
                                "重点素材：全部。"
                            ),
                        },
                    ],
                    "human_report": {},
                }
            ]
        )
        parent = self.session.versions[0]
        candidate_skill = copy.deepcopy(parent["skill"])
        candidate_skill.version = "v1"
        candidate_skill.parent_version = "v0"
        candidate_skill.instructions = dict(candidate_skill.instructions)
        candidate_skill.instructions["prose"] = (
            candidate_skill.instructions.get("prose", "")
            + "\n新增一条可验证规则。"
        )
        self.proposal = {
            "change_summary": "增加验证规则",
            "targets_failures": ["pattern-a"],
            "affected_dims": ["traceability"],
            "diagnosis": {
                "diagnoses": [{
                    "check_id": "T1",
                    "pattern_id": "pattern-a",
                    "root_cause_type": "skill",
                    "rationale": "三个 case 存在同类规则缺口",
                    "confidence": "high",
                    "evidence_ids": ["EXP-01", "EXP-02", "EXP-03"],
                }],
                "selected_target": {
                    "check_id": "T1",
                    "pattern_id": "pattern-a",
                    "root_cause_type": "skill",
                    "rationale": "影响面最大",
                    "confidence": "high",
                    "evidence_ids": ["EXP-01", "EXP-02", "EXP-03"],
                },
                "deferred_failures": [],
            },
            "selected_target": {
                "check_id": "T1",
                "pattern_id": "pattern-a",
                "root_cause_type": "skill",
                "rationale": "影响面最大",
                "confidence": "high",
                "evidence_ids": ["EXP-01", "EXP-02", "EXP-03"],
            },
            "root_cause": {
                "type": "skill",
                "rationale": "三条失败句指向同一规则缺口",
                "evidence_ids": ["EXP-01", "EXP-02", "EXP-03"],
            },
            "experiment": {
                "hypothesis": "局部规则可提高 T1",
                "examples": [
                    {
                        "evidence_id": "EXP-%02d" % index,
                        "case_id": "case-%d" % index,
                        "check_id": "T1",
                        "report_sentence": "报告原句 %d" % index,
                        "evidence": "素材证据 %d" % index,
                        "judge_verdict": "T1=0.5",
                        "expected_change": "删除无据断言",
                    }
                    for index in range(1, 4)
                ],
                "success_criteria": [{
                    "check_id": "T1",
                    "expected": "均分提高",
                }],
                "rollback_condition": "T1 不升或任一维度回退",
            },
            "patch": {
                "add": [],
                "replace": [{
                    "old_text": "旧规则",
                    "new_text": "新规则",
                }],
                "delete": [],
            },
            "budget": {
                "parent_chars": 100,
                "candidate_chars": 110,
                "net_growth_chars": 10,
            },
            "redline_preservation": {"T1": True},
            "_optimizer_trace": {
                "model_calls": 2,
                "diagnosis_prompt_chars": 600,
                "diagnosis_response_chars": 200,
                "diagnosis_duration_ms": 30,
                "diagnosis_max_tokens": 6000,
                "patch_prompt_chars": 1000,
                "patch_response_chars": 500,
                "patch_duration_ms": 70,
                "patch_max_tokens": 12000,
                "rewrite_prompt_chars": 1000,
                "rewrite_response_chars": 500,
            },
        }
        self.session._add_version(
            candidate_skill,
            adopted=False,
            proposal=self.proposal,
        )

    def tearDown(self):
        persist._BASE = self.old_base
        self.temp.cleanup()

    @property
    def root(self):
        return (
            Path(persist._BASE)
            / self.session.id
            / "iterations"
            / "v1"
        )

    def test_five_files_capture_full_iteration_summary(self):
        optimizer = iteration_trace.record_optimizer_proposal(
            self.session,
            "v1",
            {
                "open_failures": [
                    {
                        "pattern_id": "pattern-a",
                        "hit_count": 3,
                        "evidence": [
                            {"case_id": "case-a", "check_id": "T1"}
                        ],
                    }
                ],
                "failure_inventory": [{
                    "check_id": "T1",
                    "pattern_id": "pattern-a",
                }],
                "diagnosis_candidates": [{
                    "check_id": "T1",
                    "pattern_id": "pattern-a",
                }],
                "diagnosis_evidence": [
                    {"evidence_id": "EXP-%02d" % index, "check_id": "T1"}
                    for index in range(1, 4)
                ],
                "evidence_catalog": [
                    {"evidence_id": "EXP-%02d" % index, "check_id": "T1"}
                    for index in range(1, 5)
                ],
            },
            self.proposal,
        )
        iteration_id = optimizer["iteration_id"]

        iteration_trace.record_generation_job(
            self.session,
            {
                "job_id": "job-a",
                "skill_version": "v1",
                "status": "completed",
                "terminal": True,
                "generation_id": "gen-a",
                "model": "model-a",
                "case_count": 1,
                "imported_count": 1,
                "failed_case_ids": [],
                "started_at": 10,
                "finished_at": 12,
                "cases": [{"case_id": "case-a", "attempts": 2}],
            },
        )
        report = "# 摘要\n\n- 结论\n\n## 关键发现\n\n|指标|值|\n|---|---|\n|A|1|"
        iteration_trace.record_dialogue_contract(
            self.session,
            "v1",
            {"case-a": report},
            traces={
                "case-a": {
                    "rounds": [
                        {
                            "output": (
                                "请补充背景、hypothesis、重点素材和篇幅。"
                            )
                        },
                        {"output": report},
                    ]
                }
            },
        )
        iteration_trace.record_judge_run(
            self.session,
            "v1",
            "judge-a",
            [
                {
                    "status": "judged",
                    "judge_trace": {
                        "calls": [
                            {
                                "attempt": 1,
                                "retry": 0,
                                "promptChars": 200,
                                "response": "{}",
                                "durationMs": 50,
                            },
                            {
                                "attempt": 2,
                                "retry": 1,
                                "promptChars": 200,
                                "response": "{}",
                                "durationMs": 60,
                            },
                        ]
                    },
                }
            ],
            {
                "strategy": "per_dimension",
                "backend": "api",
                "model": "judge-model",
            },
        )

        parent = self.session.versions[0]
        candidate = self.session.versions[1]
        parent["dev"] = {
            "traceability": 4.0,
            "structure": 4.0,
            "narrative": 4.0,
            "insight": 4.0,
            "coverage": 4.0,
            "expression": 4.0,
            "overall": 4.0,
            "red_line_fails": 1,
        }
        candidate["dev"] = {
            **parent["dev"],
            "traceability": 4.2,
            "overall": 4.05,
            "red_line_fails": 0,
        }
        parent["_recs"] = [
            SimpleNamespace(case_id="case-a", case_failed_gate=True)
        ]
        candidate["_recs"] = [
            SimpleNamespace(case_id="case-a", case_failed_gate=False)
        ]
        candidate["adopted"] = True
        self.session.current_idx = 1
        iteration_trace.record_gate_decision(
            self.session,
            candidate,
            parent,
            "adopted",
            {"improved": True, "improved_dims": ["traceability"]},
            ["traceability"],
            0.15,
            "v0",
            "v0",
        )

        self.assertEqual(
            {path.name for path in self.root.iterdir()},
            set(iteration_trace.FILES),
        )
        manifest = json.loads(
            (self.root / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["iteration_id"], iteration_id)
        self.assertEqual(manifest["status"], "settled")
        self.assertEqual(manifest["correlation"]["generation_job_ids"], ["job-a"])
        self.assertEqual(manifest["correlation"]["judge_run_ids"], ["judge-a"])

        optimizer_payload = json.loads(
            (self.root / "optimizer_summary.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertGreater(
            optimizer_payload["instruction_metrics"]["net_chars"],
            0,
        )
        self.assertEqual(
            optimizer_payload["failure_context"]["evidence_samples_sent"],
            1,
        )
        self.assertEqual(optimizer_payload["root_cause"]["type"], "skill")
        self.assertEqual(
            optimizer_payload["selected_target"]["check_id"],
            "T1",
        )
        self.assertEqual(
            optimizer_payload["failure_context"]["diagnosis_evidence_by_check"],
            {"T1": 3},
        )
        self.assertEqual(len(optimizer_payload["experiment"]["examples"]), 3)
        self.assertEqual(
            optimizer_payload["patch"]["replace"][0]["old_text"],
            "旧规则",
        )
        self.assertEqual(
            optimizer_payload["patch_budget"]["net_growth_chars"],
            10,
        )

        dialogue = json.loads(
            (self.root / "dialogue_contract.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            dialogue["cases"]["case-a"]["unanswered_asked_fields"],
            ["length_budget"],
        )
        self.assertFalse(
            dialogue["cases"]["case-a"]["conversation_contract_passed"]
        )
        serialized = json.dumps(dialogue, ensure_ascii=False)
        self.assertNotIn(report, serialized)

        resources = json.loads(
            (self.root / "resource_usage.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            resources["generation"]["totals"]["case_attempts"],
            2,
        )
        self.assertEqual(resources["judge"]["totals"]["model_calls"], 2)
        self.assertEqual(resources["judge"]["totals"]["retry_calls"], 1)
        self.assertEqual(resources["optimizer"]["model_calls"], 2)
        self.assertEqual(resources["optimizer"]["wall_time_ms"], 100)

        gate = json.loads(
            (self.root / "gate_decision.json").read_text(encoding="utf-8")
        )
        self.assertEqual(gate["decision"], "adopted")
        self.assertEqual(
            gate["decision_rule_version"],
            "gate/v4-net-hard-improvement",
        )
        self.assertEqual(gate["comparison_baseline_version"], "v0")
        self.assertEqual(gate["failed_cases"]["resolved_case_ids"], ["case-a"])
        self.assertEqual(gate["current_pointer"], {"before": "v0", "after": "v1"})

    def test_failed_next_optimizer_attempt_is_appended_to_current_version(self):
        iteration_trace.ensure_iteration(self.session, "v0")
        summary = iteration_trace.record_optimizer_failure(
            self.session,
            "v0",
            {"open_failures": [{"pattern_id": "p1"}]},
            {
                "reason": "Optimizer LLM 返回空内容",
                "error_code": "empty_llm_response",
                "rewrite_prompt_chars": 43350,
                "rewrite_prompt_sha256": "abc",
                "rewrite_duration_ms": 80000,
                "llm_diagnostics": {
                    "max_tokens": 12000,
                    "attempts": [
                        {"finish_reason": "length"},
                        {"finish_reason": "length"},
                    ],
                },
            },
        )
        attempt = summary["next_proposal_attempts"][0]
        self.assertEqual(attempt["error_code"], "empty_llm_response")
        self.assertEqual(attempt["open_failure_count"], 1)
        self.assertEqual(
            attempt["diagnostic"]["reason"],
            "Optimizer LLM 返回空内容",
        )
        root = Path(persist._BASE) / self.session.id / "iterations" / "v0"
        resources = json.loads(
            (root / "resource_usage.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            resources["optimizer"]["failed_next_proposal_model_calls"],
            2,
        )
        manifest = json.loads(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            len(manifest["correlation"]["failed_optimizer_run_ids"]),
            1,
        )


if __name__ == "__main__":
    unittest.main()
