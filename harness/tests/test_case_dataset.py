# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parents[1]
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

from _data_prepare import (
    CaseDatasetError,
    _copy_project_source,
    _extract_pptx_text,
    main,
)
from data_workflow import main as workflow_main
from workbuddy_batch.dataset import load_cases


class CaseDatasetTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _write_material(self, directory: Path, name: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text("# 论据情报库\n", encoding="utf-8")
        return path

    def _write_executable(self, name: str, content: str) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_generate_numbered_atomic_cases_next_to_materials(self) -> None:
        materials = self.root / "materials"
        source_2 = self._write_material(materials, "2_第二个主题.md")
        source_10 = self._write_material(materials, "10_主题_含下划线.md")

        result = main(["generate", "--materials-dir", str(materials)])

        self.assertEqual(result, 0)
        output_2 = materials / "2_第二个主题.case.json"
        output_10 = materials / "10_主题_含下划线.case.json"
        self.assertTrue(output_2.is_file())
        self.assertTrue(output_10.is_file())
        cases = load_cases(output_10)
        self.assertEqual(cases[0].case_id, "case-materials-010")
        self.assertIn("主题_含下划线", cases[0].prompt)
        self.assertEqual(
            cases[0].input_files[0].source.resolve(),
            source_10.resolve(),
        )
        payload = json.loads(output_2.read_text(encoding="utf-8"))
        metadata = payload["cases"][0]["metadata"]
        self.assertEqual(metadata["source_index"], 2)
        self.assertEqual(metadata["intake_status"], "neutral")
        self.assertEqual(
            payload["cases"][0]["input_files"][0]["source"],
            "./2_第二个主题.md",
        )

    def test_public_workflow_dispatches_prepare(self) -> None:
        materials = self.root / "materials"
        self._write_material(materials, "1_测试主题.md")

        result = workflow_main(
            [
                "prepare",
                "generate",
                "--materials-dir",
                str(materials),
                "--dry-run",
            ]
        )

        self.assertEqual(result, 0)
        self.assertFalse((materials / "1_测试主题.case.json").exists())

    def test_generate_applies_reviewed_intake_override(self) -> None:
        materials = self.root / "materials"
        self._write_material(materials, "1_测试主题.md")
        overrides = self.root / "overrides.json"
        overrides.write_text(
            json.dumps(
                {
                    "1": {
                        "research_background": "具体研究背景。",
                        "hypo": "具体研究假设。",
                        "report_pages": 2,
                        "metadata": {"industry": "测试行业"},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        main(
            [
                "generate",
                "--materials-dir",
                str(materials),
                "--intake-overrides",
                str(overrides),
                "--intake-mode",
                "strict",
            ]
        )

        payload = json.loads(
            (materials / "1_测试主题.case.json").read_text(encoding="utf-8")
        )
        case = payload["cases"][0]
        self.assertEqual(case["metadata"]["intake_status"], "reviewed")
        self.assertEqual(case["metadata"]["industry"], "测试行业")
        self.assertIn("具体研究背景。", case["turns"][1]["prompt"])
        self.assertIn("具体研究假设。", case["turns"][1]["prompt"])
        self.assertIn(
            "4. 报告篇幅：控制在2页以内。", case["turns"][1]["prompt"]
        )
        self.assertNotIn("1000个中文", case["turns"][1]["prompt"])
        self.assertNotIn("2000字", case["turns"][1]["prompt"])
        self.assertEqual(case["delivery_constraints"]["max_pages"], 2)
        self.assertEqual(case["delivery_constraints"]["max_chars"], 2000)

    def test_strict_mode_rejects_missing_intake(self) -> None:
        materials = self.root / "materials"
        self._write_material(materials, "1_测试主题.md")

        with self.assertRaisesRegex(CaseDatasetError, "research_background"):
            main(
                [
                    "generate",
                    "--materials-dir",
                    str(materials),
                    "--intake-mode",
                    "strict",
                ]
            )
        self.assertFalse((materials / "1_测试主题.case.json").exists())

    def test_generate_supports_unnumbered_non_ascii_filenames(self) -> None:
        materials = self.root / "materials"
        self._write_material(materials, "没有编号的主题.md")

        main(
            [
                "generate",
                "--materials-dir",
                str(materials),
                "--filename-regex",
                r"(?P<topic>.+)",
            ]
        )

        case = load_cases(materials / "没有编号的主题.case.json")[0]
        self.assertRegex(case.case_id, r"^case-materials-[0-9a-f]{10}$")

    def test_generate_supports_one_directory_per_case(self) -> None:
        materials = self.root / "materials"
        case_materials = materials / "1_目录型主题"
        self._write_material(case_materials, "source-a.md")
        self._write_material(case_materials, "source-b.md")

        main(
            [
                "generate",
                "--materials-dir",
                str(materials),
                "--source-kind",
                "directory",
            ]
        )

        output = materials / "1_目录型主题.case.json"
        case = load_cases(output)[0]
        self.assertEqual(case.input_files[0].source.resolve(), case_materials.resolve())
        self.assertEqual(case.input_files[0].target, "materials")
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["cases"][0]["metadata"]["source_kind"], "directory")

    def test_extract_pptx_human_report_preserves_slide_numbers(self) -> None:
        pptx = self.root / "human_report.pptx"
        with zipfile.ZipFile(pptx, "w") as archive:
            archive.writestr(
                "ppt/slides/slide2.xml",
                (
                    '<p:sld xmlns:p="p" xmlns:a="a"><a:t>'
                    "第二页研究问题"
                    "</a:t></p:sld>"
                ),
            )
            archive.writestr(
                "ppt/slides/slide1.xml",
                (
                    '<p:sld xmlns:p="p" xmlns:a="a"><a:t>'
                    "第一页项目背景"
                    "</a:t></p:sld>"
                ),
            )

        text, pages = _extract_pptx_text(
            pptx,
            minimum_characters=10,
        )

        self.assertEqual(pages, 2)
        self.assertLess(text.index("第一页项目背景"), text.index("第二页研究问题"))
        self.assertIn("===== PAGE 1 =====", text)
        self.assertIn("===== PAGE 2 =====", text)

    def test_project_source_copy_can_update_read_only_destination(self) -> None:
        source = self.root / "source"
        destination = self.root / "destination"
        source.mkdir()
        source_file = source / "readonly.docx"
        source_file.write_text("version one", encoding="utf-8")
        _copy_project_source(source, destination)
        destination_file = destination / "readonly.docx"
        destination_file.chmod(0o444)
        source_file.write_text("version two", encoding="utf-8")

        _copy_project_source(source, destination)

        self.assertEqual(
            destination_file.read_text(encoding="utf-8"),
            "version two",
        )
        self.assertTrue(destination_file.stat().st_mode & 0o200)

    def test_generate_can_infer_intake_from_human_report_with_codex(self) -> None:
        materials = self.root / "materials"
        human_report = self.root / "human_report"
        self._write_material(materials / "2024", "1_测试主题.md")
        pdf = human_report / "2024" / "1_测试主题.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b"%PDF-fake")
        fake_pdftotext = self._write_executable(
            "fake-pdftotext",
            """#!/usr/bin/env python3
import sys
sys.stdout.write("第一页研究背景和待解决问题。\\f第二页研究框架和待验证问题。\\f")
""",
        )
        counter = self.root / "codex-counter.txt"
        fake_codex = self._write_executable(
            "fake-codex",
            f"""#!/usr/bin/env python3
import json
import sys
from pathlib import Path
if "--version" in sys.argv:
    print("codex-cli test")
    raise SystemExit(0)
counter = Path({str(counter)!r})
value = int(counter.read_text() or "0") + 1 if counter.exists() else 1
counter.write_text(str(value))
output = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
output.write_text(json.dumps({{
    "research_background": "这是从报告还原的研究背景。",
    "hypo": "需要验证核心业务判断。",
    "hypo_type": "reconstructed",
    "confidence": "medium",
    "evidence": [{{"page": 1, "basis": "标题和议程"}}],
    "leakage_risk": "low",
    "notes": ""
}}, ensure_ascii=False), encoding="utf-8")
""",
        )
        cache = self.root / "intake-cache.json"
        arguments = [
            "generate",
            "--materials-dir",
            str(materials),
            "--recursive",
            "--human-report-dir",
            str(human_report),
            "--intake-cache",
            str(cache),
            "--codex-cli",
            str(fake_codex),
            "--pdftotext-cli",
            str(fake_pdftotext),
            "--minimum-extracted-characters",
            "10",
            "--force",
        ]

        main(arguments)
        main(arguments)

        output = materials / "2024" / "1_测试主题.case.json"
        payload = json.loads(output.read_text(encoding="utf-8"))
        case = payload["cases"][0]
        self.assertEqual(case["metadata"]["intake_status"], "codex_inferred")
        self.assertEqual(case["metadata"]["hypo_type"], "reconstructed")
        self.assertIn("这是从报告还原的研究背景。", case["turns"][1]["prompt"])
        self.assertIn("需要验证核心业务判断。", case["turns"][1]["prompt"])
        self.assertEqual(counter.read_text(encoding="utf-8"), "1")
        cached = json.loads(cache.read_text(encoding="utf-8"))
        self.assertEqual(
            cached["cases"]["case-materials-001"]["_inference"]["status"],
            "success",
        )

    def test_merge_rebases_paths_and_materializes_defaults(self) -> None:
        collection_a = self.root / "collection-a"
        collection_b = self.root / "collection-b"
        source_a = self._write_material(collection_a, "1_主题A.md")
        source_b = self._write_material(collection_b, "2_主题B.md")
        main(["generate", "--materials-dir", str(collection_a)])
        main(["generate", "--materials-dir", str(collection_b)])
        output = self.root / "datasets" / "mixed.json"

        result = main(
            [
                "merge",
                "--input",
                str(collection_a),
                "--input",
                str(collection_b),
                "--output",
                str(output),
            ]
        )

        self.assertEqual(result, 0)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertNotIn("defaults", payload)
        self.assertEqual(len(payload["cases"]), 2)
        self.assertEqual(payload["cases"][0]["skills"], ["research-report"])
        loaded = load_cases(output)
        self.assertEqual(
            {case.input_files[0].source.resolve() for case in loaded},
            {source_a.resolve(), source_b.resolve()},
        )

    def test_merge_can_select_source_indexes(self) -> None:
        materials = self.root / "materials"
        for index in range(1, 5):
            self._write_material(materials, f"{index}_主题{index}.md")
        main(["generate", "--materials-dir", str(materials)])
        output = self.root / "selected.json"

        main(
            [
                "merge",
                "--input",
                str(materials),
                "--include",
                "1,3-4",
                "--output",
                str(output),
            ]
        )

        payload = json.loads(output.read_text(encoding="utf-8"))
        indexes = [
            item["metadata"]["source_index"] for item in payload["cases"]
        ]
        self.assertEqual(indexes, [1, 3, 4])

    def test_merge_rejects_duplicate_case_ids(self) -> None:
        materials = self.root / "materials"
        self._write_material(materials, "1_主题.md")
        main(["generate", "--materials-dir", str(materials)])
        case_file = materials / "1_主题.case.json"

        with self.assertRaisesRegex(CaseDatasetError, "case ID 重复"):
            main(
                [
                    "merge",
                    "--input",
                    str(case_file),
                    "--manifest",
                    str(self._duplicate_manifest(case_file)),
                    "--output",
                    str(self.root / "merged.json"),
                ]
            )

    def _duplicate_manifest(self, case_file: Path) -> Path:
        duplicate = self.root / "duplicate.case.json"
        duplicate.write_text(case_file.read_text(encoding="utf-8"), encoding="utf-8")
        manifest = self.root / "manifest.txt"
        manifest.write_text(
            f"{case_file}\n{duplicate}\n",
            encoding="utf-8",
        )
        return manifest


if __name__ == "__main__":
    unittest.main()
