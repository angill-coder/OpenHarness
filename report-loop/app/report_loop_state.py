"""Report Loop report-version state transitions and public action state."""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from report_loop_gate import evaluate_candidate_gate

def next_version(run: Dict) -> str:
    return "v%d" % (len(run.get("revisions") or []) + 1)


def revision(run: Dict, version: str) -> Dict:
    match = next(
        (
            item for item in run.get("revisions", [])
            if item.get("version") == version
        ),
        None,
    )
    if match is None:
        raise ValueError("报告版本不存在: %s" % version)
    return match


def current_revision(run: Dict) -> Dict:
    version = run.get("current_version")
    if not version:
        raise ValueError("尚无已采纳报告")
    return revision(run, version)


def _gate_score_view(judgment: Dict) -> Dict:
    result = dict(judgment.get("dimensions") or {})
    result["overall"] = float(judgment.get("overall") or 0)
    result["red_line_fails"] = len(judgment.get("redline_checks") or [])
    return result


def _target_dimensions(parent: Dict, rubric: Dict) -> list:
    failed = (parent.get("failure_report") or {}).get("dimensions") or []
    targets = [
        str(item.get("dimension"))
        for item in failed
        if item.get("dimension")
    ]
    if targets:
        return targets
    return [
        str(item.get("name"))
        for item in rubric.get("dimensions", [])
        if item.get("name")
    ]


def _drop_tolerance(rubric: Dict) -> float:
    gate = next(
        (
            item for item in rubric.get("gates", [])
            if item.get("id") == "no_regression"
        ),
        {},
    )
    return float(gate.get("drop_tolerance", 0.15))


@dataclass(frozen=True)
class StopReason:
    """一个让 report loop 停机的终止条件。"""
    code: str
    message: str


def _latest_attempt_usage(attempt: Dict) -> Dict[str, int]:
    """WorkBuddy resume usage is cumulative; use only the latest non-empty round."""
    rounds = ((attempt.get("usage") or {}).get("rounds") or [])
    usage = next(
        (
            item.get("usage")
            for item in reversed(rounds)
            if isinstance(item, dict) and isinstance(item.get("usage"), dict)
            and item.get("usage")
        ),
        {},
    )
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }


def budget_state(run: Dict) -> Dict:
    totals = {"input_tokens": 0, "output_tokens": 0}
    for item in run.get("revisions") or []:
        case_trace = ((item.get("trace") or {}).get("case") or {})
        for attempt in case_trace.get("attempts") or []:
            usage = _latest_attempt_usage(attempt)
            for key in totals:
                totals[key] += usage[key]
    created_at = float(run.get("created_at") or time.time())
    return {
        **totals,
        "total_tokens": totals["input_tokens"] + totals["output_tokens"],
        "elapsed_seconds": max(0, int(time.time() - created_at)),
        "token_scope": "runner_latest_cumulative_usage_per_attempt",
    }


def _unrepairable_failures(candidate: Dict) -> List[Dict]:
    report = candidate.get("failure_report") or {}
    found = []
    if (
        report.get("unrepairable")
        or report.get("requires_human")
        or report.get("needs_material")
        or str(report.get("repairability") or "").lower() == "unrepairable"
    ):
        found.append(report)
    for dimension in report.get("dimensions") or []:
        for failure in dimension.get("failures") or []:
            if (
                failure.get("unrepairable")
                or failure.get("requires_human")
                or failure.get("needs_material")
                or str(failure.get("repairability") or "").lower() == "unrepairable"
            ):
                found.append(failure)
    return found


def _stop_on_unrepairable(run: Dict, candidate: Dict, ctx: Dict) -> Optional[StopReason]:
    if not (run.get("stop_policy") or {}).get("stop_on_unrepairable_failure", False):
        return None
    failures = _unrepairable_failures(candidate)
    if failures:
        labels = [
            str(item.get("label") or item.get("check_id") or "不可修复失败")
            for item in failures[:3]
        ]
        return StopReason("unrepairable_failure", "需要外部素材或人工处理：" + "；".join(labels))
    return None


def _stop_on_time_budget(run: Dict, candidate: Dict, ctx: Dict) -> Optional[StopReason]:
    cap = (run.get("stop_policy") or {}).get("max_elapsed_seconds")
    elapsed = ctx["budget"]["elapsed_seconds"]
    if cap is not None and elapsed >= int(cap):
        return StopReason("time_budget_exhausted", "总耗时 %d 秒已达到预算 %d 秒" % (elapsed, int(cap)))
    return None


def _stop_on_target(run: Dict, candidate: Dict, ctx: Dict) -> Optional[StopReason]:
    """总分达到停止目标。"""
    target = float(run["stop_policy"]["overall_target"])
    if ctx["current_overall"] >= target:
        return StopReason(
            "target_reached",
            "总分 %.3f 已达到停止目标 %.3f" % (ctx["current_overall"], target),
        )
    return None


def _stop_on_no_improvement(run: Dict, candidate: Dict, ctx: Dict) -> Optional[StopReason]:
    """连续无提升版本数达到上限。"""
    cap = int(run["stop_policy"]["max_no_improvement"])
    if ctx["no_improvement_streak"] >= cap:
        return StopReason(
            "no_improvement",
            "连续 %d 个版本无提升" % ctx["no_improvement_streak"],
        )
    return None


