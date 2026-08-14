# -*- coding: utf-8 -*-
"""
generator.py — 从「需求描述」生成 v0 skill + rubric

对应产品流程: 下属在页面输入对产品的需求描述 -> 生成第一版 v0 skill(结构+指令+memory)
和一版 rubric(维度+权重+锚点+gate)。

两条路径:
  - 调研洞察产品: 固定读取仓库 `skills/research-report` 唯一基线，只生成
    与基线一致的 directive 版本状态和 rubric，不生成另一套 Skill 结构。
  - 离线启发式(默认): 关键词 -> 报告类型/受众 -> 套用「结构设计文档」的 v0 结构骨架,
    用于其它产品。
  - Claude(有 ANTHROPIC_API_KEY + sdk 时): 让模型按结构/rubric 方法论产出, 再对齐词汇。

设计约束: harness 的 MockBackend 只认一组固定 directive, judge 只认 4 个维度。所以生成器
产出的 v0 必须用这套词汇 —— 需求描述影响的是"产品名/报告类型/受众/初始打开哪些 directive/
维度权重", 而非发明新词汇。真实 Claude 后端接入后, 词汇可扩展。
"""
import json
import os
import re
import copy
from pathlib import Path
from typing import Any, Dict, List

import llm_client
import production_skill_policy
from model_config import (
    DEFAULT_V0_CODEX_MODEL,
    DEFAULT_V0_CODEX_REASONING_EFFORT,
    DEFAULT_V0_LLM_BACKEND,
)
from directive_registry import (
    RESEARCH_DIRECTIVES,
    load_editable_region,
    load_skill_directives,
)

# harness artifacts 目录(读六维 rubric 模板)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ART = os.path.join(_ROOT, "harness", "artifacts")

# harness 已知的 directive 动作空间(与 skill_v0.json 对齐)
KNOWN_DIRECTIVES = [
    "require_citation", "require_metric_definitions", "enforce_required_sections",
    "verifier_check_omissions", "require_attribution", "require_risk_and_next_step",
    "use_historical_baseline", "match_audience_length", "keyword_emphasis",
]

# 报告类型模板(与 skill_v0.json 的 report_templates 对齐)
REPORT_TEMPLATES = {
    "monthly_biz_review": {"kw": ["月报", "经营", "月度", "biz review"],
                           "sections": ["业绩概览", "关键指标", "异常与归因", "风险", "下一步"],
                           "audience": "exec", "tone": "concise"},
    "weekly_update": {"kw": ["周报", "本周", "weekly", "周度"],
                      "sections": ["本周进展", "关键指标", "阻塞与风险", "下周计划"],
                      "audience": "team", "tone": "detailed"},
    "ops_brief": {"kw": ["经营简报", "现金", "跑道", "runway", "ops"],
                  "sections": ["经营概览", "现金与跑道", "关键指标", "风险", "决策项"],
                  "audience": "exec", "tone": "concise"},
    "project_progress": {"kw": ["项目", "进展", "里程碑", "project", "progress"],
                         "sections": ["里程碑状态", "本期完成", "风险与阻塞", "下期计划"],
                         "audience": "team", "tone": "detailed"},
}

# 4 个质量维度(与 rubric.json 对齐)。生成器可按需求微调权重。
BASE_DIMENSIONS = [
    {"name": "data_accuracy", "name_zh": "数据准确性", "weight": 0.40, "hard_floor": 3, "is_reverse": False},
    {"name": "completeness", "name_zh": "完整性", "weight": 0.25, "hard_floor": None, "is_reverse": False},
    {"name": "insight", "name_zh": "洞察质量", "weight": 0.25, "hard_floor": None, "is_reverse": False},
    {"name": "conciseness", "name_zh": "简洁性", "weight": 0.10, "hard_floor": None, "is_reverse": True},
]

