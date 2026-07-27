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