# 停止条件注册表：新增条件只需写一个 checker 并在此追加。
# checker 返回 None（继续迭代）或 StopReason（停机）；判断只复用 harness
# 既有信号（overall / no_improvement_streak / failure_report），不新造规则。
#
# 例如「素材缺失/需人工」等不可通过重写修复的停止条件：
#   def _stop_on_material(run, candidate, ctx) -> Optional[StopReason]:
#       if (candidate.get("failure_report") or {}).get("needs_material"):
#           return StopReason("needs_material", "报告素材不足以修复下列失败项")
#       return None
# 然后把函数名加入下方元祖即可，其余结算逻辑无需改动。
STOP_CONDITIONS = (
    _stop_on_unrepairable,
    _stop_on_time_budget,
    _stop_on_target,
    _stop_on_no_improvement,
)


def enforce_pre_generation_budget(run: Dict) -> Optional[StopReason]:
    \
    current = budget_state(run)
    run["budget_state"] = current
    ctx = {"budget": current}
    reason = _stop_on_time_budget(run, {}, ctx)
    if reason:
        run["stop_state"] = {
            "stopped": True,
            "code": reason.code,
            "reason": reason.message,
        }
        run["status"] = "completed"
    return reason


def _evaluate_stop(run: Dict, candidate: Dict) -> Optional[StopReason]:
    """按注册表顺序评估停止条件，命中第一个即返回。"""
    ctx = {
        "current_overall": float(
            current_revision(run)["judgment"]["overall"]
        ),
        "no_improvement_streak": int(run.get("no_improvement_streak") or 0),
        "budget": budget_state(run),
    }
    run["budget_state"] = ctx["budget"]
    for checker in STOP_CONDITIONS:
        reason = checker(run, candidate, ctx)
        if reason is not None:
            return reason
    return None


def settle_judged_revision(run: Dict, candidate: Dict) -> Dict:
    judgment = candidate.get("judgment") or {}
    overall = float(judgment.get("overall"))
    gate_result = None
    if candidate["version"] == "v1":
        accepted = True
        baseline = None
    else:
        parent = revision(run, candidate["parent_version"])
        parent_judgment = parent["judgment"]
        parent_overall = float(parent_judgment["overall"])
        rubric = run.get("rubric") or {}
        all_dims = [
            str(item.get("name"))
            for item in rubric.get("dimensions", [])
            if item.get("name")
        ]
        target_dims = (
            candidate.get("target_dimensions")
            or _target_dimensions(parent, rubric)
        )
        gate_result = evaluate_candidate_gate(
            _gate_score_view(judgment),
            _gate_score_view(parent_judgment),
            target_dims,
            all_dims,
            _drop_tolerance(rubric),
        )
        accepted = bool(gate_result["accepted"])
        baseline = parent_overall
        candidate["target_dimensions"] = target_dims
        candidate["adoption_gate"] = gate_result
    candidate["decision"] = "accepted" if accepted else "rejected"
    candidate["status"] = "judged"
    candidate["score_delta"] = (
        None if baseline is None else round(overall - baseline, 3)
    )
    if accepted:
        run["current_version"] = candidate["version"]
        run["no_improvement_streak"] = 0
    else:
        run["no_improvement_streak"] = (
            int(run.get("no_improvement_streak") or 0) + 1
        )

    stop_reason = _evaluate_stop(run, candidate)
    run["stop_state"] = {
        "stopped": stop_reason is not None,
        "code": stop_reason.code if stop_reason else None,
        "reason": stop_reason.message if stop_reason else None,
    }
    run["status"] = (
        "blocked"
        if stop_reason and stop_reason.code == "unrepairable_failure"
        else "completed" if stop_reason else "judged"
    )
    return {
        "decision": candidate["decision"],
        "score_delta": candidate["score_delta"],
        "adoption_gate": gate_result,
        "stopped": stop_reason is not None,
        "stop_code": stop_reason.code if stop_reason else None,
        "stop_reason": stop_reason.message if stop_reason else None,
    }

def actions(run: Dict) -> Dict:
    busy = any(
        job.get("status") in {"queued", "running"}
        for job in (run.get("jobs") or {}).values()
    )
    revisions = run.get("revisions") or []
    latest = revisions[-1] if revisions else None
    stopped = bool((run.get("stop_state") or {}).get("stopped"))
    current = None
    if run.get("current_version"):
        current = revision(run, run["current_version"])
    can_iterate = bool(
        current
        and current.get("status") == "judged"
        and not stopped
        and not busy
        and (not latest or latest.get("status") == "judged")
    )
    can_generate_initial = (
        run.get("status") == "imported" and not revisions and not busy
    )
    return {
        "generate": {
            "enabled": bool(can_generate_initial or can_iterate),
            "base_version": (
                current.get("version") if can_iterate and current else None
            ),
            "next_version": next_version(run),
        },
        "judge": {
            "enabled": bool(
                latest
                and latest.get("status") == "report_ready"
                and not stopped
                and not busy
            ),
            "version": latest.get("version") if latest else None,
        },
        # Backward-compatible alias for older clients. New UI only uses generate.
        "optimize": {
            "enabled": can_iterate,
            "base_version": current.get("version") if current else None,
        },
    }


def public_run(run: Dict, report_loader=None) -> Dict:
    payload = copy.deepcopy(run)
    payload["budget_state"] = budget_state(payload)
    if report_loader:
        for item in payload.get("revisions", []):
            item["report_text"] = report_loader(item["version"])
    payload["actions"] = actions(payload)
    payload["latest_version"] = (
        payload["revisions"][-1]["version"]
        if payload.get("revisions")
        else None
    )
    return payload
