# -*- coding: utf-8 -*-
"""生产 Skill 与 Harness 评测元数据之间的单向边界。"""

from __future__ import annotations

import re
from typing import Any, Dict, List


# 这些文本是给报告生成模型执行的内容规则，不包含 check id、分数、权重或 Gate。
_CHECK_INSTRUCTIONS = {
    "T1": "每个事实、数据和结论都必须有原始素材支撑并可核验；不得补写素材中不存在的新事实，正文无需展示内部来源编号。",
    "T2": "转述、总结和改写必须保持素材原意，不得改变主体、方向、范围、条件、统计口径或确定性。",
    "T3": "素材存在事实或口径冲突时必须明确说明，不得混用为一致结论，也不得无说明地只取有利一方。",
    "T4": "访谈、个案或单一模态只能支持有限范围的机制判断，应降低确定性并说明边界；权威原始记录可直接陈述其明确事实。",
    "T5": "素材不足以回答的问题不得强行作答；若影响决策，应说明当前能确认和不能确认的判断以及所需验证。",
    "T6": "口径依赖的数字、比例、频率和区间应按理解需要说明对象、分子分母、时间范围、基准、单位及必要敏感性。",
    "S1": "摘要不超过三条，每条直接表达可独立成立的核心判断，并按对管理层决策的重要性排序。",
    "S4": "正文必须逐项展开摘要中的核心结论，避免摘要与正文脱节。",
    "S5": "每章通过标题或首句先表达中心判断，再提供事实、数据和案例支撑。",
    "N2": "全文围绕一个明确的研究或决策问题展开，章节之间形成因果、递进或分析步骤上的承接。",
    "N4": "同一概念、指标、人群、统计口径和时间范围在全文保持一致；发生变化时明确说明。",
    "N5": "呈现素材中真实存在且影响决策的差异、矛盾、反常、取舍或约束，并说明其重要性；不得人为制造冲突。",
    "I1": "从案例和素材中抽象出模式、共性或规律，不停留在逐条罗列。",
    "I2": "需要解释原因时，应形成有素材支撑且具有解释增量的原因或机制判断，不复述现象。",
    "I3": "趋势、未来判断和风险必须经过校准，必要时标明置信程度；异常或离群点须经交叉验证或情境解释。",
    "I4": "建议必须针对已识别的问题或机制，能够回溯到前文证据，并说明动作为什么有效；避免空泛建议。",
    "V1": "回答素材能够支持的关键问题，并让分析深度匹配其决策重要性；不可回答的问题明确边界，不自行补答案。",
    "V2": "用户指定的全部交付模块都必须存在并包含实质内容。",
    "V3": "用户指定的关键事实和结论都必须在报告中得到覆盖。",
    "E1": "直接表达有决策价值的结论，删除铺垫和重复；一段或一条要点原则上只承载一个核心意思，多层信息拆分呈现。",
    "E2": "关键数据和多组对比优先使用 Markdown 表格或清晰图示，不埋在长段文字中。",
    "E3": "表图应明确标题、对象、指标、时间、单位、口径及必要图例注释；缺失值说明状态和原因，读者无需反复回看正文即可理解。",
    "E4": "遵守用户确认的页数或字数上限；未指定时控制在三页或三千字以内，压缩时不得删除关键事实、口径或限制条件。",
    "E5": "禁用“不是……而是……”式对举句和没有具体事实、动作或机制的术语空话；必要术语应承载明确含义。",
    "E6": "结论、条件、范围、比例、区间、频率、指代和归属必须无歧义；定义必要术语，并把重要不确定性表达为判断边界、决策影响和验证动作。",
}


_FORBIDDEN_PATTERNS = (
    ("rubric", re.compile(r"\brubric\b", re.I)),
    ("OpenHarness", re.compile(r"OpenHarness", re.I)),
    ("champion", re.compile(r"\bchampion\b", re.I)),
    ("holdout", re.compile(r"\bholdout\b", re.I)),
    ("overall", re.compile(r"\boverall\b", re.I)),
    ("Judge", re.compile(r"\bjudge\b", re.I)),
    ("Gate", re.compile(r"\bgate\b", re.I)),
    (
        "check_id",
        re.compile(
            r"check_id|目标\s*check|\bcheck\b|"
            r"(?<![A-Za-z0-9-])(?:T[1-6]|S[145]|N[245]|I[1-4]|V[1-3]|E[1-6])"
            r"(?![A-Za-z0-9-])",
            re.I,
        ),
    ),
    ("红线术语", re.compile(r"红线")),
    ("评分权重", re.compile(r"评分权重|维度权重|按.{0,20}权重.{0,20}综合")),
    (
        "评分机制",
        re.compile(
            r"(?:1\s*[—–-]\s*5|一\s*[—–-]\s*五)\s*分|"
            r"评分(?:方法|标准|说明|规则|体系|标尺|结果|目标)|"
            r"(?:综合|维度|质量)(?:评分|得分)|按.{0,20}评分"
        ),
    ),
    ("采纳策略", re.compile(r"候选(?:稿|版本).{0,50}(?:采纳|拒绝|回退)|(?:采纳|拒绝).{0,50}候选(?:稿|版本)")),
    ("失败计数", re.compile(r"红线失败数|硬门槛失败数|新增.{0,10}红线失败")),
    ("评测系统", re.compile(r"评测(?:系统|平台|规则|指标|结果|流程)|自动评分|人工与自动评分|导入报告文本")),
    ("数值化质量目标", re.compile(r"建议质量目标：|综合质量.{0,10}(?:不低于|至少|目标).{0,10}\d")),
)


