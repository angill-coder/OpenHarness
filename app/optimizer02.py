# -*- coding: utf-8 -*-
"""optimizer02.py — 策略 B:由 LLM 自由改写整段 instructions。

与 harness/optimizer.py(翻开关搜索)并存,同一策略接口:
  propose(session, cur_skill, failures, context) -> proposal | None
  apply_proposal(cur_skill, proposal, new_version) -> SkillArtifact(freeform)

关键点:
  · 输入是"迭代记忆"(optimizer_pipeline.build_optimizer_context),把上一版的关键
    信息(当前最优全文/must_preserve/open_failures/history/tried_rejected/红线)全量
    传给 LLM,以避免回退。
  · 输出整段可编辑区正文,落进 skill.instructions.prose、mode=freeform。
  · 红线守卫:候选生成后、进真实生成流水线之前,用廉价 LLM 逐条核对候选是否仍保留
    每条红线义务;任一被删则当场拒(返回 None),不烧 WB 生成预算。
  · 采纳与否由异步 gate(optimizer_pipeline.evaluate_gate,在真实判分后)客观裁决,
    本模块只提议,不自采纳。
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, List, Optional

import llm_client
import optimizer_pipeline


REQUIRES_ASYNC_GATE = True
IS_OFFLINE = False


# ---------------- 提议(改写) ----------------

def _call_rewrite_llm(
    prompt: str,
    llm_backend: str = "workbuddy",
    llm_model: str | None = None,
) -> str:
    """Skill 全文改写较慢，使用独立的长超时与有限重试。"""
    return llm_client.call_llm(
        prompt,
        timeout_seconds=os.environ.get(
            "LLM_REWRITE_TIMEOUT_SECONDS",
            "600",
        ),
        retries=os.environ.get("LLM_REWRITE_RETRIES", "2"),
        backend=llm_backend,
        model=llm_model,
    )


def _render_prompt(context: Dict[str, Any]) -> str:
    ctx_json = json.dumps(context, ensure_ascii=False, indent=2)
    return "\n".join([
        "你是一个调研洞察汇报报告写作 skill 的优化器。你的任务:阅读下面的「迭代记忆」,",
        "改写这份 skill 的可编辑区正文(写作硬规则/自检清单/正反例),产出下一个更好的版本。",
        "",
        "目标 = 让报告在六维 rubric 上得分更高(尤其修复 open_failures),同时**绝不回退**:",
        "must_preserve 里的维度与 check 必须继续满足;tried_rejected 里试过且被拒的改法不要重犯。",
        "",
        "`requirement` 是这个产品的整体意图/受众定位;改写时让写作规则贴合它,但**不要**把",
        "需求原文抄进 skill 正文——它是方向,不是待复述的内容。rubric 六维仍是硬标准。",
        "current_best.requirement_contract 是系统冻结的任务契约,编译时会自动放在你输出之前;",
        "不要在 instructions_text 里复写、删减或改动它。",
        "",
        "硬约束(违反则本版必被拒):",
        "1. 只输出任务契约之后的「质量规则」正文(从『## 硬规则』到正反例结尾这段);不要写标题栏、",
        "   directive 清单、开场三输入、三段结构说明、版本增量区——那些是冻结的结构层。",
        "2. guardrails.redline_checks 里每一条红线义务都必须在你的正文里明确保留、不得删弱。",
        "3. 禁止 reward-hack:不堆大词/术语讨好裁判,不用『不是…而是…』注水,不复述充数。",
        "4. 相对当前最优是**增量改写**:保留有效内容,只针对 open_failures 做必要修改与补强。",
        "",
        "## 迭代记忆",
        ctx_json,
        "",
        "## 输出格式(务必严格遵守):",
        "把结果放进一个 ```json 代码块里,代码块内是**单个合法 JSON 对象**,代码块外不要有任何文字。",
        "instructions_text 的值是一个 JSON 字符串:其中的换行必须写成 \\n、内部双引号必须转义为 \\\","
        "确保整体能被 json.loads 直接解析(不要在正文里嵌入未转义的引号或裸换行)。",
        "```json",
        json.dumps({
            "instructions_text": "<改写后的可编辑区正文全文>",
            "change_summary": "<本版相对当前最优改了什么,一两句>",
            "targets_failures": ["<针对的 pattern_id 或 check_id>"],
            "preserved": ["<你刻意保留了什么、为什么(对齐 must_preserve)>"],
            "hypothesis": "<预计哪些维度↑、哪些不回退>",
            "self_check_no_hack": True,
        }, ensure_ascii=False),
        "```",
    ])


def _render_guard_prompt(instructions_text: str, redline_checks: List[Dict[str, str]]) -> str:
    lines = [
        "下面是一份调研报告写作 skill 的正文草案。请逐条判断:草案是否仍**明确要求**了以下每条红线义务?",
        "只要草案里没有明确、可执行地要求某条义务(删除或明显弱化都算 false),该条判 false。",
        "",
        "## 红线义务清单",
    ]
    for c in redline_checks:
        lines.append("- %s (%s): %s" % (c["id"], c.get("label", ""), c.get("desc", "")))
    lines += [
        "",
        "## 正文草案",
        instructions_text or "(空)",
        "",
        "## 输出(只输出严格 JSON):",
        '{"%s": true/false, ...每条红线 id 都要}' % (redline_checks[0]["id"] if redline_checks else "T2"),
    ]
    return "\n".join(lines)


def _redline_guard(
    instructions_text: str,
    rubric: Dict[str, Any],
    llm_backend: str = "workbuddy",
    llm_model: str | None = None,
) -> Dict[str, Any]:
    """返回 {ok: bool, dropped: [check_id...], raw: {...}}。任一红线被删 => ok=False。"""
    redlines = optimizer_pipeline._redline_checks(rubric)
    if not redlines:
        return {"ok": True, "dropped": [], "raw": {}}
    raw = llm_client.extract_json(
        _call_rewrite_llm(
            _render_guard_prompt(instructions_text, redlines),
            llm_backend=llm_backend,
            llm_model=llm_model,
        )
    ) or {}
    dropped = [c["id"] for c in redlines if raw.get(c["id"]) is False]
    return {"ok": not dropped, "dropped": dropped, "raw": raw}


def propose(
    session,
    cur_skill,
    failures,
    context,
    llm_backend: str = "workbuddy",
    llm_model: str | None = None,
) -> Optional[Dict[str, Any]]:
    """看迭代记忆,让 LLM 改写整段可编辑区。红线守卫不过则当场拒(返回 None)。"""
    raw = _call_rewrite_llm(
        _render_prompt(context),
        llm_backend=llm_backend,
        llm_model=llm_model,
    )
    parsed = llm_client.extract_json(raw)
    if not parsed or not (parsed.get("instructions_text") or "").strip():
        raw_text = raw or ""
        # 记录 raw 摘要, 便于排查是"截断(未闭合)"还是"解析失败/字段缺失"
        session.opt_history.append({
            "target": "instructions_freeform",
            "result": "rejected",
            "reason": "LLM 未产出有效 instructions_text",
            "raw_len": len(raw_text),
            "raw_head": raw_text[:400],
            "raw_tail": raw_text[-400:],
            "parsed_keys": (list(parsed.keys()) if isinstance(parsed, dict) else None),
        })
        return None

    instructions_text = parsed["instructions_text"].strip()
    guard = _redline_guard(
        instructions_text,
        session.rubric,
        llm_backend=llm_backend,
        llm_model=llm_model,
    )
    if not guard["ok"]:
        session.opt_history.append({
            "target": "instructions_freeform",
            "change_summary": parsed.get("change_summary", ""),
            "result": "rejected",
            "reason": "红线守卫拒绝:候选删/弱化了红线 %s" % ", ".join(guard["dropped"]),
        })
        return None

    return {
        "target": "instructions_freeform",
        "level": "L1_freeform",
        "instructions_text": instructions_text,
        "change_summary": parsed.get("change_summary", ""),
        "targets_failures": parsed.get("targets_failures", []),
        "preserved": parsed.get("preserved", []),
        "hypothesis": parsed.get("hypothesis", ""),
        "self_check_no_hack": bool(parsed.get("self_check_no_hack", False)),
        # 供 gate 用:把 targets 映射到受影响维度
        "affected_dims": _targets_to_dims(parsed.get("targets_failures", []), failures),
        "change": "LLM 改写可编辑区: " + (parsed.get("change_summary", "") or "整段重写"),
    }


def _targets_to_dims(targets, failures) -> List[str]:
    """把提议针对的 pattern_id 映射成受影响维度(用于 gate 的 target_dims)。"""
    dims = []
    by_id = {f.get("pattern_id"): f for f in (failures or [])}
    for t in targets or []:
        f = by_id.get(t)
        if f:
            for d in f.get("affected_dims", []):
                if d and d not in dims:
                    dims.append(d)
    return dims


# ---------------- 应用(生成候选版本) ----------------

def apply_proposal(cur_skill, proposal: Dict[str, Any], new_version: str):
    """把改写落成一个 freeform 候选版本(不触碰 harness 的 SkillArtifact 定义)。"""
    cand = copy.deepcopy(cur_skill)
    instr = dict(cand.instructions or {})
    instr["prose"] = proposal["instructions_text"]
    instr["mode"] = "freeform"
    cand.instructions = instr
    cand.parent_version = cur_skill.version
    cand.version = new_version
    cand.changelog = "相对 %s: %s" % (cur_skill.version, proposal.get("change_summary", "LLM 改写"))
    return cand
