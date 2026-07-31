# -*- coding: utf-8 -*-
from pathlib import Path
import unittest


APP = Path(__file__).resolve().parents[1]


class FrontendContractTest(unittest.TestCase):
    def test_judge_completion_rerenders_advance_button(self):
        source = (APP / "app.js").read_text(encoding="utf-8")
        self.assertIn(
            "finally{\n    JUDGE_RUNNING=false;render();\n  }",
            source,
        )

    def test_frontend_accepts_unified_cases_document(self):
        source = (APP / "app.js").read_text(encoding="utf-8")
        html = (APP / "index.html").read_text(encoding="utf-8")
        self.assertIn("use_configured:true", source)
        self.assertIn("configuredDataBtn", html)
        self.assertIn("JSON.parse(raw)", source)

    def test_frontend_uses_backend_actions_for_loop_buttons(self):
        source = (APP / "app.js").read_text(encoding="utf-8")
        self.assertIn("STATE.actions&&STATE.actions.advance", source)
        self.assertIn(
            "STATE&&STATE.actions&&STATE.actions.run_judge",
            source,
        )
        self.assertIn(
            "STATE&&STATE.actions&&STATE.actions.run_generation",
            source,
        )

    def test_llm_rewrite_can_choose_v0_drafting_strategy(self):
        source = (APP / "app.js").read_text(encoding="utf-8")
        html = (APP / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="v0StrategySel"', html)
        self.assertIn('value="base_skill"', html)
        self.assertIn('value="llm_scratch"', html)
        self.assertIn("v0_strategy:v0Strategy", source)
        self.assertIn("syncV0StrategyVisibility", source)
        self.assertIn("LLM 正在起草 V0", source)

    def test_frontend_sends_generation_and_judge_parallelism(self):
        source = (APP / "app.js").read_text(encoding="utf-8")
        html = (APP / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="generationParallel"', html)
        self.assertIn('id="judgeParallel"', html)
        self.assertIn(
            "'/api/generation/start','POST',{\n      id:SID,idempotency_key:key,parallel",
            source,
        )
        self.assertIn(
            "id:SID,version:STATE.current_version,parallel",
            source,
        )
