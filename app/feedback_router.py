# -*- coding: utf-8 -*-
"""Classify expert feedback before Rubrics or Memory processing."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable

import llm_client


DESTINATIONS = {"rubric", "memory", "ignore"}


def build_prompt(feedback, rubric, reports):
    payload = {
        "feedback": feedback,
        "current_rubric": rubric,
        "reports": reports,
    }
    return "\n".join([
        "你是 OpenHarness Feedback Router。只负责分类，不修改 Rubrics，也不提炼 Memory。",
        "逐条判断 Feedback 的唯一去向：",
        "- rubric：可跨用户、跨同类报告稳定评判的通用质量标准；",
        "- memory：用户个人写作偏好、特定受众/报告类型/情境的可复用要求；",
        "- ignore：数据缺失、单 case 事实纠错、裸操作或不属于写作质量/偏好的内容。",
        "不要判断 L0/L1/L2；这是后续 Memory Agent 的职责。",
        "只输出 JSON：{routes:[{feedback_id,destination,reason,confidence}]}。",
        "confidence 为 0 到 1。每个输入 feedback_id 必须且只能出现一次。",
        "\n## 输入\n" + json.dumps(payload, ensure_ascii=False, indent=2),
    ])


def route_feedback(
    feedback: Iterable[Dict[str, Any]],
    rubric: Dict[str, Any],
    reports: list[Dict[str, Any]],
    model_config: Dict[str, Any],
    call_model: Callable[..., str] | None = None,
):
    items = list(feedback)
    caller = call_model or llm_client.call_llm
    raw = caller(
        build_prompt(items, rubric, reports),
        timeout_seconds="600",
        retries="2",
        backend=model_config.get("llm_backend"),
        model=model_config.get("llm_model"),
        reasoning_effort=model_config.get("llm_reasoning_effort"),
    )
    parsed = llm_client.extract_json(raw)
    routes = parsed.get("routes") if isinstance(parsed, dict) else None
    if not isinstance(routes, list):
        raise ValueError("Feedback Router 未返回 routes")
    expected = {str(item.get("feedback_id") or "") for item in items}
    values = {}
    for route in routes:
        feedback_id = str(route.get("feedback_id") or "")
        destination = str(route.get("destination") or "")
        if feedback_id not in expected or feedback_id in values:
            raise ValueError("Feedback Router 返回了重复或未知 feedback_id")
        if destination not in DESTINATIONS:
            raise ValueError("非法 Feedback 去向: %s" % destination)
        try:
            confidence = max(0.0, min(1.0, float(route.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        values[feedback_id] = {
            "feedback_id": feedback_id,
            "destination": destination,
            "reason": str(route.get("reason") or "").strip(),
            "confidence": confidence,
        }
    missing = expected - set(values)
    if missing:
        raise ValueError("Feedback Router 遗漏: %s" % ", ".join(sorted(missing)))
    return [values[str(item["feedback_id"])] for item in items]
