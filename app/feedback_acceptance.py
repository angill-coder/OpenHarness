# -*- coding: utf-8 -*-
"""Feedback Acceptance Evaluator for Rubrics Loop validation experiments."""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Callable, Dict, Optional

import llm_client


ACCEPTANCE_STATUSES = {
    "followed", "partially_followed", "not_followed", "unable_to_judge",
}
STABILITY_STATUSES = {
    "stable", "improving", "unstable", "not_improved", "unknown",
}
FAILURE_LAYERS = {
    "none", "rubric_gap", "rubric_not_operational",
    "skill_translation_failure", "runner_execution_failure", "data_issue",
    "one_off_feedback", "feedback_conflict", "unknown",
}


class FeedbackAcceptanceError(ValueError):
    pass


def _prompt(item: Dict[str, Any], context: Dict[str, Any]) -> str:
    payload = {
        "feedback": item,
        "rubric_changes": context.get("rubric_changes") or [],
        "feedback_analysis": context.get("feedback_analysis") or [],
        "reports": context.get("reports") or [],
        "judge_signals": context.get("judge_signals") or [],
        "skill_versions": context.get("skill_versions") or [],
    }
    return "\n".join([
        "你是 OpenHarness 的 Feedback Acceptance Evaluator。你的任务不是重新打 Judge 分，也不是修改 Rubrics，而是判断本条专家 Feedback 在两轮 Skill 迭代后的报告中是否真正被执行。",
        "必须比较 baseline 与每一轮迭代报告；Judge 分只作辅助信号，不能替代阅读报告。",
        "status 只能是 followed / partially_followed / not_followed / unable_to_judge。",
        "stability 只能是 stable / improving / unstable / not_improved / unknown。只有连续两轮均遵循，才可判 stable。",
        "failure_layer 只能是 none / rubric_gap / rubric_not_operational / skill_translation_failure / runner_execution_failure / data_issue / one_off_feedback / feedback_conflict / unknown。",
        "evidence 必须引用输入报告中逐字存在的 Markdown 原文；每项含 phase、skill_version、case_id、quote、assessment。没有证据就返回空数组，不得编造。",
        "next_action 只能给出人工可执行的下一步；rubric_suggestions 仅在仍需改 Rubrics 时填写，不得自动改写 Rubrics。",
        "只输出一个 JSON 对象，字段为 feedback_id、status、stability、failure_layer、reason、evidence、next_action、rubric_suggestions。",
        "\n## 输入\n" + json.dumps(payload, ensure_ascii=False, indent=2),
    ])


def _normalize_result(
    parsed: Dict[str, Any], feedback_id: str, reports: list[Dict[str, Any]]
) -> Dict[str, Any]:
    if str(parsed.get("feedback_id") or "") != feedback_id:
        raise FeedbackAcceptanceError("AI 验收返回了错误的 feedback_id")
    status = str(parsed.get("status") or "")
    stability = str(parsed.get("stability") or "")
    failure_layer = str(parsed.get("failure_layer") or "")
    if status not in ACCEPTANCE_STATUSES:
        raise FeedbackAcceptanceError("AI 验收返回了非法 status: %s" % status)
    if stability not in STABILITY_STATUSES:
        raise FeedbackAcceptanceError(
            "AI 验收返回了非法 stability: %s" % stability
        )
    if failure_layer not in FAILURE_LAYERS:
        raise FeedbackAcceptanceError(
            "AI 验收返回了非法 failure_layer: %s" % failure_layer
        )

    report_index = {
        (
            str(report.get("phase") or ""),
            str(report.get("skill_version") or ""),
            str(report.get("case_id") or ""),
        ): str(report.get("report_text") or "")
        for report in reports
    }
    evidence = []
    for item in parsed.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("phase") or ""),
            str(item.get("skill_version") or ""),
            str(item.get("case_id") or ""),
        )
        quote = str(item.get("quote") or "").strip()
        if quote and quote in report_index.get(key, ""):
            evidence.append({
                "phase": key[0],
                "skill_version": key[1],
                "case_id": key[2],
                "quote": quote,
                "assessment": str(item.get("assessment") or "").strip(),
            })

    suggestions = parsed.get("rubric_suggestions") or []
    if not isinstance(suggestions, list):
        suggestions = [str(suggestions)]
    return {
        "feedback_id": feedback_id,
        "status": status,
        "stability": stability,
        "failure_layer": failure_layer,
        "reason": str(parsed.get("reason") or "").strip(),
        "evidence": evidence,
        "next_action": str(parsed.get("next_action") or "").strip(),
        "rubric_suggestions": [
            str(value).strip() for value in suggestions if str(value).strip()
        ],
    }


def _overall(results: list[Dict[str, Any]]) -> str:
    statuses = [item.get("status") for item in results]
    if statuses and all(value == "followed" for value in statuses):
        return "followed"
    if any(value == "not_followed" for value in statuses):
        return "not_followed"
    if any(value == "partially_followed" for value in statuses):
        return "partially_followed"
    return "unable_to_judge"


def evaluate(
    feedback: list[Dict[str, Any]],
    contexts: Dict[str, Dict[str, Any]],
    model_config: Dict[str, Any],
    existing_results: Optional[Dict[str, Dict[str, Any]]] = None,
    on_result: Optional[Callable[[Dict[str, Any]], None]] = None,
    call_model: Optional[Callable[..., str]] = None,
) -> Dict[str, Any]:
    """Evaluate each feedback independently so partial work can be resumed."""
    caller = call_model or llm_client.call_llm
    completed = copy.deepcopy(existing_results or {})
    ordered = []
    for item in feedback:
        feedback_id = str(item.get("feedback_id") or "")
        if not feedback_id:
            continue
        if feedback_id in completed:
            ordered.append(completed[feedback_id])
            continue
        context = contexts.get(feedback_id) or {}
        prompt = _prompt(item, context)
        raw = caller(
            prompt,
            timeout_seconds=os.environ.get(
                "FEEDBACK_ACCEPTANCE_TIMEOUT_SECONDS", "600"
            ),
            retries=os.environ.get("FEEDBACK_ACCEPTANCE_RETRIES", "2"),
            backend=model_config.get("llm_backend"),
            model=model_config.get("llm_model"),
            reasoning_effort=model_config.get("llm_reasoning_effort"),
        )
        parsed = llm_client.extract_json(raw)
        if not isinstance(parsed, dict):
            raise FeedbackAcceptanceError("AI 验收未返回有效 JSON")
        result = _normalize_result(
            parsed, feedback_id, context.get("reports") or []
        )
        result.update({
            "feedback_scope": str(item.get("scope") or ""),
            "feedback_content": str(item.get("content") or "").strip(),
            "feedback_quote": str(item.get("quote") or "").strip(),
        })
        completed[feedback_id] = result
        ordered.append(result)
        if on_result:
            on_result(copy.deepcopy(result))

    counts = {
        status: sum(item.get("status") == status for item in ordered)
        for status in sorted(ACCEPTANCE_STATUSES)
    }
    return {
        "status": "completed",
        "overall_status": _overall(ordered),
        "counts": counts,
        "feedback_results": ordered,
        "model_config": copy.deepcopy(model_config),
    }
