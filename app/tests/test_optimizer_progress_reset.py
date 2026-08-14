# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
HARNESS = APP.parent / "harness"
for path in (str(APP), str(HARNESS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import persistence as persist  # noqa: E402
from session import Session  # noqa: E402


class OptimizerProgressResetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_base = persist._BASE
        persist._BASE = str(Path(self.tmp.name) / "sessions")
        self.session = Session(
            "reset-session",
            "生成面向高管的调研洞察报告",
            "research_insight",
            optimizer_mode="llm_rewrite",
        )

    def tearDown(self):
        persist._BASE = self.old_base
        self.tmp.cleanup()

    def test_reset_clears_patience_but_preserves_versions(self):
        self.session.optimization_progress.update(
            {
                "best_overall": 4.6,
                "no_improvement_streak": 5,
                "evaluated_candidates": 7,
            }
        )
        version_count = len(self.session.versions)

        result = self.session.reset_optimization_progress(
            "Runner 从 WorkBuddy 切到 Codex",
            account="tester",
        )

        self.assertNotIn("error", result)
        self.assertEqual(len(self.session.versions), version_count)
        self.assertIsNone(self.session.optimization_progress["best_overall"])
        self.assertEqual(
            self.session.optimization_progress["no_improvement_streak"],
            0,
        )
        self.assertEqual(
            self.session.optimization_progress["evaluated_candidates"],
            0,
        )
        events = [
            json.loads(line)
            for line in (
                Path(persist._BASE)
                / self.session.id
                / "events.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        reset = [
            event
            for event in events
            if event.get("type") == "optimization_progress_reset"
        ]
        self.assertEqual(reset[-1]["payload"]["baseline_version"], "v0")

    def test_pending_candidate_blocks_reset(self):
        self.session.pending_idx = 0
        result = self.session.reset_optimization_progress("backend changed")
        self.assertIn("待结算候选", result["error"])


if __name__ == "__main__":
    unittest.main()
