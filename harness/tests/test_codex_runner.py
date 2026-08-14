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

from codex_runner import (  # noqa: E402
    build_command,
    compile_replay_prompt,
    run_external_cases,
)
from external_run_models import (  # noqa: E402
    ExternalRunRequest,
    ReportOutputContract,
)
from workbuddy_batch.models import CaseSpec, Interaction  # noqa: E402


class CodexRunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.skill = self.root / "research-report"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text(
            "---\nname: research-report\n---\n# Test Skill\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _request(self, dataset: Path) -> ExternalRunRequest:
        return ExternalRunRequest(
            case_file=dataset,
            output_root=self.root / "runs",
            skill_version="v-test",
            session_id="session-test",
            skill_path=self.skill,
            model="gpt-5.6-sol",
            effort="medium",
            parallel=1,
            timeout_seconds=30,
            stall_timeout_seconds=10,
            max_report_retries=0,
            output_contract=ReportOutputContract(min_bytes=100),
            command=(
                sys.executable,
                str(Path(__file__).with_name("fake_codex.py")),
            ),
        )

    def test_replay_prompt_preserves_turn_order_and_delivery_contract(self):
        request = self._request(self.root / "unused.json")
        case = CaseSpec(
            case_id="case-a",
            prompt="先提出必要问题",
            interactions=(Interaction("这是补充答案", "intake_answers"),),
        )

        prompt = compile_replay_prompt(case, "research-report", request)

        self.assertLess(prompt.index("先提出必要问题"), prompt.index("这是补充答案"))
        self.assertIn(".codebuddy/skills/research-report/SKILL.md", prompt)
        self.assertIn("deliverables/report.md", prompt)
        self.assertIn("不要继续追问", prompt)

    def test_command_is_isolated_workspace_write_codex_exec(self):
        request = self._request(self.root / "unused.json")
        workspace = self.root / "workspace"
        output = self.root / "last.txt"

        args = build_command(
            ("codex",),
            workspace,
            output,
            request,
            "prompt",
        )

        self.assertIn("workspace-write", args)
        self.assertIn("--ephemeral", args)
        self.assertIn("--ignore-user-config", args)
        self.assertIn("--ignore-rules", args)
        self.assertIn('model_reasoning_effort="medium"', args)
        self.assertEqual(args[-1], "prompt")

    def test_fake_codex_run_produces_valid_external_result(self):
        dataset = self.root / "data.json"
        dataset.write_text(
            json.dumps(
                {
                    "schema_version": "openharness-wb/v1",
                    "cases": [
                        {
                            "case_id": "case-a",
                            "split": "dev",
                            "turns": [
                                {
                                    "round": 0,
                                    "label": "task",
                                    "prompt": "生成报告",
                                },
                                {
                                    "round": 1,
                                    "label": "intake_answers",
                                    "prompt": "背景与假设已提供",
                                },
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = run_external_cases(self._request(dataset))

        self.assertTrue(result.succeeded)
        self.assertTrue(result.generation_id.startswith("gen-codex-"))
        case = result.cases[0]
        self.assertEqual(case.openharness_case_id, "case-a")
        self.assertEqual(case.status, "generated")
        self.assertIsNotNone(case.report)
        self.assertEqual(case.attempts[0].wb_session_id, "fake-codex-thread")
        self.assertEqual(case.attempts[0].configured_model, "gpt-5.6-sol")
        self.assertEqual(case.attempts[0].usage["input_tokens"], 100)
        self.assertFalse(
            Path(case.attempts[0].trace_path, "workspace").exists()
        )


if __name__ == "__main__":
    unittest.main()