DIM_ANCHORS = {
    "data_accuracy": {
        "criteria": "每个数字/结论可回溯到 finding(带 source_ref);口径与计算正确;无编造数字。",
        "5": "全部可回溯、口径正确、无编造。", "4": "可回溯完整,仅1处非关键引用缺失。",
        "3": "1处关键数字无出处或口径错误。", "2": ">=1个编造数字,可信度受损。", "1": "大量无法回溯或编造。",
        "positive_example": "本月 ARR ¥1,240万,环比+8.3%[F-012],增长由新增贡献[F-014]…",
        "negative_example": "ARR突破1200万,同比约30%,满意度显著上升。(无出处/疑编造)"},
    "completeness": {
        "criteria": "required_sections 全覆盖且非空;关键 finding 无遗漏。",
        "5": "段落全覆盖,关键finding无遗漏。", "4": "段落全覆盖,1个次要finding未提。",
        "3": "缺1个段落或遗漏1个关键finding。", "2": "缺>=2段或遗漏多个关键finding。", "1": "大面积缺段。",
        "positive_example": "五段齐全,写入了华东区下滑异常。", "negative_example": "缺'风险'段,漏'现金跑道仅剩5月'。"},
    "insight": {
        "criteria": "含归因/风险/下一步三要素,判断落在findings内、非复读。",
        "5": "三要素俱全且有据,非平凡判断。", "4": "三要素齐,个别较泛。",
        "3": "有判断但偏表面。", "2": "基本数据复读。", "1": "纯数字罗列。",
        "positive_example": "拆分新增vs留存,识别华东风险,给排查+加固建议。", "negative_example": "各项向好,请继续保持。(复读)"},
    "conciseness": {
        "criteria": "无冗余、不堆术语;长度匹配受众。反向维度,防reward hacking。",
        "5": "无冗余,长度匹配受众。", "4": "基本精炼,个别可删。",
        "3": "一定冗余或轻度堆术语,或长度偏离。", "2": "明显冗长/术语堆砌。", "1": "充斥空话。",
        "positive_example": "exec版~200字,直击业绩/风险/决策项。", "negative_example": "满篇'数据驱动/闭环/赋能'。"},
}


def _detect_report_type(text: str) -> str:
    text_l = text.lower()
    best, best_hits = "monthly_biz_review", 0
    for rtype, meta in REPORT_TEMPLATES.items():
        hits = sum(1 for k in meta["kw"] if k.lower() in text_l)
        if hits > best_hits:
            best, best_hits = rtype, hits
    return best


def _detect_audience(text: str, default: str) -> str:
    t = text.lower()
    if any(k in t for k in ["高管", "老板", "ceo", "exec", "决策", "董事"]):
        return "exec"
    if any(k in t for k in ["团队", "同事", "team", "详细"]):
        return "team"
    return default


def _emphasis_weights(text: str) -> Dict[str, float]:
    """需求里若强调某方面, 微调维度权重(仍归一)。"""
    w = {d["name"]: d["weight"] for d in BASE_DIMENSIONS}
    t = text.lower()
    if any(k in t for k in ["准确", "可信", "数据质量", "不能错", "口径"]):
        w["data_accuracy"] += 0.10
    if any(k in t for k in ["洞察", "分析", "归因", "建议", "insight"]):
        w["insight"] += 0.05
    if any(k in t for k in ["简洁", "精简", "短", "concise"]):
        w["conciseness"] += 0.05
    s = sum(w.values())
    return {k: round(v / s, 3) for k, v in w.items()}


def _is_research_insight(text: str) -> bool:
    """需求是否属于'基于异构素材的调研洞察汇报'(与算数字型报告区分)。"""
    t = text.lower()
    kws = ["调研", "洞察", "素材", "访谈", "研究报告", "调查", "高管报告", "research", "insight",
           "多份", "异构", "报告输出"]
    return sum(1 for k in kws if k.lower() in t) >= 1


def generate_v0(requirement: str, product_id: str = "custom-skill",
                prefer_real: bool = False,
                optimizer_mode: str = "switch_search",
                v0_strategy: str = "base_skill") -> Dict[str, Any]:
    """返回 {skill, rubric, rationale, detected}。"""
    if product_id == "research_insight" or _is_research_insight(requirement):
        return _generate_research(
            requirement,
            "research_insight",
            optimizer_mode,
            v0_strategy,
        )
    if v0_strategy == "llm_scratch":
        raise ValueError("llm_scratch 当前仅支持调研洞察类 Skill")
    if prefer_real and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _generate_via_claude(requirement, product_id)
        except Exception as e:
            print("[generator] Claude 生成失败(%s), 回退启发式" % e)
    return _generate_heuristic(requirement, product_id)


