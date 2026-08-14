# -*- coding: utf-8 -*-
"""optimizer_pipeline.py — 策略无关的共享评测件。

两件事,与"用哪个优化器策略"无关:
  · build_optimizer_context —— 装配"迭代记忆"(carry-forward),喂给 LLM 改写策略,
    是防回退的地基:把 rubric、当前最优、must_preserve、open_failures、history、
    tried_rejected、guardrails 全部一次性交给下一版决策者。
  · evaluate_gate —— 候选真实分 vs 历史 champion 的 Gate。
    硬红线数和维度硬底线数必须 Pareto 不恶化；允许失败键在
    case/check 之间有限迁移，但保留全维、目标 check 和 holdout 保护。
    供 llm_rewrite 的异步结算调用。

switch_search 路径不经此文件(其 gate 仍内联在 session_eval,行为逐字不变)。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import production_skill_policy


MIN_EFFECTIVE_OVERALL_DELTA = 0.02
TARGET_CHECK_DROP_TOLERANCE = 0.05
HOLDOUT_OVERALL_DROP_TOLERANCE = 0.05
MIN_EXPERIMENT_EXAMPLES = 3
MAX_EXPERIMENT_EXAMPLES = 5
MAX_DIAGNOSIS_CANDIDATES = 5
DIAGNOSIS_EVIDENCE_PER_CANDIDATE = 3
MAX_REPLAYABLE_EVIDENCE_PER_CHECK = 5
DEFAULT_MAX_INSTRUCTION_CHARS = 8000
DEFAULT_MAX_NET_GROWTH_CHARS = 200
DEFAULT_MAX_PATCH_OPERATIONS = 6


# ---------------- 采纳 gate(纯函数) ----------------

def _redline_failure_key_set(
    metrics: Dict[str, Any],
) -> Tuple[set[Tuple[str, str]], bool]:
    """读取可比较的 (case_id, check_id) 红线失败集合。

    旧记录只有聚合计数，不能把人造的 legacy 序号当成真实失败键；
    此时返回 unavailable。Gate v4 只把具体键用于诊断，决策使用
    可向后兼容的聚合硬失败计数。
    """
    if not metrics.get("redline_failure_keys_available"):
        return set(), False
    raw_keys = metrics.get("redline_failure_keys")
    if not isinstance(raw_keys, (list, tuple)):
        return set(), False
    keys: set[Tuple[str, str]] = set()
    for item in raw_keys:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return set(), False
        keys.add((str(item[0]), str(item[1])))
    return keys, True


def evaluate_gate(
    champion_dims: Dict[str, float],
    cand_dims: Dict[str, float],
    target_dims: List[str],
    tol: float,
    dims: List[str],
    *,
    champion_hard: Optional[Dict[str, Any]] = None,
    candidate_hard: Optional[Dict[str, Any]] = None,
    holdout: Optional[Dict[str, Any]] = None,
    target_check: Optional[Dict[str, Any]] = None,
    min_overall_improvement: float = MIN_EFFECTIVE_OVERALL_DELTA,
) -> Tuple[bool, str, Dict[str, Any]]:
    """候选只能通过两条路径之一被采纳。

    1. 硬红线数和维度硬底线数 Pareto 改善，全维和目标 check
       无实质回退；如果存在 holdout，还必须通过宽容度保护；
    2. 两类硬失败均持平，overall 相对 champion 至少提升
       ``min_overall_improvement``，且独立 holdout 不回退。

    (case_id, check_id) 失败键仍完整记录供诊断，但不再因为存在
    新键而一票否决；决策依据是净硬失败、维度、目标 check 和 holdout。
    """
    targets = [d for d in (target_dims or []) if d]
    score_delta = {
        d: round(cand_dims.get(d, 0.0) - champion_dims.get(d, 0.0), 3)
        for d in dims
    }
    improved_dims = [d for d in targets if score_delta.get(d, 0.0) > 0]
    regressed_dims = [
        d for d in dims
        if champion_dims.get(d, 0.0) - cand_dims.get(d, 0.0) > tol
    ]
    all_dims_stable = not regressed_dims

    champion_hard = dict(champion_hard or {})
    candidate_hard = dict(candidate_hard or {})
    champion_red = int(champion_hard.get(
        "redline_failures",
        champion_dims.get("red_line_fails", 0) or 0,
    ))
    candidate_red = int(candidate_hard.get(
        "redline_failures",
        cand_dims.get("red_line_fails", 0) or 0,
    ))
    champion_floor = int(champion_hard.get("hard_floor_failures", 0) or 0)
    candidate_floor = int(candidate_hard.get("hard_floor_failures", 0) or 0)
    champion_redline_keys, champion_redline_keys_available = (
        _redline_failure_key_set(champion_hard)
    )
    candidate_redline_keys, candidate_redline_keys_available = (
        _redline_failure_key_set(candidate_hard)
    )
    redline_failure_keys_comparable = bool(
        champion_redline_keys_available
        and candidate_redline_keys_available
    )
    redline_failure_keys_verified = bool(
        redline_failure_keys_comparable or candidate_red == 0
    )
    new_redline_keys = (
        candidate_redline_keys - champion_redline_keys
        if redline_failure_keys_comparable else set()
    )
    resolved_redline_keys = (
        champion_redline_keys - candidate_redline_keys
        if redline_failure_keys_comparable else set()
    )
    if redline_failure_keys_comparable:
        red_line_new = bool(new_redline_keys)
    elif candidate_red == 0:
        red_line_new = False
    elif candidate_red > champion_red:
        # 即使缺少键，计数增加也足以证明至少出现了一个新增失败。
        red_line_new = True
    else:
        # 计数下降/持平无法证明失败集合没有发生替换。
        red_line_new = None
    champion_hard_key = (champion_red, champion_floor)
    candidate_hard_key = (candidate_red, candidate_floor)
    hard_failures_not_worse = bool(
        candidate_red <= champion_red
        and candidate_floor <= champion_floor
    )
    hard_failures_improved = bool(
        hard_failures_not_worse
        and candidate_hard_key != champion_hard_key
    )
    hard_failures_equal = candidate_hard_key == champion_hard_key
    hard_failures_regressed = not hard_failures_not_worse

    target_check = dict(target_check or {})
    target_check_available = bool(target_check.get("available"))
    target_check_delta = (
        float(target_check.get("delta"))
        if target_check_available and target_check.get("delta") is not None
        else None
    )
    target_check_stable = bool(
        not target_check_available
        or (
            target_check_delta is not None
            and target_check_delta + 1e-12 >= -TARGET_CHECK_DROP_TOLERANCE
        )
    )

    overall_delta = round(
        cand_dims.get("overall", 0.0) - champion_dims.get("overall", 0.0),
        3,
    )
    effective_overall_improvement = (
        overall_delta + 1e-12 >= float(min_overall_improvement)
    )

    holdout = dict(holdout or {})
    holdout_available = bool(holdout.get("available"))
    holdout_champion = dict(holdout.get("champion_scores") or {})
    holdout_candidate = dict(holdout.get("candidate_scores") or {})
    holdout_score_delta = {
        d: round(
            holdout_candidate.get(d, 0.0) - holdout_champion.get(d, 0.0),
            3,
        )
        for d in dims
    } if holdout_available else {}
    holdout_regressed_dims = [
        d for d in dims
        if holdout_champion.get(d, 0.0) - holdout_candidate.get(d, 0.0) > tol
    ] if holdout_available else []
    holdout_champion_hard = dict(holdout.get("champion_hard") or {})
    holdout_candidate_hard = dict(holdout.get("candidate_hard") or {})
    holdout_champion_key = (
        int(holdout_champion_hard.get("redline_failures", 0) or 0),
        int(holdout_champion_hard.get("hard_floor_failures", 0) or 0),
    )
    holdout_candidate_key = (
        int(holdout_candidate_hard.get("redline_failures", 0) or 0),
        int(holdout_candidate_hard.get("hard_floor_failures", 0) or 0),
    )
    holdout_champion_redline_keys, holdout_champion_keys_available = (
        _redline_failure_key_set(holdout_champion_hard)
    )
    holdout_candidate_redline_keys, holdout_candidate_keys_available = (
        _redline_failure_key_set(holdout_candidate_hard)
    )
    holdout_redline_keys_comparable = bool(
        holdout_champion_keys_available
        and holdout_candidate_keys_available
    )
    holdout_redline_keys_verified = bool(
        holdout_redline_keys_comparable or holdout_candidate_key[0] == 0
    )
    holdout_new_redline_keys = (
        holdout_candidate_redline_keys - holdout_champion_redline_keys
        if holdout_redline_keys_comparable else set()
    )
    if holdout_redline_keys_comparable:
        holdout_red_line_new = bool(holdout_new_redline_keys)
    elif holdout_candidate_key[0] == 0:
        holdout_red_line_new = False
    elif holdout_candidate_key[0] > holdout_champion_key[0]:
        holdout_red_line_new = True
    else:
        holdout_red_line_new = None
    holdout_overall_delta = (
        round(
            holdout_candidate.get("overall", 0.0)
            - holdout_champion.get("overall", 0.0),
            3,
        )
        if holdout_available else None
    )
    holdout_hard_not_worse = bool(
        holdout_candidate_key[0] <= holdout_champion_key[0]
        and holdout_candidate_key[1] <= holdout_champion_key[1]
    )
    holdout_guard_passed = bool(
        holdout_available
        and holdout_hard_not_worse
        and not holdout_regressed_dims
        and holdout_overall_delta is not None
        and holdout_overall_delta + 1e-12 >= -HOLDOUT_OVERALL_DROP_TOLERANCE
    )
    holdout_passed = bool(
        holdout_guard_passed
        and holdout_overall_delta is not None
        and holdout_overall_delta >= 0
    )

    hard_improvement_path = bool(
        hard_failures_improved
        and all_dims_stable
        and target_check_stable
        and (not holdout_available or holdout_guard_passed)
    )
    overall_improvement_path = bool(
        hard_failures_equal
        and effective_overall_improvement
        and all_dims_stable
        and target_check_stable
        and holdout_passed
    )
    adopt = hard_improvement_path or overall_improvement_path
    verdict = "adopted" if adopt else "rejected"

    reasons = {
        "gate_rule": "net_hard_improvement_champion/v4",
        "comparison_baseline": "historical_champion",
        "improved": adopt,
        "adoption_path": (
            "hard_failures_reduced" if hard_improvement_path
            else "overall_effective_and_holdout_passed" if overall_improvement_path
            else None
        ),
        "improved_dims": improved_dims,
        "overall_delta": overall_delta,
        "min_overall_improvement": float(min_overall_improvement),
        "effective_overall_improvement": effective_overall_improvement,
        "regressed_dim": regressed_dims[0] if regressed_dims else None,
        "regressed_dims": regressed_dims,
        "regressed_tol": tol,
        "all_dimensions_stable": all_dims_stable,
        "target_check_available": target_check_available,
        "target_check_delta": target_check_delta,
        "target_check_drop_tolerance": TARGET_CHECK_DROP_TOLERANCE,
        "target_check_stable": target_check_stable,
        "hard_failures_not_worse": hard_failures_not_worse,
        "hard_failures_improved": hard_failures_improved,
        "hard_failures_equal": hard_failures_equal,
        "hard_failures_regressed": hard_failures_regressed,
        "champion_redline_failures": champion_red,
        "candidate_redline_failures": candidate_red,
        "champion_hard_floor_failures": champion_floor,
        "candidate_hard_floor_failures": candidate_floor,
        "red_line_new": red_line_new,
        "redline_failure_keys_comparable": redline_failure_keys_comparable,
        "redline_failure_keys_verified": redline_failure_keys_verified,
        "champion_redline_failure_keys": [
            list(item) for item in sorted(champion_redline_keys)
        ],
        "candidate_redline_failure_keys": [
            list(item) for item in sorted(candidate_redline_keys)
        ],
        "new_redline_failure_keys": [
            list(item) for item in sorted(new_redline_keys)
        ],
        "resolved_redline_failure_keys": [
            list(item) for item in sorted(resolved_redline_keys)
        ],
        # 保留旧键，避免旧 UI/日志解析断裂。
        "parent_red_line_fails": champion_red,
        "cand_red_line_fails": candidate_red,
        "holdout_available": holdout_available,
        "holdout_guard_passed": holdout_guard_passed,
        "holdout_hard_not_worse": holdout_hard_not_worse,
        "holdout_overall_drop_tolerance": HOLDOUT_OVERALL_DROP_TOLERANCE,
        "holdout_passed": holdout_passed,
        "holdout_overall_delta": holdout_overall_delta,
        "holdout_regressed_dims": holdout_regressed_dims,
        "holdout_champion_hard_key": list(holdout_champion_key),
        "holdout_candidate_hard_key": list(holdout_candidate_key),
        "holdout_redline_failure_keys_comparable": (
            holdout_redline_keys_comparable
        ),
        "holdout_redline_failure_keys_verified": (
            holdout_redline_keys_verified
        ),
        "holdout_new_redline_failure_keys": [
            list(item) for item in sorted(holdout_new_redline_keys)
        ],
        "holdout_red_line_new": holdout_red_line_new,
        "target_check": target_check,
    }
    if not adopt:
        if hard_failures_regressed:
            reasons["message"] = "硬红线数或维度硬底线数相对历史 champion 恶化"
        elif regressed_dims:
            reasons["message"] = "维度 %s 回退超容差 %.2f" % (
                ", ".join(regressed_dims), tol,
            )
        elif not target_check_stable:
            reasons["message"] = (
                "本轮目标 check 回退 %.3f，超过容差 %.3f"
                % (target_check_delta or 0.0, TARGET_CHECK_DROP_TOLERANCE)
            )
        elif hard_failures_improved and holdout_available and not holdout_guard_passed:
            reasons["message"] = "dev 净硬失败改善，但 holdout 保护未通过"
        elif hard_failures_equal and not effective_overall_improvement:
            reasons["message"] = (
                "硬失败持平，overall 提升 %.3f 未达最小有效阈值 %.3f"
                % (overall_delta, min_overall_improvement)
            )
        elif hard_failures_equal and not holdout_available:
            reasons["message"] = "硬失败持平且 overall 有效提升，但缺少独立 holdout"
        elif hard_failures_equal and not holdout_passed:
            reasons["message"] = "硬失败持平且 overall 有效提升，但 holdout 未通过"
        else:
            reasons["message"] = "候选未满足两条允许采纳的路径"
    else:
        reasons["message"] = (
            "净硬失败 Pareto 改善，且维度、目标 check "
            "与（如有）holdout 保护通过"
            if hard_improvement_path
            else "overall 达最小有效提升，且独立 holdout 通过"
        )
    return adopt, verdict, reasons


def no_regression_tol(rubric: Dict[str, Any]) -> float:
    for g in rubric.get("gates", []):
        if g.get("id") == "no_regression" and g.get("drop_tolerance") is not None:
            return float(g["drop_tolerance"])
    return 0.15


def min_overall_improvement(rubric: Dict[str, Any]) -> float:
    """返回平台统一的最小有效 overall 提升。"""
    return MIN_EFFECTIVE_OVERALL_DELTA


def hydrate_gate_policy(rubric: Dict[str, Any]) -> Dict[str, Any]:
    """给旧会话补齐 Gate v4 机读元数据，避免 UI 继续展示旧规则。"""
    for gate in rubric.get("gates", []):
        if gate.get("id") != "no_regression":
            continue
        # 这是平台统一 Gate 策略，不让旧会话快照里的 0.05 覆盖新门槛。
        gate["min_overall_improvement"] = MIN_EFFECTIVE_OVERALL_DELTA
        gate["decision_rule_version"] = "net_hard_improvement_champion/v4"
        gate["comparison_baseline"] = "historical_champion"
        gate["holdout_split"] = "test"
        gate["target_check_drop_tolerance"] = TARGET_CHECK_DROP_TOLERANCE
        gate["holdout_overall_drop_tolerance"] = (
            HOLDOUT_OVERALL_DROP_TOLERANCE
        )
        gate["rule"] = (
            "候选永远与历史 champion 比较；"
            "(case_id, check_id) 失败键迁移仅作诊断，不再一票否决。"
            "仅当硬红线数和维度硬底线数 Pareto 改善，"
            "且全维、目标 check 及已有 holdout 通过保护时采纳；"
            "或者硬失败持平、overall 至少提升 %.2f "
            "并通过 test holdout 时采纳。"
            % float(gate["min_overall_improvement"])
        )
        break
    return rubric


def _records_for_split(entry: Dict[str, Any], split: str) -> List[Any]:
    records = list(entry.get("_recs") or [])
    selected = [r for r in records if getattr(r, "dataset_split", None) == split]
    return selected


def hard_failure_metrics(session, entry: Dict[str, Any], split: str = "dev") -> Dict[str, Any]:
    """按 case/check 统计硬红线，按 case/dimension 统计维度硬底线。

    旧记录没有逐 check 分时，红线数回退到 mean_scores.red_line_fails；
    这个兼容分支只用于旧 mock/直接六维分。
    """
    records = _records_for_split(entry, split)
    if not records:
        aggregate = entry.get("test") if split == "test" else entry.get("dev")
        aggregate = aggregate or {}
        fallback_floor_dims = [
            str(dimension.get("name"))
            for dimension in session.rubric.get("dimensions", [])
            if dimension.get("name")
            and dimension.get("hard_floor") is not None
            and aggregate.get(dimension.get("name")) is not None
            and float(aggregate[dimension.get("name")])
            < float(dimension["hard_floor"])
        ]
        fallback_red = int(aggregate.get("red_line_fails", 0) or 0)
        return {
            "available": False,
            "redline_failures": fallback_red,
            "hard_floor_failures": len(fallback_floor_dims),
            "redline_failure_keys_available": False,
            "redline_failure_keys": [
                ["legacy", str(index)] for index in range(fallback_red)
            ],
            "hard_floor_failure_keys": [
                ["aggregate", dim] for dim in fallback_floor_dims
            ],
        }

    redline_ids = {
        str(check.get("id"))
        for dimension in session.rubric.get("dimensions", [])
        for check in dimension.get("checks", [])
        if check.get("id") and check.get("redline")
    }
    hard_floors = {
        str(dimension.get("name")): float(dimension["hard_floor"])
        for dimension in session.rubric.get("dimensions", [])
        if dimension.get("name") and dimension.get("hard_floor") is not None
    }
    redline_keys = set()
    hard_floor_keys = set()
    redline_failure_keys_available = bool(redline_ids)
    for record in records:
        case_id = str(getattr(record, "case_id", ""))
        checks = dict(getattr(record, "judge_checks", {}) or {})
        if not redline_ids.issubset(set(checks)):
            redline_failure_keys_available = False
        for check_id in redline_ids:
            value = checks.get(check_id)
            if value is not None and float(value) <= 0:
                redline_keys.add((case_id, check_id))
        scores = dict(getattr(record, "scores", {}) or {})
        for dim, floor in hard_floors.items():
            value = scores.get(dim)
            if value is not None and float(value) < floor:
                hard_floor_keys.add((case_id, dim))

    aggregate = entry.get("test") if split == "test" else entry.get("dev")
    if not redline_failure_keys_available:
        fallback_count = int((aggregate or {}).get("red_line_fails", 0) or 0)
        redline_keys = {("legacy", str(i)) for i in range(fallback_count)}
    return {
        "available": True,
        "redline_failures": len(redline_keys),
        "hard_floor_failures": len(hard_floor_keys),
        "redline_failure_keys_available": redline_failure_keys_available,
        "redline_failure_keys": [list(item) for item in sorted(redline_keys)],
        "hard_floor_failure_keys": [list(item) for item in sorted(hard_floor_keys)],
    }


def champion_entry(session) -> Optional[Dict[str, Any]]:
    """返回词典序历史 champion，而不是最近一个 adopted 版本。"""
    eligible = [
        entry for entry in session.versions
        if entry.get("adopted")
        and (entry.get("dev") or {}).get("overall") is not None
    ]
    if not eligible:
        return None

    def rank(entry):
        hard = hard_failure_metrics(session, entry, "dev")
        return (
            -int(hard.get("redline_failures", 0) or 0),
            -int(hard.get("hard_floor_failures", 0) or 0),
            float((entry.get("dev") or {}).get("overall", 0.0) or 0.0),
        )

    return max(eligible, key=rank)


def target_check_metrics(
    session,
    champion: Dict[str, Any],
    candidate: Dict[str, Any],
    proposal: Optional[Dict[str, Any]],
    split: str = "dev",
) -> Dict[str, Any]:
    """计算本轮目标 check 的平均分变化，作为 Gate 最后一层归因证据。"""
    proposal = proposal or {}
    targets = {str(item) for item in proposal.get("targets_failures", []) if item}
    all_check_ids = {
        str(check.get("id"))
        for dimension in session.rubric.get("dimensions", [])
        for check in dimension.get("checks", [])
        if check.get("id")
    }
    check_ids = {item for item in targets if item in all_check_ids}
    for failure in champion.get("failure_report") or champion.get("failures") or []:
        if str(failure.get("pattern_id")) not in targets:
            continue
        for evidence in failure.get("evidence", []):
            check_id = evidence.get("check_id")
            if check_id:
                check_ids.add(str(check_id))

    def values(entry):
        out = []
        for record in _records_for_split(entry, split):
            checks = dict(getattr(record, "judge_checks", {}) or {})
            out.extend(
                float(checks[check_id])
                for check_id in check_ids
                if checks.get(check_id) is not None
            )
        return out

    champion_values = values(champion)
    candidate_values = values(candidate)
    champion_mean = (
        round(sum(champion_values) / len(champion_values), 3)
        if champion_values else None
    )
    candidate_mean = (
        round(sum(candidate_values) / len(candidate_values), 3)
        if candidate_values else None
    )
    return {
        "check_ids": sorted(check_ids),
        "champion_mean": champion_mean,
        "candidate_mean": candidate_mean,
        "delta": (
            round(candidate_mean - champion_mean, 3)
            if champion_mean is not None and candidate_mean is not None
            else None
        ),
        "available": champion_mean is not None and candidate_mean is not None,
    }


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


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def optimizer_patch_constraints(parent_text: str) -> Dict[str, int]:
    """Optimizer patch 的硬预算；已超总预算的旧版只能持平或缩短。"""
    configured_total = _positive_int_env(
        "OPTIMIZER_MAX_INSTRUCTION_CHARS",
        DEFAULT_MAX_INSTRUCTION_CHARS,
    )
    return {
        "max_instruction_chars": max(configured_total, len(parent_text)),
        "configured_max_instruction_chars": configured_total,
        "max_net_growth_chars": _positive_int_env(
            "OPTIMIZER_MAX_NET_GROWTH_CHARS",
            DEFAULT_MAX_NET_GROWTH_CHARS,
        ),
        "max_patch_operations": _positive_int_env(
            "OPTIMIZER_MAX_PATCH_OPERATIONS",
            DEFAULT_MAX_PATCH_OPERATIONS,
        ),
        "min_experiment_examples": MIN_EXPERIMENT_EXAMPLES,
        "max_experiment_examples": MAX_EXPERIMENT_EXAMPLES,
    }


_QUOTED_TEXT = re.compile(r"[“‘\"]([^”’\"]{4,180})[”’\"]")
_TOKEN_TEXT = re.compile(r"[\u4e00-\u9fffA-Za-z0-9.%]+")


def _report_sentence(report: str, reasoning: str) -> Optional[str]:
    """从 Judge 理由中的引文反查报告，只返回真实存在的原句/原行。"""
    report = report or ""
    if not report.strip():
        return None
    quoted = [item.strip() for item in _QUOTED_TEXT.findall(reasoning or "")]
    fragments = list(quoted)
    for item in quoted:
        fragments.extend(
            part.strip()
            for part in re.split(r"[，,;；。]", item)
            if len(part.strip()) >= 6
        )
    lines = [line.strip() for line in report.splitlines() if line.strip()]
    # dict 保留 Judge 引文顺序；同长度引文不能受 Python hash 随机化影响。
    unique_fragments = list(dict.fromkeys(fragments))
    for fragment in sorted(unique_fragments, key=len, reverse=True):
        for line in lines:
            if fragment in line:
                sentences = re.findall(r"[^。！？!?]+[。！？!?]?", line)
                for sentence in sentences:
                    if fragment in sentence:
                        return sentence.strip()
                # 引文跨句或表格行没有句末标点时，保留完整原行，不做截断。
                return line
    return None


def _session_data_version(session=None) -> Optional[str]:
    if session is None:
        return None
    marker = getattr(session, "experiment_data", None)
    if not marker and getattr(session, "id", None):
        try:
            import persistence as persist
            metadata = persist.load_meta(session.id) or {}
            marker = metadata.get("experiment_data") or metadata.get(
                "data_version"
            )
        except (ImportError, OSError, ValueError, TypeError):
            marker = None
    if isinstance(marker, dict):
        marker = marker.get("id") or marker.get("label")
    match = re.search(r"v([123])", str(marker or "").lower())
    return "v" + match.group(1) if match else None


def _dataset_roots(session=None) -> List[Path]:
    """返回所有可用数据集根，兼容多版本配置与仓库内默认布局。"""
    project_root = Path(__file__).resolve().parent.parent
    selected = _session_data_version(session)
    versions = [selected] if selected else []
    versions.extend(
        version for version in ("v1", "v2", "v3")
        if version not in versions
    )
    configured = [os.environ.get("OPENHARNESS_WB_DATASET")]
    configured.extend(
        os.environ.get("OPENHARNESS_WB_DATASET_" + version.upper())
        for version in versions
    )
    defaults = []
    if selected:
        defaults.append(
            project_root / "data" / "research-report" / selected / "data.json"
        )
    defaults.append(
        project_root / "data" / "v3_20260804_real_project_package" / "data.json"
    )
    defaults.extend(
        project_root / "data" / "research-report" / version / "data.json"
        for version in versions
        if version != selected
    )
    roots = []
    for value in configured + defaults:
        if not value:
            continue
        path = Path(value).expanduser().resolve()
        if path.is_file() and path.parent not in roots:
            roots.append(path.parent)
    return roots


def _structured_data_for_case(
    case: Dict[str, Any],
    dataset_roots: Optional[List[Path]] = None,
) -> Optional[Dict[str, Any]]:
    embedded = case.get("structured_data")
    if isinstance(embedded, dict) and isinstance(embedded.get("items"), list):
        return embedded
    candidates = []
    dataset_roots = dataset_roots if dataset_roots is not None else _dataset_roots()
    for item in case.get("input_files") or []:
        if not isinstance(item, dict) or not item.get("source"):
            continue
        source = Path(str(item["source"])).expanduser()
        resolved_sources = (
            [source.resolve()]
            if source.is_absolute()
            else [(root / source).resolve() for root in dataset_roots]
        )
        for resolved in resolved_sources:
            if resolved.name == "structured_data.json":
                candidates.append(resolved)
            elif resolved.name == "source":
                candidates.append(resolved.parent / "structured_data.json")
    unique = list(dict.fromkeys(candidates))
    for path in unique:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, dict)
            and isinstance(payload.get("items"), list)
            and str(payload.get("case_id") or "")
            == str(case.get("case_id") or "")
        ):
            # roots 已按 Session 数据版本和显式配置排序；首个匹配即权威来源。
            return payload
    return None


def _match_terms(text: str) -> set[str]:
    compact = "".join(_TOKEN_TEXT.findall(text or "")).lower()
    terms = {
        compact[index:index + 2]
        for index in range(max(0, len(compact) - 1))
    }
    terms.update(re.findall(r"\d+(?:\.\d+)?%?", compact))
    return terms


def _source_evidence(structured: Dict[str, Any], query: str) -> Optional[str]:
    query_terms = _match_terms(query)
    ranked = []
    for item in structured.get("items") or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        item_terms = _match_terms(content)
        overlap = len(query_terms & item_terms)
        number_overlap = len(
            set(re.findall(r"\d+(?:\.\d+)?%?", query or ""))
            & set(re.findall(r"\d+(?:\.\d+)?%?", content))
        )
        score = overlap + 8 * number_overlap
        if score <= 0:
            continue
        ranked.append((score, item))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    snippets = []
    for _, item in ranked[:2]:
        snippets.append(
            "%s | %s | %s"
            % (
                item.get("id", ""),
                item.get("source_ref", "未标注来源"),
                str(item.get("content") or "")[:500],
            )
        )
    return "\n".join(snippets)


def _check_catalog(rubric: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(check.get("id")): {
            "dimension": dimension.get("name"),
            "label": check.get("label", check.get("id")),
            "desc": check.get("desc", ""),
            "redline": bool(check.get("redline")),
            "priority": int(
                ((check.get("optimizer") or {}).get("priority", 100))
                if isinstance(check.get("optimizer"), dict)
                else 100
            ),
            "pattern_id": (
                (check.get("optimizer") or {}).get("pattern_id")
                if isinstance(check.get("optimizer"), dict)
                else None
            ),
        }
        for dimension in rubric.get("dimensions", [])
        for check in dimension.get("checks", [])
        if check.get("id")
    }


def build_experiment_evidence(
    session,
    champion: Dict[str, Any],
    failures: List[Dict[str, Any]],
    limit: Optional[int] = 10,
    per_check_limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """生成可验证的「报告原句—素材证据—Judge 判定—改法」候选包。"""
    reports = session.report_outputs.get(champion.get("version"), {})
    cases = {str(case.get("case_id")): case for case in session.cases}
    checks = _check_catalog(session.rubric)
    packets = []
    seen = set()
    per_check_counts: Dict[str, int] = {}
    structured_cache = {}
    dataset_roots = _dataset_roots(session)
    for failure in failures or []:
        pattern_id = str(failure.get("pattern_id") or "")
        for evidence in failure.get("evidence") or []:
            case_id = str(evidence.get("case_id") or "")
            check_id = str(evidence.get("check_id") or "")
            key = (case_id, check_id)
            if not case_id or not check_id or key in seen:
                continue
            if (
                per_check_limit is not None
                and per_check_counts.get(check_id, 0) >= per_check_limit
            ):
                continue
            reasoning = str(evidence.get("reasoning") or "").strip()
            sentence = _report_sentence(reports.get(case_id, ""), reasoning)
            case = cases.get(case_id)
            if not sentence or not case or not reasoning:
                continue
            if case_id not in structured_cache:
                structured_cache[case_id] = _structured_data_for_case(
                    case,
                    dataset_roots,
                )
            structured = structured_cache[case_id]
            source = _source_evidence(
                structured or {},
                sentence + "\n" + reasoning,
            )
            if not source:
                continue
            check = checks.get(check_id, {})
            packet = {
                "evidence_id": "EXP-%02d" % (len(packets) + 1),
                "pattern_id": pattern_id,
                "case_id": case_id,
                "check_id": check_id,
                "report_sentence": sentence,
                "evidence": source,
                "judge_verdict": "%s=%s；%s" % (
                    check_id,
                    evidence.get("value"),
                    reasoning,
                ),
                "expected_change_hint": (
                    "仅修改会导致该原句的生成行为，使其满足 %s：%s"
                    % (check.get("label", check_id), check.get("desc", ""))
                ),
            }
            packets.append(packet)
            seen.add(key)
            per_check_counts[check_id] = per_check_counts.get(check_id, 0) + 1
            if limit is not None and len(packets) >= limit:
                return packets
    return packets


def build_failure_inventory(
    rubric: Dict[str, Any],
    failures: List[Dict[str, Any]],
    evidence_catalog: List[Dict[str, Any]],
    history: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """把全量 case 失败汇总为 check 级 backlog，不丢弃长尾失败。"""
    checks = _check_catalog(rubric)
    replayable_counts: Dict[Tuple[str, str], int] = {}
    for packet in evidence_catalog or []:
        key = (
            str(packet.get("pattern_id") or ""),
            str(packet.get("check_id") or ""),
        )
        replayable_counts[key] = replayable_counts.get(key, 0) + 1

    targeted_history = [
        item for item in history or [] if item.get("targets")
    ]
    inventory = []
    for failure in failures or []:
        pattern_id = str(failure.get("pattern_id") or "")
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for evidence in failure.get("evidence") or []:
            check_id = str(evidence.get("check_id") or "")
            if check_id:
                grouped.setdefault(check_id, []).append(evidence)
        for check_id, evidence_rows in grouped.items():
            metadata = checks.get(check_id, {})
            miss_count = sum(
                float(item.get("value", 1.0)) <= 0
                for item in evidence_rows
            )
            partial_count = sum(
                0 < float(item.get("value", 1.0)) < 1
                for item in evidence_rows
            )
            case_ids = sorted({
                str(item.get("case_id") or "")
                for item in evidence_rows
                if item.get("case_id")
            })
            matched_history = [
                (index, item)
                for index, item in enumerate(targeted_history)
                if check_id in {str(v) for v in item.get("targets") or []}
                or pattern_id in {str(v) for v in item.get("targets") or []}
            ]
            consecutive = 0
            for item in reversed(targeted_history):
                targets = {str(v) for v in item.get("targets") or []}
                if check_id in targets or pattern_id in targets:
                    consecutive += 1
                else:
                    break
            last_index = matched_history[-1][0] if matched_history else None
            last_item = matched_history[-1][1] if matched_history else {}
            recent_rejections = sum(
                item.get("verdict") == "rejected"
                or item.get("candidate_state") == "rejected"
                for _, item in matched_history[-3:]
            )
            replayable = replayable_counts.get((pattern_id, check_id), 0)
            inventory.append({
                "check_id": check_id,
                "pattern_id": pattern_id,
                "dimension": metadata.get("dimension")
                or (failure.get("affected_dims") or [None])[0],
                "label": metadata.get("label", check_id),
                "redline": bool(metadata.get("redline")),
                "priority": int(metadata.get("priority", 100)),
                "miss_count": miss_count,
                "partial_count": partial_count,
                "failure_mass": 2 * miss_count + partial_count,
                "affected_case_count": len(case_ids),
                "affected_case_ids": case_ids,
                "replayable_evidence_count": replayable,
                "last_targeted_version": last_item.get("version"),
                "last_targeted_verdict": last_item.get("verdict"),
                "rounds_since_last_targeted": (
                    len(targeted_history) - 1 - last_index
                    if last_index is not None else len(targeted_history)
                ),
                "consecutive_target_count": consecutive,
                "recent_rejection_count": recent_rejections,
                "cooldown_recommended": bool(
                    consecutive >= 2
                    or (
                        matched_history
                        and last_index == len(targeted_history) - 1
                        and (
                            last_item.get("verdict") == "rejected"
                            or last_item.get("candidate_state") == "rejected"
                        )
                    )
                ),
            })
    inventory.sort(key=lambda item: (
        not item["redline"],
        -item["failure_mass"],
        -item["rounds_since_last_targeted"],
        item["priority"],
        item["check_id"],
    ))
    return inventory


def select_diagnosis_candidates(
    inventory: List[Dict[str, Any]],
    minimum_examples: int = MIN_EXPERIMENT_EXAMPLES,
    limit: int = MAX_DIAGNOSIS_CANDIDATES,
) -> List[Dict[str, Any]]:
    """选出有足够回放证据的主要 failure，避免单 check 连续垄断。"""
    actionable = [
        item for item in inventory
        if int(item.get("replayable_evidence_count") or 0) >= minimum_examples
    ]
    if not actionable:
        return []
    not_cooling = [
        item for item in actionable if not item.get("cooldown_recommended")
    ]
    pool = not_cooling or actionable
    redlines = [item for item in pool if item.get("redline")]
    non_redlines = [item for item in pool if not item.get("redline")]
    selected = redlines[:limit]
    if len(selected) < limit:
        selected.extend(non_redlines[:limit - len(selected)])
    return [dict(item) for item in selected]


def build_diagnosis_evidence(
    candidates: List[Dict[str, Any]],
    evidence_catalog: List[Dict[str, Any]],
    per_candidate: int = DIAGNOSIS_EVIDENCE_PER_CANDIDATE,
) -> List[Dict[str, Any]]:
    """每个候选 check 等额抽样，供 Diagnosis LLM 做跨 case 归因。"""
    selected = []
    for candidate in candidates or []:
        pattern_id = str(candidate.get("pattern_id") or "")
        check_id = str(candidate.get("check_id") or "")
        matching = [
            item for item in evidence_catalog or []
            if str(item.get("pattern_id") or "") == pattern_id
            and str(item.get("check_id") or "") == check_id
        ]
        selected.extend(matching[:per_candidate])
    return selected


def build_optimizer_context(session, account=None) -> Dict[str, Any]:
    """装配 carry-forward 迭代记忆。只读 session,不改状态。"""
    rubric = session.rubric
    dims = list(session.dims)
    target = rubric.get("target", {})
    cur = champion_entry(session) or session._current()
    cur_dev = _version_dev(cur)
    cur_skill = cur["skill"]

    # 当前最优:全文 + 每维分
    current_best_prose = production_skill_policy.sanitize_legacy_production_text(
        (cur_skill.instructions or {}).get("prose", "")
    )
    current_best_contract = (
        production_skill_policy.sanitize_legacy_production_text(
            (cur_skill.instructions or {}).get("requirement_contract", "")
        )
    )
    current_best = {
        "version": cur["version"],
        "requirement_contract": current_best_contract,
        "instructions_text": current_best_prose,
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
            "priority": f.get("priority"),
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
        "gate_policy": {
            "comparison_baseline": "historical_champion",
            "hard_failure_rule": "pareto_not_worse",
            "all_dimensions_no_material_regression": True,
            "redline_failure_key_churn_is_diagnostic_only": True,
            "dimension_drop_tolerance": no_regression_tol(rubric),
            "target_check_drop_tolerance": TARGET_CHECK_DROP_TOLERANCE,
            "min_effective_overall_improvement": min_overall_improvement(rubric),
            "overall_path_requires_test_holdout": True,
            "hard_improvement_path_uses_holdout_when_available": True,
            "allowed_adoption_paths": [
                "net_hard_failures_improved_and_safety_guards_passed",
                "hard_failures_equal_and_overall_effective_and_holdout_passed",
            ],
        },
    }
    evidence_catalog = build_experiment_evidence(
        session,
        cur,
        failures,
        limit=None,
        per_check_limit=MAX_REPLAYABLE_EVIDENCE_PER_CHECK,
    )
    patch_constraints = optimizer_patch_constraints(
        current_best["instructions_text"],
    )
    failure_inventory = build_failure_inventory(
        rubric,
        failures,
        evidence_catalog,
        history,
    )
    diagnosis_candidates = select_diagnosis_candidates(
        failure_inventory,
        minimum_examples=patch_constraints["min_experiment_examples"],
    )
    diagnosis_evidence = build_diagnosis_evidence(
        diagnosis_candidates,
        evidence_catalog,
    )
    root_cause_signals = []
    if cur.get("failure_mapping_error"):
        root_cause_signals.append({
            "type": "judge",
            "reason": "Judge 失败 check 无 optimizer 映射",
            "check_ids": list(cur.get("failure_mapping_error") or []),
            "blocks_skill_patch": True,
        })

    return {
        "requirement": getattr(session, "requirement", "") or "",
        "rubric": {
            "product": rubric.get("product"),
            "overall_target": target.get("overall"),
            "dimensions": _dim_summary(rubric),
            "no_regression_tol": no_regression_tol(rubric),
            "min_overall_improvement": min_overall_improvement(rubric),
        },
        "current_best": current_best,
        "must_preserve": must_preserve,
        "open_failures": open_failures,
        "history": history,
        "tried_rejected": tried_rejected,
        "guardrails": guardrails,
        "production_quality_requirements": (
            production_skill_policy.quality_requirements(rubric)
        ),
        "production_requirement_by_check": {
            str(check.get("id")): production_skill_policy.requirement_for_check(
                rubric,
                str(check.get("id") or ""),
            )
            for dimension in rubric.get("dimensions", [])
            for check in dimension.get("checks", [])
            if check.get("id")
        },
        "mandatory_production_requirements": (
            production_skill_policy.mandatory_requirements(rubric)
        ),
        "failure_inventory": failure_inventory,
        "diagnosis_candidates": diagnosis_candidates,
        "diagnosis_evidence": diagnosis_evidence,
        # 兼容旧日志/UI；新 Optimizer 的 Diagnosis 阶段读取 diagnosis_evidence。
        "experiment_evidence": diagnosis_evidence,
        # 仅供 Diagnosis 选定目标后，Patch 阶段取该目标的 3–5 条证据。
        # Diagnosis prompt 不序列化这个全量目录。
        "evidence_catalog": evidence_catalog,
        "patch_constraints": patch_constraints,
        "root_cause_policy": {
            "allowed_types": [
                "skill",
                "data",
                "judge",
                "replay_protocol",
                "mixed",
            ],
            "skill_patch_allowed_only_when": "root_cause.type == skill",
            "non_skill_types_block_patch": [
                "data",
                "judge",
                "replay_protocol",
                "mixed",
            ],
            "deterministic_signals": root_cause_signals,
        },
    }
