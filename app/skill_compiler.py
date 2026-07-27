# -*- coding: utf-8 -*-
"""把 Session SkillArtifact 编译成不可变的 WB CLI Skill 目录。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from directive_registry import executable_directive_text


COMPILER_VERSION = "session-skill/v1"


@dataclass(frozen=True)
class FrozenSkill:
    path: Path
    artifact_hash: str
    directory_hash: str
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


def _render_flow(skill) -> List[str]:
    lines = []
    for step in (skill.structure or {}).get("flow", []):
        details = []
        if step.get("subagent"):
            details.append("执行角色：%s" % step["subagent"])
        if step.get("produces"):
            details.append("产物：%s" % step["produces"])
        if step.get("rule"):
            details.append("规则：%s" % step["rule"])
        if step.get("loop_back_to") is not None:
            details.append("不通过时回到步骤 %s" % step["loop_back_to"])
        suffix = "；" + "；".join(details) if details else ""
        lines.append(
            "- **%s. %s**%s"
            % (step.get("step", ""), step.get("name", ""), suffix)
        )
    return lines


def _render_agents(skill) -> List[str]:
    lines = []
    for agent in (skill.structure or {}).get("subagents", []):
        suffix = "；必须独立对抗核验" if agent.get("independent") else ""
        lines.append(
            "- **%s**：%s%s"
            % (agent.get("name", ""), agent.get("responsibility", ""), suffix)
        )
    return lines


def render_skill_markdown(session_id: str, skill, artifact_hash: str) -> str:
    directives = _enabled_directives(skill)
    config = (skill.memory_content or {}).get("config", {})
    sections = config.get("required_sections") or []
    lines = [
        "---",
        "name: research-report",
        "description: OpenHarness Session %s 的冻结报告生成 Skill，版本 %s。"
        % (session_id, skill.version),
        "---",
        "",
        "# OpenHarness Session Skill · %s/%s" % (session_id, skill.version),
        "",
        "## 冻结身份",
        "",
        "- Session：`%s`" % session_id,
        "- Skill version：`%s`" % skill.version,
        "- Parent：`%s`" % (skill.parent_version or "none"),
        "- Artifact SHA-256：`%s`" % artifact_hash,
        "- Compiler：`%s`" % COMPILER_VERSION,
        "- Changelog：%s" % (skill.changelog or "无"),
        "",
        "不得读取 OpenHarness Rubric、ground truth、Judge 结果或其他版本 Skill 来补强本版。",
        "",
        "## 必读指令",
        "",
        "开始分析前必须完整读取 `references/instructions.md`。"
        "`references/session_artifact.json` 只用于版本审计。",
        "",
        "## 角色与目标",
        "",
        skill.instructions.get("prose", ""),
        "",
        "## 固定执行流程",
        "",
    ]
    lines.extend(_render_flow(skill))
    lines.extend(["", "## 角色分工", ""])
    lines.extend(_render_agents(skill) or ["- 按固定执行流程完成各角色职责。"])
    lines.extend(["", "## 当前版本已启用的优化指令", ""])
    if directives:
        lines.extend("- **%s**：%s" % item for item in directives)
    else:
        lines.append("- 当前版本没有启用额外 directive。")
    if skill.few_shots:
        lines.extend(["", "## Few-shot 约束", ""])
        for item in skill.few_shots:
            kind = item.get("kind", "unknown") if isinstance(item, dict) else item
            lines.append("- `%s`" % kind)
    if sections:
        lines.extend(["", "## 必需章节", ""])
        lines.extend("- %s" % section for section in sections)
    lines.extend([
        "",
        "## 交付",
        "",
        "将最终 Markdown 报告写入 `deliverables/report.md`，确认完整落盘后再结束。",
        "",
    ])
    return "\n".join(lines)


def render_instructions_markdown(
    session_id: str,
    skill,
    artifact_hash: str,
) -> str:
    directives = _enabled_directives(skill)
    config = (skill.memory_content or {}).get("config", {})
    sections = config.get("required_sections") or []
    audiences = config.get("audience_profiles") or {}
    lines = [
        "# 调研洞察汇报 · 完整执行指令",
        "",
        "本文件由 OpenHarness 从冻结 SkillArtifact 编译生成，执行时必须完整遵守。",
        "",
        "## 版本边界",
        "",
        "- Session：`%s`" % session_id,
        "- Skill version：`%s`" % skill.version,
        "- Artifact SHA-256：`%s`" % artifact_hash,
        "- 只允许使用本文件明确列出的规则，不得读取评测信息或其他版本补强本版。",
        "",
        "## 任务目标",
        "",
        skill.instructions.get("prose", ""),
        "",
        "最终交付是基于当前 case 素材形成的高管调研洞察报告，"
        "不是素材摘要、分析过程、评分说明或规则复述。",
        "",
        "## 固定执行流程",
        "",
    ]
    lines.extend(_render_flow(skill))
    lines.extend(["", "## 角色责任", ""])
    lines.extend(_render_agents(skill) or ["- 按固定执行流程完成各角色职责。"])
    lines.extend(["", "## 本版本生效的写作与核验规则", ""])
    if directives:
        for index, (name, text) in enumerate(directives, 1):
            lines.append("%s. **%s**：%s" % (index, name, text))
    else:
        lines.append("- 本版本没有启用额外 directive。")
    lines.extend(["", "## 报告结构", ""])
    if sections:
        for index, section in enumerate(sections, 1):
            lines.append("%s. **%s**" % (index, section))
    else:
        lines.append("按任务输入要求组织必要章节。")
    if audiences:
        lines.extend([
            "",
            "## 受众与篇幅",
            "",
            "```json",
            json.dumps(audiences, ensure_ascii=False, indent=2),
            "```",
        ])
    lines.extend([
        "",
        "## 交付前核验",
        "",
        "- 逐项核对事实、数字、口径和结论来自当前 workspace 素材。",
        "- 删除编造、曲解、无据补全和越界外推；素材不足时明确留白。",
        "- 检查结构、启用指令、受众和篇幅要求均已满足。",
        "",
        "## 最终交付",
        "",
        "将完整 Markdown 报告写入 `deliverables/report.md`，完整落盘后再结束。",
        "",
    ])
    return "\n".join(lines)


def compile_session_skill(
    output_root: Path,
    session_id: str,
    skill,
) -> FrozenSkill:
    artifact = skill.to_dict()
    artifact_hash = _json_hash(artifact)
    final_dir = (
        output_root.expanduser().resolve()
        / "_session_skills"
        / _safe_segment(session_id)
        / _safe_segment(skill.version)
        / artifact_hash[:12]
        / "research-report"
    )
    parent = final_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".compile-", dir=str(parent)))
    try:
        references = temp_dir / "references"
        references.mkdir(parents=True)
        (temp_dir / "SKILL.md").write_text(
            render_skill_markdown(session_id, skill, artifact_hash),
            encoding="utf-8",
        )
        (references / "instructions.md").write_text(
            render_instructions_markdown(session_id, skill, artifact_hash),
            encoding="utf-8",
        )
        audit = {
            "compiler_version": COMPILER_VERSION,
            "session_id": session_id,
            "skill_version": skill.version,
            "skill_artifact_hash": artifact_hash,
            "skill": artifact,
        }
        (references / "session_artifact.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
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
            compiler_version=COMPILER_VERSION,
        )
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