def _generate_heuristic(requirement: str, product_id: str) -> Dict[str, Any]:
    rtype = _detect_report_type(requirement)
    tmpl = REPORT_TEMPLATES[rtype]
    audience = _detect_audience(requirement, tmpl["audience"])
    weights = _emphasis_weights(requirement)

    # v0 directives: 全部关闭(留给优化器逐个打开) —— 与「结构定上限、优化逼近」一致
    directives = {k: False for k in KNOWN_DIRECTIVES}

    skill = {
        "id": product_id, "version": "v0", "parent_version": None,
        "structure": {
            "flow": [
                {"step": 0, "name": "Intake & Scoping", "produces": "report_spec"},
                {"step": 1, "name": "Data Ingestion & Validation", "subagent": "DataAnalyst",
                 "produces": "findings", "rule": "只出数字,不写文章"},
                {"step": 2, "name": "Insight Extraction", "subagent": "Insight",
                 "produces": "insights", "rule": "只引用findings里的数字"},
                {"step": 3, "name": "Narrative Composition", "subagent": "Writer",
                 "produces": "draft_report", "rule": "每个论断携带[finding_id]"},
                {"step": 4, "name": "Verification", "subagent": "Verifier",
                 "produces": "verification_report", "rule": "独立、对抗性", "loop_back_to": 3, "max_retries": 2},
                {"step": 5, "name": "Format & Deliver", "produces": "final_report"},
            ],
            "subagents": [
                {"name": "DataAnalyst", "responsibility": "算数、校验、给数字配出处"},
                {"name": "Insight", "responsibility": "判断重要性/归因/风险,只在findings内推理"},
                {"name": "Writer", "responsibility": "组织语言、套模板,携带引用标记"},
                {"name": "Verifier", "responsibility": "对抗性查错(编造/遗漏),不参与生成", "independent": True},
            ],
            "memory_schema": ["config", "facts", "learned_rules"],
        },
        "instructions": {
            "prose": "你是%s。根据输入数据生成一份%s。受众: %s。" % (
                product_id, _rtype_zh(rtype), audience),
            "directives": directives,
        },
        "few_shots": [],
        "memory_content": {
            "config": {
                "report_templates": {rtype: {"required_sections": tmpl["sections"],
                                             "audience": audience, "tone": tmpl["tone"]}},
                "metric_definitions": {
                    "churn_rate": "当期流失客户数 / 期初客户数", "arr": "年度经常性收入",
                    "mom": "环比 = (本期-上期)/上期", "yoy": "同比 = (本期-去年同期)/去年同期"},
                "audience_profiles": {
                    "exec": {"length": "short", "focus": ["风险", "决策项"], "detail_level": "low"},
                    "team": {"length": "long", "detail_level": "high"}},
            },
            "facts": {"historical_baselines": [], "prior_reports": []},
            "learned_rules": [],
        },
        "changelog": "v0 由需求描述生成(启发式)。报告类型=%s, 受众=%s。directives 全关, 作为优化起跑线。" % (rtype, audience),
    }

    rubric = _build_rubric(product_id, weights)

    rationale = ("识别报告类型: %s；受众: %s。\n"
                 "维度权重(按需求侧重微调): %s。\n"
                 "v0 directives 全部关闭 —— 结构定上限, 由优化闭环逐个打开逼近上限。") % (
        _rtype_zh(rtype), audience,
        ", ".join("%s=%.2f" % (DIM_ANCHORS_ZH(k), v) for k, v in weights.items()))

    return {"skill": skill, "rubric": rubric, "rationale": rationale,
            "detected": {"report_type": rtype, "audience": audience}}


