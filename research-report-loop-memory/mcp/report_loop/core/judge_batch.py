# -*- coding: utf-8 -*-
"""模型 Judge 的批量执行逻辑。

一次 HTTP 操作覆盖当前版本全部 case；每个 case 仍使用独立 Prompt，
以避免报告之间互相污染，并通过有限并发缩短总耗时。
"""

from __future__ import annotations

import hashlib
import re
import threading
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
DEFAULT_JUDGE_MAX_RETRIES = 3
DEFAULT_DIMENSION_PARALLELISM = 6

_DIMENSION_CONTEXT_KEYS = {
    "traceability": ("background", "structured_data"),
    "structure": (),
    "narrative": (),
    "insight": ("background", "structured_data"),
    "coverage": ("background", "structured_data"),
    "expression": ("delivery_constraints", "report_stats"),
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


def normalize_judge_max_retries(value=None) -> int:
    raw = DEFAULT_JUDGE_MAX_RETRIES if value is None else value
    if isinstance(raw, bool) or (
        isinstance(raw, float) and not raw.is_integer()
    ):
        raise ValueError("Judge 重试次数必须是非负整数")
    try:
        retries = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Judge 重试次数必须是非负整数") from exc
    if retries < 0:
        raise ValueError("Judge 重试次数必须是非负整数")
    return retries


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


def _delivery_constraints_context(case: Dict) -> Dict:
    value = case.get("delivery_constraints")
    if not isinstance(value, dict):
        case_input = case.get("input") or {}
        if isinstance(case_input, dict):
            value = case_input.get("report_length")
    result = {}
    if isinstance(value, dict):
        for key in ("max_pages", "max_chars", "chars_per_page"):
            if value.get(key) not in (None, ""):
                result[key] = value[key]
        if value.get("counting_rule"):
            result["counting_rule"] = str(value["counting_rule"])
    prompt_candidates = []
    for turn in case.get("turns") or []:
        if not isinstance(turn, dict):
            continue
        prompt_candidates.append(
            str(
                turn.get("prompt")
                or turn.get("input")
                or turn.get("text")
                or ""
            )
        )
    case_input = case.get("input") or {}
    if isinstance(case_input, dict):
        prompt_candidates.append(str(case_input.get("intake") or ""))
    for prompt_text in prompt_candidates:
        length_lines = [
            line.strip()
            for line in prompt_text.splitlines()
            if "报告篇幅" in line or "篇幅要求" in line
        ]
        if length_lines:
            result["user_prompt"] = "\n".join(length_lines)
            if "max_pages" not in result:
                match = re.search(r"(\d+)\s*页", result["user_prompt"])
                if match:
                    result["max_pages"] = int(match.group(1))
            if "max_chars" not in result:
                match = re.search(
                    r"(?:不超过|控制在|约)?\s*(\d+)\s*(?:字|个?字符)",
                    result["user_prompt"],
                )
                if match:
                    result["max_chars"] = int(match.group(1))
            if "chars_per_page" not in result and "max_pages" in result:
                result["chars_per_page"] = 1000
            if (
                "max_chars" not in result
                and result.get("max_pages")
                and result.get("chars_per_page")
            ):
                result["max_chars"] = (
                    int(result["max_pages"])
                    * int(result["chars_per_page"])
                )
            break
    return result


def _report_stats(report_text: str) -> Dict:
    """Return deterministic Markdown-visible length metrics for E4."""
    text = str(report_text or "")
    visible = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    visible = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", visible)
    visible = re.sub(r"(?m)^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$", "", visible)
    visible = re.sub(r"(?m)^\s{0,3}(?:#{1,6}|>|[-+*]|\d+[.)])\s+", "", visible)
    visible = re.sub(r"```[^\n]*|```", "", visible)
    visible = re.sub(r"[*_`~|]", "", visible)
    visible_chars = len(re.sub(r"\s+", "", visible))
    return {
        "raw_chars": len(text),
        "visible_chars": visible_chars,
        "estimated_pages_at_1000_chars": round(visible_chars / 1000.0, 3),
        "counting_rule": (
            "去除 Markdown 标记与空白后的可见字符；表格单元格文字计入"
        ),
    }


def _full_case_context(case: Dict, report_text: str = "") -> Dict:
    context = {
        "case_id": str(case.get("case_id") or ""),
        "background": _background_context(case),
        "report_stats": _report_stats(report_text),
    }
    delivery_constraints = _delivery_constraints_context(case)
    if delivery_constraints:
        context["delivery_constraints"] = delivery_constraints
    if case.get("structured_data"):
        context["structured_data"] = case["structured_data"]
    return context


def _dimension_case_context(
    case: Dict,
    dimension_name: str,
    report_text: str = "",
) -> Dict:
    full = _full_case_context(case, report_text)
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
    max_retries: int = DEFAULT_JUDGE_MAX_RETRIES,
    existing_judgments: Optional[Dict[str, Dict]] = None,
    dimension_parallel: int = DEFAULT_DIMENSION_PARALLELISM,
) -> List[Dict]:
    """批量 Judge，返回与输入 case 顺序一致的逐 case 结果。

    单 case 的模型错误或解析错误不会中断其它 case。没有报告的 case 会
    明确标记为 ``missing_report``，由调用方决定是否允许后续版本推进。
    """
    case_list = list(cases)
    strategy = normalize_judge_strategy(strategy)
    max_retries = normalize_judge_max_retries(max_retries)
    existing_judgments = existing_judgments or {}
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
        trace_lock = threading.Lock()

        def invoke(prompt: str, dimension: str, expected_ids):
            last_error = None
            for retry_index in range(max_retries + 1):
                call_started = time.monotonic()
                trace = {
                    "dimension": dimension or "all",
                    "attempt": retry_index + 1,
                    "retry": retry_index,
                    "promptSha256": hashlib.sha256(
                        prompt.encode("utf-8")
                    ).hexdigest(),
                    "promptChars": len(prompt),
                }
                try:
                    response = call_model(prompt)
                    trace["response"] = str(response)[:12000]
                    parsed = extract_json(response)
                    checks, reasoning = _validate_payload(
                        parsed,
                        expected_ids,
                    )
                    trace.update({
                        "status": "completed",
                        "durationMs": int(
                            (time.monotonic() - call_started) * 1000
                        ),
                    })
                    with trace_lock:
                        call_traces.append(trace)
                    return checks, reasoning, retry_index + 1
                except Exception as exc:
                    last_error = exc
                    trace.update({
                        "status": "failed",
                        "durationMs": int(
                            (time.monotonic() - call_started) * 1000
                        ),
                        "error": str(exc),
                    })
                    with trace_lock:
                        call_traces.append(trace)
            raise RuntimeError(
                "%s Judge 失败（已重试 %d 次）: %s"
                % (dimension or "整份报告", max_retries, last_error)
            ) from last_error
        try:
            if strategy == JUDGE_STRATEGY_SINGLE:
                prompt = build_prompt(
                    rubric,
                    report,
                    _full_case_context(case, report),
                )
                checks, reasoning, model_attempts = invoke(
                    prompt,
                    "all",
                    expected,
                )
                model_calls = 1
                errors = []
            else:
                previous = existing_judgments.get(case_id) or {}
                checks = dict(previous.get("checks") or {})
                reasoning = dict(previous.get("reasoning") or {})
                errors_by_dimension = {}
                dimensions = [
                    dimension
                    for dimension in rubric.get("dimensions", [])
                    if dimension.get("checks")
                ]
                pending_dimensions = []
                for dimension_index, dimension in enumerate(dimensions):
                    dimension_rubric = dict(rubric)
                    dimension_rubric["dimensions"] = [dimension]
                    dimension_ids = _expected_check_ids(dimension_rubric)
                    dimension_name = str(
                        dimension.get("name") or ""
                    )
                    if all(
                        check_id in checks
                        for check_id in dimension_ids
                    ):
                        continue
                    pending_dimensions.append(
                        (
                            dimension_index,
                            dimension_name,
                            dimension_rubric,
                            dimension_ids,
                        )
                    )

                def run_dimension(item):
                    (
                        dimension_index,
                        dimension_name,
                        dimension_rubric,
                        dimension_ids,
                    ) = item
                    prompt = build_prompt(
                        dimension_rubric,
                        report,
                        _dimension_case_context(
                            case,
                            dimension_name,
                            report,
                        ),
                    )
                    try:
                        dim_checks, dim_reasoning, _attempts = invoke(
                            prompt,
                            dimension_name,
                            dimension_ids,
                        )
                        return (
                            dimension_index,
                            dimension_name,
                            dim_checks,
                            dim_reasoning,
                            None,
                        )
                    except Exception as exc:
                        return (
                            dimension_index,
                            dimension_name,
                            {},
                            {},
                            str(exc),
                        )

                dimension_results = []
                workers = max(
                    1,
                    min(
                        int(dimension_parallel or 1),
                        len(pending_dimensions) or 1,
                    ),
                )
                if pending_dimensions:
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = [
                            pool.submit(run_dimension, item)
                            for item in pending_dimensions
                        ]
                        for future in as_completed(futures):
                            dimension_results.append(future.result())
                for (
                    dimension_index,
                    dimension_name,
                    dim_checks,
                    dim_reasoning,
                    error,
                ) in sorted(dimension_results, key=lambda item: item[0]):
                    if error:
                        errors_by_dimension[dimension_index] = (
                            f"{dimension_name}: {error}"
                        )
                        continue
                    checks.update(dim_checks)
                    reasoning.update(dim_reasoning)
                errors = [
                    errors_by_dimension[index]
                    for index in sorted(errors_by_dimension)
                ]
                model_calls = len(pending_dimensions)
                model_attempts = len(call_traces)
                dimension_order = {
                    str(dimension.get("name") or ""): index
                    for index, dimension in enumerate(dimensions)
                }
                call_traces.sort(
                    key=lambda trace: (
                        dimension_order.get(str(trace.get("dimension")), 999),
                        int(trace.get("attempt") or 0),
                    )
                )
            status = "judged" if not errors else (
                "partial" if checks else "failed"
            )
            judge_meta = {
                "strategy": strategy,
                "model_calls": model_calls,
            }
            if strategy == JUDGE_STRATEGY_PER_DIMENSION:
                judge_meta["dimension_parallelism"] = min(
                    max(1, int(dimension_parallel or 1)),
                    max(1, model_calls),
                )
            if model_attempts > model_calls:
                judge_meta.update({
                    "model_attempts": model_attempts,
                    "retries": model_attempts - model_calls,
                })
            return index, {
                "case_id": case_id,
                "status": status,
                "checks": checks,
                "reasoning": reasoning,
                **({"error": "; ".join(errors)} if errors else {}),
                "judge_meta": judge_meta,
                "judge_trace": {
                    "status": "completed" if not errors else status,
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
    if len(case_list) == 1:
        _, result = run_one((0, case_list[0]))
        if on_result is not None:
            replacement = on_result(result)
            if replacement is not None:
                result = replacement
        return [result]

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


def judge_report(
    case: Dict,
    report: str,
    rubric: Dict,
    build_prompt: Callable[[Dict, str, Dict], str],
    call_model: Callable[[str], str],
    extract_json: Callable[[str], Dict | None],
    strategy: str = JUDGE_STRATEGY_SINGLE,
    max_retries: int = DEFAULT_JUDGE_MAX_RETRIES,
    existing_judgment: Optional[Dict] = None,
    dimension_parallel: int = DEFAULT_DIMENSION_PARALLELISM,
) -> Dict:
    """Judge exactly one report while preserving the OpenHarness check contract."""
    case_id = str(case.get("case_id") or "")
    return judge_cases(
        [case],
        {case_id: report},
        rubric,
        build_prompt,
        call_model,
        extract_json,
        parallel=1,
        strategy=strategy,
        max_retries=max_retries,
        dimension_parallel=dimension_parallel,
        existing_judgments=(
            {case_id: existing_judgment} if existing_judgment else None
        ),
    )[0]
