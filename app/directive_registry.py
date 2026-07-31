# -*- coding: utf-8 -*-
"""调研报告 directive 的唯一可执行定义。

生成器、优化器产出的 directive ID 最终都由这里编译为 WB Skill 指令。
Rubric 只负责评测映射，不在报告生成链路中读取。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict


DIRECTIVE_MANIFEST_RE = re.compile(
    r"<!-- OPENHARNESS_DIRECTIVES: (\[.*?\]) -->"
)
VERSION_RULES_START = "<!-- OPENHARNESS_VERSION_RULES_START -->"
VERSION_RULES_END = "<!-- OPENHARNESS_VERSION_RULES_END -->"

# freeform 优化策略(optimizer02)的可编辑区标记。标记之间的正文由 LLM 整段
# 改写;标记之外(标题/directive 清单/开场输入/三段结构/版本增量区)属结构层,
# LLM 不可动。
EDITABLE_START = "<!-- OPENHARNESS_EDITABLE_START -->"
EDITABLE_END = "<!-- OPENHARNESS_EDITABLE_END -->"
EDITABLE_REGION_RE = re.compile(
    re.escape(EDITABLE_START) + r"(.*?)" + re.escape(EDITABLE_END),
    re.DOTALL,
)


RESEARCH_DIRECTIVE_DEFINITIONS: Dict[str, Dict[str, object]] = {
    "require_source_ref": {
        "text": "确保每个事实、数据和结论都能回溯到真实素材；正文可不展示内部来源编号。",
    },
    "flag_source_conflict": {
        "text": "主动识别并披露素材冲突，不得把冲突口径混成一个一致结论。",
    },
    "honest_on_unsupportable": {
        "text": "素材不足时明确写明证据不足并留白，不得强行回答。",
    },
    "require_two_sources": {
        "text": "只有至少两个独立信源支持时才作确定结论；单一信源必须标为待验证。",
    },
    "summary_format": {
        "text": "摘要最多三条 bullet，每条直接给结论并按重要性排序，且与正文严格对应。",
    },
    "pyramid_body": {
        "text": "正文采用金字塔结构，每章先给论点，再给证据和解释。",
    },
    "mece_sections": {
        "text": "章节应当 MECE，必需段落完整、有实质内容且不重复交叉。",
    },
    "concept_consistency": {
        "text": "同一概念、指标和统计口径在全文保持一致；切换口径时显式说明。",
    },
    "ensure_narrative_flow": {
        "text": "用一条清晰主线贯穿全文，章节按因果或递进关系自然衔接。",
    },
    "require_insight_triplet": {
        "text": "洞察必须包含有据的归因、标注置信度的趋势和可执行建议。",
    },
    "abstract_cases": {
        "text": "把案例抽象为模式或共性，不得逐条罗列代替洞察。",
    },
    "drop_noise": {
        "text": "识别并剔除噪音素材，不得引用噪音片段充数。",
    },
    "mark_extrapolation_confidence": {
        "text": "趋势判断必须标注置信度和适用边界，不得越界外推。",
    },
    "crosscheck_outliers": {
        "text": "一次性异常或离群点必须交叉验证或给出情境解释，不得直接当作趋势。",
    },
    "cover_key_claims": {
        "text": "逐项覆盖素材支持的关键问题和关键 claim，不得遗漏。",
    },
    "ban_bushi_ershi": {
        "text": "禁用“不是，而是”句式和术语堆砌式空话。",
    },
    "require_charts": {
        "text": "关键数据和对比必须用 Markdown 表格或合适的结构化形式呈现。",
    },
    "match_exec_length": {
        "text": "面向高管精炼表达，控制在约 1.5 页量级并删除注水内容。",
    },
    "require_rigorous_wording": {
        "text": "结论先行，限定条件清楚，措辞严谨且不模棱两可。",
    },
    "verify_no_fabrication": {
        "text": "交付前逐项对照素材核验事实、数字和结论，删除或改写任何编造、曲解或无据外推。",
    },
    "note_metric_caveat": {
        "text": "口径依赖的数字和结论必须注明统计口径，必要时给出敏感性说明。",
    },
    "disclose_sample_bias": {
        "text": "披露已知的样本代表性和选择偏差，不得把有偏样本当作客观总体。",
    },
    "buzzword_emphasis": {
        "text": "奖励术语密度的实验性负向指令。",
        "exportable": False,
        "forbidden": True,
    },
}

RESEARCH_DIRECTIVES = tuple(RESEARCH_DIRECTIVE_DEFINITIONS)


def directive_manifest(enabled) -> str:
    ordered = [
        directive_id
        for directive_id in RESEARCH_DIRECTIVES
        if directive_id in set(enabled)
    ]
    return "<!-- OPENHARNESS_DIRECTIVES: %s -->" % json.dumps(
        ordered,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def load_skill_directives(skill_path: Path) -> Dict[str, bool]:
    root = skill_path.expanduser().resolve()
    instructions = root / "references" / "instructions.md"
    if not (root / "SKILL.md").is_file():
        raise ValueError("基础 Skill 缺少 SKILL.md: %s" % root)
    if not instructions.is_file():
        raise ValueError(
            "基础 Skill 缺少 references/instructions.md: %s" % root
        )
    text = instructions.read_text(encoding="utf-8")
    match = DIRECTIVE_MANIFEST_RE.search(text)
    if match is None:
        raise ValueError(
            "基础 Skill 未声明 OPENHARNESS_DIRECTIVES: %s"
            % instructions
        )
    try:
        declared = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(
            "基础 Skill 的 OPENHARNESS_DIRECTIVES 不是合法 JSON"
        ) from exc
    if not isinstance(declared, list) or any(
        not isinstance(item, str) for item in declared
    ):
        raise ValueError(
            "基础 Skill 的 OPENHARNESS_DIRECTIVES 必须是字符串数组"
        )
    unknown = sorted(set(declared) - set(RESEARCH_DIRECTIVES))
    if unknown:
        raise ValueError(
            "基础 Skill 声明了未知 directive: %s"
            % ", ".join(unknown)
        )
    for directive_id in declared:
        executable_directive_text(directive_id)
    enabled = set(declared)
    return {
        directive_id: directive_id in enabled
        for directive_id in RESEARCH_DIRECTIVES
    }


def executable_directive_text(directive_id: str) -> str:
    definition = RESEARCH_DIRECTIVE_DEFINITIONS.get(directive_id)
    if definition is None:
        raise ValueError("未知的 research directive: %s" % directive_id)
    if definition.get("forbidden") or definition.get("exportable") is False:
        raise ValueError("禁止导出到执行 Skill 的 directive: %s" % directive_id)
    return str(definition["text"])


def load_editable_region(skill_path: Path) -> str:
    """读取基础 Skill 的 freeform 可编辑区正文(EDITABLE 标记之间,不含标记本身)。

    供 optimizer02(LLM 自由改写)取 v0 全文;缺标记则报错。
    """
    root = skill_path.expanduser().resolve()
    instructions = root / "references" / "instructions.md"
    if not instructions.is_file():
        raise ValueError(
            "基础 Skill 缺少 references/instructions.md: %s" % root
        )
    text = instructions.read_text(encoding="utf-8")
    match = EDITABLE_REGION_RE.search(text)
    if match is None:
        raise ValueError(
            "基础 Skill 未声明 OPENHARNESS_EDITABLE 可编辑区: %s" % instructions
        )
    return match.group(1).strip("\n")
