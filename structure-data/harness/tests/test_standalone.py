"""Packaging checks: no sibling repository modules or real model calls."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class StandalonePackagingTest(unittest.TestCase):
    def test_cli_help_works_outside_checkout(self):
        with tempfile.TemporaryDirectory() as cwd:
            for args in (["--help"], ["run", "--help"], ["audit", "--help"]):
                # Isolated Python ignores PYTHONPATH and user site-packages;
                # only the copied harness directory is added to its path.
                result = subprocess.run(
                    [sys.executable, "-I", "-c",
                     "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); "
                     "runpy.run_path(sys.argv.pop(1),run_name='__main__')",
                     str(ROOT / "harness"), str(ROOT / "harness/data_workflow.py"), *args],
                    cwd=cwd, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("generate-projects", result.stdout)

    def test_all_model_assets_are_bundled(self):
        assets = ROOT / "harness/data_quality_assets"
        self.assertEqual(len(list(assets.glob("*.schema.json"))), 3)
        for schema in assets.glob("*.schema.json"):
            self.assertIsInstance(json.loads(schema.read_text(encoding="utf-8")), dict)
        for file in [assets / "structured_data_prompt.md",
                     ROOT / "skills/data-quality-audit/references/dimensions.md",
                     ROOT / "skills/data-quality-audit/references/result-schema.md"]:
            self.assertTrue(file.read_text(encoding="utf-8").strip())
        self.assertFalse((ROOT / "harness/_data_prepare.py").exists())
        self.assertFalse((ROOT / "harness/workbuddy_batch").exists())


if __name__ == "__main__":
    unittest.main()