def requirement_for_check(rubric: Dict[str, Any], check_id: str) -> str:
    """把 Harness check 投影成无评测术语的生产执行规则。"""
    normalized = str(check_id or "")
    if normalized in _CHECK_INSTRUCTIONS:
        return _CHECK_INSTRUCTIONS[normalized]
    for dimension in rubric.get("dimensions", []):
        for check in dimension.get("checks", []):
            if str(check.get("id") or "") == normalized:
                text = str(check.get("desc") or check.get("label") or "").strip()
                validate_production_text(text)
                return text
    return ""


def quality_requirements(rubric: Dict[str, Any]) -> List[Dict[str, Any]]:
    """生成给写作模型的去标识化内容要求；不暴露权重、分数、ID 或 Gate。"""
    result = []
    for dimension in rubric.get("dimensions", []):
        requirements = []
        mandatory = []
        for check in dimension.get("checks", []):
            instruction = requirement_for_check(rubric, str(check.get("id") or ""))
            if not instruction:
                continue
            requirements.append(instruction)
            if check.get("redline"):
                mandatory.append(instruction)
        if requirements:
            result.append({
                "area": dimension.get("name_zh") or dimension.get("name") or "质量要求",
                "requirements": requirements,
                "mandatory_requirements": mandatory,
            })
    return result


def mandatory_requirements(rubric: Dict[str, Any]) -> List[str]:
    return [
        requirement_for_check(rubric, str(check.get("id") or ""))
        for dimension in rubric.get("dimensions", [])
        for check in dimension.get("checks", [])
        if check.get("redline")
    ]


def forbidden_metadata_hits(text: str) -> List[str]:
    source = str(text or "")
    return [label for label, pattern in _FORBIDDEN_PATTERNS if pattern.search(source)]


def validate_production_text(text: str) -> None:
    hits = forbidden_metadata_hits(text)
    if hits:
        raise ValueError(
            "生产 Skill 不得包含 Harness 评测元数据: " + ", ".join(hits)
        )


def _strip_evaluation_sections(text: str) -> str:
    lines = str(text or "").splitlines()
    output = []
    skipped_level = None
    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if skipped_level is not None:
            if heading and len(heading.group(1)) <= skipped_level:
                skipped_level = None
            else:
                continue
        if heading and re.search(
            r"评分|评测|交付\s*Gate|Evaluation|Scor(?:e|ing)|候选采纳",
            heading.group(2),
            re.I,
        ):
            skipped_level = len(heading.group(1))
            continue
        output.append(line)
    return "\n".join(output)


def sanitize_legacy_production_text(text: str) -> str:
    """清理历史版本曾写入的评分章节，供旧 Session 无损迁移到生产边界。"""
    cleaned = _strip_evaluation_sections(text)
    cleaned = re.sub(
        r"^(#{2,6}\s+)[TSNIVE]\d+\s*[｜|]\s*",
        r"\1",
        cleaned,
        flags=re.MULTILINE,
    )
    cleaned = cleaned.replace("【红线】", "【必须遵守】")
    cleaned = re.sub(
        r"命中禁用句式或明显术语注水即判定为红线失败[；;，,]?.*?(?=\n|$)",
        "不得使用上述禁用句式或术语空话。",
        cleaned,
    )
    cleaned = cleaned.replace("事实红线", "事实边界")
    cleaned = cleaned.replace("红线义务", "强制义务")
    cleaned = cleaned.replace("红线条款", "强制条款")
    cleaned = cleaned.replace("红线", "强制要求")
    cleaned = re.sub(r"按\s*T\d+\s*留白", "按上述证据边界留白", cleaned)
    cleaned = re.sub(
        r"数值真实性、转述忠实和统计口径分别由\s*T\d+(?:/T\d+)+\s*判断。?",
        "",
        cleaned,
    )
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", cleaned):
        if not paragraph.strip():
            continue
        if forbidden_metadata_hits(paragraph):
            continue
        paragraphs.append(paragraph.strip())
    cleaned = "\n\n".join(paragraphs).strip()
    validate_production_text(cleaned)
    return cleaned
