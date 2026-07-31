# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
HARNESS = APP.parent / "harness"
for path in (str(APP), str(HARNESS)):
    if path not in sys.path:
        sys.path.insert(0, path)

from directive_registry import RESEARCH_DIRECTIVES  # noqa: E402
from schemas import SkillArtifact  # noqa: E402
from skill_compiler import compile_session_skill  # noqa: E402


def _skill():
    return SkillArtifact(
        id="research_insight",
        version="v0",
        parent_version=None,
        structure={},
        instructions={
            "prose": "",
            "directives": {
                directive_id: directive_id == "summary_format"
                for directive_id in RESEARCH_DIRECTIVES
            },
        },
        few_shots=[],
        memory_content={},
        changelog="base",
    )


class SkillCompilerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.base = self.root / "base" / "research-report"
        references = self.base / "references"
        references.mkdir(parents=True)
        (self.base / "SKILL.md").write_text(
            "---\nname: research-report\n---\n# Base Skill\n",
            encoding="utf-8",
        )
        (self.base / "DRAFT_from_old_version.md").write_text(
            "历史草稿，不应进入执行目录。\n",
            encoding="utf-8",
        )
        (references / "instructions.md").write_text(
            "# Base Instructions\n\n"
            "<!-- OPENHARNESS_DIRECTIVES: [\"summary_format\"] -->\n\n"
            "摘要最多三条。\n\n"
            "<!-- OPENHARNESS_VERSION_RULES_START -->\n"
            "<!-- 当前基线没有 optimizer 增量规则。 -->\n"
            "<!-- OPENHARNESS_VERSION_RULES_END -->\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_compile_is_deterministic(self):
        skill = _skill()
        skill.directives()["require_source_ref"] = True
        first = compile_session_skill(
            self.root / "runs",
            "real-eval",
            skill,
            self.base,
        )
        second = compile_session_skill(
            self.root / "runs",
            "real-eval",
            skill,
            self.base,
        )

        self.assertEqual(first, second)
        self.assertEqual(
            (first.path / "SKILL.md").read_text(encoding="utf-8"),
            (self.base / "SKILL.md").read_text(encoding="utf-8"),
        )
        self.assertFalse(
            (first.path / "DRAFT_from_old_version.md").exists()
        )
        instructions = (
            first.path / "references" / "instructions.md"
        ).read_text(encoding="utf-8")
        self.assertIn("require_source_ref", instructions)
        self.assertIn("回溯到真实素材", instructions)

    def test_different_versions_apply_cumulative_directives(self):
        v0 = _skill()
        v1 = v0.clone_with_directive(
            "require_source_ref",
            True,
            "v1",
            "启用可回溯性规则",
        )
        frozen_v0 = compile_session_skill(
            self.root / "runs",
            "real-eval",
            v0,
            self.base,
        )
        frozen_v1 = compile_session_skill(
            self.root / "runs",
            "real-eval",
            v1,
            self.base,
        )

        self.assertNotEqual(frozen_v0.path, frozen_v1.path)
        self.assertNotIn(
            "**require_source_ref**",
            (
                frozen_v0.path / "references" / "instructions.md"
            ).read_text(encoding="utf-8"),
        )
        self.assertIn(
            "**require_source_ref**",
            (
                frozen_v1.path / "references" / "instructions.md"
            ).read_text(encoding="utf-8"),
        )

    def test_unknown_or_forbidden_enabled_directive_is_rejected(self):
        unknown = _skill()
        unknown.directives()["not_registered"] = True
        with self.assertRaisesRegex(ValueError, "未知"):
            compile_session_skill(
                self.root / "runs",
                "real-eval",
                unknown,
                self.base,
            )

        forbidden = _skill()
        forbidden.directives()["buzzword_emphasis"] = True
        with self.assertRaisesRegex(ValueError, "禁止"):
            compile_session_skill(
                self.root / "runs",
                "real-eval",
                forbidden,
                self.base,
            )


if __name__ == "__main__":
    unittest.main()
