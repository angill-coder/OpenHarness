# -*- coding: utf-8 -*-
"""模型 Judge 的批量执行逻辑。

一次 HTTP 操作覆盖当前版本全部 case；每个 case 仍使用独立 Prompt，
以避免报告之间互相污染，并通过有限并发缩短总耗时。
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, Iterable, List, Optional


_VALID_CHECK_VALUES = {"met", "partial", "miss", 1, 1.0, 0.5, 0, 0.0}
JUDGE_STRATEGY_SINGLE = "single_call"
JUDGE_STRATEGY_PER_DIMENSION = "per_dimension"
# 兼容沙盒阶段的旧名称；归一化后统一使用 per_dimension。
JUDGE_STRATEGY_SIX_AGENT = "six_agent"
JUDGE_STRATEGIES = {
    JUDGE_STRATEGY_SINGLE,
    JUDGE_STRATEGY_PER_DIMENSION,
}

_DIMENSION_CONTEXT_KEYS = {
    "traceability": ("background", "evidence_metadata"),
    "structure": (),
    "narrative": (),
    "insight": ("background", "evidence_metadata"),
    "coverage": ("background", "evidence_metadata", "ground_truth"),
    "expression": (),
}


def normalize_judge_strategy(value: str | None) -> str:
    strategy = str(value or JUDGE_STRATEGY_SINGLE).strip().lower()
    if strategy == JUDGE_STRATEGY_SIX_AGENT:
        return JUDGE_STRATEGY_PER_DIMENSION
    if strategy not in JUDGE_STRATEGIES:
        raise ValueError(
            "不支持的 Judge 策略: %s；可选: %s"
            % (strategy, ", ".join(sorted(JUDGE_STRATEGIES)))
        )
    return strategy


def _expected_check_ids(rubric: Dict) -> List[str]:
    return [
        str(check["id"])
        for dimension in rubric.get("dimensions", [])
        for check in dimension.get("checks", [])
        if check.get("id")
    ]


def _validate_payload(payload: Dict, expected_ids: Iterable[str]) -> tuple[Dict, Dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("checks"), dict):
        raise ValueError("judge 输出缺少 checks 对象")
    checks = payload["checks"]
    expected = list(expected_ids)
    missing = [check_id for check_id in expected if check_id not in checks]
    if missing:
        raise ValueError("judge 漏评 check: %s" % ", ".join(missing))
    invalid = [
        check_id
        for check_id in expected
        if checks.get(check_id) not in _VALID_CHECK_VALUES
    ]
    if invalid:
        raise ValueError("judge check 值非法: %s" % ", ".join(invalid))
    reasoning = payload.get("reasoning")
    if not isinstance(reasoning, dict):
        reasoning = {}
    return (
        {check_id: checks[check_id] for check_id in expected},
        {check_id: str(reasoning.get(check_id, "")) for check_id in expected},
    )


def _background_context(case: Dict) -> Dict:
    turns = []
    for turn in case.get("turns") or []:
        if not isinstance(turn, dict):
            continue
        if turn.get("round") not in (0, 1, "0", "1"):
            continue
        turns.append(
            {
                "round": turn.get("round"),
                "label": turn.get("label"),
                "prompt": (
                    turn.get("prompt")
                    or turn.get("input")
                    or turn.get("text")
                    or ""
                ),
            }
        )
    background = (
        {"round_0_1": turns}
        if turns
        else {"input": case.get("input") or {}}
    )
    if case.get("audience") not in (None, ""):
        background["audience"] = case.get("audience")
    if case.get("required_sections"):
        background["required_sections"] = (
            case.get("required_sections") or []
        )
    return background


def _full_case_context(case: Dict) -> Dict:
    ground_truth = case.get("ground_truth")
    if ground_truth is None:
        ground_truth = case.get("ground_truth_findings", {})
    context = {
        "case_id": str(case.get("case_id") or ""),
        "background": _background_context(case),
        "ground_truth": ground_truth or {},
    }
    if case.get("evidence_metadata"):
        context["evidence_metadata"] = case["evidence_metadata"]
    return context


def _dimension_case_context(case: Dict, dimension_name: str) -> Dict:
    full = _full_case_context(case)
    keys = _DIMENSION_CONTEXT_KEYS.get(dimension_name)
    if keys is None:
        # 自定义 rubric 的未知维度沿用完整上下文，避免兼容性退化。
        return full
    return {
        key: full[key]
        for key in ("case_id", *keys)
        if key in full
    }


def judge_cases(
    cases: Iterable[Dict],
    reports: Dict[str, str],
    rubric: Dict,
    build_prompt: Callable[[Dict, str, Dict], str],
    call_model: Callable[[str], str],
    extract_json: Callable[[str], Dict | None],
    parallel: int = 3,
    on_result: Optional[Callable[[Dict], Optional[Dict]]] = None,
    strategy: str = JUDGE_STRATEGY_SINGLE,
) -> List[Dict]:
    """批量 Judge，返回与输入 case 顺序一致的逐 case 结果。

    单 case 的模型错误或解析错误不会中断其它 case。没有报告的 case 会
    明确标记为 ``missing_report``，由调用方决定是否允许后续版本推进。
    """
    case_list = list(cases)
    strategy = normalize_judge_strategy(strategy)
    expected = _expected_check_ids(rubric)
    if not expected:
        raise ValueError("Rubric 未配置任何 Judge checks")

    def run_one(index_case):
        index, case = index_case
        case_id = str(case.get("case_id") or "")
        report = (reports.get(case_id) or "").strip()
        if not report:
            return index, {
                "case_id": case_id,
                "status": "missing_report",
                "error": "当前版本尚未导入报告",
            }
        judge_started = time.monotonic()
        call_traces = []

        def invoke(prompt: str, dimension: str) -> str:
            call_started = time.monotonic()
            trace = {
                "dimension": dimension or "all",
                "promptSha256": hashlib.sha256(
                    prompt.encode("utf-8")
                ).hexdigest(),
                "promptChars": len(prompt),
            }
            try:
                response = call_model(prompt)
                trace.update({
                    "status": "completed",
                    "durationMs": int(
                        (time.monotonic() - call_started) * 1000
                    ),
                    "response": str(response)[:12000],
                })
                call_traces.append(trace)
                return response
            except Exception as exc:
                trace.update({
                    "status": "failed",
                    "durationMs": int(
                        (time.monotonic() - call_started) * 1000
                    ),
                    "error": str(exc),
                })
                call_traces.append(trace)
                raise
        try:
            if strategy == JUDGE_STRATEGY_SINGLE:
                prompt = build_prompt(
                    rubric,
                    report,
                    _full_case_context(case),
                )
                raw = invoke(prompt, "all")
                parsed = extract_json(raw)
                checks, reasoning = _validate_payload(parsed, expected)
                model_calls = 1
            else:
                checks, reasoning = {}, {}
                dimensions = [
                    dimension
                    for dimension in rubric.get("dimensions", [])
                    if dimension.get("checks")
                ]
                for dimension in dimensions:
                    dimension_rubric = dict(rubric)
                    dimension_rubric["dimensions"] = [dimension]
                    dimension_ids = _expected_check_ids(dimension_rubric)
                    dimension_name = str(
                        dimension.get("name") or ""
                    )
                    try:
                        prompt = build_prompt(
                            dimension_rubric,
                            report,
                            _dimension_case_context(
                                case,
                                dimension_name,
                            ),
                        )
                        raw = invoke(prompt, dimension_name)
                        parsed = extract_json(raw)
                        dim_checks, dim_reasoning = _validate_payload(
                            parsed,
                            dimension_ids,
                        )
                    except Exception as exc:
                        raise RuntimeError(
                            "维度 %s Judge 失败: %s"
                            % (dimension_name or "unknown", exc)
                        ) from exc
                    checks.update(dim_checks)
                    reasoning.update(dim_reasoning)
                model_calls = len(dimensions)
            return index, {
                "case_id": case_id,
                "status": "judged",
                "checks": checks,
                "reasoning": reasoning,
                "judge_meta": {
                    "strategy": strategy,
                    "model_calls": model_calls,
                },
                "judge_trace": {
                    "status": "completed",
                    "strategy": strategy,
                    "durationMs": int(
                        (time.monotonic() - judge_started) * 1000
                    ),
                    "calls": call_traces,
                },
            }
        except Exception as exc:
            return index, {
                "case_id": case_id,
                "status": "failed",
                "error": str(exc),
                "judge_trace": {
                    "status": "failed",
                    "strategy": strategy,
                    "durationMs": int(
                        (time.monotonic() - judge_started) * 1000
                    ),
                    "calls": call_traces,
                },
            }

    if not case_list:
        return []
    workers = max(1, min(int(parallel or 1), len(case_list)))
    ordered = [None] * len(case_list)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(run_one, item): item[0]
            for item in enumerate(case_list)
        }
        for future in as_completed(futures):
            index, result = future.result()
            if on_result is not None:
                replacement = on_result(result)
                if replacement is not None:
                    result = replacement
            ordered[index] = result
    return ordered
