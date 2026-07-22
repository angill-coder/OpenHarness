# -*- coding: utf-8 -*-
"""
optimizer.py — 反思式优化器 (对应架构文档 OPTIMIZER + Optimizer 五机制)

它不是"让 LLM 重写 prompt", 是一个带记忆的分层搜索器:
  机制1 输入: 吃 failure_report(诊断), 不吃原始 trace
  机制2 改法: 按代价分层 L1(改指令/directive) -> L2(few-shot) -> L3(memory)。MVP 主要用 L1。
  机制3 验证: 产出候选, 由 loop 的 dev gate 决定采纳(本文件只提议, 不自采纳)
  机制4 防 hack: 绝不提议 keyword_emphasis 这类讨好裁判的杠杆
  机制5 记忆: 记住试过什么(history), 不重复提被否决的改动; 无更多提议 => 收敛信号

输出是结构化 proposal(改动 + addresses + hypothesis), 可被证伪。
"""
from typing import Any, Dict, List, Optional


# 明确禁止提议的 directive(reward hacking 杠杆)。这是机制4的硬编码防线。
#   keyword_emphasis  —— 算数字型: 堆术语讨好裁判
#   buzzword_emphasis —— 调研洞察型: 堆大词/注水讨好裁判
FORBIDDEN = {"keyword_emphasis", "buzzword_emphasis"}


def propose(skill, failure_report: List[Dict[str, Any]], history: List[Dict[str, Any]]
            ) -> Optional[Dict[str, Any]]:
    """看失败报告, 提出下一个最该做、最便宜的改动。无可提议则返回 None(收敛)。"""
    tried = {(h["target"], h["directive"]) for h in history}
    directives = skill.directives()

    # 遍历失败模式(已按 severity/命中数排序), 找第一个"有对应 directive、当前是关闭、且没试过"的
    for pattern in failure_report:
        hint = pattern.get("directive_hint")
        if not hint or hint in FORBIDDEN:
            continue
        if directives.get(hint):        # 已经打开了, 跳过
            continue
        if ("instructions", hint) in tried:  # 试过且被否决, 不重复(机制5)
            continue
        return {
            "target": "instructions",   # L1
            "level": "L1",
            "directive": hint,
            "value": True,
            "change": "打开 directive: %s" % hint,
            "addresses": pattern["pattern"],
            "affected_dims": pattern["affected_dims"],
            "hypothesis": "预计提升 %s, 不损伤其它维度" % "/".join(pattern["affected_dims"]),
            "from_pattern": pattern["pattern_id"],
            "hit_count": pattern["hit_count"],
        }
    return None  # 没有可提议的改动 => 撞平台期/收敛


def apply_proposal(skill, proposal: Dict[str, Any], new_version: str):
    """把 proposal 应用成一个候选 skill 版本(L1: 翻 directive)。"""
    note = "相对 %s: %s (针对'%s')" % (skill.version, proposal["change"], proposal["addresses"])
    return skill.clone_with_directive(proposal["directive"], proposal["value"], new_version, note)
