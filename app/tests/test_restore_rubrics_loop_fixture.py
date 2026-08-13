import json
import sys
import tempfile
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

import restore_rubrics_loop_fixture as fixture  # noqa: E402


class RestoreRubricsLoopFixtureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.saved = self.root / "fixture"
        live = self.runtime / "sessions" / "source"
        (live / "rubrics_loop" / "experiments").mkdir(parents=True)
        (live / "state.json").write_text("{}", encoding="utf-8")
        (live / "rubrics_loop" / "experiments" / "rx-one.json").write_text(
            json.dumps({
                "experiment_id": "rx-one",
                "experiment_session_id": "validation-one",
            }),
            encoding="utf-8",
        )
        validation = self.runtime / "sessions" / "validation-one"
        validation.mkdir(parents=True)
        (validation / "state.json").write_text("{}", encoding="utf-8")
        compiled = (
            self.runtime / "generation_runs" / "_session_skills"
            / "validation-one"
        )
        compiled.mkdir(parents=True)
        (compiled / "SKILL.md").write_text("test", encoding="utf-8")
        (self.saved / "rubrics_loop" / "experiments").mkdir(parents=True)
        (self.saved / "rubrics_loop" / "candidates").mkdir()
        (self.saved / "rubrics_loop" / "candidates" / "rc-one.json").write_text(
            json.dumps({"candidate_id": "rc-one", "status": "staged"}),
            encoding="utf-8",
        )
        (self.saved / "manifest.json").write_text(json.dumps({
            "source_session_id": "source",
            "candidate_id": "rc-one",
            "draft_id": "rd-one",
        }), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_plan_is_read_only_and_apply_archives_validation_session(self):
        plan = fixture.plan_restore(self.runtime, self.saved)
        self.assertEqual(1, len(plan["experiments_to_archive"]))
        self.assertTrue(
            (self.runtime / "sessions" / "validation-one").is_dir()
        )

        result = fixture.apply_restore(self.runtime, self.saved)

        self.assertEqual("restored", result["status"])
        self.assertFalse(
            (self.runtime / "sessions" / "validation-one").exists()
        )
        restored = json.loads((
            self.runtime / "sessions" / "source" / "rubrics_loop"
            / "candidates" / "rc-one.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual("staged", restored["status"])
        archive = Path(result["archive_root"])
        self.assertTrue(
            (archive / "sessions" / "validation-one" / "state.json").is_file()
        )
        self.assertTrue(
            (archive / "session-skills" / "validation-one" / "SKILL.md").is_file()
        )


if __name__ == "__main__":
    unittest.main()
