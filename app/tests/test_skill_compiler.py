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

from generator import generate_v0  # noqa: E402
from schemas import SkillArtifact  # noqa: E402
from skill_compiler import compile_session_skill  # noqa: E402


def _skill():
    generated = generate_v0(
        "生成面向高管的调研洞察报告",
        "research_insight",
    )
    return SkillArtifact.from_dict(generated["skill"])


class SkillCompilerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_compile_is_deterministic_and_contains_only_skill_artifact(self):
        skill = _skill()
        skill.directives()["require_source_ref"] = True
        first = compile_session_skill(self.root, "real-eval", skill)
        second = compile_session_skill(self.root, "real-eval", skill)

        self.assertEqual(first, second)
        instructions = (
            first.path / "references" / "instructions.md"
        ).read_text(encoding="utf-8")
        self.assertIn("require_source_ref", instructions)
        self.assertIn("回溯到真实素材", instructions)
        audit = json.loads(
            (
                first.path
                / "references"
                / "session_artifact.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(audit["skill_artifact_hash"], first.artifact_hash)
        serialized = json.dumps(audit, ensure_ascii=False).lower()
        self.assertNotIn("ground_truth", serialized)
        self.assertNotIn("rubric", serialized)
        self.assertNotIn("judge", serialized)

    def test_different_versions_compile_to_different_frozen_skills(self):
        v0 = _skill()
        v1 = v0.clone_with_directive(
            "require_source_ref",
            True,
            "v1",
            "启用可回溯性规则",
        )
        frozen_v0 = compile_session_skill(self.root, "real-eval", v0)
        frozen_v1 = compile_session_skill(self.root, "real-eval", v1)

        self.assertNotEqual(
            frozen_v0.artifact_hash,
            frozen_v1.artifact_hash,
        )
        self.assertNotEqual(
            frozen_v0.directory_hash,
            frozen_v1.directory_hash,
        )
        self.assertNotEqual(frozen_v0.path, frozen_v1.path)
        self.assertNotIn(
            "require_source_ref",
            (
                frozen_v0.path / "references" / "instructions.md"
            ).read_text(encoding="utf-8"),
        )
        self.assertIn(
            "require_source_ref",
            (
                frozen_v1.path / "references" / "instructions.md"
            ).read_text(encoding="utf-8"),
        )

    def test_unknown_or_forbidden_enabled_directive_is_rejected(self):
        unknown = _skill()
        unknown.directives()["not_registered"] = True
        with self.assertRaisesRegex(ValueError, "未知"):
            compile_session_skill(self.root, "real-eval", unknown)

        forbidden = _skill()
        forbidden.directives()["buzzword_emphasis"] = True
        with self.assertRaisesRegex(ValueError, "禁止"):
            compile_session_skill(self.root, "real-eval", forbidden)


if __name__ == "__main__":
    unittest.main()
