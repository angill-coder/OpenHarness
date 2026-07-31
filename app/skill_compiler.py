# -*- coding: utf-8 -*-
"""从唯一基础 Skill 复制并编译 Session 的 directive 增量。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Set, Tuple

from directive_registry import (
    DIRECTIVE_MANIFEST_RE,
    EDITABLE_END,
    EDITABLE_REGION_RE,
    EDITABLE_START,
    VERSION_RULES_END,
    VERSION_RULES_START,
    directive_manifest,
    executable_directive_text,
    load_skill_directives,
)


COMPILER_VERSION = "session-skill/v3"


@dataclass(frozen=True)
class FrozenSkill:
    path: Path
    artifact_hash: str
    directory_hash: str
    base_skill_hash: str
    compiler_version: str


def _json_hash(value) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def directory_hash(path: Path) -> str:
    root = path.expanduser().resolve()
    digest = hashlib.sha256()
    for item in sorted(
        candidate
        for candidate in root.rglob("*")
        if candidate.is_file()
        and "__pycache__" not in candidate.parts
        and not candidate.name.startswith(".")
    ):
        digest.update(str(item.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _safe_segment(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-.")
    return clean or "unknown"


def _enabled_directives(skill) -> List[Tuple[str, str]]:
    result = []
    for directive_id, enabled in skill.directives().items():
        if enabled:
            result.append(
                (directive_id, executable_directive_text(directive_id))
            )
    return result


def _replace_directive_manifest(
    text: str,
    enabled: Set[str],
) -> str:
    if DIRECTIVE_MANIFEST_RE.search(text) is None:
        raise ValueError(
            "基础 Skill 的 instructions.md 缺少 OPENHARNESS_DIRECTIVES"
        )
    return DIRECTIVE_MANIFEST_RE.sub(
        directive_manifest(enabled),
        text,
        count=1,
    )


def _render_version_rules(
    additional: List[Tuple[str, str]],
    few_shots,
) -> str:
    lines = [VERSION_RULES_START]
    if additional:
        lines.extend(["", "## 当前版本新增优化规则", ""])
        lines.extend("- **%s**：%s" % item for item in additional)
    if few_shots:
        lines.extend(["", "## 当前版本 Few-shot 约束", ""])
        for item in few_shots:
            kind = (
                item.get("kind", "unknown")
                if isinstance(item, dict)
                else str(item)
            )
            lines.append("- `%s`" % kind)
    if not additional and not few_shots:
        lines.append("<!-- 当前基线没有 optimizer 增量规则。 -->")
    lines.append(VERSION_RULES_END)
    return "\n".join(lines)


def _replace_version_rules(
    text: str,
    additional: List[Tuple[str, str]],
    few_shots,
) -> str:
    pattern = re.compile(
        re.escape(VERSION_RULES_START)
        + r".*?"
        + re.escape(VERSION_RULES_END),
        re.DOTALL,
    )
    if pattern.search(text) is None:
        raise ValueError(
            "基础 Skill 的 instructions.md 缺少版本增量区域"
        )
    rendered = _render_version_rules(additional, few_shots)
    return pattern.sub(lambda _match: rendered, text, count=1)


def _replace_editable_region(
    text: str,
    prose: str,
    requirement_contract: str = "",
) -> str:
    """freeform 策略:把冻结需求契约 + LLM 正文写进 EDITABLE 区。

    requirement_contract 由 v0 基于需求与 rubric 确定，后续版本只改 prose，
    从结构上避免 LLM 优化时把受众、交互、输出结构或素材边界改丢。
    """
    if EDITABLE_REGION_RE.search(text) is None:
        raise ValueError(
            "基础 Skill 的 instructions.md 缺少 OPENHARNESS_EDITABLE 可编辑区"
        )
    parts = [
        part.strip("\n")
        for part in (requirement_contract, prose)
        if (part or "").strip()
    ]
    rendered = "%s\n%s\n%s" % (
        EDITABLE_START,
        "\n\n".join(parts),
        EDITABLE_END,
    )
    return EDITABLE_REGION_RE.sub(lambda _match: rendered, text, count=1)


def compile_session_skill(
    output_root: Path,
    session_id: str,
    skill,
    base_skill_path: Path,
) -> FrozenSkill:
    base = base_skill_path.expanduser().resolve()
    baseline_states = load_skill_directives(base)
    baseline_enabled = {
        directive_id
        for directive_id, enabled in baseline_states.items()
        if enabled
    }
    artifact_enabled = {
        directive_id
        for directive_id, _ in _enabled_directives(skill)
    }
    # 基线规则始终生效；版本只能在此基础上累积新规则。
    effective_enabled = baseline_enabled | artifact_enabled
    additional = [
        (directive_id, executable_directive_text(directive_id))
        for directive_id in skill.directives()
        if directive_id in effective_enabled
        and directive_id not in baseline_enabled
    ]

    artifact = skill.to_dict()
    artifact_hash = _json_hash(artifact)
    base_skill_hash = directory_hash(base)
    compiled_identity = _json_hash(
        {
            "artifact_hash": artifact_hash,
            "base_skill_hash": base_skill_hash,
            "compiler_version": COMPILER_VERSION,
        }
    )
    final_dir = (
        output_root.expanduser().resolve()
        / "_session_skills"
        / _safe_segment(session_id)
        / _safe_segment(skill.version)
        / compiled_identity[:12]
        / base.name
    )
    parent = final_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".compile-", dir=str(parent)))
    try:
        shutil.copytree(
            base,
            temp_dir,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                ".*",
                "__pycache__",
                "DRAFT*.md",
            ),
        )
        instructions = temp_dir / "references" / "instructions.md"
        text = instructions.read_text(encoding="utf-8")
        if skill.instructions.get("mode") == "freeform":
            # freeform 策略(optimizer02):LLM 整段改写可编辑区;
            # manifest/version_rules 保持基线原样,不做 per-directive 注入。
            text = _replace_editable_region(
                text,
                skill.instructions.get("prose", ""),
                skill.instructions.get("requirement_contract", ""),
            )
        else:
            text = _replace_directive_manifest(text, effective_enabled)
            text = _replace_version_rules(
                text,
                additional,
                skill.few_shots,
            )
        instructions.write_text(text, encoding="utf-8")

        expected_hash = directory_hash(temp_dir)
        if final_dir.exists():
            if directory_hash(final_dir) != expected_hash:
                raise ValueError(
                    "冻结 Skill 目录已存在但内容不一致: %s" % final_dir
                )
        else:
            temp_dir.replace(final_dir)
        return FrozenSkill(
            path=final_dir,
            artifact_hash=artifact_hash,
            directory_hash=expected_hash,
            base_skill_hash=base_skill_hash,
            compiler_version=COMPILER_VERSION,
        )
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
