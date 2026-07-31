# -*- coding: utf-8 -*-
"""optimizer_pipeline.py — 策略无关的共享评测件。

两件事,与"用哪个优化器策略"无关:
  · build_optimizer_context —— 装配"迭代记忆"(carry-forward),喂给 LLM 改写策略,
    是防回退的地基:把 rubric、当前最优、must_preserve、open_failures、history、
    tried_rejected、guardrails 全部一次性交给下一版决策者。
  · evaluate_gate —— 候选真实分 vs 当前最优的采纳判定纯函数(目标维↑ ∧ 其它维不回退
    超容差 ∧ 无新红线)。供 llm_rewrite 的异步结算(settle)调用。

switch_search 路径不经此文件(其 gate 仍内联在 session_eval,行为逐字不变)。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# ---------------- 采纳 gate(纯函数) ----------------

def evaluate_gate(
    parent_dims: Dict[str, float],
    cand_dims: Dict[str, float],
    target_dims: List[str],
    tol: float,
    dims: List[str],
) -> Tuple[bool, str, Dict[str, Any]]:
    """采纳当且仅当:目标维(或 overall)↑ ∧ 其它维不回退超容差 ∧ 无新红线。

    parent_dims/cand_dims 均为 runner.mean_scores 的输出(含每维分 + overall +
    red_line_fails)。返回 (adopt, verdict, reasons)。
    """
    eps = 0.001
    targets = [d for d in (target_dims or []) if d]
    improved_dims = [
        d for d in targets
        if cand_dims.get(d, 0.0) - parent_dims.get(d, 0.0) > eps
    ]
    overall_up = cand_dims.get("overall", 0.0) - parent_dims.get("overall", 0.0) > eps
    improved = bool(improved_dims) or overall_up

    regressed = None
    for d in dims:
        if d in targets:
            continue
        if parent_dims.get(d, 0.0) - cand_dims.get(d, 0.0) > tol:
            regressed = d
            break

    red_line_new = cand_dims.get("red_line_fails", 0) > parent_dims.get("red_line_fails", 0)

    adopt = improved and regressed is None and not red_line_new
    if adopt:
        verdict = "adopted"
    elif not improved:
        verdict = "rejected"
    elif regressed is not None:
        verdict = "rejected"
    else:
        verdict = "rejected"

    reasons = {
        "improved": improved,
        "improved_dims": improved_dims,
        "overall_delta": round(cand_dims.get("overall", 0.0) - parent_dims.get("overall", 0.0), 3),
        "regressed_dim": regressed,
        "regressed_tol": tol,
        "red_line_new": red_line_new,
        "parent_red_line_fails": parent_dims.get("red_line_fails", 0),
        "cand_red_line_fails": cand_dims.get("red_line_fails", 0),
    }
    if not adopt:
        if not improved:
            reasons["message"] = "目标维与 overall 均未提升"
        elif regressed is not None:
            reasons["message"] = "维度 %s 回退超容差 %.2f" % (regressed, tol)
        elif red_line_new:
            reasons["message"] = "引入新的红线失败"
    else:
        reasons["message"] = "目标维/overall 提升且无回退、无新红线"
    return adopt, verdict, reasons


def no_regression_tol(rubric: Dict[str, Any]) -> float:
    for g in rubric.get("gates", []):
        if g.get("id") == "no_regression" and g.get("drop_tolerance") is not None:
            return float(g["drop_tolerance"])
    return 0.15


# ---------------- 迭代记忆装配 ----------------

def _redline_checks(rubric: Dict[str, Any]) -> List[Dict[str, str]]:
    """rubric 里标了 redline 的 check 清单(供 must_preserve/守卫用)。"""
    out = []
    for dim in rubric.get("dimensions", []):
        for c in dim.get("checks", []):
            if c.get("redline"):
                out.append({
                    "id": c["id"],
                    "dim": dim.get("name", ""),
                    "label": c.get("label", c["id"]),
                    "desc": c.get("desc", ""),
                })
    return out


def _dim_summary(rubric: Dict[str, Any]) -> List[Dict[str, Any]]:
    target = rubric.get("target", {})
    out = []
    for dim in rubric.get("dimensions", []):
        name = dim.get("name", "")
        out.append({
            "name": name,
            "name_zh": dim.get("name_zh", ""),
            "weight": dim.get("weight"),
            "target": target.get(name),
            "is_reverse": dim.get("is_reverse", False),
            "hard_floor": dim.get("hard_floor"),
            "checks": [
                {
                    "id": c["id"],
                    "label": c.get("label", c["id"]),
                    "desc": c.get("desc", ""),
                    "redline": bool(c.get("redline")),
                }
                for c in dim.get("checks", [])
            ],
        })
    return out


def _version_dev(version: Dict[str, Any]) -> Dict[str, float]:
    return version.get("dev") or {}


def build_optimizer_context(session, account=None) -> Dict[str, Any]:
    """装配 carry-forward 迭代记忆。只读 session,不改状态。"""
    rubric = session.rubric
    dims = list(session.dims)
    target = rubric.get("target", {})
    cur = session._current()
    cur_dev = _version_dev(cur)
    cur_skill = cur["skill"]

    # 当前最优:全文 + 每维分
    current_best = {
        "version": cur["version"],
        "requirement_contract": (
            cur_skill.instructions or {}
        ).get("requirement_contract", ""),
        "instructions_text": (cur_skill.instructions or {}).get("prose", ""),
        "scores": {d: round(cur_dev.get(d, 0.0), 3) for d in dims},
        "overall": round(cur_dev.get("overall", 0.0), 3),
        "red_line_fails": cur_dev.get("red_line_fails", 0),
    }

    # open_failures:当前版本的失败报告(check 级证据 + 归因维)
    failures = cur.get("failure_report") or cur.get("failures") or []
    open_failures = []
    failing_check_ids = set()
    for f in failures:
        for ev in f.get("evidence", []):
            failing_check_ids.add(ev.get("check_id"))
        open_failures.append({
            "pattern_id": f.get("pattern_id"),
            "pattern": f.get("pattern"),
            "affected_dims": f.get("affected_dims", []),
            "severity": f.get("severity"),
            "hit_count": f.get("hit_count"),
            "is_red_line": f.get("severity") == "high",
            "evidence": [
                {
                    "case_id": ev.get("case_id"),
                    "check_id": ev.get("check_id"),
                    "reasoning": ev.get("reasoning", ""),
                }
                for ev in f.get("evidence", [])[:4]
            ],
        })

    # must_preserve:当前 ≥target 的维度 + 未出现在失败里的 check(视为通过)
    all_check_ids = [c["id"] for d in rubric.get("dimensions", []) for c in d.get("checks", [])]
    preserved_dims = [
        d for d in dims
        if target.get(d) is not None and cur_dev.get(d, 0.0) >= float(target[d])
    ]
    passing_checks = [cid for cid in all_check_ids if cid not in failing_check_ids]
    must_preserve = {
        "dims_at_or_above_target": preserved_dims,
        "passing_checks": passing_checks,
    }

    # history:逐版改动 + 结果 + overall delta(相对父版)
    by_version = {v["version"]: v for v in session.versions}
    history = []
    for v in session.versions:
        prop = v.get("proposal") or {}
        parent_dev = _version_dev(by_version.get(v.get("parent"), {})) if v.get("parent") else {}
        cand_dev = _version_dev(v)
        history.append({
            "version": v["version"],
            "parent": v.get("parent"),
            "change_summary": prop.get("change_summary") or v["skill"].changelog or "",
            "targets": prop.get("targets_failures") or prop.get("affected_dims") or [],
            "candidate_state": v.get("candidate_state"),
            "adopted": v.get("adopted", True),
            "verdict": v.get("verdict"),
            "verdict_reasons": v.get("verdict_reasons"),
            "overall_delta": (
                round(cand_dev.get("overall", 0.0) - parent_dev.get("overall", 0.0), 3)
                if parent_dev else None
            ),
        })

    # tried_rejected:被拒的改动摘要 + 原因(别重犯)
    tried_rejected = [
        {
            "change_summary": h["change_summary"],
            "reason": (h.get("verdict_reasons") or {}).get("message")
            if isinstance(h.get("verdict_reasons"), dict) else h.get("verdict_reasons"),
        }
        for h in history
        if h.get("verdict") == "rejected" or h.get("candidate_state") == "rejected"
    ]

    guardrails = {
        "structure_frozen": "禁止改动结构层(开场三输入/三段结构/标题体系);只改可编辑区正文。",
        "requirement_contract_frozen": (
            "current_best.requirement_contract 由系统编译时自动置于正文前，"
            "优化器不得复写、删减或改动；输出只包含它后面的质量规则。"
        ),
        "no_reward_hack": "禁止堆砌大词/术语讨好裁判、禁'不是…而是…'句式注水、禁复述充数。",
        "redline_must_keep": "以下红线义务任何一条都不得删除或弱化:",
        "redline_checks": _redline_checks(rubric),
    }

    return {
        "requirement": getattr(session, "requirement", "") or "",
        "rubric": {
            "product": rubric.get("product"),
            "overall_target": target.get("overall"),
            "dimensions": _dim_summary(rubric),
            "no_regression_tol": no_regression_tol(rubric),
        },
        "current_best": current_best,
        "must_preserve": must_preserve,
        "open_failures": open_failures,
        "history": history,
        "tried_rejected": tried_rejected,
        "guardrails": guardrails,
    }