def _draft_research_v0_from_scratch(
    requirement: str,
    requirement_contract: str,
    rubric: Dict[str, Any],
) -> str:
    """仅基于需求与去标识化内容规则起草，不读取基础 Skill 正文。"""
    production_requirements = production_skill_policy.quality_requirements(
        rubric
    )
    prompt = "\n".join([
        "你是 Skill 架构师。请从零起草一份调研洞察汇报 Skill 的可执行质量规则。",
        "这是 V0 初稿，不存在可继承的旧 instructions；禁止假设或复用任何基础 Skill 正文。",
        "系统会把冻结任务契约自动放在你的输出之前，因此不要复写任务契约，只输出其后的质量规则。",
        "",
        "要求：",
        "1. 将下面的生产内容要求转化为明确、可执行的写作与交付前自检规则。",
        "2. mandatory_requirements 中每条义务必须明确保留，不得弱化。",
        "3. 输出应自包含、无历史版本引用，包含硬规则、自检清单和必要正反例。",
        "4. 不堆砌术语，不输出分析过程，不引用本提示词。",
        "5. 输出只包含可直接指导报告生成和交付自检的内容规则。",
        "",
        "## 用户需求",
        requirement,
        "",
        "## 冻结任务契约（仅供理解，不要复写）",
        requirement_contract,
        "",
        "## 生产内容要求",
        json.dumps(production_requirements, ensure_ascii=False, indent=2),
        "",
        "## 输出格式",
        "只输出一个 ```json 代码块，代码块内是可被 json.loads 解析的单个对象，代码块外不要写文字。",
        "instructions_text 必须是完整 Markdown 字符串，换行和引号按 JSON 规则转义。",
        "```json",
        json.dumps({
            "instructions_text": "<从零起草的完整质量规则 Markdown>",
        }, ensure_ascii=False),
        "```",
    ])
    raw = _call_v0_llm(
        prompt,
    )
    parsed = llm_client.extract_json(raw)
    instructions_text = (
        parsed.get("instructions_text", "").strip()
        if isinstance(parsed, dict)
        else ""
    )
    if not instructions_text:
        raise llm_client.LLMClientError(
            "LLM 未产出有效 instructions_text"
        )
    try:
        production_skill_policy.validate_production_text(instructions_text)
    except ValueError as exc:
        raise llm_client.LLMClientError(str(exc)) from exc

    # 仅 llm_scratch V0 使用独立守卫；迭代 Optimizer 固定为 Diagnosis + Patch 两次调用。
    guard = _guard_scratch_v0_redlines(instructions_text, rubric)
    redline_ids = [
        check["id"]
        for dimension in rubric.get("dimensions", [])
        for check in dimension.get("checks", [])
        if check.get("redline") and check.get("id")
    ]
    uncovered = [
        check_id
        for check_id in redline_ids
        if (guard.get("raw") or {}).get(check_id) is not True
    ]
    if uncovered:
        raise llm_client.LLMClientError(
            "LLM 起草的 v0 未明确保留红线: %s"
            % ", ".join(uncovered)
        )
    return instructions_text


def _call_v0_llm(prompt: str, max_tokens=None) -> str:
    """V0 起草与红线守卫统一使用 Codex CLI gpt-5.6-sol medium。"""
    return llm_client.call_llm(
        prompt,
        timeout_seconds=os.environ.get(
            "LLM_REWRITE_TIMEOUT_SECONDS",
            "600",
        ),
        retries=os.environ.get("LLM_REWRITE_RETRIES", "2"),
        backend=DEFAULT_V0_LLM_BACKEND,
        model=DEFAULT_V0_CODEX_MODEL,
        reasoning_effort=DEFAULT_V0_CODEX_REASONING_EFFORT,
        max_tokens=max_tokens,
    )


def _guard_scratch_v0_redlines(
    instructions_text: str,
    rubric: Dict[str, Any],
) -> Dict[str, Any]:
    """从零起草 V0 的独立红线检查，不参与后续两阶段 Optimizer。"""
    redlines = [
        {
            "id": check["id"],
            "label": check.get("label", check["id"]),
            "desc": check.get("desc", ""),
        }
        for dimension in rubric.get("dimensions", [])
        for check in dimension.get("checks", [])
        if check.get("redline") and check.get("id")
    ]
    if not redlines:
        return {"ok": True, "dropped": [], "raw": {}}
    prompt = "\n".join([
        "判断下面的 V0 Skill 是否明确保留每条红线义务。",
        "未明确、不可执行、删除或弱化均判 false。只输出严格 JSON。",
        "",
        "## 红线义务",
        *(
            "- %s (%s): %s"
            % (item["id"], item["label"], item["desc"])
            for item in redlines
        ),
        "",
        "## V0 正文",
        instructions_text,
        "",
        "## 输出",
        json.dumps(
            {item["id"]: True for item in redlines},
            ensure_ascii=False,
        ),
    ])
    raw_response = _call_v0_llm(
        prompt,
        max_tokens=os.environ.get("LLM_GUARD_MAX_TOKENS", "2000"),
    )
    raw = llm_client.extract_json(raw_response) or {}
    dropped = [item["id"] for item in redlines if raw.get(item["id"]) is not True]
    return {"ok": not dropped, "dropped": dropped, "raw": raw}


