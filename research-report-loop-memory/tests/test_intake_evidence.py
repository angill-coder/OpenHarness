import copy
import os
import tempfile
import unittest
from pathlib import Path

from mcp.report_loop.runner import JobError, load_job


class IntakeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.material = self.root / "interview.docx"
        self.material.write_text("material", encoding="utf-8")
        self.v1 = self.root / "report-v1.md"
        self.v1.write_text("# report", encoding="utf-8")
        self.previous_host_model = os.environ.get("RESEARCH_REPORT_LOOP_HOST_MODEL_ID")
        os.environ["RESEARCH_REPORT_LOOP_HOST_MODEL_ID"] = "codewise-jump"

    def tearDown(self):
        if self.previous_host_model is None:
            os.environ.pop("RESEARCH_REPORT_LOOP_HOST_MODEL_ID", None)
        else:
            os.environ["RESEARCH_REPORT_LOOP_HOST_MODEL_ID"] = self.previous_host_model
        self.temporary.cleanup()

    def job(self):
        return {
            "schemaVersion": 2,
            "originalUserQuery": "make a report",
            "intakeContext": {
                "reportBackground": {"value": "for management"},
                "materialHypothesis": {"value": "retention is improving"},
                "priorityMaterials": [
                    {
                        "path": str(self.material),
                        "displayName": self.material.name,
                    }
                ],
                "userInputEvidence": {
                    "reportBackground": "给管理层看，用于资源决策",
                    "materialHypothesis": "我想验证留存是否改善",
                    "priorityMaterials": "重点看访谈文件",
                },
            },
            "v1ArtifactPath": str(self.v1),
            "hostModel": {"modelId": "codewise-jump"},
            "outputPath": str(self.root / "report-final.md"),
        }

    def write_job(self, payload):
        path = self.root / "job.json"
        path.write_text(
            __import__("json").dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_paths_without_user_evidence_are_rejected(self):
        payload = self.job()
        payload["intakeContext"].pop("userInputEvidence")
        with self.assertRaisesRegex(JobError, "userInputEvidence"):
            load_job(self.write_job(payload))

    def test_each_evidence_value_is_required(self):
        for key in (
            "reportBackground",
            "materialHypothesis",
            "priorityMaterials",
        ):
            with self.subTest(key=key):
                payload = copy.deepcopy(self.job())
                payload["intakeContext"]["userInputEvidence"][key] = " "
                with self.assertRaisesRegex(JobError, key):
                    load_job(self.write_job(payload))

    def test_user_evidence_is_preserved(self):
        payload = self.job()
        loaded = load_job(self.write_job(payload))
        self.assertEqual(
            loaded["intakeContext"]["userInputEvidence"],
            payload["intakeContext"]["userInputEvidence"],
        )

    def test_priority_material_can_be_a_directory_without_expansion(self):
        directory = self.root / "原始录音逐字稿"
        directory.mkdir()
        (directory / "访谈一.txt").write_text("transcript", encoding="utf-8")
        payload = self.job()
        payload["intakeContext"]["priorityMaterials"] = [{
            "path": str(directory),
            "displayName": "原始录音逐字稿",
        }]

        loaded = load_job(self.write_job(payload))

        self.assertEqual(loaded["intakeContext"]["priorityMaterials"], [{
            "path": str(directory.resolve()),
            "displayName": "原始录音逐字稿",
        }])

    def test_workbuddy_is_default_and_codex_is_optional(self):
        default_job = load_job(self.write_job(self.job()))
        self.assertEqual(default_job["judgeProvider"], "workbuddy")

        payload = self.job()
        payload["judgeProvider"] = "codex"
        codex_job = load_job(self.write_job(payload))
        self.assertEqual(codex_job["judgeProvider"], "codex")


if __name__ == "__main__":
    unittest.main()