def _generate_research(requirement: str, product_id: str,
                       optimizer_mode: str = "switch_search",
                       v0_strategy: str = "base_skill") -> Dict[str, Any]:
    """调研洞察 v0：支持基础 Skill 起步或 LLM 按需求与 Rubric 从零起草。"""
    if v0_strategy not in ("base_skill", "llm_scratch"):
        raise ValueError("非法 v0_strategy: %s" % v0_strategy)
    if optimizer_mode != "llm_rewrite" and v0_strategy != "base_skill":
        raise ValueError("llm_scratch 仅适用于 llm_rewrite 模式")

    base_skill = os.environ.get(
        "OPENHARNESS_WB_SKILL_PATH",
        os.path.join(_ROOT, "skills", "research-report"),
    )
    rubric = _build_rubric_research()
    directives = load_skill_directives(Path(base_skill))
    if optimizer_mode == "llm_rewrite":
        requirement_contract = _research_requirement_contract(requirement)
        prose = (
            _draft_research_v0_from_scratch(
                requirement,
                requirement_contract,
                rubric,
            )
            if v0_strategy == "llm_scratch"
            else load_editable_region(Path(base_skill))
        )
        instructions = {
            "prose": prose,
            "mode": "freeform",
            "directives": directives,   # freeform 编译忽略，仅供审计
            "requirement_contract": requirement_contract,
            "v0_strategy": v0_strategy,
        }
        changelog = (
            "v0(freeform) = 冻结需求契约 + LLM 仅依据需求与 Rubric 从零起草质量规则。"
            if v0_strategy == "llm_scratch"
            else "v0(freeform) = 冻结需求契约 + 基础 Skill 可编辑规则。"
        )
    else:
        instructions = {
            "prose": "执行内容以 skills/research-report 唯一基线为准。",
            "directives": directives,
            "v0_strategy": "base_skill",
        }
        changelog = "v0 读取 skills/research-report 唯一基线及其已启用 directive。"
    skill = {
        "id": product_id, "version": "v0", "parent_version": None,
        "structure": {},
        "instructions": instructions,
        "few_shots": [],
        "memory_content": {
            "config": {
                "base_skill": "research-report",
                "v0_strategy": (
                    v0_strategy
                    if optimizer_mode == "llm_rewrite"
                    else "base_skill"
                ),
            },
            "facts": {},
            "learned_rules": [],
        },
        "changelog": changelog,
    }
    if optimizer_mode == "llm_rewrite" and v0_strategy == "llm_scratch":
        origin_text = (
            "v0 的可编辑质量规则由 LLM 仅依据用户需求与完整 Rubric 从零起草，"
            "未读取基础 Skill 的可编辑规则；基础目录仅作为运行壳和编译模板。"
        )
    elif optimizer_mode == "llm_rewrite":
        origin_text = "v0 的可编辑质量规则直接继承基础 Skill，后续版本再由 LLM 改写。"
    else:
        origin_text = "v0 读取基础 Skill，优化器后续按 directive 搜索推进。"
    rationale = (
        "识别为**调研洞察汇报**（基于异构素材的提炼与写作）。\n"
        "六维 Rubric：可回溯性 0.28 / 结构 0.15 / 逻辑 0.12 / 洞察 0.22 / "
        "覆盖 0.08 / 表达 0.15。\n"
        + origin_text
    )
    return {"skill": skill, "rubric": rubric, "rationale": rationale,
            "detected": {"report_type": "research_insight", "audience": "exec"}}


def _research_requirement_contract(
    requirement: str,
) -> str:
    """把产品需求固化成 freeform v0 的不可变生产任务契约。

    这里不调用 LLM，保证新建会话离线、确定性可复现；自由改写策略只优化
    契约之后的质量规则。契约使用语义化表述，不把用户原文机械复读进 Skill。
    """
    text = (requirement or "").lower()
    audience = (
        "总裁/最高管理层"
        if any(k in text for k in ("总裁", "最高管理层", "高管", "ceo", "董事"))
        else "管理层"
    )
    interaction_fields = []
    if any(k in text for k in ("背景", "场合", "决策")):
        interaction_fields.append("汇报背景（受众、场合、要支撑的决策）")
    if any(k in text for k in ("hypo", "假设", "预判")):
        interaction_fields.append("hypothesis（只用于验证或证伪，绝不迎合）")
    if any(k in text for k in ("重点分布", "重点素材", "材料重点", "汇报的重点")):
        interaction_fields.append(
            "材料重点分布（重点主题/结论/素材及预期篇幅或权重）"
        )
    if not interaction_fields:
        interaction_fields = [
            "汇报背景",
            "hypothesis",
            "材料重点分布",
        ]

    lines = [
        "## 本次报告任务约束",
        "",
        "- **受众与目标**：面向%s，围绕用户给定的话题形成可直接用于决策的汇报。" % audience,
        "- **初始输入**：接收用户给定的话题与原始素材；原始素材文件只读，不修改。",
        "- **补充交互**：动笔前检查并补齐%s；用户已提供的不得重复询问，仍缺失或含糊的逐项追问，不自行猜测。"
        % "、".join(interaction_fields),
        "- **输出结构**：严格三段式——摘要、关键发现、启示。摘要结论先行；关键发现承载事实、数据和有据归因；启示面向决策，素材不足处明确留白。",
        "- **素材边界**：所有结论与数据只来自原始素材，不新增、篡改或偷换事实与数据口径。允许为高管阅读做有据的筛选、压缩和重组，但关键 claim 不遗漏、噪音不充数、原意不改变。",
        "- **图表规则**：数据密集或多组对比处优先用 markdown 表格或清晰图表；图表中的每个数字仍须来自素材，禁止补数或推算未给出的数值。",
        "- **写作方式**：结论先行、金字塔展开、信息组织 MECE；整体简洁、严谨，避免铺陈和术语注水。",
    ]
    contract = "\n".join(lines)
    production_skill_policy.validate_production_text(contract)
    return contract


def _build_rubric_research() -> Dict[str, Any]:
    """六维 rubric = harness/artifacts/rubric_research.json 的副本(避免锚点重复维护)。"""
    with open(os.path.join(_ART, "rubric_research.json"), encoding="utf-8") as f:
        return json.load(f)


def hydrate_research_optimizer_metadata(
    rubric: Dict[str, Any],
) -> Dict[str, Any]:
    """Backfill optimizer metadata for snapshots created before this contract."""

    if rubric.get("product") != "research_insight":
        return rubric
    canonical = _build_rubric_research()
    optimizer_by_check = {
        str(check["id"]): copy.deepcopy(check.get("optimizer"))
        for dimension in canonical.get("dimensions", [])
        for check in dimension.get("checks", [])
        if check.get("id") and "optimizer" in check
    }
    for dimension in rubric.get("dimensions", []):
        for check in dimension.get("checks", []):
            check_id = str(check.get("id") or "")
            if "optimizer" not in check and check_id in optimizer_by_check:
                check["optimizer"] = copy.deepcopy(
                    optimizer_by_check[check_id]
                )
    return rubric


def _build_rubric(product_id: str, weights: Dict[str, float]) -> Dict[str, Any]:
    dims = []
    for base in BASE_DIMENSIONS:
        name = base["name"]
        a = DIM_ANCHORS[name]
        dims.append({
            "name": name, "name_zh": base["name_zh"], "scale": [1, 5],
            "weight": weights[name], "hard_floor": base["hard_floor"],
            "is_reverse": base["is_reverse"], "criteria": a["criteria"],
            "anchors": {k: a[k] for k in ["5", "4", "3", "2", "1"]},
            "positive_example": a["positive_example"], "negative_example": a["negative_example"],
        })
    return {
        "product": product_id, "version": "v0", "dimensions": dims,
        "aggregate": "weighted_avg",
        "gates": [
            {"id": "red_line_accuracy", "type": "hard_floor", "dimension": "data_accuracy",
             "floor": 3, "rule": "任一 case data_accuracy < 3 -> 该 case 不合格"},
            {"id": "no_regression", "type": "no_regression", "drop_tolerance": 0.15,
             "rule": "候选版本采纳条件:目标维度↑ 且 其它维度均不塌"},
        ],
        "target": {"data_accuracy": 4.2, "completeness": 4.0, "insight": 3.8,
                   "conciseness": 3.5, "overall": 4.0},
    }


def _rtype_zh(rtype):
    return {"monthly_biz_review": "经营月报", "weekly_update": "周报",
            "ops_brief": "经营简报", "project_progress": "项目进展报告"}.get(rtype, "业务汇报")


def DIM_ANCHORS_ZH(name):
    return {"data_accuracy": "数据准确性", "completeness": "完整性",
            "insight": "洞察质量", "conciseness": "简洁性"}.get(name, name)


def _generate_via_claude(requirement: str, product_id: str) -> Dict[str, Any]:
    # 骨架: 有 key 时让 Claude 按方法论产出结构化 v0, 再校验对齐 KNOWN_DIRECTIVES / 维度词汇。
    raise NotImplementedError("Claude 生成需 ANTHROPIC_API_KEY + sdk;本环境走启发式。")
